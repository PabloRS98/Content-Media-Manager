"""Los géneros de tu catálogo, y poder arreglarlos. [N4]

Con los géneros dentro de una cadena no había dónde renombrarlos: "Sci-Fi" y
"Ciencia ficción" convivían para siempre, y cada uno filtraba la mitad de lo
que debía. Ahora son filas, así que renombrar es una operación de verdad -- y
renombrar a un nombre que ya existe FUSIONA los dos, que es el caso real.

Los nombres del vocabulario se comparten entre cuentas (igual que las
etiquetas), pero esta página solo lista los que tienen ítems TUYOS: no hay por
qué enseñarte los géneros del catálogo de al lado.
"""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..auth import verify_auth
from ..cuentas import usuario_actual
from ..database import get_db
from ..flash import redirect_flash
from ..models import Genero, Usuario
from ..services import generos as generos_svc
from ..templating import templates

router = APIRouter(tags=["generos"], dependencies=[Depends(verify_auth)])


@router.get("/generos")
def listar(request: Request, db: Session = Depends(get_db),
           usuario: Usuario = Depends(usuario_actual)):
    return templates.TemplateResponse(request, "generos.html", {
        "generos": generos_svc.con_cuantos_items(db, usuario),
    })


@router.post("/generos/{genero_id}/renombrar")
def renombrar(genero_id: int, nombre: str = Form(""),
              db: Session = Depends(get_db),
              usuario: Usuario = Depends(usuario_actual)):
    genero = db.get(Genero, genero_id)
    # Que el género tenga algún ítem tuyo: si no, estarías renombrando una
    # palabra que solo usa otra cuenta.
    if not genero or not any(i.usuario_id == usuario.id for i in genero.items):
        return redirect_flash("/generos", "Ese género no está en tu catálogo", "error")

    anterior = genero.nombre
    error = generos_svc.renombrar(db, genero, nombre)
    if error:
        return redirect_flash("/generos", error, "error")
    return redirect_flash("/generos", '"%s" pasa a ser "%s"' % (anterior, nombre.strip()))
