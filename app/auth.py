"""Autenticacion HTTP Basic opcional, activable via ENABLE_AUTH en .env.

No usa `fastapi.security.HTTPBasic`: esa clase decodifica el header
`Authorization: Basic <base64>` con `.decode("ascii")` y devuelve 401 antes de
que este módulo llegue a ejecutarse si hay cualquier caracter no-ASCII
(tildes, eñes...). Con `AUTH_PASSWORD=contraseña` (el .env.example está en
español, así que es fácil poner una así) el usuario queda bloqueado sin
ninguna pista de por qué. Se parsea la cabecera a mano, decodificando en UTF-8."""
import base64
import binascii
import secrets

from fastapi import HTTPException, Request, status

from .config import settings


def _parse_basic_auth(authorization: str | None) -> tuple[str, str] | None:
    if not authorization:
        return None
    scheme, _, param = authorization.partition(" ")
    if scheme.lower() != "basic" or not param:
        return None
    try:
        decoded = base64.b64decode(param).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    username, separator, password = decoded.partition(":")
    if not separator:
        return None
    return username, password


def verify_auth(request: Request) -> bool:
    if not settings.enable_auth:
        return True
    credenciales = _parse_basic_auth(request.headers.get("authorization"))
    if credenciales is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticacion requerida",
            headers={"WWW-Authenticate": "Basic"},
        )
    username, password = credenciales
    # secrets.compare_digest sobre str exige ASCII puro (lanza TypeError con
    # cualquier tilde); comparando en bytes se evita esa limitación.
    user_ok = secrets.compare_digest(username.encode("utf-8"), settings.auth_username.encode("utf-8"))
    pass_ok = secrets.compare_digest(password.encode("utf-8"), settings.auth_password.encode("utf-8"))
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True
