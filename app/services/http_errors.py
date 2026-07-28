"""Descripción segura de errores HTTP para los logs.

TMDB, RAWG y Google Books llevan la API key en la query string, y Telegram su
bot token en el propio path. El mensaje que genera `httpx.raise_for_status()`
incluye la URL completa, así que un `logger.exception()` sobre esos fallos
escribe el secreto en el log (y en `docker logs`, que la gente pega en issues).

Estas funciones devuelven una descripción con esquema + host + path saneado,
nunca la query string."""
import httpx

# Segmentos de path que llevan un secreto embebido (Telegram: /bot<token>/...)
_SECRET_PREFIXES = ("bot",)


def _safe_path(url: httpx.URL) -> str:
    partes = []
    for segmento in url.path.split("/"):
        if any(segmento.startswith(p) and len(segmento) > len(p) for p in _SECRET_PREFIXES):
            partes.append(segmento[: len(_SECRET_PREFIXES[0])] + "***")
        else:
            partes.append(segmento)
    return "/".join(partes)


def safe_url(url: httpx.URL | str) -> str:
    """`https://host/path` sin query string ni credenciales."""
    if isinstance(url, str):
        url = httpx.URL(url)
    return f"{url.scheme}://{url.host}{_safe_path(url)}"


def describe(exc: Exception) -> str:
    """Resumen de un fallo de red/HTTP apto para loguear, sin secretos.

    Se llama SIEMPRE desde dentro de un `except`, así que no puede lanzar nada:
    una excepción aquí se convertiría en un 500 en la petición del usuario.
    Ojo con `exc.request`, que en httpx lanza RuntimeError si la excepción se
    construyó sin asignarle petición."""
    try:
        if isinstance(exc, httpx.HTTPStatusError):
            return "HTTP %d en %s" % (exc.response.status_code, safe_url(exc.request.url))
        if isinstance(exc, httpx.RequestError):
            return "%s en %s" % (type(exc).__name__, safe_url(exc.request.url))
    except Exception:  # noqa: BLE001  nunca debe tapar el error original
        pass
    return type(exc).__name__
