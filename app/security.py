"""Utilidades de seguridad transversales.

El modelo de amenaza es una app mono-usuario que puede quedar expuesta en LAN o
VPN. No hay multi-tenencia, así que no hay control de acceso por recurso; lo que
sí hay que cubrir es lo que entra por formulario y acaba en el HTML, y que un
sitio de terceros no pueda dirigir el navegador del usuario contra esta app.

La protección CSRF vive en `csrf.py`, que documenta su propio modelo de amenaza.
"""
from urllib.parse import urlparse

# Esquemas admitidos en las URLs que introduce el usuario.
SAFE_URL_SCHEMES = {"http", "https"}


def safe_external_url(url: str | None) -> str | None:
    """Devuelve `url` solo si es http(s) absoluta; si no, None.

    Se aplica a `cover_url`, que acaba en el `src` de un `<img>`. El autoescape
    de Jinja escapa HTML pero no valida esquemas, así que un `javascript:...`
    llegaría intacto al navegador. Hoy eso no ejecuta en el `src` de una imagen
    --por eso el hallazgo es MEDIO y no ALTO--, pero:

    - un esquema `data:` puede embeber contenido arbitrario;
    - el día que ese campo se use en un `<a href>` ("ver portada en grande"),
      pasa a ser un XSS inmediato, y el sitio donde se guarda es este;
    - la URL se pide en cada carga del catálogo, así que apuntarla a un host
      controlado la convierte en una baliza de seguimiento con `Referer`.

    El campo además se autorrellena desde seis APIs distintas, así que validar
    en el cliente no sirve de nada.
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme.lower() not in SAFE_URL_SCHEMES or not parsed.netloc:
        return None
    return url
