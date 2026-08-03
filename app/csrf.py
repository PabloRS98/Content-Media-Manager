"""Protección CSRF ligera: sin librería ni token, basada en cabeceras que ya
manda cualquier navegador moderno (`Sec-Fetch-Site`), con fallback a `Origin`
para navegadores antiguos que no la mandan.

Ninguno de los POST de la app (/agregar, /item/{id}/actualizar,
/item/{id}/eliminar, /importar...) lleva token anti-CSRF. Con
`ENABLE_AUTH=true` la situación empeora en vez de mejorar: HTTP Basic hace que
el navegador reenvíe las credenciales en cualquier petición cross-site, así
que cualquier página que el usuario visite puede borrar ítems o crear
entradas con solo un `<form>` que se auto-envíe.

Solo se rechaza cuando hay evidencia POSITIVA de que la petición viene de otro
origen (`Sec-Fetch-Site: cross-site`, u `Origin` que no coincide con el
`Host`). Si ninguna de las dos cabeceras está presente (clientes sin
navegador: curl, scripts, integraciones) se deja pasar a propósito — es un
fallo abierto deliberado, para no romper usos legítimos que no son un
navegador siguiendo un enlace de otra página."""
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

_METODOS_QUE_ESCRIBEN = {"POST", "PUT", "PATCH", "DELETE"}


def _es_peticion_cruzada(request: Request) -> bool:
    sec_fetch_site = request.headers.get("sec-fetch-site")
    if sec_fetch_site is not None:
        return sec_fetch_site == "cross-site"
    origin = request.headers.get("origin")
    if origin:
        return urlparse(origin).netloc != request.headers.get("host", "")
    return False


class CSRFProtectionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in _METODOS_QUE_ESCRIBEN and _es_peticion_cruzada(request):
            return PlainTextResponse("Petición rechazada: origen no fiable.", status_code=403)
        return await call_next(request)
