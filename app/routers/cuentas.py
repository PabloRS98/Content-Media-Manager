"""Selector de cuenta: quién de la casa está usando la app.

El selector NO pide autenticación: es la pantalla a la que se llega cuando no
hay ninguna cuenta abierta, así que exigir una cuenta para verla sería un bucle.
Lo que sí hace es no filtrar nada: solo muestra los nombres y si cada una lleva
candado. Nunca cuántos ítems tiene ni nada de su contenido.
"""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..auth import verify_auth
from ..cuentas import (
    abrir_sesion,
    cerrar_sesion,
    cifrar_password,
    comprobar_password,
    cuenta_en_sesion,
    usuario_actual,
)
from ..database import get_db
from ..flash import redirect_flash
from ..models import Usuario
from ..templating import templates

router = APIRouter(tags=["cuentas"], dependencies=[Depends(verify_auth)])

MIN_PASSWORD = 4


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
    if usuario.tiene_password and not comprobar_password(password, usuario.password_hash):
        return redirect_flash("/cuentas", "Contraseña incorrecta", "error")

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
def cambiar_password(actual: str = Form(""), nueva: str = Form(""),
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
    db.commit()
    return redirect_flash("/cuentas/ajustes", "Contraseña actualizada")
