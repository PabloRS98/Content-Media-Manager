"""Tus datos: llevártelos y borrarlos. [R3-M21]

Hasta ahora, la única forma de sacar el catálogo de la app era copiar el
fichero SQLite del volumen, con las cuentas de todos dentro, y no había forma
de borrar una cuenta. Aquí, todo va acotado a una cuenta.

- `exportar_json`: todo lo de la cuenta, con episodios, géneros, etiquetas y
  listas. Es lo que hace falta para no perder nada al irse.
- `exportar_csv`: una fila por ítem, para abrirlo en una hoja de cálculo.
- `borrar_cuenta`: la cuenta y todo lo suyo, en una transacción.
"""
import csv
import enum
import io
from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    Episode,
    Lista,
    MediaItem,
    Tag,
    Usuario,
    list_items,
    media_item_generos,
    media_item_tags,
)
from . import generos

# Lo que no se exporta: identificadores internos (no significan nada fuera de
# esta base) y marcas de funcionamiento de la app.
_COLUMNAS_INTERNAS = {"id", "usuario_id", "cover_checked_at", "release_notified"}
COLUMNAS_DE_ITEM = [c.name for c in MediaItem.__table__.columns if c.name not in _COLUMNAS_INTERNAS]
COLUMNAS_DE_EPISODIO = [c.name for c in Episode.__table__.columns
                        if c.name not in {"id", "item_id", "notified"}]


def _valor(v):
    if isinstance(v, enum.Enum):
        return v.value
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _items(db: Session, usuario: Usuario) -> list[MediaItem]:
    return (
        db.query(MediaItem)
        .filter(MediaItem.usuario_id == usuario.id)
        .options(selectinload(MediaItem.episodes), selectinload(MediaItem.generos),
                 selectinload(MediaItem.tags))
        .order_by(MediaItem.id)
        .all()
    )


def exportar_json(db: Session, usuario: Usuario) -> dict:
    items = _items(db, usuario)
    listas = (db.query(Lista).filter(Lista.usuario_id == usuario.id)
              .options(selectinload(Lista.items)).order_by(Lista.name).all())
    return {
        "formato": "content-media-manager/1",
        "exportado": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "cuenta": usuario.nombre,
        "items": [
            {
                **{c: _valor(getattr(it, c)) for c in COLUMNAS_DE_ITEM},
                "generos": [g.nombre for g in it.generos],
                "etiquetas": [t.name for t in it.tags],
                "episodios": [{c: _valor(getattr(e, c)) for c in COLUMNAS_DE_EPISODIO}
                              for e in it.episodes],
            }
            for it in items
        ],
        # Las listas automáticas ("Completados"...) se calculan del estado:
        # exportarlas sería repetir lo que ya dice cada ítem.
        "listas": [
            {"nombre": lista.name, "items": [i.title for i in lista.items]}
            for lista in listas if lista.filtro_estado is None
        ],
    }


def exportar_csv(db: Session, usuario: Usuario) -> str:
    salida = io.StringIO()
    escritor = csv.writer(salida)
    escritor.writerow([*COLUMNAS_DE_ITEM, "generos", "etiquetas", "episodios_vistos", "episodios"])
    for it in _items(db, usuario):
        vistos = sum(1 for e in it.episodes if e.watched)
        escritor.writerow([
            *(_valor(getattr(it, c)) for c in COLUMNAS_DE_ITEM),
            ", ".join(g.nombre for g in it.generos),
            ", ".join(t.name for t in it.tags),
            vistos if it.episodes else "",
            len(it.episodes) if it.episodes else "",
        ])
    # BOM: sin él, Excel abre el UTF-8 como Latin-1 y las tildes salen rotas.
    return "﻿" + salida.getvalue()


def borrar_cuenta(db: Session, usuario: Usuario) -> int:
    """Borra la cuenta y todo lo suyo. Devuelve cuántos ítems se fueron.

    A mano y en orden, de las hojas hacia la raíz: con las FK activas
    [R3-B9], borrar un ítem que aún tiene episodios o pertenencias falla.
    Las etiquetas y géneros que se queden sin ítems se borran después,
    porque son vocabulario compartido entre cuentas.
    """
    ids = select(MediaItem.id).where(MediaItem.usuario_id == usuario.id)
    listas = select(Lista.id).where(Lista.usuario_id == usuario.id)
    cuantos = db.query(MediaItem).filter(MediaItem.usuario_id == usuario.id).count()

    db.execute(delete(Episode).where(Episode.item_id.in_(ids)))
    db.execute(delete(list_items).where(list_items.c.media_item_id.in_(ids)))
    db.execute(delete(list_items).where(list_items.c.list_id.in_(listas)))
    db.execute(delete(media_item_tags).where(media_item_tags.c.media_item_id.in_(ids)))
    db.execute(delete(media_item_generos).where(media_item_generos.c.media_item_id.in_(ids)))
    db.execute(delete(MediaItem).where(MediaItem.usuario_id == usuario.id))
    db.execute(delete(Lista).where(Lista.usuario_id == usuario.id))
    db.delete(usuario)
    db.execute(delete(Tag).where(~Tag.id.in_(select(media_item_tags.c.tag_id))))
    db.commit()
    generos.borrar_huerfanos(db)
    db.commit()
    return cuantos
