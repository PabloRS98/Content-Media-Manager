"""Consultas del catálogo, separadas de la vista que las pinta.

`list_catalog` tenía 210 líneas mezclando construcción de la consulta,
traducción y presentación, en el fichero más grande del proyecto. Esto se lleva
la parte de "hablar con la base de datos"; el router se queda orquestando.
"""
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..catalogo_config import BUCKETS_DURACION, condicion_de_duracion
from ..models import Episode, MediaItem, MediaStatus, MediaType, Usuario

# Columnas donde se busca. Son las cuatro cosas que uno recuerda de un ítem:
# cómo se llama, quién lo hizo, de qué va y a qué saga pertenece.
_COLUMNAS_DE_BUSQUEDA = (
    MediaItem.title,
    MediaItem.creator,
    MediaItem.genres,
    MediaItem.saga,
)


def _escapar_comodines(texto: str) -> str:
    """`%` y `_` son comodines de LIKE: sin escaparlos, buscar "100%" devuelve
    el catálogo entero. Mismo tratamiento que ya se le daba al filtro de género."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def filtrar_por_busqueda(query, texto: str | None):
    """Filtra por palabras en AND: cada palabra tiene que aparecer en alguna de
    las columnas, pero no necesariamente en la misma.

    Así "sanderson nieblas" encuentra un libro cuyo autor es Sanderson y cuyo
    título lleva "nieblas", que es como se busca de verdad cuando uno recuerda
    la mitad de cada cosa. Es el criterio que `projects-dashboard` ya tenía
    resuelto, aquí llevado a SQL para no traerse el catálogo entero a memoria.
    """
    if not texto or not texto.strip():
        return query
    for palabra in texto.lower().split():
        patron = "%%%s%%" % _escapar_comodines(palabra)
        query = query.filter(
            or_(*[
                func.lower(columna).like(patron, escape="\\")
                for columna in _COLUMNAS_DE_BUSQUEDA
            ])
        )
    return query


def aplicar_filtros(db: Session, query, media_type: MediaType | None,
                    estado: MediaStatus | None, genero: str | None,
                    tiempo: str | None, busqueda: str | None = None):
    """Aplica a `query` los filtros del catálogo que estén puestos."""
    if media_type:
        query = query.filter(MediaItem.media_type == media_type)
    if estado:
        query = query.filter(MediaItem.status == estado)
    query = filtrar_por_busqueda(query, busqueda)
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


def generos_de(db: Session, usuario: Usuario, media_type: MediaType | None) -> list[str]:
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
        MediaItem.usuario_id == usuario.id,
    ).all()
    for (cadena,) in filas:
        if not cadena:
            continue
        for genero in cadena.split(","):
            limpio = genero.strip().capitalize()
            if limpio:
                encontrados.add(limpio)
    return sorted(encontrados)


def contar_sin_portada(db: Session, usuario: Usuario, media_type: MediaType | None) -> int:
    """Solo del tipo que se está viendo: si no, el botón "Buscar portadas" de
    la pestaña de Películas mostraría un número que en realidad son libros sin
    portada, sin relación con lo que se ve en pantalla."""
    query = db.query(func.count(MediaItem.id)).filter(
        MediaItem.cover_url.is_(None), MediaItem.usuario_id == usuario.id
    )
    if media_type:
        query = query.filter(MediaItem.media_type == media_type)
    return query.scalar()
