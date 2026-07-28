"""Autenticacion HTTP Basic opcional, activable via ENABLE_AUTH en .env."""
import logging
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import settings

logger = logging.getLogger(__name__)

security = HTTPBasic(auto_error=False)


def _eq(a: str, b: str) -> bool:
    """Comparación en tiempo constante sobre bytes.

    `secrets.compare_digest` sobre `str` exige ASCII puro y lanza TypeError con
    cualquier otra cosa (un 500 en mitad del login). Comparando los UTF-8 se
    evita esa vía."""
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def warn_if_weak_config() -> None:
    """Avisos al arrancar sobre configuraciones de auth problemáticas."""
    if not settings.enable_auth:
        return
    if settings.auth_password == "changeme":
        logger.warning(
            "ENABLE_AUTH está activo pero AUTH_PASSWORD sigue siendo el valor de ejemplo "
            "('changeme'). Cámbialo en .env antes de exponer la app."
        )
    if not settings.auth_password.isascii() or not settings.auth_username.isascii():
        logger.warning(
            "AUTH_USERNAME/AUTH_PASSWORD contienen caracteres no ASCII. El estándar HTTP "
            "Basic no los transporta de forma interoperable y los navegadores no podrán "
            "autenticarse: usa solo caracteres ASCII."
        )


def verify_auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> bool:
    if not settings.enable_auth:
        return True
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticacion requerida",
            headers={"WWW-Authenticate": "Basic"},
        )
    user_ok = _eq(credentials.username, settings.auth_username)
    pass_ok = _eq(credentials.password, settings.auth_password)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True
