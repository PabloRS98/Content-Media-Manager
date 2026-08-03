"""Podcasts vía la API pública de búsqueda de iTunes/Apple (gratis, sin key).
La búsqueda devuelve el feed RSS del programa; de ahí se sacan los episodios."""
import ipaddress
import logging
import socket
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


def _es_url_publica(url: str) -> bool:
    """Rechaza esquemas que no sean http/https y hosts que resuelvan a rangos
    privados, loopback, link-local o multicast.

    `external_id` (el feed RSS) lo escribe libremente quien da de alta un
    podcast, y `fetch_podcast_episodes` hace un `httpx.get` con ese valor tal
    cual. Sin esto, un alta con `external_id=http://169.254.169.254/...` hace
    que el propio servidor emita esa petición (SSRF ciego: el cuerpo no vuelve
    al atacante, pero sirve para escanear la red interna por temporización).

    No protege contra un servidor que redirige a una IP privada DESPUÉS de
    pasar esta comprobación (`follow_redirects=True` sigue activo, porque
    varios feeds de podcast legítimos dependen de una redirección para
    funcionar); cubre el caso realista, que es la URL indicada directamente."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


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
    except Exception:
        logger.exception("Fallo al buscar podcasts para '%s'", query)
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
    """Episodios (más recientes primero en el RSS; se devuelven cronológicos)."""
    if not _es_url_publica(feed_url):
        logger.warning("feed_url rechazada por no ser una URL http(s) pública: %s", feed_url)
        return []
    try:
        resp = httpx.get(feed_url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
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
    except Exception:
        logger.exception("Fallo al leer el feed de podcast %s", feed_url)
        return []
