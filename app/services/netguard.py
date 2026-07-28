"""Validación de URLs antes de pedirlas desde el servidor (anti-SSRF).

El `external_id` de un podcast es la URL de su feed RSS y llega por formulario,
así que un `POST /agregar` puede hacer que el servidor pida una URL arbitraria:
metadatos de cloud (169.254.169.254), servicios en localhost o el resto de la
LAN doméstica, que es justo lo que una app self-hosted tiene alrededor.

Se comprueba esquema y, resolviendo el host, que ninguna de sus IPs sea privada,
loopback, link-local o reservada."""
import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

ESQUEMAS_PERMITIDOS = ("http", "https")


class UnsafeURLError(ValueError):
    """La URL apunta a un destino que el servidor no debe pedir."""


def _ip_es_publica(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_multicast or addr.is_reserved or addr.is_unspecified
    )


def ensure_public_url(url: str) -> str:
    """Devuelve la URL si es segura de pedir; si no, lanza UnsafeURLError."""
    if not url or not isinstance(url, str):
        raise UnsafeURLError("URL vacía")

    partes = urlparse(url.strip())
    if partes.scheme not in ESQUEMAS_PERMITIDOS:
        raise UnsafeURLError("esquema no permitido: %r" % (partes.scheme or "",))
    if not partes.hostname:
        raise UnsafeURLError("URL sin host")

    try:
        infos = socket.getaddrinfo(partes.hostname, partes.port or 0, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError("no se pudo resolver %r" % partes.hostname) from exc

    for info in infos:
        ip = info[4][0]
        if not _ip_es_publica(ip):
            raise UnsafeURLError("%s resuelve a una dirección interna (%s)" % (partes.hostname, ip))

    return url
