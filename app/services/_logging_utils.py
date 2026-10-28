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
import threading

# Cuántos fallos de API lleva este hilo. Los clientes devuelven `[]` tanto si
# no hay resultados como si la API falló (cuota agotada, timeout), y quien los
# llama no podía distinguirlo: el lote de portadas daba por "buscado sin
# éxito" un ítem que no se llegó a buscar. Con el contador, quien quiera saberlo
# mira si subió durante su llamada, sin cambiar lo que devuelve cada cliente.
# Por hilo porque el lote corre en uno de fondo mientras otras peticiones usan
# los mismos clientes. [R3-B14]
_hilo = threading.local()


def anotar_fallo() -> None:
    _hilo.fallos = fallos_de_api() + 1


def fallos_de_api() -> int:
    return getattr(_hilo, "fallos", 0)


def log_fallo_api(logger: logging.Logger, mensaje: str, *args, exc: Exception) -> None:
    anotar_fallo()
    status = getattr(getattr(exc, "response", None), "status_code", None)
    sufijo = f" (HTTP {status})" if status else f" ({type(exc).__name__})"
    logger.warning(mensaje + sufijo, *args)
