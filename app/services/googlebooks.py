"""Búsqueda de libros vía Google Books API. API pública y gratuita."""
import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.googleapis.com/books/v1/volumes"


def search_books(query: str, limit: int = 8, year: int | None = None, idioma: str | None = None) -> list[dict]:
    """Busca libros en Google Books y devuelve un formato común para el catálogo.
    Si google_books_api_key está configurada en Settings, la usa para evitar cuotas limitadas.

    `idioma` ("es"/"en") filtra los resultados a ese idioma. Se manda como
    `langRestrict`, pero verificado contra la API real: Google lo trata como
    sugerencia, no como filtro — la misma consulta con `langRestrict=es` y
    `langRestrict=en` puede devolver el mismo listado mixto. El filtrado real
    se hace aquí, por el campo `language` que sí viene bien poblado en cada
    volumen, sobre un conjunto de candidatos más amplio que `limit`.

    Sin `idioma` (uso interno del enriquecimiento automático, que no conoce el
    idioma del ítem) no se filtra nada: se conserva el comportamiento de antes."""
    q = query
    if year:
        q += f" publishedDate:{year}"

    # Con idioma pedimos más candidatos de los que se van a mostrar, porque el
    # filtrado por idioma ocurre después de traerlos (40 es el máximo de Google).
    params = {
        "q": q,
        "maxResults": min(40, limit * 4) if idioma else limit,
    }
    if idioma:
        params["langRestrict"] = idioma
    if settings.google_books_api_key:
        params["key"] = settings.google_books_api_key

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    import time
    resp = None
    for attempt in range(3):
        try:
            resp = httpx.get(SEARCH_URL, params=params, headers=headers, timeout=10)
            if resp.status_code == 429:
                logger.debug("Google Books 429 rate-limit, cayendo a fallback")
                return []
            if resp.status_code == 503:
                time.sleep(1.0 * (attempt + 1))
                continue
            resp.raise_for_status()
            break
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.debug("Google Books 429 rate-limit, cayendo a fallback")
                return []
            if e.response.status_code == 503 and attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise e

    if not resp:
        return []

    try:
        items = resp.json().get("items", [])
        results = []
        for item in items:
            vol = item.get("volumeInfo", {})
            info_id = item.get("id")

            # Autores
            authors = vol.get("authors", [])
            creator = ", ".join(authors) if authors else None

            # Año de publicación
            pub_date = vol.get("publishedDate", "")
            pub_year = None
            if pub_date:
                # publishedDate puede ser AAAA-MM-DD o solo AAAA
                pub_year_str = pub_date.split("-")[0]
                if pub_year_str.isdigit():
                    pub_year = int(pub_year_str)

            # URL de la portada (thumbnail)
            images = vol.get("imageLinks", {})
            cover_url = images.get("thumbnail") or images.get("smallThumbnail")
            if cover_url and cover_url.startswith("http://"):
                cover_url = cover_url.replace("http://", "https://")

            # Géneros/Categorías
            categories = vol.get("categories", [])
            genres = ", ".join(categories) if categories else None

            # Cantidad de páginas
            page_count = vol.get("pageCount")

            results.append({
                "external_id": info_id,
                "title": vol.get("title", "Sin título"),
                "creator": creator,
                "year": pub_year,
                "cover_url": cover_url,
                "overview": vol.get("description", ""),
                "genres": genres,
                "release_date": vol.get("publishedDate") or None,
                "page_count": page_count,
                "language": vol.get("language"),
            })
        if idioma:
            results = [r for r in results if r["language"] == idioma]
        return results[:limit]
    except Exception:
        logger.exception("Fallo al buscar libros en Google Books para '%s'", query)
        return []
