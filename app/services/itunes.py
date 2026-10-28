"""Podcasts vía la API pública de búsqueda de iTunes/Apple (gratis, sin key).
La búsqueda devuelve el feed RSS del programa; de ahí se sacan los episodios."""
import ipaddress
import logging
import socket
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

SEARCH_URL = "https://itunes.apple.com/search"
ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"

# Tope del RSS que se acepta parsear. Un feed de podcast real, con cientos de
# episodios y sus notas, ronda 1-3 MB; 5 MB deja margen y corta lo absurdo.
MAX_BYTES_FEED = 5 * 1024 * 1024


def _parsear_feed(contenido: bytes) -> ET.Element:
    """Parsea un RSS externo con xml.etree sin exponerse a XML malicioso. [R3-B26]

    El feed lo sirve un tercero cualquiera (la URL la escribe quien da de alta
    el podcast). xml.etree no resuelve entidades externas, pero sí expande las
    internas: un documento con entidades anidadas ("billion laughs") ocupa
    unos pocos KB en disco y gigas en memoria. Un RSS legítimo no declara ni
    DOCTYPE ni ENTITY, así que se rechaza cualquier documento que los lleve en
    vez de añadir defusedxml como dependencia. El tope de tamaño evita además
    cargar en memoria un cuerpo desmesurado.
    """
    if len(contenido) > MAX_BYTES_FEED:
        raise ValueError("el feed supera %d bytes" % MAX_BYTES_FEED)
    # En minúsculas y sin bytes nulos: un documento en UTF-16 intercala un \x00
    # entre letras y esquivaría la búsqueda de bytes.
    cabecera = contenido.replace(b"\x00", b"").lower()
    if b"<!doctype" in cabecera or b"<!entity" in cabecera:
        raise ValueError("el feed declara DOCTYPE/ENTITY")
    return ET.fromstring(contenido)


def _es_url_publica(url: str) -> bool:
    """Rechaza esquemas que no sean http/https y hosts que resuelvan a rangos
    privados, loopback, link-local o multicast.

    `external_id` (el feed RSS) lo escribe libremente quien da de alta un
    podcast, y `fetch_podcast_episodes` hace un `httpx.get` con ese valor tal
    cual. Sin esto, un alta con `external_id=http://169.254.169.254/...` hace
    que el propio servidor emita esa petición (SSRF ciego: el cuerpo no vuelve
    al atacante, pero sirve para escanear la red interna por temporización).

    Se exige `is_global` y no una lista de rangos prohibidos: la lista dejaba
    pasar `100.64.0.0/10`, que es justo la red de Tailscale que recomienda el
    README, y cualquier rango que se olvidara. Las redirecciones se revalidan
    salto a salto en `_get_publico`. [R3-M6]"""
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
        ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            return False
    return True


MAX_REDIRECCIONES = 5


def _get_publico(url: str, timeout: float = 15) -> httpx.Response:
    """GET que sigue las redirecciones a mano, comprobando cada destino.

    Con `follow_redirects=True`, un feed público podía responder 302 hacia
    `http://192.168.1.1/...` y el servidor iba detrás sin preguntar. Muchos
    feeds legítimos redirigen (cambios de hosting, http a https), así que no
    se cortan: se revalida cada salto. [R3-M6]
    """
    for _ in range(MAX_REDIRECCIONES + 1):
        if not _es_url_publica(url):
            raise ValueError("URL no pública: %s" % url)
        resp = httpx.get(url, timeout=timeout, follow_redirects=False)
        if not resp.is_redirect:
            return resp
        url = urljoin(url, resp.headers.get("location", ""))
    raise ValueError("Demasiadas redirecciones")


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
        resp = _get_publico(feed_url)
        resp.raise_for_status()
        root = _parsear_feed(resp.content)
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
