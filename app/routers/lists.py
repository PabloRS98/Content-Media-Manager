"""Listas/colecciones del usuario y su gestión.

Además de las listas manuales, existen 4 "vistas automáticas" (una por
estado: en progreso, pendiente, completado, wishlist) que dan destino real
-- en esta misma pestaña -- a los accesos rápidos de inicio. Su contenido
se calcula en vivo por `MediaItem.status`, no se guarda como una relación:
ver `seed_smart_lists()`.
"""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..auth import verify_auth
from ..database import get_db
from ..flash import redirect_flash
from ..models import Lista, MediaItem, MediaStatus
from ..templating import templates

router = APIRouter(tags=["listas"], dependencies=[Depends(verify_auth)])

SMART_LISTS = [
    (MediaStatus.EN_PROGRESO, "En progreso"),
    (MediaStatus.PENDIENTE, "Pendientes"),
    (MediaStatus.COMPLETADO, "Completados"),
    (MediaStatus.WISHLIST, "Wishlist"),
]


def seed_smart_lists(db: Session) -> None:
    """Crea (si faltan) las 4 vistas automáticas. Idempotente: no toca las
    que ya existan. Si el nombre ya lo usa una lista manual del usuario, la
    automática se crea con un nombre distinto para no chocar con el
    `unique` de `Lista.name`."""
    existentes = {x.filtro_estado for x in db.query(Lista).filter(Lista.filtro_estado.isnot(None))}
    creadas = False
    for estado, nombre in SMART_LISTS:
        if estado.value in existentes:
            continue
        candidato = nombre
        if db.query(Lista).filter(Lista.name == candidato).first():
            candidato = f"{nombre} (automática)"
        db.add(Lista(name=candidato, filtro_estado=estado.value))
        creadas = True
    if creadas:
        db.commit()


@router.get("/listas")
def list_lists(request: Request, db: Session = Depends(get_db)):
    dinamicas = db.query(Lista).filter(Lista.filtro_estado.isnot(None)).order_by(Lista.id).all()
    for lista in dinamicas:
        lista.item_count = db.query(MediaItem).filter(MediaItem.status == MediaStatus(lista.filtro_estado)).count()
    manuales = db.query(Lista).filter(Lista.filtro_estado.is_(None)).order_by(Lista.name).all()
    return templates.TemplateResponse(request, "listas.html", {"dinamicas": dinamicas, "listas": manuales})


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
    if lista.filtro_estado:
        items = (
            db.query(MediaItem)
            .filter(MediaItem.status == MediaStatus(lista.filtro_estado))
            .order_by(MediaItem.updated_at.desc())
            .all()
        )
    else:
        items = lista.items
    return templates.TemplateResponse(request, "lista_detail.html", {
        "lista": lista, "items": items, "es_dinamica": bool(lista.filtro_estado),
    })


@router.post("/listas/{list_id}/eliminar")
def delete_list(list_id: int, db: Session = Depends(get_db)):
    lista = db.get(Lista, list_id)
    if lista and lista.filtro_estado:
        return redirect_flash("/listas/%d" % list_id, "Es una vista automática, no se puede eliminar", "error")
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
    if lista.filtro_estado:
        return redirect_flash("/item/%d" % item_id, "Es una vista automática, no admite ítems a mano", "error")
    if item not in lista.items:
        lista.items.append(item)
    db.commit()
    return redirect_flash("/item/%d" % item_id, 'Añadido a "%s"' % lista.name)
