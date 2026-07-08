"""Enriquecimiento de ítems sin portada (típicamente importados del CSV de IMDB):
cruza título+año contra TMDB (pelis/series), Open Library (libros) y RAWG (juegos)
para rellenar cover_url, y de paso géneros y sinopsis si faltan."""
import logging
import time

from sqlalchemy.orm import Session

from ..models import MediaItem, MediaType
from . import openlibrary, googlebooks, rawg, tmdb

logger = logging.getLogger(__name__)

BATCH_SIZE = 30  # ítems por ejecución, para respetar los límites de las APIs gratuitas
SLEEP_BETWEEN = 0.7


def _search_for(item: MediaItem) -> list[dict]:
    # Si tiene ID de IMDb guardado, usar la búsqueda directa rápida
    if item.external_id and item.external_id.startswith("imdb:") and item.media_type.value in ("pelicula", "serie"):
        imdb_id = item.external_id.replace("imdb:", "").strip()
        match = tmdb.find_by_imdb_id(imdb_id, item.media_type.value)
        if match:
            return [match]

    if item.media_type == MediaType.PELICULA:
        return tmdb.search_movies(item.title, limit=3, year=item.year)
    if item.media_type == MediaType.SERIE:
        return tmdb.search_tv(item.title, limit=3, year=item.year)
    if item.media_type == MediaType.LIBRO:
        import re
        # Limpieza de títulos de Goodreads (ej. "The Well of Ascension (Mistborn, #2)")
        cleaned_title = re.sub(r'\(.*?\)', '', item.title)
        cleaned_title = re.sub(r'\[.*?\]', '', cleaned_title)
        cleaned_title = " ".join(cleaned_title.split()).strip()
        query_str = cleaned_title if len(cleaned_title) > 2 else item.title
        
        # Añadir autor a la consulta para mayor precisión si existe
        if item.creator:
            query_str += f" {item.creator}"
            
        try:
            res = googlebooks.search_books(query_str, limit=5, year=item.year)
        except Exception:
            logger.warning("Fallo al buscar en Google Books para '%s', cayendo en Open Library", query_str)
            res = []
        if not res:
            res = openlibrary.search_books(query_str, limit=3, year=item.year)
        return res
    if item.media_type == MediaType.VIDEOJUEGO:
        return rawg.search_games(item.title, limit=3, year=item.year)
    return []


def _pick_match(item: MediaItem, results: list[dict]) -> dict | None:
    """Selecciona la mejor coincidencia del listado basándose en la compatibilidad de títulos y el idioma."""
    def clean(s: str) -> str:
        import re
        s_clean = re.sub(r'\(.*?\)', '', s or '')
        s_clean = re.sub(r'\[.*?\]', '', s_clean)
        return "".join(c for c in s_clean.lower() if c.isalnum())

    with_cover = [r for r in results if r.get("cover_url")]
    if not with_cover:
        return None

    # Si es una película o serie de búsqueda directa (como TMDB /find), ya es 100% segura
    if item.external_id and item.external_id.startswith("imdb:") and item.media_type.value in ("pelicula", "serie"):
        return with_cover[0]

    c1 = clean(item.title)

    # 1. Si es un libro, priorizar fuertemente cualquier resultado en español que sea compatible
    if item.media_type == MediaType.LIBRO:
        spanish_results = [r for r in with_cover if r.get("language") == "es"]
        for r in spanish_results:
            c2 = clean(r.get("title"))
            if c1 and c2 and (c1 in c2 or c2 in c1):
                return r

    # 2. Si no hay en español o no es un libro, buscar cualquier resultado compatible (mismo idioma o inglés)
    for r in with_cover:
        c2 = clean(r.get("title"))
        if c1 and c2 and (c1 in c2 or c2 in c1):
            return r

    return None  # No retornar nada si ninguno es compatible


def enrich_missing_covers(db: Session) -> dict:
    """Procesa hasta BATCH_SIZE ítems sin portada. Devuelve contadores para la UI."""
    query = db.query(MediaItem).filter(MediaItem.cover_url.is_(None)).order_by(MediaItem.id)
    total_missing = query.count()
    batch = query.limit(BATCH_SIZE).all()

    found = 0
    for item in batch:
        try:
            match = _pick_match(item, _search_for(item))
        except Exception:
            logger.exception("Fallo enriqueciendo '%s'", item.title)
            match = None

        if match:
            if match.get("title") and match["title"].strip() and match["title"].strip().lower() != item.title.lower():
                item.title = match["title"].strip()
            item.cover_url = match.get("cover_url")
            if not item.genres and match.get("genres"):
                item.genres = match["genres"]
            if not item.overview and match.get("overview"):
                item.overview = match["overview"]
            if not item.year and match.get("year"):
                item.year = match["year"]
            found += 1
        time.sleep(SLEEP_BETWEEN)
    db.commit()

    return {
        "procesados": len(batch),
        "encontrados": found,
        "restantes": max(0, total_missing - len(batch)),
    }
