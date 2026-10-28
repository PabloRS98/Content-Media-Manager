"""Selector de cuenta: quién de la casa está usando la app.

El selector NO pide autenticación: es la pantalla a la que se llega cuando no
hay ninguna cuenta abierta, así que exigir una cuenta para verla sería un bucle.
Lo que sí hace es no filtrar nada: solo muestra los nombres y si cada una lleva
candado. Nunca cuántos ítems tiene ni nada de su contenido.
"""
import json

from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy.orm import Session

from .. import fechas, intentos
from ..auth import verify_auth
from ..cuentas import (
    abrir_sesion,
    cerrar_sesion,
    cerrar_todas_las_sesiones,
    cifrar_password,
    comprobar_password,
    cuenta_en_sesion,
    usuario_actual,
)
from ..database import get_db
from ..flash import redirect_flash
from ..models import Usuario
from ..services import datos
from ..templating import templates

router = APIRouter(tags=["cuentas"], dependencies=[Depends(verify_auth)])

# 8 y no 4: con 4 caracteres el espacio entero se recorría en horas incluso con
# scrypt. Solo se exige al poner una nueva; las cuentas que ya tenían una más
# corta siguen entrando con ella. [R3-M3]
MIN_PASSWORD = 8


@router.get("/cuentas")
def selector(request: Request, db: Session = Depends(get_db)):
    """Quién eres. Sin datos de ninguna cuenta, solo nombres y candados."""
    return templates.TemplateResponse(request, "cuentas.html", {
        "usuarios": db.query(Usuario).order_by(Usuario.nombre).all(),
        "actual": cuenta_en_sesion(request, db),
    })


@router.post("/cuentas/entrar/{usuario_id}")
def entrar(usuario_id: int, request: Request, password: str = Form(""),
           db: Session = Depends(get_db)):
    usuario = db.get(Usuario, usuario_id)
    if not usuario:
        return redirect_flash("/cuentas", "Esa cuenta ya no existe", "error")

    # Una cuenta con contraseña no se abre sin ella. Elegirla en el selector no
    # basta: si bastara, la contraseña no protegería nada.
    if usuario.tiene_password:
        # Por cuenta y por IP: bloquear solo por cuenta dejaría a cualquiera
        # dejar a su dueño fuera a base de fallar adrede desde otro sitio.
        clave = (intentos.ip_de(request), usuario.id)
        espera = intentos.cuentas.espera(clave)
        if espera:
            return redirect_flash(
                "/cuentas", "Demasiados intentos. Prueba otra vez en %d s." % espera, "error"
            )
        if not comprobar_password(password, usuario.password_hash):
            intentos.cuentas.fallo(clave)
            return redirect_flash("/cuentas", "Contraseña incorrecta", "error")
        intentos.cuentas.acierto(clave)

    abrir_sesion(request, usuario)
    return redirect_flash("/", "Hola, %s" % usuario.nombre)


@router.post("/cuentas/salir")
def salir(request: Request):
    cerrar_sesion(request)
    return redirect_flash("/cuentas", "Sesión cerrada")


@router.post("/cuentas")
def crear(request: Request, nombre: str = Form(...), password: str = Form(""),
          db: Session = Depends(get_db)):
    nombre = nombre.strip()
    if not nombre:
        return redirect_flash("/cuentas", "Ponle un nombre a la cuenta", "error")
    if db.query(Usuario).filter(Usuario.nombre == nombre).first():
        return redirect_flash("/cuentas", "Ya hay una cuenta con ese nombre", "error")
    if password and len(password) < MIN_PASSWORD:
        return redirect_flash(
            "/cuentas", "La contraseña necesita al menos %d caracteres" % MIN_PASSWORD, "error"
        )

    usuario = Usuario(
        nombre=nombre,
        # Sin contraseña es una opción legítima, no un descuido: en un servidor
        # de casa lo normal es entrar de un clic.
        password_hash=cifrar_password(password) if password else None,
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)

    from .lists import seed_smart_lists
    seed_smart_lists(db, usuario.id)

    return redirect_flash("/cuentas", 'Cuenta "%s" creada' % nombre)


@router.get("/cuentas/ajustes")
def ajustes(request: Request, usuario: Usuario = Depends(usuario_actual)):
    """Ajustes de la cuenta abierta. Solo la suya: no hay administración de
    unas cuentas sobre otras porque no hay roles ni jerarquía."""
    return templates.TemplateResponse(request, "cuenta_ajustes.html", {"usuario": usuario})


@router.post("/cuentas/ajustes/nombre")
def cambiar_nombre(nombre: str = Form(...), usuario: Usuario = Depends(usuario_actual),
                   db: Session = Depends(get_db)):
    nombre = nombre.strip()
    if not nombre:
        return redirect_flash("/cuentas/ajustes", "El nombre no puede estar vacío", "error")
    otra = db.query(Usuario).filter(Usuario.nombre == nombre, Usuario.id != usuario.id).first()
    if otra:
        return redirect_flash("/cuentas/ajustes", "Ya hay una cuenta con ese nombre", "error")
    usuario.nombre = nombre
    db.commit()
    return redirect_flash("/cuentas/ajustes", "Nombre actualizado")


@router.post("/cuentas/ajustes/password")
def cambiar_password(request: Request, actual: str = Form(""), nueva: str = Form(""),
                     usuario: Usuario = Depends(usuario_actual),
                     db: Session = Depends(get_db)):
    """Poner, cambiar o quitar la contraseña de la cuenta abierta.

    Para cambiarla o quitarla hay que saber la que hay. Sin eso, cualquiera que
    pillara la sesión abierta podría dejar la cuenta sin protección -- o
    ponerle una y dejar fuera a su dueño.
    """
    if usuario.tiene_password and not comprobar_password(actual, usuario.password_hash):
        return redirect_flash("/cuentas/ajustes", "La contraseña actual no es correcta", "error")

    if not nueva:
        usuario.password_hash = None
        db.commit()
        return redirect_flash(
            "/cuentas/ajustes", "Cuenta sin contraseña: ahora entra de un clic"
        )

    if len(nueva) < MIN_PASSWORD:
        return redirect_flash(
            "/cuentas/ajustes",
            "La contraseña necesita al menos %d caracteres" % MIN_PASSWORD, "error",
        )
    usuario.password_hash = cifrar_password(nueva)
    # Una contraseña nueva cierra las sesiones abiertas en otros sitios: es
    # justo lo que se hace cuando se sospecha que alguien entró. [R3-M4]
    cerrar_todas_las_sesiones(request, usuario)
    db.commit()
    return redirect_flash("/cuentas/ajustes", "Contraseña actualizada")


@router.get("/cuentas/ajustes/exportar.{formato}")
def exportar(formato: str, usuario: Usuario = Depends(usuario_actual),
             db: Session = Depends(get_db)):
    """Descarga de todo lo de tu cuenta, en JSON (completo) o CSV. [R3-M21]"""
    if formato not in ("json", "csv"):
        return redirect_flash("/cuentas/ajustes", "Formato desconocido", "error")
    nombre = "catalogo-%s-%s.%s" % (
        "".join(c for c in usuario.nombre if c.isalnum()) or "cuenta",
        fechas.hoy().isoformat(), formato)
    if formato == "json":
        cuerpo = json.dumps(datos.exportar_json(db, usuario), ensure_ascii=False, indent=1)
        tipo = "application/json"
    else:
        cuerpo = datos.exportar_csv(db, usuario)
        tipo = "text/csv; charset=utf-8"
    return Response(cuerpo, media_type=tipo, headers={
        "Content-Disposition": 'attachment; filename="%s"' % nombre})


@router.post("/cuentas/ajustes/borrar")
def borrar(request: Request, confirmacion: str = Form(""), password: str = Form(""),
           usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    """Borra la cuenta y todo su catálogo. No se puede deshacer. [R3-M21]

    Pide escribir el nombre (un clic de más no basta) y, si la cuenta tiene
    contraseña, la contraseña: con la sesión abierta en un ordenador ajeno,
    nadie debería poder borrarte el catálogo.
    """
    if confirmacion.strip() != usuario.nombre:
        return redirect_flash("/cuentas/ajustes",
                              "Para borrar la cuenta, escribe su nombre exactamente", "error")
    if usuario.tiene_password:
        # El mismo freno que al entrar: esto también es probar contraseñas. [R3-M3]
        clave = (intentos.ip_de(request), usuario.id)
        if intentos.cuentas.espera(clave):
            return redirect_flash("/cuentas/ajustes", "Demasiados intentos: espera un poco", "error")
        if not comprobar_password(password, usuario.password_hash):
            intentos.cuentas.fallo(clave)
            return redirect_flash("/cuentas/ajustes", "La contraseña no es correcta", "error")
        intentos.cuentas.acierto(clave)
    nombre = usuario.nombre
    cuantos = datos.borrar_cuenta(db, usuario)
    cerrar_sesion(request)
    return redirect_flash("/cuentas", 'Cuenta "%s" borrada, con sus %d ítems' % (nombre, cuantos))


@router.post("/cuentas/ajustes/cerrar-sesiones")
def cerrar_sesiones(request: Request, usuario: Usuario = Depends(usuario_actual),
                    db: Session = Depends(get_db)):
    """Cierra la cuenta en todos los navegadores menos en este. [R3-M4]"""
    cerrar_todas_las_sesiones(request, usuario)
    db.commit()
    return redirect_flash("/cuentas/ajustes", "Sesiones cerradas en los demás dispositivos")
