"""Cuentas de la casa: quién está usando la app y qué puede ver.

Dos cosas que conviene no confundir:

- `ENABLE_AUTH` (HTTP Basic, `auth.py`) es la **puerta de la calle**: decide
  si alguien de fuera puede llegar a la aplicación. Es una sola credencial
  compartida y no distingue personas.
- Las cuentas de aquí son la **puerta de cada habitación**: una vez dentro,
  cada persona ve su catálogo y no el de los demás.

Son independientes a propósito. Se puede tener la app abierta en la LAN sin
Basic y con cuentas separadas, o al revés.

**La contraseña de una cuenta es opcional y protege de verdad.** Una cuenta sin
contraseña entra de un clic desde el selector, que es lo normal en un servidor
doméstico. Una cuenta con contraseña no se abre sin ella: no vale con elegirla
en el selector. Si no fuera así, no serviría para lo único que se le pide.
"""
import hashlib
import hmac
import logging
import secrets

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from .database import SessionLocal, escribir_meta, get_db, leer_meta
from .models import Usuario

# Clave de la sesión. No se pide configurar: si no está en el .env se genera
# una y se guarda en `app_meta`, así la app funciona sin tocar nada y la sesión
# sobrevive a los reinicios. Regenerarla solo cierra las sesiones abiertas.
CLAVE_SECRETO = "clave_de_sesion"

CLAVE_SESION = "usuario_id"

# Parámetros de scrypt. Los recomendados por la documentación de Python para
# uso interactivo: ~100 ms por comprobación en hardware modesto, que es
# suficiente para que probar contraseñas a lo bruto no salga a cuenta y no
# tanto como para que se note al entrar.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1


def secreto_de_sesion() -> str:
    """Devuelve la clave de firma, creándola la primera vez.

    Se guarda en la base y no en un fichero: es el mismo sitio donde ya vive la
    marca del backfill, viaja con el volumen de datos y no obliga a añadir una
    variable de entorno obligatoria a una app cuyo README promete que todo es
    opcional.
    """
    from .config import settings

    if settings.secret_key:
        return settings.secret_key
    try:
        guardada = leer_meta(CLAVE_SECRETO)
        if guardada:
            return guardada
        nueva = secrets.token_urlsafe(48)
        escribir_meta(CLAVE_SECRETO, nueva)
        return nueva
    except Exception:
        # La tabla aún no existe. Pasa cuando se importa `app.main` contra una
        # base sin migrar -- los tests, sobre todo: en producción el entrypoint
        # ya ha migrado en otro proceso antes de que uvicorn importe nada.
        # Se usa una clave de usar y tirar: las sesiones no sobreviven al
        # reinicio, que en ese escenario es lo correcto y no lo que se quiere
        # ocultar.
        logging.getLogger(__name__).warning(
            "No se pudo leer la clave de sesión de la base (¿sin migrar?): "
            "se usa una temporal, así que las sesiones abiertas se perderán "
            "al reiniciar."
        )
        return secrets.token_urlsafe(48)


def cifrar_password(password: str) -> str:
    """`scrypt$<sal>$<hash>`, con sal propia por cuenta.

    scrypt está en la biblioteca estándar, así que esto no añade dependencias.
    La sal es obligatoria: sin ella, dos personas con la misma contraseña
    tendrían el mismo hash y bastaría una tabla precalculada.
    """
    sal = secrets.token_bytes(16)
    derivada = hashlib.scrypt(
        password.encode("utf-8"), salt=sal, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return "scrypt$%s$%s" % (sal.hex(), derivada.hex())


def comprobar_password(password: str, cifrada: str | None) -> bool:
    """Comparación en tiempo constante. Una cuenta sin contraseña no se valida
    por aquí: se entra sin pedir nada, que es lo que significa no tenerla."""
    if not cifrada:
        return False
    try:
        algoritmo, sal_hex, esperado_hex = cifrada.split("$")
    except ValueError:
        return False
    if algoritmo != "scrypt":
        return False
    derivada = hashlib.scrypt(
        password.encode("utf-8"), salt=bytes.fromhex(sal_hex),
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
    )
    return hmac.compare_digest(derivada.hex(), esperado_hex)


def cuenta_en_sesion(request: Request, db: Session) -> Usuario | None:
    """La cuenta abierta, o None si no hay ninguna o ya no existe."""
    id_usuario = request.session.get(CLAVE_SESION)
    if not id_usuario:
        return None
    return db.get(Usuario, id_usuario)


def abrir_sesion(request: Request, usuario: Usuario) -> None:
    request.session[CLAVE_SESION] = usuario.id


def cerrar_sesion(request: Request) -> None:
    request.session.pop(CLAVE_SESION, None)


def usuario_actual(request: Request, db: Session = Depends(get_db)) -> Usuario:
    """Dependencia: la cuenta abierta, o redirección al selector.

    Se lanza una excepción en vez de devolver None para que sea IMPOSIBLE
    escribir una vista que olvide comprobarlo: si la dependencia está puesta,
    o hay cuenta o la petición no llega al cuerpo de la función.
    """
    from .errores import SinCuenta

    usuario = cuenta_en_sesion(request, db)
    if usuario is None:
        raise SinCuenta()
    # Para la barra superior, que necesita el nombre en cada página. Se pone
    # aquí y no en un middleware para no repetir la consulta: esta dependencia
    # ya tiene la cuenta y la sesión de la petición.
    request.state.cuenta_abierta = usuario
    return usuario


def items_de(db: Session, usuario: Usuario):
    """Consulta de ítems ya acotada a la cuenta. **Punto de entrada único.**

    Todas las vistas parten de aquí en vez de `db.query(MediaItem)`. La
    diferencia importa: olvidar el filtro en una sola vista enseñaría el
    catálogo de otra persona, y eso no lo detecta ningún test que no lo busque
    a propósito. Con un solo sitio, hay un solo sitio que revisar.
    """
    from .models import MediaItem

    return db.query(MediaItem).filter(MediaItem.usuario_id == usuario.id)


def item_de(db: Session, usuario: Usuario, item_id: int):
    """Un ítem por id, o None si no es de esta cuenta.

    No basta con `db.get(...)`: los ids son globales, así que pedir
    `/item/123` desde otra cuenta traería el ítem 123 de quien sea. Devolver
    None hace que el 404 sea el mismo que si no existiera, que además no
    confirma a nadie que ese id exista en otra cuenta.
    """
    from .models import MediaItem

    return db.query(MediaItem).filter(
        MediaItem.id == item_id, MediaItem.usuario_id == usuario.id
    ).first()


def listas_de(db: Session, usuario: Usuario):
    from .models import Lista

    return db.query(Lista).filter(Lista.usuario_id == usuario.id)


def lista_de(db: Session, usuario: Usuario, lista_id: int):
    from .models import Lista

    return db.query(Lista).filter(
        Lista.id == lista_id, Lista.usuario_id == usuario.id
    ).first()


def cuenta_predeterminada(db: Session) -> Usuario:
    """La cuenta a la que pertenece todo lo que había antes de que existieran
    las cuentas. La crea si hace falta (instalación nueva)."""
    usuario = db.query(Usuario).order_by(Usuario.id).first()
    if usuario:
        return usuario
    usuario = Usuario(nombre="Yo")
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


def asegurar_cuenta_inicial() -> None:
    """Al arrancar: una instalación nueva necesita al menos una cuenta, o el
    selector estaría vacío y no habría forma de entrar."""
    db = SessionLocal()
    try:
        cuenta_predeterminada(db)
    finally:
        db.close()
