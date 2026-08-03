"""Ayuda para loguear fallos de red sin filtrar las API keys.

TMDB, RAWG y Google Books se llaman con la clave como query param. Cuando la
petición falla, `httpx.Response.raise_for_status()` lanza una excepción cuyo
mensaje incluye la URL COMPLETA, key incluida:

    Client error '401 Unauthorized' for url
    'https://api.themoviedb.org/3/search/movie?api_key=SUPER_SECRETA&query=dune'

`logger.exception(...)` vuelca esa excepción tal cual al log, y con
`docker-compose.yml` usando el driver `json-file`, ese log persiste en disco y
acaba fácilmente pegado en un issue de GitHub. Esta función registra solo el
código HTTP (o el tipo de excepción si no hay respuesta, p. ej. un timeout),
nunca la excepción cruda."""
import logging


def log_fallo_api(logger: logging.Logger, mensaje: str, *args, exc: Exception) -> None:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    sufijo = f" (HTTP {status})" if status else f" ({type(exc).__name__})"
    logger.warning(mensaje + sufijo, *args)
