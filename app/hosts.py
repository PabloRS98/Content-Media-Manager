"""Con qué nombre se puede llegar a la app: defensa contra el DNS rebinding.

El ataque: una página que la víctima abre en su navegador (evil.example) hace
que su propio dominio resuelva, unos segundos después, a la IP de la app en la
LAN o a 127.0.0.1. Para el navegador todo sigue siendo "evil.example", así que
manda `Sec-Fetch-Site: same-origin` y la página puede leer las respuestas: elige
una cuenta sin contraseña, lee el catálogo entero y escribe lo que quiera. Ni
el CSRF ni el bind a 127.0.0.1 lo paran, porque desde dentro todo cuadra. Lo
único que delata el ataque es la cabecera `Host`: lleva el nombre del atacante.
[R3-M2]

Por defecto se admiten, sin configurar nada, los nombres que un atacante NO
puede hacer resolver a donde quiera desde fuera:

- IPs literales (`192.168.1.10`, `[::1]`): no hay DNS por medio.
- Nombres de una sola palabra (`nas`, `localhost`) y los dominios reservados
  para redes privadas (`.local`, `.lan`, `.home.arpa`, `.internal`...), que no
  se pueden registrar en internet.
- `*.ts.net`: Tailscale, que el README recomienda. El DNS de ese dominio lo
  controla Tailscale, no quien tenga una tailnet.

Un dominio público propio (un proxy inverso en `catalogo.midominio.es`) hay
que declararlo en `ALLOWED_HOSTS`, igual que en Django. Con `ALLOWED_HOSTS=*`
se desactiva la comprobación, para quien tenga su propia defensa delante.
"""
import ipaddress
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from .config import settings

logger = logging.getLogger(__name__)

# Dominios de nivel superior que no se pueden registrar públicamente (RFC 6761,
# RFC 8375 y los de uso doméstico de facto), más el de Tailscale.
SUFIJOS_PRIVADOS = (
    ".local", ".lan", ".home", ".home.arpa", ".internal", ".intranet",
    ".corp", ".private", ".localdomain", ".localhost", ".test", ".ts.net",
)


def nombre_sin_puerto(host: str) -> str:
    """'casa:8002' -> 'casa'; '[::1]:8002' -> '::1'. En minúsculas."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    if host.count(":") == 1:
        return host.split(":", 1)[0]
    return host


def _es_ip(nombre: str) -> bool:
    try:
        ipaddress.ip_address(nombre)
        return True
    except ValueError:
        return False


def host_admitido(host: str) -> bool:
    configurados = settings.allowed_host_names()
    if "*" in configurados:
        return True
    nombre = nombre_sin_puerto(host).rstrip(".")
    if not nombre:
        return False
    if nombre in configurados or nombre in {nombre_sin_puerto(h) for h in settings.trusted_origin_hosts()}:
        return True
    if _es_ip(nombre) or "." not in nombre:
        return True
    return nombre.endswith(SUFIJOS_PRIVADOS)


class HostPermitidoMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "")
        if host_admitido(host):
            return await call_next(request)
        # Se dice qué hacer: quien llegue aquí sin ser un ataque es alguien con
        # un proxy inverso en un dominio propio, y necesita saber la variable.
        logger.warning(
            "Petición con Host %r rechazada. Si es el nombre con el que accedes "
            "a la app, añádelo a ALLOWED_HOSTS en el .env.", host,
        )
        return PlainTextResponse(
            "Nombre de host no admitido: %s\n\n"
            "Si es el nombre con el que accedes a la aplicación (por ejemplo, el "
            "de tu proxy inverso), añádelo a ALLOWED_HOSTS en el .env y reinicia.\n"
            % nombre_sin_puerto(host),
            status_code=400,
        )
