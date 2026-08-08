"""Integridad referencial de las tablas puente.

SQLite no aplica las claves foráneas sin `PRAGMA foreign_keys=ON`, que esta app
no activa (`database.py` solo pone `journal_mode` y `synchronous`), así que la
limpieza depende por completo de que SQLAlchemy sepa que la fila existe.
"""
from sqlalchemy import select

from app.models import Lista, list_items, media_item_tags


def filas_de_listas(db):
    return db.execute(select(list_items)).all()


def test_borrar_un_item_lo_quita_de_sus_listas(usuario, db, crear_item):
    """La relación estaba declarada solo del lado de `Lista`, así que al borrar
    un `MediaItem` SQLAlchemy limpiaba `media_item_tags` (esa sí tiene relación
    en `MediaItem`) pero no `list_items`: no sabía que existía desde ese lado."""
    item = crear_item(title="Para borrar")
    lista = Lista(usuario_id=usuario.id, name="Mi lista")
    lista.items.append(item)
    db.add(lista)
    db.commit()
    assert len(filas_de_listas(db)) == 1

    db.delete(item)
    db.commit()

    assert filas_de_listas(db) == []


def test_un_item_nuevo_no_hereda_listas(usuario, db, crear_item):
    """El bug visible, y el motivo de que esto no sea solo higiene: SQLite
    reasigna los ids de `media_items` cuando no hay AUTOINCREMENT (y no lo
    hay), así que un ítem nuevo podía recibir el id de uno borrado y aparecer
    solo en las listas donde estaba el anterior."""
    viejo = crear_item(title="El de antes")
    lista = Lista(usuario_id=usuario.id, name="Mi lista")
    lista.items.append(viejo)
    db.add(lista)
    db.commit()

    id_reutilizable = viejo.id
    db.delete(viejo)
    db.commit()

    nuevo = crear_item(title="Recién llegado")
    db.refresh(lista)
    assert nuevo.title not in [i.title for i in lista.items]
    # Y si además le tocó el mismo id, la lista sigue vacía.
    if nuevo.id == id_reutilizable:
        assert lista.items == []


def test_borrar_un_item_sigue_limpiando_sus_etiquetas(db, crear_item):
    """Lo que ya funcionaba: no romperlo al añadir la relación inversa."""
    from app.models import Tag

    item = crear_item(title="Con etiquetas")
    item.tags = [Tag(name="documental")]
    db.commit()
    assert len(db.execute(select(media_item_tags)).all()) == 1

    db.delete(item)
    db.commit()
    assert db.execute(select(media_item_tags)).all() == []


def test_borrar_una_lista_no_borra_sus_items(usuario, db, crear_item):
    """El otro sentido de la relación: quitar la lista deja los ítems."""
    from app.models import MediaItem

    item = crear_item(title="Superviviente")
    lista = Lista(usuario_id=usuario.id, name="Efímera")
    lista.items.append(item)
    db.add(lista)
    db.commit()

    db.delete(lista)
    db.commit()

    assert db.get(MediaItem, item.id) is not None
    assert filas_de_listas(db) == []


def test_la_limpieza_borra_las_filas_huerfanas_que_ya_existieran(usuario, db, crear_item):
    """Las bases de producción ya arrastran filas muertas de los ítems que se
    borraron antes de este arreglo: la relación inversa no las limpia sola."""
    from app.database import limpiar_filas_huerfanas

    item = crear_item(title="Ya borrado")
    lista = Lista(usuario_id=usuario.id, name="Con basura")
    lista.items.append(item)
    db.add(lista)
    db.commit()

    # Se simula el estado antiguo: la fila puente sobrevive al ítem.
    db.execute(list_items.delete())
    db.execute(list_items.insert().values(list_id=lista.id, media_item_id=99999))
    db.commit()
    assert len(filas_de_listas(db)) == 1

    limpiar_filas_huerfanas(db.get_bind())

    assert filas_de_listas(db) == []
