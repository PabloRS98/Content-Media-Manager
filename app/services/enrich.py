"""Enriquecimiento de ítems sin portada (típicamente importados del CSV de IMDB):
cruza título+año contra TMDB (pelis/series), Open Library (libros) y RAWG (juegos)
para rellenar cover_url, y de paso géneros y sinopsis si faltan."""
import logging
import re
import time

from sqlalchemy.orm import Session

from ..models import MediaItem, MediaType
from . import googlebooks, openlibrary, rawg, tmdb, wikipedia_covers

logger = logging.getLogger(__name__)

BATCH_SIZE = 30  # ítems por ejecución, para respetar los límites de las APIs gratuitas
SLEEP_BETWEEN = 0.7
# Tope de tiempo por petición: cada ítem puede encadenar 3 llamadas HTTP de hasta
# 10 s, así que sin este límite el lote completo puede tardar minutos y cualquier
# proxy delante cortaría la conexión antes de terminar. Al agotarse se para y se
# devuelven los ítems que falten, que el usuario procesa pulsando otra vez.
TIME_BUDGET_SECONDS = 25.0

_PARENTESIS_RE = re.compile(r"\(.*?\)")
_CORCHETES_RE = re.compile(r"\[.*?\]")


def _limpiar_titulo(texto: str | None) -> str:
    """Quita aclaraciones entre paréntesis/corchetes (ej. 'Mistborn, #2')."""
    limpio = _CORCHETES_RE.sub("", _PARENTESIS_RE.sub("", texto or ""))
    return " ".join(limpio.split()).strip()


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
        # Limpieza de títulos de Goodreads (ej. "The Well of Ascension (Mistborn, #2)")
        cleaned_title = _limpiar_titulo(item.title)
        query_str = cleaned_title if len(cleaned_title) > 2 else item.title

        # Añadir autor a la consulta para mayor precisión si existe
        if item.creator:
            query_str += f" {item.creator}"

        # Cascada: Google Books -> Wikipedia -> Open Library
        try:
            res = googlebooks.search_books(query_str, limit=5, year=item.year)
        except Exception:
            logger.warning("Fallo al buscar en Google Books para '%s'", query_str)
            res = []

        if not res:
            try:
                res = wikipedia_covers.search_book_cover(
                    title=cleaned_title if len(cleaned_title) > 2 else item.title,
                    author=item.creator or "", year=item.year,
                )
            except Exception:
                logger.warning("Fallo al buscar en Wikipedia para '%s'", query_str)

        if not res:
            res = openlibrary.search_books(query_str, limit=3, year=item.year)
        return res
    if item.media_type == MediaType.VIDEOJUEGO:
        return rawg.search_games(item.title, limit=3, year=item.year)
    return []


def _pick_match(item: MediaItem, results: list[dict]) -> dict | None:
    """Selecciona la mejor coincidencia del listado basándose en la compatibilidad de títulos y el idioma."""
    def clean(s: str) -> str:
        return "".join(c for c in _limpiar_titulo(s).lower() if c.isalnum())

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
    """Procesa hasta BATCH_SIZE ítems sin portada (o hasta agotar el tiempo
    disponible). Devuelve contadores para la UI."""
    query = db.query(MediaItem).filter(MediaItem.cover_url.is_(None)).order_by(MediaItem.id)
    total_missing = query.count()
    batch = query.limit(BATCH_SIZE).all()

    deadline = time.monotonic() + TIME_BUDGET_SECONDS
    found = 0
    procesados = 0
    for item in batch:
        if time.monotonic() >= deadline:
            logger.info("Presupuesto de tiempo agotado tras %d ítems; quedan para el próximo lote", procesados)
            break
        try:
            match = _pick_match(item, _search_for(item))
        except Exception as exc:
            logger.warning("Fallo enriqueciendo '%s': %s", item.title, exc)
            match = None
        procesados += 1

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

    # Solo los ítems para los que se ENCONTRÓ portada dejan de faltar: restar el
    # tamaño del lote daría por resueltos los que se procesaron sin éxito.
    return {
        "procesados": procesados,
        "encontrados": found,
        "restantes": max(0, total_missing - found),
    }
