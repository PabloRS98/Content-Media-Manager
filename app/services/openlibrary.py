"""Búsqueda de libros vía Open Library. API pública y gratuita, sin necesidad de API key."""
import logging

import httpx

logger = logging.getLogger(__name__)

SEARCH_URL = "https://openlibrary.org/search.json"
COVER_URL = "https://covers.openlibrary.org/b/id/{cover_id}-M.jpg"


def search_books(query: str, limit: int = 8, year: int | None = None) -> list[dict]:
    params = {
        "q": query, "limit": limit,
        # Pedir explícitamente el nº de páginas mediano para el tiempo de lectura
        "fields": "key,title,author_name,first_publish_year,cover_i,number_of_pages_median",
    }
    if year:
        params["q"] = f"{query} first_publish_year:{year}"
    try:
        resp = httpx.get(SEARCH_URL, params=params, timeout=10)
        if resp.status_code == 422:
            # Open Library rechaza algunas búsquedas muy cortas/ambiguas; no es un error
            logger.info("Open Library 422 para '%s' (búsqueda ignorada)", query)
            return []
        resp.raise_for_status()
        docs = resp.json().get("docs", [])
        results = []
        for d in docs[:limit]:
            cover_id = d.get("cover_i")
            results.append({
                "external_id": (d.get("key") or "").replace("/works/", ""),
                "title": d.get("title", "Sin título"),
                "creator": ", ".join(d.get("author_name", [])) or None,
                "year": d.get("first_publish_year"),
                "cover_url": COVER_URL.format(cover_id=cover_id) if cover_id else None,
                "overview": "",
                "genres": None,
                "page_count": d.get("number_of_pages_median"),
            })
        return results
    except Exception:
        logger.exception("Fallo al buscar libros para '%s'", query)
        return []
