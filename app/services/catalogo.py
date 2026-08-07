"""Consultas del catálogo, separadas de la vista que las pinta.

`list_catalog` tenía 210 líneas mezclando construcción de la consulta,
traducción y presentación, en el fichero más grande del proyecto. Esto se lleva
la parte de "hablar con la base de datos"; el router se queda orquestando.
"""
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..catalogo_config import BUCKETS_DURACION, condicion_de_duracion
from ..models import Episode, MediaItem, MediaStatus, MediaType


def aplicar_filtros(db: Session, query, media_type: MediaType | None,
                    estado: MediaStatus | None, genero: str | None,
                    tiempo: str | None):
    """Aplica a `query` los filtros del catálogo que estén puestos."""
    if media_type:
        query = query.filter(MediaItem.media_type == media_type)
    if estado:
        query = query.filter(MediaItem.status == estado)
    if genero:
        # No es inyección SQL (SQLAlchemy parametriza), pero % y _ del usuario
        # se interpretan como comodines de LIKE si no se escapan: sin esto,
        # ?genero=% devolvía el catálogo entero y ?genero=_ cualquier género
        # de un carácter.
        escapado = genero.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(MediaItem.genres.like(f"%{escapado}%", escape="\\"))
    if tiempo and media_type:
        query = _filtrar_por_duracion(db, query, media_type, tiempo)
    return query


def _filtrar_por_duracion(db: Session, query, media_type: MediaType, tiempo: str):
    """Los rangos y sus etiquetas viven juntos en `catalogo_config.py`."""
    if media_type == MediaType.SERIE:
        # Las series se miden en episodios, así que la "columna" es un recuento
        # y hay que traerlo con una subconsulta.
        subq = (
            db.query(Episode.item_id, func.count(Episode.id).label("ep_count"))
            .group_by(Episode.item_id).subquery()
        )
        condicion = condicion_de_duracion(
            media_type, tiempo, func.coalesce(subq.c.ep_count, 0)
        )
        if condicion is None:
            return query
        return query.outerjoin(subq, MediaItem.id == subq.c.item_id).filter(condicion)

    columna = BUCKETS_DURACION[media_type][0] if media_type in BUCKETS_DURACION else None
    condicion = condicion_de_duracion(media_type, tiempo, columna)
    return query.filter(condicion) if condicion is not None else query


def generos_de(db: Session, media_type: MediaType | None) -> list[str]:
    """Géneros distintos presentes en el catálogo, para poblar el filtro.

    Se agrupan en Python porque `genres` es una cadena separada por comas y no
    una relación: no hay forma de hacerlo en SQL mientras siga siéndolo. Ese es
    el problema de fondo, y su solución es normalizarlos a tabla.
    """
    if not media_type:
        return []
    encontrados: set[str] = set()
    filas = db.query(MediaItem.genres).filter(
        MediaItem.media_type == media_type,
        MediaItem.genres.is_not(None),
    ).all()
    for (cadena,) in filas:
        if not cadena:
            continue
        for genero in cadena.split(","):
            limpio = genero.strip().capitalize()
            if limpio:
                encontrados.add(limpio)
    return sorted(encontrados)


def contar_sin_portada(db: Session, media_type: MediaType | None) -> int:
    """Solo del tipo que se está viendo: si no, el botón "Buscar portadas" de
    la pestaña de Películas mostraría un número que en realidad son libros sin
    portada, sin relación con lo que se ve en pantalla."""
    query = db.query(func.count(MediaItem.id)).filter(MediaItem.cover_url.is_(None))
    if media_type:
        query = query.filter(MediaItem.media_type == media_type)
    return query.scalar()
