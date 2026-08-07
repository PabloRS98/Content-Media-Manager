"""Protección CSRF ligera: sin librería ni token, basada en cabeceras que ya
manda cualquier navegador moderno (`Sec-Fetch-Site`), con respaldo a `Origin` y
a `Referer` para navegadores antiguos que no la mandan.

Ninguno de los POST de la app (/agregar, /item/{id}/actualizar,
/item/{id}/eliminar, /importar...) lleva token anti-CSRF. Con
`ENABLE_AUTH=true` la situación empeora en vez de mejorar: HTTP Basic hace que
el navegador reenvíe las credenciales en cualquier petición cross-site, así
que cualquier página que el usuario visite puede borrar ítems o crear
entradas con solo un `<form>` que se auto-envíe.

**Falla cerrada.** Si no llega ninguna de las tres cabeceras, se rechaza. Antes
se dejaba pasar a propósito, para no romper clientes que no son navegadores
(curl, scripts, integraciones). El razonamiento tenía lógica pero el balance
salía al revés: un navegador manda SIEMPRE al menos una de las tres en un POST,
así que el fallo abierto solo beneficiaba a los clientes automatizados --que
pueden añadir la cabecera con una línea-- mientras dejaba la puerta abierta a
cualquier webview antiguo (una app de televisor, un lector de RSS embebido, un
navegador de coche) que no mandara ni `Sec-Fetch-Site` ni `Origin`. Justamente
el caso que el segundo párrafo de este docstring describe como peligroso.

Si se accede a la app por un nombre distinto al del proxy inverso, ese host se
declara en `TRUSTED_ORIGINS` (separados por comas) en vez de reabrir el fallo.
"""
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from .config import settings

_METODOS_QUE_ESCRIBEN = {"POST", "PUT", "PATCH", "DELETE"}


def _hosts_fiables() -> set[str]:
    """Se lee en cada petición, no al importar, para que un test pueda
    sustituirla y para no congelar la configuración en el arranque."""
    return settings.trusted_origin_hosts()


def _es_peticion_cruzada(request: Request) -> bool:
    sec_fetch_site = request.headers.get("sec-fetch-site")
    if sec_fetch_site is not None:
        # La más fiable: es la única que distingue "same-site" de "cross-site"
        # sin comparar cadenas a mano.
        return sec_fetch_site == "cross-site"

    # `Origin` primero: `Referer` puede venir recortado o ausente según la
    # política de referencia de la página de origen, así que solo se mira si
    # no hay `Origin`.
    for cabecera in ("origin", "referer"):
        valor = request.headers.get(cabecera)
        if valor:
            netloc = urlparse(valor).netloc
            if not netloc:
                return True
            # Detrás de un proxy inverso, `Host` lleva el nombre público que ve
            # el navegador, que es con el que se construye `Origin`.
            return netloc != request.headers.get("host", "") and netloc not in _hosts_fiables()

    return True


class CSRFProtectionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in _METODOS_QUE_ESCRIBEN and _es_peticion_cruzada(request):
            return PlainTextResponse("Petición rechazada: origen no fiable.", status_code=403)
        return await call_next(request)
