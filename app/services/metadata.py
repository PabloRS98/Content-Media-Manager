"""Orquestación al crear/gestionar un ítem: enriquece metadatos (duración, reparto,
saga) y, en series, trae sus episodios desde TMDB. También recalcula el estado de
una serie/podcast a partir de qué episodios están vistos."""
import logging
from datetime import date

from sqlalchemy.orm import Session

from ..models import Episode, MediaItem, MediaStatus, MediaType
from . import hltb, itunes, tmdb

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
                item.genres = item.genres or d["genres"]
                item.overview = item.overview or d["overview"]
        elif item.media_type == MediaType.SERIE and item.external_source == "tmdb" and item.external_id:
            d = tmdb.get_tv_details(item.external_id)
            if d:
                if d.get("title") and d["title"].strip():
                    item.title = d["title"].strip()
                item.cast = d["cast"]
                item.runtime_minutes = d["runtime_minutes"]
                item.creator = item.creator or d["creator"]
                item.genres = item.genres or d["genres"]
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
    """Crea los episodios de un podcast desde su feed RSS (external_id = feedUrl)."""
    if not item.external_id:
        return 0
    today = date.today()
    existing = {(e.season_number, e.episode_number) for e in item.episodes}
    added = 0
    for i, e in enumerate(itunes.fetch_podcast_episodes(item.external_id, limit), start=1):
        if (1, i) in existing:
            continue
        item.episodes.append(Episode(
            season_number=1, episode_number=i,
            name=e["name"], air_date=e["air_date"], runtime_minutes=e["runtime_minutes"],
            notified=bool(e["air_date"] and e["air_date"] <= today),
        ))
        added += 1
    return added


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
    """Crea las filas de Episode que falten para una serie. Devuelve cuántas añadió."""
    if not item.external_id:
        return 0
    today = date.today()
    existing = {(e.season_number, e.episode_number) for e in item.episodes}
    added = 0
    for e in tmdb.fetch_tv_episodes(item.external_id, seasons):
        key = (e["season_number"], e["episode_number"])
        if key in existing:
            continue
        air = _to_date(e["air_date"])
        item.episodes.append(Episode(
            season_number=e["season_number"],
            episode_number=e["episode_number"],
            name=e["name"],
            overview=e["overview"],
            air_date=air,
            runtime_minutes=e["runtime_minutes"],
            # Los ya emitidos al añadir cuentan como "vistos de aviso" (no spamear
            # el historial); los futuros dispararán aviso cuando se estrenen
            notified=bool(air and air <= today),
        ))
        existing.add(key)
        added += 1
    return added


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
    else:
        item.status = MediaStatus.EN_PROGRESO
        item.completed_at = None


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
