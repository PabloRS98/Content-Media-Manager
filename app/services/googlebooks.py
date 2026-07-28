"""Búsqueda de libros vía Google Books API. API pública y gratuita."""
import logging
import time

import httpx

from ..config import settings
from .http_errors import describe

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.googleapis.com/books/v1/volumes"
MAX_INTENTOS = 3

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _fetch(params: dict) -> httpx.Response | None:
    """Petición con reintentos solo ante 503. Devuelve None si hay que rendirse
    (429 = cuota agotada, se cae al siguiente proveedor de la cascada)."""
    for intento in range(MAX_INTENTOS):
        resp = httpx.get(SEARCH_URL, params=params, headers=_HEADERS, timeout=10)
        if resp.status_code == 429:
            logger.debug("Google Books 429 rate-limit, cayendo a fallback")
            return None
        if resp.status_code == 503 and intento < MAX_INTENTOS - 1:
            time.sleep(1.0 * (intento + 1))
            continue
        resp.raise_for_status()
        return resp
    return None


def search_books(query: str, limit: int = 8, year: int | None = None) -> list[dict]:
    """Busca libros en Google Books y devuelve un formato común para el catálogo.
    Si google_books_api_key está configurada en Settings, la usa para evitar cuotas
    limitadas.

    Nunca propaga excepciones: ante cualquier fallo devuelve [] para que el
    llamador siga con el resto de la cascada (Wikipedia / Open Library), igual
    que hacen el resto de servicios."""
    q = query
    if year:
        q += f" publishedDate:{year}"

    params = {"q": q, "maxResults": limit}
    if settings.google_books_api_key:
        params["key"] = settings.google_books_api_key

    try:
        resp = _fetch(params)
        if resp is None:
            return []
        items = resp.json().get("items", [])
    except Exception as exc:
        logger.warning("Fallo al buscar libros en Google Books: %s", describe(exc))
        return []

    results = []
    for item in items[:limit]:
        vol = item.get("volumeInfo", {})

        authors = vol.get("authors", [])
        creator = ", ".join(authors) if authors else None

        # publishedDate puede ser AAAA-MM-DD o solo AAAA
        pub_year = None
        pub_year_str = (vol.get("publishedDate") or "").split("-")[0]
        if pub_year_str.isdigit():
            pub_year = int(pub_year_str)

        images = vol.get("imageLinks", {})
        cover_url = images.get("thumbnail") or images.get("smallThumbnail")
        if cover_url and cover_url.startswith("http://"):
            cover_url = cover_url.replace("http://", "https://", 1)

        categories = vol.get("categories", [])

        results.append({
            "external_id": item.get("id"),
            "title": vol.get("title", "Sin título"),
            "creator": creator,
            "year": pub_year,
            "cover_url": cover_url,
            "overview": vol.get("description", ""),
            "genres": ", ".join(categories) if categories else None,
            "release_date": vol.get("publishedDate") or None,
            "page_count": vol.get("pageCount"),
            "language": vol.get("language"),
        })
    return results
