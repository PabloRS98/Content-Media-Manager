"""Consultas del catálogo, separadas de la vista que las pinta.

`list_catalog` tenía 210 líneas mezclando construcción de la consulta,
traducción y presentación, en el fichero más grande del proyecto. Esto se lleva
la parte de "hablar con la base de datos"; el router se queda orquestando.
"""
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..catalogo_config import BUCKETS_DURACION, condicion_de_duracion
from ..models import (
    Episode,
    Genero,
    MediaItem,
    MediaStatus,
    MediaType,
    Usuario,
    media_item_generos,
)

# Columnas donde se busca. Son las cosas que uno recuerda de un ítem: cómo se
# llama, quién lo hizo y a qué saga pertenece. El género se busca aparte,
# porque desde [N4] ya no es una columna sino una relación.
_COLUMNAS_DE_BUSQUEDA = (
    MediaItem.title,
    MediaItem.creator,
    MediaItem.saga,
)


def _items_con_genero_que_contenga(patron: str):
    """Subconsulta de ids cuyo género encaja con el patrón de búsqueda."""
    return (
        select(media_item_generos.c.media_item_id)
        .join(Genero, Genero.id == media_item_generos.c.genero_id)
        .where(func.lower(Genero.nombre).like(patron, escape="\\"))
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
            or_(
                *[
                    func.lower(columna).like(patron, escape="\\")
                    for columna in _COLUMNAS_DE_BUSQUEDA
                ],
                MediaItem.id.in_(_items_con_genero_que_contenga(patron)),
            )
        )
    return query


def aplicar_filtros(db: Session, query, media_type: MediaType | None,
                    estado: MediaStatus | None, genero: str | None,
                    tiempo: str | None, busqueda: str | None = None,
                    plataforma: str | None = None):
    """Aplica a `query` los filtros del catálogo que estén puestos."""
    if media_type:
        query = query.filter(MediaItem.media_type == media_type)
    if estado:
        query = query.filter(MediaItem.status == estado)
    if plataforma:
        # Igualdad, no LIKE: son valores completos elegidos de un desplegable,
        # y con LIKE "Movistar" se llevaría por delante "Movistar Plus+", que
        # es otra suscripción distinta.
        query = query.filter(MediaItem.plataforma == plataforma)
    query = filtrar_por_busqueda(query, busqueda)
    if genero:
        # Igualdad sobre la tabla de géneros, no un LIKE sobre una cadena. Ya
        # no hay comodines que escapar --que era un apaño para que ?genero=%
        # no devolviera el catálogo entero-- y, sobre todo, pedir "Acción" ya
        # no arrastra "Acción y aventura", que es otro género de TMDB. [N4]
        query = query.filter(
            MediaItem.id.in_(
                select(media_item_generos.c.media_item_id)
                .join(Genero, Genero.id == media_item_generos.c.genero_id)
                .where(Genero.nombre == genero)
            )
        )
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

    Un DISTINCT en SQL desde [N4]. Antes había que traer la columna `genres` de
    todas las filas del tipo y partirla en Python, porque no hay forma de
    agrupar por los trozos de una cadena.
    """
    if not media_type:
        return []
    filas = (
        db.query(Genero.nombre)
        .join(media_item_generos, Genero.id == media_item_generos.c.genero_id)
        .join(MediaItem, MediaItem.id == media_item_generos.c.media_item_id)
        .filter(
            MediaItem.media_type == media_type,
            MediaItem.usuario_id == usuario.id,
        )
        .distinct()
        .order_by(Genero.nombre)
        .all()
    )
    return [nombre for (nombre,) in filas]


def plataformas_de(db: Session, usuario: Usuario, media_type: MediaType | None) -> list[str]:
    """Plataformas distintas del catálogo, para poblar el filtro.

    Esta sí sale de un DISTINCT en SQL, al revés que los géneros: `plataforma`
    guarda un valor por ítem, no una lista dentro de una cadena. Es exactamente
    la diferencia que hace que los géneros haya que agruparlos en Python.
    """
    query = db.query(MediaItem.plataforma).filter(
        MediaItem.usuario_id == usuario.id,
        MediaItem.plataforma.is_not(None),
    )
    if media_type:
        query = query.filter(MediaItem.media_type == media_type)
    return sorted({p for (p,) in query.distinct().all() if p})


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
