"""Listas/colecciones del usuario y su gestión.

Además de las listas manuales, existen 4 "vistas automáticas" (una por
estado: en progreso, pendiente, completado, wishlist) que dan destino real
-- en esta misma pestaña -- a los accesos rápidos de inicio. Su contenido
se calcula en vivo por `MediaItem.status`, no se guarda como una relación:
ver `seed_smart_lists()`.
"""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import verify_auth
from ..cuentas import item_de, items_de, lista_de, listas_de, usuario_actual
from ..database import get_db
from ..flash import redirect_flash
from ..models import Lista, MediaItem, MediaStatus, Usuario
from ..services import episodios
from ..templating import templates

router = APIRouter(tags=["listas"], dependencies=[Depends(verify_auth)])

SMART_LISTS = [
    (MediaStatus.EN_PROGRESO, "En progreso"),
    (MediaStatus.PENDIENTE, "Pendientes"),
    (MediaStatus.COMPLETADO, "Completados"),
    (MediaStatus.WISHLIST, "Wishlist"),
]


def seed_smart_lists(db: Session, usuario_id: int) -> None:
    """Crea (si faltan) las 4 vistas automáticas DE ESA CUENTA. Idempotente: no
    toca las que ya existan. Si el nombre ya lo usa una lista manual suya, la
    automática se crea con un nombre distinto para no chocar con el `unique`
    de (usuario_id, name).

    Cada cuenta tiene las suyas: son vistas sobre su catálogo, y el de al lado
    no tiene nada que ver."""
    suyas = db.query(Lista).filter(Lista.usuario_id == usuario_id)
    existentes = {x.filtro_estado for x in suyas.filter(Lista.filtro_estado.isnot(None))}
    creadas = False
    for estado, nombre in SMART_LISTS:
        if estado.value in existentes:
            continue
        candidato = nombre
        if suyas.filter(Lista.name == candidato).first():
            candidato = f"{nombre} (automática)"
        db.add(Lista(name=candidato, filtro_estado=estado.value, usuario_id=usuario_id))
        creadas = True
    if creadas:
        db.commit()


@router.get("/listas")
def list_lists(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(usuario_actual)):
    # Un COUNT agrupado, no uno por lista. Y el resultado va en un diccionario
    # aparte en vez de inyectarse como atributo en los objetos del ORM: eso
    # último funcionaba, pero `item_count` no existe en el modelo, así que
    # cualquier `db.refresh()` o expiración de sesión lo borraba sin avisar --
    # y con StrictUndefined (MC-M16) eso ya no falla en silencio, revienta la
    # página. Además podría colisionar con una columna futura del mismo nombre.
    conteos = dict(
        items_de(db, usuario)
        .with_entities(MediaItem.status, func.count(MediaItem.id))
        .group_by(MediaItem.status).all()
    )
    dinamicas = [
        {"lista": lista, "total": conteos.get(MediaStatus(lista.filtro_estado), 0)}
        for lista in listas_de(db, usuario).filter(Lista.filtro_estado.isnot(None))
        .order_by(Lista.id).all()
    ]
    manuales = listas_de(db, usuario).filter(Lista.filtro_estado.is_(None)).order_by(Lista.name).all()
    return templates.TemplateResponse(request, "listas.html", {"dinamicas": dinamicas, "listas": manuales})


@router.post("/listas")
def create_list(name: str = Form(...), db: Session = Depends(get_db), usuario: Usuario = Depends(usuario_actual)):
    name = name.strip()
    if not name:
        return redirect_flash("/listas", "Ponle un nombre a la lista", "error")
    if listas_de(db, usuario).filter(Lista.name == name).first():
        return redirect_flash("/listas", "Ya existe una lista con ese nombre", "error")
    db.add(Lista(name=name, usuario_id=usuario.id))
    db.commit()
    return redirect_flash("/listas", 'Lista "%s" creada' % name)


@router.get("/listas/{list_id}")
def list_detail(
    list_id: int,
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(usuario_actual),
):
    lista = lista_de(db, usuario, list_id)
    if not lista:
        return redirect_flash("/listas", "La lista ya no existe", "error")
    if lista.filtro_estado:
        items = (
            items_de(db, usuario)
            .filter(MediaItem.status == MediaStatus(lista.filtro_estado))
            .order_by(MediaItem.updated_at.desc())
            .all()
        )
    else:
        items = lista.items
    # Una lista no está paginada: se pinta entera, así que el N+1 de las
    # tarjetas se notaba aquí más que en ningún otro sitio (MC-X2).
    episodios.precalcular(db, items)
    return templates.TemplateResponse(request, "lista_detail.html", {
        "lista": lista, "items": items, "es_dinamica": bool(lista.filtro_estado),
    })


@router.post("/listas/{list_id}/eliminar")
def delete_list(list_id: int, db: Session = Depends(get_db), usuario: Usuario = Depends(usuario_actual)):
    lista = lista_de(db, usuario, list_id)
    if lista and lista.filtro_estado:
        return redirect_flash("/listas/%d" % list_id, "Es una vista automática, no se puede eliminar", "error")
    if lista:
        db.delete(lista)
        db.commit()
    return redirect_flash("/listas", "Lista eliminada", "info")


@router.post("/listas/{list_id}/quitar/{item_id}")
def remove_from_list(
    list_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(usuario_actual),
):
    lista = lista_de(db, usuario, list_id)
    item = item_de(db, usuario, item_id)
    if lista and item and item in lista.items:
        lista.items.remove(item)
        db.commit()
    return redirect_flash("/listas/%d" % list_id, "Quitado de la lista", "info")


@router.post("/item/{item_id}/anadir-lista")
def add_to_list(
    item_id: int,
    list_id: str = Form(""),
    nueva: str = Form(""),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(usuario_actual),
):
    """Añade el ítem a una lista existente o a una nueva creada al vuelo."""
    item = item_de(db, usuario, item_id)
    if not item:
        return redirect_flash("/catalogo", "El ítem ya no existe", "error")

    lista = None
    nueva = nueva.strip()
    if nueva:
        lista = (listas_de(db, usuario).filter(Lista.name == nueva).first()
                 or Lista(name=nueva, usuario_id=usuario.id))
        db.add(lista)
        db.flush()
    elif list_id.strip().isdigit():
        lista = lista_de(db, usuario, int(list_id))

    if not lista:
        return redirect_flash("/item/%d" % item_id, "Elige o crea una lista", "error")
    if lista.filtro_estado:
        return redirect_flash("/item/%d" % item_id, "Es una vista automática, no admite ítems a mano", "error")
    if item not in lista.items:
        lista.items.append(item)
    db.commit()
    return redirect_flash("/item/%d" % item_id, 'Añadido a "%s"' % lista.name)
