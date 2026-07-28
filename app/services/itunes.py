"""Podcasts vía la API pública de búsqueda de iTunes/Apple (gratis, sin key).
La búsqueda devuelve el feed RSS del programa; de ahí se sacan los episodios."""
import logging
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import httpx

from .http_errors import describe
from .netguard import UnsafeURLError, ensure_public_url

logger = logging.getLogger(__name__)

SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
# Los feeds de podcast son XML de terceros: se limita lo que se descarga para no
# comerse la memoria con un feed enorme o malicioso.
MAX_FEED_BYTES = 10 * 1024 * 1024


def search_podcasts(query: str, limit: int = 8) -> list[dict]:
    if not query or len(query.strip()) < 2:
        return []
    try:
        resp = httpx.get(
            SEARCH_URL,
            params={"media": "podcast", "term": query, "limit": limit, "country": "ES"},
            timeout=10,
        )
        resp.raise_for_status()
        results = []
        for r in resp.json().get("results", []):
            if not r.get("feedUrl"):
                continue
            rel = (r.get("releaseDate") or "")[:4]
            results.append({
                # el feed RSS es la clave: de él salen los episodios
                "external_id": r.get("feedUrl"),
                "title": r.get("collectionName") or r.get("trackName") or "Sin título",
                "creator": r.get("artistName") or None,
                "year": int(rel) if rel.isdigit() else None,
                "cover_url": r.get("artworkUrl600") or r.get("artworkUrl100") or None,
                "overview": "",
                "genres": r.get("primaryGenreName") or None,
            })
        return results
    except Exception as exc:
        logger.warning("Fallo al buscar podcasts: %s", describe(exc))
        return []


def _duration_minutes(text: str | None) -> int | None:
    if not text:
        return None
    text = text.strip()
    try:
        if ":" in text:
            parts = [int(p) for p in text.split(":")]
            while len(parts) < 3:
                parts.insert(0, 0)
            h, m, s = parts[-3], parts[-2], parts[-1]
            return h * 60 + m + (1 if s >= 30 else 0)
        secs = int(text)
        return max(1, round(secs / 60))
    except (ValueError, TypeError):
        return None


def fetch_podcast_episodes(feed_url: str, limit: int = 50) -> list[dict]:
    """Episodios (más recientes primero en el RSS; se devuelven cronológicos).

    `feed_url` viene del formulario de alta, así que se valida antes de pedirla
    para no convertir el servidor en un proxy hacia su propia red interna."""
    try:
        ensure_public_url(feed_url)
    except UnsafeURLError as exc:
        logger.warning("Feed de podcast rechazado: %s", exc)
        return []
    try:
        resp = httpx.get(feed_url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        if len(resp.content) > MAX_FEED_BYTES:
            logger.warning("Feed de podcast demasiado grande (%d bytes), ignorado", len(resp.content))
            return []
        root = ET.fromstring(resp.content)
        items = root.findall(".//channel/item")[:limit]
        episodes = []
        for it in reversed(items):  # del más antiguo al más nuevo
            title = it.findtext("title") or None
            pub = it.findtext("pubDate")
            air = None
            if pub:
                try:
                    air = parsedate_to_datetime(pub).date()
                except (TypeError, ValueError):
                    air = None
            dur = it.findtext(f"{ITUNES_NS}duration")
            episodes.append({
                "name": title,
                "air_date": air,
                "runtime_minutes": _duration_minutes(dur),
            })
        return episodes
    except Exception as exc:
        logger.warning("Fallo al leer el feed de podcast: %s", describe(exc))
        return []
