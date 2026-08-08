"""Pantalla de inicio estilo Trakt/Ryot: continuar, próximos por prioridad,
wishlist, actividad reciente, próximamente (estrenos) y sugerencia."""
from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..auth import verify_auth
from ..database import get_db
from ..models import EPISODIC_TYPES, Episode, Lista, MediaItem, MediaStatus, MediaType, Priority
from ..services import metadata, recomendaciones
from ..templating import templates

router = APIRouter(tags=["inicio"], dependencies=[Depends(verify_auth)])

_PRIORITY_RANK = {Priority.ALTA: 0, Priority.MEDIA: 1, Priority.BAJA: 2}

# El mismo criterio que `_PRIORITY_RANK`, expresado para que lo aplique SQLite:
# así el ORDER BY y el LIMIT ocurren en la base y no hace falta traerse las
# filas para ordenarlas en Python. `else_` cubre también priority NULL, que es
# lo que hacía el `.get(..., 1)` del diccionario.
_ORDEN_PRIORIDAD = case(
    (MediaItem.priority == Priority.ALTA, 0),
    (MediaItem.priority == Priority.BAJA, 2),
    else_=1,
)
_FOLLOWING = (MediaStatus.EN_PROGRESO, MediaStatus.PENDIENTE, MediaStatus.WISHLIST)


def _upcoming(db: Session, limit: int | None = None) -> list[dict]:
    """Estrenos futuros: próximos episodios de series que sigues + lanzamientos
    de la wishlist. Ordenado por fecha ascendente.

    Con `limit`, el recorte se aplica también a cada una de las dos consultas y
    no solo al resultado combinado: como las dos vienen ordenadas por fecha
    ascendente, los `limit` primeros del total están necesariamente entre los
    `limit` primeros de cada una. Antes se traían TODOS los episodios futuros y
    TODA la wishlist con fecha para quedarse con seis.

    Sin `limit` (que es como lo llama `/calendario`) sigue trayéndolo todo: esa
    vista lo necesita, y recortarla ahí sería perder entradas en silencio.
    """
    hoy = date.today()
    entradas: list[dict] = []

    eps = (
        db.query(Episode).join(MediaItem)
        .filter(
            Episode.air_date.isnot(None), Episode.air_date >= hoy,
            # TMDB usa la temporada 0 para especiales/recaps, que a menudo se
            # "estrenan" el mismo día que el episodio real y lo duplican aquí.
            Episode.season_number != 0,
            MediaItem.media_type.in_(EPISODIC_TYPES),
            MediaItem.status.in_(_FOLLOWING),
        )
        .order_by(Episode.air_date)
        .limit(limit)
        .all()
    )
    for ep in eps:
        entradas.append({"fecha": ep.air_date, "item": ep.item, "tipo": "episodio", "episodio": ep})

    releases = (
        db.query(MediaItem)
        .filter(
            MediaItem.status == MediaStatus.WISHLIST,
            MediaItem.release_date.isnot(None), MediaItem.release_date >= hoy,
        )
        .order_by(MediaItem.release_date)
        .limit(limit)
        .all()
    )
    for it in releases:
        entradas.append({"fecha": it.release_date, "item": it, "tipo": "estreno", "episodio": None})

    entradas.sort(key=lambda e: e["fecha"])
    return entradas[:limit] if limit else entradas


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    en_progreso = (
        db.query(MediaItem)
        .filter(MediaItem.status == MediaStatus.EN_PROGRESO)
        .order_by(MediaItem.updated_at.desc())
        .limit(12)
        .all()
    )

    # Ordenar por prioridad (alta→media→baja) y luego por actividad reciente.
    # El orden lo hace SQL con un CASE, no Python: antes se traían TODOS los
    # pendientes --cada objeto con todas sus columnas, incluida `overview`, que
    # es Text-- para quedarse con doce. Con 3 000 pendientes eran 3 000 objetos
    # ORM materializados para descartar 2 988.
    proximos = (
        db.query(MediaItem)
        .filter(MediaItem.status == MediaStatus.PENDIENTE)
        .order_by(_ORDEN_PRIORIDAD, MediaItem.updated_at.desc())
        .limit(12)
        .all()
    )

    wishlist = (
        db.query(MediaItem)
        .filter(MediaItem.status == MediaStatus.WISHLIST)
        .order_by(MediaItem.updated_at.desc())
        .limit(8)
        .all()
    )

    recientes = (
        db.query(MediaItem)
        .filter(MediaItem.completed_at.isnot(None))
        .order_by(MediaItem.completed_at.desc(), MediaItem.updated_at.desc())
        .limit(8)
        .all()
    )

    por_estado = dict(
        db.query(MediaItem.status, func.count(MediaItem.id)).group_by(MediaItem.status).all()
    )
    resumen = {
        "total": db.query(MediaItem).count(),
        "en_progreso": por_estado.get(MediaStatus.EN_PROGRESO, 0),
        "pendientes": por_estado.get(MediaStatus.PENDIENTE, 0),
        "completados": por_estado.get(MediaStatus.COMPLETADO, 0),
        "wishlist": por_estado.get(MediaStatus.WISHLIST, 0),
    }

    proximamente = _upcoming(db, limit=6)

    # Destino real (pestaña Listas) de los 4 accesos rápidos de abajo: ver
    # seed_smart_lists() en routers/lists.py.
    listas_dinamicas = {
        x.filtro_estado: x.id
        for x in db.query(Lista).filter(Lista.filtro_estado.isnot(None)).all()
    }

    return templates.TemplateResponse(request, "home.html", {
        "en_progreso": en_progreso,
        "recomendaciones": recomendaciones.recomendar(db, limite=6),
        "proximos": proximos,
        "wishlist": wishlist,
        "recientes": recientes,
        "proximamente": proximamente,
        "resumen": resumen,
        "listas_dinamicas": listas_dinamicas,
        "media_types": list(MediaType),
        "hay_algo": bool(en_progreso or proximos or wishlist or recientes),
    })


@router.get("/calendario")
def calendario(request: Request, db: Session = Depends(get_db)):
    """Todos los estrenos futuros agrupados por fecha."""
    entradas = _upcoming(db)
    por_fecha: dict = {}
    for e in entradas:
        por_fecha.setdefault(e["fecha"], []).append(e)
    grupos = [{"fecha": f, "entradas": por_fecha[f]} for f in sorted(por_fecha)]
    return templates.TemplateResponse(request, "calendario.html", {"grupos": grupos})


@router.get("/tengo-tiempo")
def time_fit(request: Request, minutos: int = 60, db: Session = Depends(get_db)):
    """Sugiere qué pendiente/en curso cabe en el tiempo disponible (fragmento HTMX)."""
    minutos = max(5, min(minutos, 1000))
    candidatos = (
        db.query(MediaItem)
        .filter(MediaItem.status.in_([MediaStatus.PENDIENTE, MediaStatus.EN_PROGRESO]))
        .all()
    )
    encajan = []
    for item in candidatos:
        est = metadata.estimated_minutes(item)
        if est is not None and est <= minutos:
            encajan.append((item, est))
    # Aprovechar el tiempo: primero los más largos que aún caben, priorizando prioridad alta
    encajan.sort(key=lambda t: (_PRIORITY_RANK.get(t[0].priority, 1), -t[1]))
    encajan = encajan[:8]

    return templates.TemplateResponse(request, "_timefit.html", {
        "encajan": encajan,
        "minutos": minutos,
    })
