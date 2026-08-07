"""Job de estrenos: detecta episodios recién emitidos de las series que sigues y
estrenos de tu wishlist, y avisa por Telegram. Sin TMDB key no puede traer
episodios nuevos, pero sí avisa de los que ya estén en la base con fecha pasada."""
import logging
import os
import sqlite3
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


def refresh_following_episodes(db: Session) -> None:
    """Trae episodios nuevos de las series que sigues (requiere TMDB key)."""
    if not settings.tmdb_api_key:
        return
    series = db.query(MediaItem).filter(
        MediaItem.media_type.in_(EPISODIC_TYPES),
        MediaItem.status.in_(FOLLOWING),
        MediaItem.external_source == "tmdb",
        MediaItem.external_id.isnot(None),
    ).all()
    for it in series:
        d = tmdb.get_tv_details(it.external_id)
        if d:
            metadata.load_episodes(db, it, d["seasons"])
    db.commit()


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
    )
    scheduler.add_job(backup_database, "cron", hour=4, minute=45, id="daily_backup")
    scheduler.start()
    return scheduler
