"""Listas/colecciones manuales del usuario y su gestión."""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..auth import verify_auth
from ..database import get_db
from ..flash import redirect_flash
from ..models import Lista, MediaItem
from ..templating import templates

router = APIRouter(tags=["listas"], dependencies=[Depends(verify_auth)])


@router.get("/listas")
def list_lists(request: Request, db: Session = Depends(get_db)):
    listas = db.query(Lista).order_by(Lista.name).all()
    return templates.TemplateResponse(request, "listas.html", {"listas": listas})


@router.post("/listas")
def create_list(name: str = Form(...), db: Session = Depends(get_db)):
    name = name.strip()
    if not name:
        return redirect_flash("/listas", "Ponle un nombre a la lista", "error")
    if db.query(Lista).filter(Lista.name == name).first():
        return redirect_flash("/listas", "Ya existe una lista con ese nombre", "error")
    db.add(Lista(name=name))
    db.commit()
    return redirect_flash("/listas", 'Lista "%s" creada' % name)


@router.get("/listas/{list_id}")
def list_detail(list_id: int, request: Request, db: Session = Depends(get_db)):
    lista = db.get(Lista, list_id)
    if not lista:
        return redirect_flash("/listas", "La lista ya no existe", "error")
    return templates.TemplateResponse(request, "lista_detail.html", {"lista": lista})


@router.post("/listas/{list_id}/eliminar")
def delete_list(list_id: int, db: Session = Depends(get_db)):
    lista = db.get(Lista, list_id)
    if lista:
        db.delete(lista)
        db.commit()
    return redirect_flash("/listas", "Lista eliminada", "info")


@router.post("/listas/{list_id}/quitar/{item_id}")
def remove_from_list(list_id: int, item_id: int, db: Session = Depends(get_db)):
    lista = db.get(Lista, list_id)
    item = db.get(MediaItem, item_id)
    if lista and item and item in lista.items:
        lista.items.remove(item)
        db.commit()
    return redirect_flash("/listas/%d" % list_id, "Quitado de la lista", "info")


@router.post("/item/{item_id}/anadir-lista")
def add_to_list(item_id: int, list_id: str = Form(""), nueva: str = Form(""), db: Session = Depends(get_db)):
    """Añade el ítem a una lista existente o a una nueva creada al vuelo."""
    item = db.get(MediaItem, item_id)
    if not item:
        return redirect_flash("/catalogo", "El ítem ya no existe", "error")

    lista = None
    nueva = nueva.strip()
    if nueva:
        lista = db.query(Lista).filter(Lista.name == nueva).first() or Lista(name=nueva)
        db.add(lista)
        db.flush()
    elif list_id.strip().isdigit():
        lista = db.get(Lista, int(list_id))

    if not lista:
        return redirect_flash("/item/%d" % item_id, "Elige o crea una lista", "error")
    if item not in lista.items:
        lista.items.append(item)
    db.commit()
    return redirect_flash("/item/%d" % item_id, 'Añadido a "%s"' % lista.name)
