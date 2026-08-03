"""Búsqueda de películas/series vía TMDB. Requiere una API key gratuita (TMDB_API_KEY).
Incluye géneros (mapeados desde /genre/*/list, cacheados en memoria)."""
import logging

import httpx

from ..config import settings
from ._logging_utils import log_fallo_api

logger = logging.getLogger(__name__)

BASE_URL = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w342"

_genre_cache: dict[str, dict[int, str]] = {}


def _genre_map(kind: str) -> dict[int, str]:
    """kind: 'movie' | 'tv'. Mapa {id: nombre} cacheado en memoria."""
    if kind in _genre_cache:
        return _genre_cache[kind]
    if not settings.tmdb_api_key:
        return {}
    try:
        resp = httpx.get(
            f"{BASE_URL}/genre/{kind}/list",
            params={"api_key": settings.tmdb_api_key, "language": "es-ES"},
            timeout=10,
        )
        resp.raise_for_status()
        mapping = {g["id"]: g["name"] for g in resp.json().get("genres", [])}
        _genre_cache[kind] = mapping
        return mapping
    except Exception as e:
        log_fallo_api(logger, "Fallo al obtener géneros TMDB (%s)", kind, exc=e)
        return {}


def _search(endpoint: str, media_label: str, query: str, limit: int, year: int | None = None) -> list[dict]:
    if not settings.tmdb_api_key:
        logger.warning("TMDB_API_KEY no configurada: no se puede buscar %s", media_label)
        return []
    params = {"api_key": settings.tmdb_api_key, "query": query, "language": "es-ES"}
    if year:
        params["primary_release_year" if endpoint == "movie" else "first_air_date_year"] = year
    try:
        resp = httpx.get(f"{BASE_URL}/search/{endpoint}", params=params, timeout=10)
        resp.raise_for_status()
        genre_map = _genre_map(endpoint)
        results = []
        for r in resp.json().get("results", [])[:limit]:
            title = r.get("title") or r.get("name") or "Sin título"
            date_str = r.get("release_date") or r.get("first_air_date") or ""
            year_found = int(date_str[:4]) if date_str[:4].isdigit() else None
            genres = ", ".join(genre_map[g] for g in r.get("genre_ids", []) if g in genre_map)
            results.append({
                "external_id": str(r.get("id")),
                "title": title,
                "creator": None,
                "year": year_found,
                "cover_url": f"{IMAGE_BASE}{r['poster_path']}" if r.get("poster_path") else None,
                "overview": r.get("overview", ""),
                "genres": genres or None,
                "release_date": date_str or None,
            })
        return results
    except Exception as e:
        log_fallo_api(logger, "Fallo al buscar %s para '%s'", media_label, query, exc=e)
        return []


def search_movies(query: str, limit: int = 8, year: int | None = None) -> list[dict]:
    return _search("movie", "películas", query, limit, year)


def search_tv(query: str, limit: int = 8, year: int | None = None) -> list[dict]:
    return _search("tv", "series", query, limit, year)


def find_by_imdb_id(imdb_id: str, media_type: str) -> dict | None:
    """Busca directamente en TMDB por ID de IMDb (ej: tt1234567).
    Retorna un diccionario mapeado en formato común o None si no hay coincidencia."""
    if not settings.tmdb_api_key or not imdb_id:
        return None
    try:
        resp = httpx.get(
            f"{BASE_URL}/find/{imdb_id}",
            params={"api_key": settings.tmdb_api_key, "external_source": "imdb_id", "language": "es-ES"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        # Mapeo según el tipo
        if media_type == "pelicula":
            movies = data.get("movie_results", [])
            if movies:
                m = movies[0]
                genre_map = _genre_map("movie")
                genres = ", ".join(genre_map[g] for g in m.get("genre_ids", []) if g in genre_map)
                date_str = m.get("release_date") or ""
                year = int(date_str[:4]) if date_str[:4].isdigit() else None
                return {
                    "external_id": str(m.get("id")),
                    "title": m.get("title") or "Sin título",
                    "creator": None,
                    "year": year,
                    "cover_url": f"{IMAGE_BASE}{m['poster_path']}" if m.get("poster_path") else None,
                    "overview": m.get("overview", ""),
                    "genres": genres or None,
                    "release_date": date_str or None,
                }
        elif media_type == "serie":
            tv = data.get("tv_results", [])
            if tv:
                t = tv[0]
                genre_map = _genre_map("tv")
                genres = ", ".join(genre_map[g] for g in t.get("genre_ids", []) if g in genre_map)
                date_str = t.get("first_air_date") or ""
                year = int(date_str[:4]) if date_str[:4].isdigit() else None
                return {
                    "external_id": str(t.get("id")),
                    "title": t.get("name") or "Sin título",
                    "creator": None,
                    "year": year,
                    "cover_url": f"{IMAGE_BASE}{t['poster_path']}" if t.get("poster_path") else None,
                    "overview": t.get("overview", ""),
                    "genres": genres or None,
                    "release_date": date_str or None,
                }
        return None
    except Exception as e:
        log_fallo_api(logger, "Fallo al buscar por IMDb ID '%s' en TMDB", imdb_id, exc=e)
        return None


# ---------- Detalles ampliados (reparto, duración, saga, episodios) ----------

def _credits_people(credits: dict) -> tuple[str | None, str | None]:
    """Devuelve (reparto, director) desde el bloque credits de TMDB."""
    cast = ", ".join(p.get("name", "") for p in (credits.get("cast") or [])[:6] if p.get("name")) or None
    director = None
    for c in credits.get("crew") or []:
        if c.get("job") == "Director":
            director = c.get("name")
            break
    return cast, director


def get_movie_details(tmdb_id: str) -> dict | None:
    """Duración, reparto, director, saga (collection) y géneros de una película."""
    if not settings.tmdb_api_key:
        return None
    try:
        resp = httpx.get(
            f"{BASE_URL}/movie/{tmdb_id}",
            params={"api_key": settings.tmdb_api_key, "language": "es-ES", "append_to_response": "credits"},
            timeout=10,
        )
        resp.raise_for_status()
        d = resp.json()
        cast, director = _credits_people(d.get("credits", {}))
        coll = d.get("belongs_to_collection") or {}
        return {
            "title": d.get("title"),
            "runtime_minutes": d.get("runtime") or None,
            "cast": cast,
            "creator": director,
            "genres": ", ".join(g["name"] for g in d.get("genres", [])) or None,
            "overview": d.get("overview") or "",
            "tmdb_collection_id": coll.get("id"),
            "saga": (coll.get("name") or "").replace(" Collection", "").replace(" (Colección)", "").strip() or None,
            "release_date": d.get("release_date") or None,
        }
    except Exception as e:
        log_fallo_api(logger, "Fallo al obtener detalles de película TMDB %s", tmdb_id, exc=e)
        return None


def get_tv_details(tmdb_id: str) -> dict | None:
    """Reparto, creadores/cadena, duración media de episodio, géneros y lista de
    temporadas (números) de una serie."""
    if not settings.tmdb_api_key:
        return None
    try:
        resp = httpx.get(
            f"{BASE_URL}/tv/{tmdb_id}",
            params={"api_key": settings.tmdb_api_key, "language": "es-ES", "append_to_response": "credits"},
            timeout=10,
        )
        resp.raise_for_status()
        d = resp.json()
        cast, _ = _credits_people(d.get("credits", {}))
        creators = ", ".join(c.get("name", "") for c in (d.get("created_by") or []) if c.get("name")) or None
        if not creators:
            nets = d.get("networks") or []
            creators = nets[0].get("name") if nets else None
        run = d.get("episode_run_time") or []
        return {
            "title": d.get("name"),
            "cast": cast,
            "creator": creators,
            "genres": ", ".join(g["name"] for g in d.get("genres", [])) or None,
            "overview": d.get("overview") or "",
            "runtime_minutes": int(run[0]) if run else None,
            # season_number 0 = specials: se incluyen para no perder episodios sueltos
            "seasons": [s.get("season_number") for s in d.get("seasons", []) if s.get("season_number") is not None],
        }
    except Exception as e:
        log_fallo_api(logger, "Fallo al obtener detalles de serie TMDB %s", tmdb_id, exc=e)
        return None


def fetch_tv_episodes(tmdb_id: str, seasons: list[int]) -> list[dict]:
    """Episodios de las temporadas indicadas: temporada, número, nombre, fecha,
    duración y sinopsis. Cada temporada es una llamada aparte (así lo da TMDB)."""
    if not settings.tmdb_api_key:
        return []
    out: list[dict] = []
    for sn in seasons:
        try:
            resp = httpx.get(
                f"{BASE_URL}/tv/{tmdb_id}/season/{sn}",
                params={"api_key": settings.tmdb_api_key, "language": "es-ES"},
                timeout=10,
            )
            resp.raise_for_status()
            for e in resp.json().get("episodes", []):
                if e.get("episode_number") is None:
                    continue
                out.append({
                    "season_number": e.get("season_number", sn),
                    "episode_number": e.get("episode_number"),
                    "name": e.get("name") or None,
                    "overview": e.get("overview") or "",
                    "air_date": e.get("air_date") or None,
                    "runtime_minutes": e.get("runtime") or None,
                })
        except Exception as e:
            log_fallo_api(logger, "Fallo al obtener episodios TMDB %s temporada %s", tmdb_id, sn, exc=e)
            continue
    return out
