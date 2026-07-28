"""Defensas transversales: CSRF y saneado de destinos de redirección.

La app usa formularios HTML y, opcionalmente, HTTP Basic. Basic es especialmente
delicado frente a CSRF porque el navegador reenvía las credenciales solo con que
la petición salga hacia este origen, venga de donde venga: sin esta comprobación,
cualquier página que visite el usuario podría borrarle ítems del catálogo.

En lugar de tokens por formulario (que obligarían a tocar cada plantilla) se
comprueba el origen de la petición, que es suficiente para formularios y fetch:
- `Sec-Fetch-Site` lo pone el navegador y no es falsificable desde JS.
- `Origin` cubre navegadores sin Fetch Metadata.
Las peticiones sin ninguna de las dos (curl, apps nativas, tests) se permiten:
no tienen credenciales ambientales que robar, que es lo que define el CSRF."""
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def safe_redirect_path(url: str | None, fallback: str = "/", host: str | None = None) -> str:
    """Devuelve una ruta interna segura a partir de una URL no confiable.

    La cabecera `Referer` la controla el cliente; usarla tal cual como destino de
    un 303 es una redirección abierta. Los navegadores mandan el Referer absoluto,
    así que se acepta una URL absoluta solo si su host coincide con `host` (el de
    la petición en curso); en cualquier otro caso se cae al fallback.

    Quedarse con el path de una URL ajena NO vale: `https://evil.com/x` daría `/x`,
    que es una ruta válida de este sitio y enmascara el intento."""
    if not url:
        return fallback

    partes = urlparse(url)
    if partes.scheme or partes.netloc:
        if not host or partes.netloc != host:
            return fallback
    if not partes.path.startswith("/") or partes.path.startswith("//"):
        return fallback
    return partes.path + (("?" + partes.query) if partes.query else "")


class CSRFMiddleware(BaseHTTPMiddleware):
    """Rechaza peticiones de escritura que vengan de otro sitio."""

    async def dispatch(self, request, call_next):
        if request.method in SAFE_METHODS:
            return await call_next(request)

        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site is not None:
            if fetch_site not in ("same-origin", "same-site", "none"):
                return PlainTextResponse("Petición cross-site bloqueada (CSRF)", status_code=403)
            return await call_next(request)

        origin = request.headers.get("origin")
        if origin is not None:
            host = request.headers.get("host")
            if urlparse(origin).netloc != host:
                return PlainTextResponse("Petición cross-site bloqueada (CSRF)", status_code=403)

        return await call_next(request)
