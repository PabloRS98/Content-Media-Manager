"""Recuentos de episodios para muchas fichas de una vez [MC-X2].

`MediaItem.episode_stats()` lee la relación `episodes`, que es perezosa: en una
ficha suelta da igual, pero una página de catálogo con 24 series son 24
consultas más --una por tarjeta-- y crecen con lo que tengas. Peor aún, cada
una se trae TODOS los episodios de esa serie para contar dos números: una serie
con nueve temporadas son cientos de filas materializadas para pintar "12/180".

Aquí se hace por lotes: dos consultas para toda la página, una que agrega los
recuentos y otra que saca el próximo episodio de cada serie, y el resultado se
deja puesto en cada ítem. `episode_stats()` lo usa si está.

No sustituye a `episode_stats()`: la ficha de detalle, el recálculo de estado y
los avisos siguen llamándola tal cual, porque van de uno en uno y ahí la
relación perezosa es exactamente lo que hace falta.
"""
from collections.abc import Iterable

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased

from ..models import ATRIBUTO_STATS, Episode, MediaItem


def precalcular(db: Session, items: Iterable[MediaItem]) -> None:
    """Deja los recuentos ya calculados en cada ítem episódico de `items`.

    Es un adelanto, no un cambio de contrato: si no se llama, o si la sesión
    expira los objetos por un commit posterior, `episode_stats()` vuelve sola a
    la relación perezosa y devuelve lo mismo. Solo cambia cuántas consultas
    hacen falta.
    """
    episodicos = [i for i in items if i is not None and i.is_episodic]
    if not episodicos:
        return
    ids = {i.id for i in episodicos}

    vistos = func.sum(case((Episode.watched.is_(True), 1), else_=0))
    recuentos = {
        item_id: (total, vistos_ or 0)
        for item_id, total, vistos_ in db.execute(
            select(Episode.item_id, func.count(Episode.id), vistos)
            .where(Episode.item_id.in_(ids))
            .group_by(Episode.item_id)
        )
    }

    # El próximo de cada serie es el primero sin ver que no sea de la temporada
    # 0 (los especiales y recaps de TMDB, que al ordenar van delante y
    # contaminaban el aviso: ver `episode_stats`). `row_number()` lo saca para
    # todas las series a la vez en lugar de una consulta por serie.
    orden = func.row_number().over(
        partition_by=Episode.item_id,
        order_by=(Episode.season_number, Episode.episode_number),
    ).label("orden")
    numerados = (
        select(Episode, orden)
        .where(
            Episode.item_id.in_(ids),
            Episode.watched.is_(False),
            Episode.season_number != 0,
        )
        .subquery()
    )
    siguiente = aliased(Episode, numerados)
    proximos = {
        ep.item_id: ep
        for ep in db.execute(select(siguiente).where(numerados.c.orden == 1)).scalars()
    }

    for item in episodicos:
        total, vistos_de_este = recuentos.get(item.id, (0, 0))
        item.__dict__[ATRIBUTO_STATS] = {
            "total": total,
            "watched": vistos_de_este,
            "next": proximos.get(item.id),
        }
