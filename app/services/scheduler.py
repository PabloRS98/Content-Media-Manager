"""Job de estrenos: detecta episodios recién emitidos de las series que sigues y
estrenos de tu wishlist, y avisa por Telegram. Sin TMDB key no puede traer
episodios nuevos, pero sí avisa de los que ya estén en la base con fecha pasada."""
import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session, joinedload

from ..config import settings
from ..database import SessionLocal
from ..models import EPISODIC_TYPES, Episode, MediaItem, MediaStatus
from . import metadata, telegram, tmdb

logger = logging.getLogger(__name__)

# Estados que consideramos "siguiendo" para avisos
FOLLOWING = (MediaStatus.EN_PROGRESO, MediaStatus.PENDIENTE, MediaStatus.WISHLIST)


# Cuánto tiempo se sigue considerando "viva" una temporada tras su último
# episodio conocido. Una temporada que cerró hace más de esto no va a recibir
# episodios nuevos, así que pedirla es una petición HTTP tirada.
VENTANA_TEMPORADA_VIVA = timedelta(days=30)

# Series refrescadas a la vez. Ojo: fetch_tv_episodes ya paraleliza las
# temporadas de UNA serie con 5 hilos, así que esto multiplica -- 4 x 5 = 20
# peticiones simultáneas como mucho, holgadamente por debajo de lo que admite
# TMDB (del orden de 50 por segundo).
SERIES_EN_PARALELO = 4


def temporadas_que_pueden_cambiar(item: MediaItem, temporadas_remotas: list[int],
                                  hoy: date | None = None) -> list[int]:
    """De las temporadas que declara TMDB, las que pueden traer novedades.

    `load_episodes` pedía TODAS las temporadas en cada pasada, incluidas las que
    terminaron hace años y no van a cambiar nunca. Se piden solo:

    - las que aún no tenemos en la base,
    - las que tienen algún episodio sin emitir o emitido hace poco,
    - las que no tienen ninguna fecha conocida (no se puede afirmar que estén
      cerradas),
    - y siempre la última, que es donde aparecen los episodios nuevos y por
      donde se entera uno de una renovación.

    Con esto, una serie terminada deja de consumir peticiones por completo
    salvo la comprobación de su última temporada.
    """
    if not temporadas_remotas:
        return []
    hoy = hoy or date.today()
    limite = hoy - VENTANA_TEMPORADA_VIVA

    ultima_fecha: dict[int, date | None] = {}
    for ep in item.episodes:
        if ep.season_number not in ultima_fecha:
            ultima_fecha[ep.season_number] = None
        actual = ultima_fecha[ep.season_number]
        if ep.air_date and (actual is None or ep.air_date > actual):
            ultima_fecha[ep.season_number] = ep.air_date

    candidatas = set()
    for sn in temporadas_remotas:
        if sn not in ultima_fecha:
            candidatas.add(sn)
            continue
        fecha = ultima_fecha[sn]
        if fecha is None or fecha >= limite:
            candidatas.add(sn)
    candidatas.add(max(temporadas_remotas))
    return sorted(candidatas)


def _refrescar_una_serie(item_id: int, session_factory) -> int:
    """Refresca una serie con su propia sesión. Devuelve episodios añadidos.

    Sesión por hilo (patrón de projects-dashboard) y commit por serie: antes
    había un único commit al final de todas, así que si la 25ª fallaba se
    perdía el trabajo de las 24 anteriores.
    """
    db = session_factory()
    try:
        item = db.get(MediaItem, item_id)
        if not item or not item.external_id:
            return 0
        detalles = tmdb.get_tv_details(item.external_id)
        if not detalles:
            return 0
        temporadas = temporadas_que_pueden_cambiar(item, detalles["seasons"])
        if not temporadas:
            return 0
        añadidos = metadata.load_episodes(db, item, temporadas)
        db.commit()
        return añadidos
    except Exception:
        logger.exception("Fallo al refrescar los episodios del ítem %s", item_id)
        db.rollback()
        return 0
    finally:
        db.close()


def refresh_following_episodes(db: Session, session_factory=SessionLocal) -> None:
    """Trae episodios nuevos de las series que sigues (requiere TMDB key).

    Antes era 1+N peticiones HTTP secuenciales por serie: con 30 series de 5
    temporadas de media, 30 + 150 = 180 peticiones encadenadas con 10 s de
    timeout cada una. En el peor caso, media hora de job -- y corre también 25 s
    después de arrancar, así que un reinicio del contenedor lo disparaba entero.
    """
    if not settings.tmdb_api_key:
        return
    ids = [
        fila[0] for fila in db.query(MediaItem.id).filter(
            MediaItem.media_type.in_(EPISODIC_TYPES),
            MediaItem.status.in_(FOLLOWING),
            MediaItem.external_source == "tmdb",
            MediaItem.external_id.isnot(None),
        ).all()
    ]
    if not ids:
        return

    with ThreadPoolExecutor(max_workers=SERIES_EN_PARALELO) as pool:
        total = sum(pool.map(lambda i: _refrescar_una_serie(i, session_factory), ids))
    if total:
        logger.info("Refresco de series: %d episodios nuevos", total)


def check_new_episodes(db: Session) -> int:
    """Avisa de episodios ya emitidos aún sin notificar. Devuelve cuántos."""
    today = date.today()
    sent = 0
    eps = (
        db.query(Episode).join(MediaItem)
        # El join filtra, pero no carga la relación: sin esto, cada
        # `ep.item.title` del bucle de abajo dispara un SELECT aparte. Es el
        # mismo N+1 que ya se resolvió en catalog.stats, con el mismo remedio.
        .options(joinedload(Episode.item))
        .filter(
            Episode.notified.is_(False),
            Episode.air_date.isnot(None),
            Episode.air_date <= today,
            # Especiales/recaps (temporada 0): no generan aviso, igual que no
            # aparecen en "Próximamente" (home._upcoming).
            Episode.season_number != 0,
            MediaItem.status.in_(FOLLOWING),
        )
        .all()
    )
    for ep in eps:
        enviado = telegram.send_message(
            "📺 <b>%s</b> — nuevo episodio %s%s" % (
                telegram.esc(ep.item.title), ep.code,
                (" · " + telegram.esc(ep.name)) if ep.name else "")
        )
        # Marcar solo si el envío funcionó. Antes se marcaba siempre, y como
        # send_message se traga la excepción, un aviso que Telegram rechazaba
        # (un título con "&" da 400) quedaba como notificado y no se
        # reintentaba jamás: el aviso se perdía para siempre.
        if enviado:
            ep.notified = True
            sent += 1
    db.commit()
    return sent


def check_releases(db: Session) -> int:
    """Avisa de estrenos de la wishlist ya disponibles. Devuelve cuántos."""
    today = date.today()
    sent = 0
    items = (
        db.query(MediaItem)
        .filter(
            MediaItem.status == MediaStatus.WISHLIST,
            MediaItem.release_notified.is_(False),
            MediaItem.release_date.isnot(None),
            MediaItem.release_date <= today,
        )
        .all()
    )
    for it in items:
        # Mismo criterio que en check_new_episodes: escapar el título y marcar
        # solo si el envío funcionó.
        if telegram.send_message("🎉 Ya disponible: <b>%s</b>" % telegram.esc(it.title)):
            it.release_notified = True
            sent += 1
    db.commit()
    return sent


def run_alerts() -> None:
    db = SessionLocal()
    try:
        refresh_following_episodes(db)
        n = check_new_episodes(db)
        r = check_releases(db)
        if n or r:
            logger.info("Avisos enviados: %d episodios, %d estrenos", n, r)
    except Exception:
        logger.exception("Fallo en el job de estrenos")
    finally:
        db.close()


def backup_database(dest_path: str | None = None) -> str:
    """Copia consistente de la BD (API de backup de SQLite, segura aunque haya
    escrituras). Sin `dest_path` va a /data/backups/media-AAAAMMDD.db y rota
    las copias antiguas (se conservan `backup_keep`). Devuelve la ruta creada."""
    if dest_path is None:
        backups_dir = os.path.join(os.path.dirname(settings.db_path), "backups")
        os.makedirs(backups_dir, exist_ok=True)
        dest_path = os.path.join(backups_dir, "media-%s.db" % date.today().strftime("%Y%m%d"))

    src = sqlite3.connect(settings.db_path)
    dst = sqlite3.connect(dest_path)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()

    # Rotación (solo en el directorio estándar de backups)
    backups_dir = os.path.dirname(dest_path)
    if os.path.basename(backups_dir) == "backups":
        existing = sorted(
            f for f in os.listdir(backups_dir)
            if f.startswith("media-") and f.endswith(".db")
        )
        # existing[:-0] es existing[:0] (lista vacía), no "todos": con
        # BACKUP_KEEP=0 no se borraba ningún backup, justo lo contrario de
        # la intención. Con 0 se conserva al menos el que se acaba de crear.
        a_borrar = existing[:-settings.backup_keep] if settings.backup_keep > 0 else existing[:-1]
        for old in a_borrar:
            os.remove(os.path.join(backups_dir, old))
    logger.info("Backup de la BD guardado en %s", dest_path)
    return dest_path


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    # next_run_time debe llevar tz: el reloj del contenedor va en UTC y un datetime
    # naive se interpreta en la zona del scheduler (quedaría "en el pasado").
    scheduler.add_job(
        run_alerts, "cron", hour=9, minute=0,
        next_run_time=datetime.now(scheduler.timezone) + timedelta(seconds=25),  # también al poco de arrancar
        id="media_alerts",
        # Sin esto, un reinicio del contenedor durante una tanda larga deja dos
        # ejecuciones solapadas: el disparo de los 25 s de arranque se suma al
        # de las 9:00 (o al que siguiera corriendo).
        max_instances=1,
    )
    scheduler.add_job(backup_database, "cron", hour=4, minute=45, id="daily_backup")
    scheduler.start()
    return scheduler
