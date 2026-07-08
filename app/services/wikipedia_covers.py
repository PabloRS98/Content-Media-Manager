"""Búsqueda de portadas de libros vía la API de Wikipedia. Fallback gratuito
para libros que Google Books no encuentra."""
import logging

import httpx

logger = logging.getLogger(__name__)

WIKI_APIS = {
    "es": "https://es.wikipedia.org/w/api.php",
    "en": "https://en.wikipedia.org/w/api.php",
}
HEADERS = {"User-Agent": "MediaCatalog/1.0 (+https://github.com/PabloRS98/Content-Media-Manager)"}

_SKIP_MARKERS = ("autor", "writer", "poet", "novelist", "desambiguaci", "disambiguation")


def search_book_cover(title: str, author: str = "", year: int | None = None) -> list[dict]:
    """Busca la portada de un libro en Wikipedia (ES -> EN). El título devuelto es
    el de la página realmente encontrada (no el de la consulta), para que el
    matching de enrich.py pueda rechazar coincidencias erróneas de verdad."""
    clean_title = title.strip()
    if clean_title.isdigit():
        # Títulos puramente numéricos (ej. "1984") casi siempre enganchan el
        # artículo del año en vez del libro -> mejor no arriesgar una portada mala.
        return []

    candidates = [title]
    for prefix in ("La ", "El ", "Los ", "Las ", "The "):
        if clean_title.lower().startswith(prefix.lower()):
            candidates.append(clean_title[len(prefix):])
            break

    for query_title in candidates:
        for lang, api_url in WIKI_APIS.items():
            found_title, cover_url = _fetch_page_image(api_url, query_title)
            if cover_url:
                return [{
                    "external_id": None, "title": found_title, "creator": author or None,
                    "year": year, "cover_url": cover_url, "overview": "", "genres": None,
                    "language": lang,
                }]
    return []


def _fetch_page_image(api_url: str, page_title: str) -> tuple[str | None, str | None]:
    params = {
        "action": "query", "titles": page_title, "prop": "pageimages",
        "format": "json", "pithumbsize": 300,
    }
    try:
        resp = httpx.get(api_url, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
        for p in pages.values():
            if "missing" in p:
                continue
            thumb = p.get("thumbnail", {}).get("source")
            page_title_found = p.get("title", "")
            if thumb and not any(w in page_title_found.lower() for w in _SKIP_MARKERS):
                return page_title_found, thumb
        return None, None
    except Exception:
        logger.debug("Wikipedia: fallo para '%s' en %s", page_title, api_url)
        return None, None
