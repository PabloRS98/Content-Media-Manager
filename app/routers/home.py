"""Pantalla de inicio estilo Trakt/Ryot: continuar, próximos por prioridad,
wishlist, actividad reciente, próximamente (estrenos) y sugerencia."""
from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import verify_auth
from ..database import get_db
from ..models import EPISODIC_TYPES, Episode, MediaItem, MediaStatus, MediaType, Priority
from ..services import metadata
from ..templating import templates

router = APIRouter(tags=["inicio"], dependencies=[Depends(verify_auth)])

_PRIORITY_RANK = {Priority.ALTA: 0, Priority.MEDIA: 1, Priority.BAJA: 2}
_FOLLOWING = (MediaStatus.EN_PROGRESO, MediaStatus.PENDIENTE, MediaStatus.WISHLIST)


def _upcoming(db: Session, limit: int | None = None) -> list[dict]:
    """Estrenos futuros: próximos episodios de series que sigues + lanzamientos
    de la wishlist. Ordenado por fecha ascendente."""
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

    pendientes = (
        db.query(MediaItem)
        .filter(MediaItem.status == MediaStatus.PENDIENTE)
        .all()
    )
    # Ordenar por prioridad (alta→media→baja) y luego por actividad reciente
    pendientes.sort(key=lambda i: (_PRIORITY_RANK.get(i.priority, 1), -(i.updated_at.timestamp() if i.updated_at else 0)))
    proximos = pendientes[:12]

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

    return templates.TemplateResponse(request, "home.html", {
        "en_progreso": en_progreso,
        "proximos": proximos,
        "wishlist": wishlist,
        "recientes": recientes,
        "proximamente": proximamente,
        "resumen": resumen,
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
