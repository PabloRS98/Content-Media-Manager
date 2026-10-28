"""Orquestación al crear/gestionar un ítem: enriquece metadatos (duración, reparto,
saga) y, en series, trae sus episodios desde TMDB. También recalcula el estado de
una serie/podcast a partir de qué episodios están vistos."""
import logging
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..models import Episode, MediaItem, MediaStatus, MediaType
from . import generos, hltb, itunes, tmdb

logger = logging.getLogger(__name__)

# Ritmo de lectura medio para estimar minutos a partir de páginas
MINUTES_PER_PAGE = 1.5


def _to_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def enrich_item(db: Session, item: MediaItem) -> None:
    """Rellena duración/reparto/saga y, en series de TMDB, crea sus episodios.
    Best-effort: cualquier fallo de red se ignora (el ítem ya existe). No commitea:
    lo hace el llamador."""
    try:
        if item.media_type == MediaType.PELICULA and item.external_source == "tmdb" and item.external_id:
            d = tmdb.get_movie_details(item.external_id)
            if d:
                if d.get("title") and d["title"].strip():
                    item.title = d["title"].strip()
                item.runtime_minutes = d["runtime_minutes"]
                item.cast = d["cast"]
                item.tmdb_collection_id = d["tmdb_collection_id"]
                item.saga = item.saga or d["saga"]
                item.release_date = _to_date(d.get("release_date"))
                item.creator = item.creator or d["creator"]
                generos.añadir_si_faltan(db, item, d["genres"] or "")
                item.overview = item.overview or d["overview"]
        elif item.media_type == MediaType.SERIE and item.external_source == "tmdb" and item.external_id:
            d = tmdb.get_tv_details(item.external_id)
            if d:
                if d.get("title") and d["title"].strip():
                    item.title = d["title"].strip()
                item.cast = d["cast"]
                item.runtime_minutes = d["runtime_minutes"]
                item.creator = item.creator or d["creator"]
                generos.añadir_si_faltan(db, item, d["genres"] or "")
                item.overview = item.overview or d["overview"]
                load_episodes(db, item, d["seasons"])
        elif item.media_type == MediaType.PODCAST and item.external_source == "itunes" and item.external_id:
            load_podcast_episodes(item)
        elif item.media_type == MediaType.VIDEOJUEGO and item.hltb_hours is None:
            # HowLongToBeat es independiente de RAWG; best-effort
            hours = hltb.search_hours(item.title, item.year)
            if hours:
                item.hltb_hours = hours
    except Exception:
        logger.exception("Fallo enriqueciendo '%s'", item.title)


def load_podcast_episodes(item: MediaItem, limit: int = 50) -> int:
    """Crea los episodios de un podcast desde su feed RSS (external_id = feedUrl).

    Antes se numeraba por la posición en el feed, y el feed solo trae los
    últimos `limit`: en cuanto salía uno nuevo, todos se corrían una posición
    y "el 3" pasaba a ser otro episodio. Ahora cada episodio se reconoce por
    su nombre y su fecha, los que ya están conservan su número y los nuevos
    van detrás. Solo se añaden los posteriores al último conocido: los que el
    feed deja caer por la cola no se pierden, y los viejos que reaparecieran
    no se cuelan al final. [R3-M13]
    """
    if not item.external_id:
        return 0
    conocidos = {(e.name, e.air_date) for e in item.episodes}
    fechas = [e.air_date for e in item.episodes if e.air_date]
    ultima = max(fechas) if fechas else None
    numero = max((e.episode_number for e in item.episodes if e.season_number == 1), default=0)
    primera_carga = not item.episodes
    added = 0
    for e in itunes.fetch_podcast_episodes(item.external_id, limit):
        if (e["name"], e["air_date"]) in conocidos:
            continue
        if ultima and (e["air_date"] is None or e["air_date"] < ultima):
            continue
        numero += 1
        item.episodes.append(Episode(
            season_number=1, episode_number=numero,
            name=e["name"], air_date=e["air_date"], runtime_minutes=e["runtime_minutes"],
            notified=_ya_avisado(e["air_date"], primera_carga),
        ))
        conocidos.add((e["name"], e["air_date"]))
        added += 1
    return added


# Un episodio que aparece en un refresco con fecha de hace unos días se avisa
# igualmente: los podcasts (y a veces TMDB) publican el episodio el mismo día
# que sale, así que nunca llega a estar "en el futuro". Más atrás ya no es
# noticia.
VENTANA_AVISO_TARDIO = timedelta(days=7)


def _ya_avisado(air: date | None, primera_carga: bool) -> bool:
    """Valor inicial de `notified` para un episodio nuevo.

    En la primera carga los ya emitidos cuentan como avisados (dar de alta una
    serie de diez temporadas no puede mandar cien mensajes). En un refresco se
    avisa también de lo emitido hace poco.
    """
    if air is None:
        return False
    hoy = date.today()
    if primera_carga:
        return air <= hoy
    return air < hoy - VENTANA_AVISO_TARDIO


def estimated_minutes(item: MediaItem) -> int | None:
    """Minutos estimados para 'consumir' el ítem: duración de peli, del próximo
    episodio, horas de juego (HLTB) o tiempo de lectura del libro. None si falta el dato."""
    if item.media_type == MediaType.PELICULA:
        return item.runtime_minutes
    if item.media_type in (MediaType.SERIE, MediaType.PODCAST):
        nxt = item.episode_stats()["next"]
        per_ep = (nxt.runtime_minutes if nxt and nxt.runtime_minutes else None) or item.runtime_minutes
        return per_ep or 45  # asume 45 min/episodio si no hay dato
    if item.media_type == MediaType.VIDEOJUEGO:
        return int(item.hltb_hours * 60) if item.hltb_hours else None
    if item.media_type == MediaType.LIBRO:
        return int(item.page_count * MINUTES_PER_PAGE) if item.page_count else None
    return None


def load_episodes(db: Session, item: MediaItem, seasons: list[int]) -> int:
    """Crea las filas de Episode que falten y pone al día las que ya hay.

    Devuelve cuántas añadió. Los existentes reciben fecha, nombre, sinopsis y
    duración nuevos: TMDB suele anunciar los episodios sin fecha o con un
    "Episodio 5" provisional, y antes eso se quedaba así para siempre, fuera
    del calendario y sin aviso. Si la fecha pasa a ser futura, el aviso vuelve
    a quedar pendiente. [R3-M12]
    """
    if not item.external_id:
        return 0
    today = date.today()
    existing = {(e.season_number, e.episode_number): e for e in item.episodes}
    primera_carga = not existing
    added = 0
    for e in tmdb.fetch_tv_episodes(item.external_id, seasons):
        key = (e["season_number"], e["episode_number"])
        air = _to_date(e["air_date"])
        ep = existing.get(key)
        if ep is not None:
            if air and air != ep.air_date:
                ep.air_date = air
                if air > today:
                    ep.notified = False
            if e["name"]:
                ep.name = e["name"]
            if e["overview"]:
                ep.overview = e["overview"]
            if e["runtime_minutes"]:
                ep.runtime_minutes = e["runtime_minutes"]
            continue
        nuevo = Episode(
            season_number=e["season_number"],
            episode_number=e["episode_number"],
            name=e["name"],
            overview=e["overview"],
            air_date=air,
            runtime_minutes=e["runtime_minutes"],
            notified=_ya_avisado(air, primera_carga),
        )
        item.episodes.append(nuevo)
        existing[key] = nuevo
        added += 1
    return added


def reabrir_si_hay_novedades(item: MediaItem) -> bool:
    """Devuelve a "en progreso" una serie completada que tiene episodios nuevos.

    Al ver el último episodio emitido la serie pasa a completada; cuando sale
    el siguiente (o una temporada nueva) sigue completada, y nada de lo que
    mira series en curso -- avisos, "Próximamente", "Seguir viendo" -- la
    vuelve a ver. Cuenta solo lo emitido DESPUÉS de completarla: lo anunciado
    y aún sin emitir no la reabre (seguiría "en progreso" meses sin nada que
    ver), y una serie vieja completada a mano sin marcar episodios no se
    reabre por ellos. Devuelve True si cambió. [R3-A3]
    """
    if not item.is_episodic or item.status != MediaStatus.COMPLETADO or item.completed_at is None:
        return False
    hoy = date.today()
    hay_nuevo = any(
        not e.watched and e.season_number != 0 and e.air_date
        and item.completed_at < e.air_date <= hoy
        for e in item.episodes
    )
    if not hay_nuevo:
        return False
    recompute_status(item)
    return item.status != MediaStatus.COMPLETADO


def marcar_comienzo(item: MediaItem) -> None:
    """Deja la fecha de hoy como comienzo, si no había ninguna.

    Solo la primera vez: volver a guardar algo que empezaste en enero no puede
    moverlo a hoy, o el diario reescribiría el pasado cada vez que corrigieras
    una nota. [N5]
    """
    if item.started_at is None:
        item.started_at = date.today()


def cambiar_estado(item: MediaItem, nuevo: MediaStatus) -> None:
    """Pone el estado y las fechas que van con él, igual venga de donde venga.

    Lo usan el formulario de edición y los botones de estado de la ficha: si
    cada uno llevara su copia de las reglas de fechas, el diario dependería
    de por dónde se hubiera marcado algo como terminado.
    """
    if item.status != MediaStatus.COMPLETADO and nuevo == MediaStatus.COMPLETADO and item.completed_at is None:
        item.completed_at = date.today()  # primera vez que se marca completado
    if nuevo != MediaStatus.COMPLETADO:
        # Salir de "completado" deshace la fecha: si no, un "Visto" pulsado por
        # error seguía contando en estadísticas, "mejores del año", portada y
        # diario aunque el ítem volviera a "por ver". [R3-M7]
        item.completed_at = None
    if nuevo == MediaStatus.EN_PROGRESO:
        marcar_comienzo(item)  # solo si no había fecha ya (ver N5)
    if nuevo == MediaStatus.COMPLETADO and item.is_episodic:
        _marcar_emitidos_como_vistos(item)
    item.status = nuevo


def _marcar_emitidos_como_vistos(item: MediaItem) -> None:
    """Dar una serie por vista es haber visto sus episodios emitidos.

    Sin esto quedaba "completada" con 0/N vistos, el tiempo total no contaba
    nada y el primer episodio que se marcara la devolvía a "en progreso". Los
    que aún no han salido se quedan como están. `watched_at` va vacío a
    propósito: se sabe que los viste, no cuándo, y poner hoy llenaría el diario
    con todos los episodios de golpe en un día que no fue. [R3-M8]
    """
    hoy = date.today()
    for e in item.episodes:
        if not e.watched and (e.air_date is None or e.air_date <= hoy):
            e.watched = True


def recompute_status(item: MediaItem) -> None:
    """Ajusta el estado de una serie/podcast según sus episodios vistos.
    No toca wishlist (sigue siendo un deseo aunque no haya episodios)."""
    if not item.is_episodic or item.status == MediaStatus.WISHLIST:
        return
    stats = item.episode_stats()
    if stats["total"] == 0:
        return
    if stats["watched"] == 0:
        if item.status == MediaStatus.EN_PROGRESO:
            item.status = MediaStatus.PENDIENTE
    elif stats["watched"] >= stats["total"]:
        item.status = MediaStatus.COMPLETADO
        if item.completed_at is None:
            item.completed_at = date.today()
        # También aquí: una serie de un solo episodio se empieza y se termina
        # en el mismo gesto, y no haberla empezado nunca sería falso. En el
        # diario sale una sola línea igualmente, la de terminada.
        marcar_comienzo(item)
    else:
        item.status = MediaStatus.EN_PROGRESO
        item.completed_at = None
        # Una serie se empieza viendo un episodio, no editando un formulario:
        # si esto no estuviera aquí, el diario no vería empezar ninguna.
        marcar_comienzo(item)


def toggle_episode(item: MediaItem, episode: Episode) -> None:
    episode.watched = not episode.watched
    episode.watched_at = date.today() if episode.watched else None
    recompute_status(item)


def mark_through(item: MediaItem, episode: Episode) -> None:
    """Marca como vistos todos los episodios hasta 'episode' (incluido)."""
    target = (episode.season_number, episode.episode_number)
    today = date.today()
    for e in item.episodes:
        if (e.season_number, e.episode_number) <= target and not e.watched:
            e.watched = True
            e.watched_at = today
    recompute_status(item)
