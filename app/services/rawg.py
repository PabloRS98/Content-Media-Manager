"""Búsqueda de videojuegos vía RAWG. Requiere una API key gratuita (RAWG_API_KEY)."""
import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.rawg.io/api/games"


def search_games(query: str, limit: int = 8, year: int | None = None) -> list[dict]:
    if not settings.rawg_api_key:
        logger.warning("RAWG_API_KEY no configurada: no se pueden buscar videojuegos")
        return []
    params = {"key": settings.rawg_api_key, "search": query, "page_size": limit}
    if year:
        params["dates"] = f"{year}-01-01,{year}-12-31"
    try:
        resp = httpx.get(BASE_URL, params=params, timeout=10)
        resp.raise_for_status()
        results = []
        for r in resp.json().get("results", [])[:limit]:
            released = r.get("released") or ""
            year_found = int(released[:4]) if released[:4].isdigit() else None
            genres = ", ".join(g.get("name", "") for g in (r.get("genres") or []) if g.get("name"))
            results.append({
                "external_id": str(r.get("id")),
                "title": r.get("name", "Sin título"),
                "creator": None,
                "year": year_found,
                "cover_url": r.get("background_image"),
                "overview": "",
                "genres": genres or None,
                "release_date": released or None,
            })
        return results
    except Exception:
        logger.exception("Fallo al buscar videojuegos para '%s'", query)
        return []
