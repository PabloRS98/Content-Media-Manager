"""Catálogo de medios: búsqueda con autocompletado, alta, edición completa,
ficha de detalle con episodios, orden + paginación y estadísticas."""
from datetime import UTC, date, datetime
from math import ceil
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from sqlalchemy import extract, func, or_
from sqlalchemy.orm import Session, joinedload

from ..auth import verify_auth
from ..database import SessionLocal, get_db
from ..flash import redirect_flash
from ..models import Episode, Lista, MediaItem, MediaStatus, MediaType, Priority, Tag
from ..security import safe_external_url
from ..services import googlebooks, itunes, metadata, openlibrary, rawg, tmdb
from ..templating import templates

router = APIRouter(tags=["catalogo"], dependencies=[Depends(verify_auth)])

PER_PAGE = 24

ORDERINGS = {
    "recientes": ("Actividad reciente", lambda q: q.order_by(MediaItem.updated_at.desc())),
    "añadido": ("Fecha de añadido", lambda q: q.order_by(MediaItem.created_at.desc())),
    "alfabetico": ("Alfabético", lambda q: q.order_by(func.lower(MediaItem.title))),
    "rating": ("Mejor valorados", lambda q: q.order_by(MediaItem.rating.is_(None), MediaItem.rating.desc())),
    "año": ("Año", lambda q: q.order_by(MediaItem.year.is_(None), MediaItem.year.desc())),
}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _parse_optional(value: str, caster):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return caster(value)
    except ValueError:
        return None


def _parse_rating(value: str) -> int | None:
    """La nota es 1-10. El min/max del HTML es solo del lado del cliente:
    sin esto, un rating de 99 se guardaba y desaparecía silenciosamente del
    histograma de /estadisticas (que sí filtra 1 <= rating <= 10) sin avisar."""
    parsed = _parse_optional(value, int)
    return parsed if parsed is not None and 1 <= parsed <= 10 else None


def _enum_or_none(enum_cls, value):
    """Convierte un valor de query param a enum, o None si no es válido (evita 500)."""
    try:
        return enum_cls(value) if value else None
    except ValueError:
        return None


@router.get("/catalogo")
def list_catalog(
    request: Request,
    tipo: str | None = None,
    estado: str | None = None,
    genero: str | None = None,
    tiempo: str | None = None,
    orden: str = "recientes",
    pagina: int = 1,
    db: Session = Depends(get_db),
):
    query = db.query(MediaItem)
    mt = _enum_or_none(MediaType, tipo)
    if mt:
        query = query.filter(MediaItem.media_type == mt)
    ms = _enum_or_none(MediaStatus, estado)
    if ms:
        query = query.filter(MediaItem.status == ms)

    # 1. Filtro de Género
    if genero:
        # No es inyección SQL (SQLAlchemy parametriza), pero % y _ del usuario
        # se interpretan como comodines de LIKE si no se escapan: sin esto,
        # ?genero=% devolvía el catálogo entero y ?genero=_ cualquier género
        # de un carácter.
        genero_escapado = genero.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(MediaItem.genres.like(f"%{genero_escapado}%", escape="\\"))

    # 2. Filtro de Tiempo/Duración
    if tiempo and mt:
        if mt == MediaType.LIBRO:
            if tiempo == "corto":
                query = query.filter(MediaItem.page_count < 150)
            elif tiempo == "medio":
                query = query.filter(MediaItem.page_count >= 150, MediaItem.page_count <= 300)
            elif tiempo == "largo":
                query = query.filter(MediaItem.page_count > 300, MediaItem.page_count <= 500)
            elif tiempo == "muy_largo":
                query = query.filter(MediaItem.page_count > 500)
        elif mt == MediaType.PELICULA:
            if tiempo == "corto":
                query = query.filter(MediaItem.runtime_minutes < 90)
            elif tiempo == "medio":
                query = query.filter(MediaItem.runtime_minutes >= 90, MediaItem.runtime_minutes <= 150)
            elif tiempo == "largo":
                query = query.filter(MediaItem.runtime_minutes > 150)
        elif mt == MediaType.SERIE:
            from sqlalchemy import func

            from ..models import Episode
            subq = db.query(Episode.item_id, func.count(Episode.id).label("ep_count")).group_by(Episode.item_id).subquery()
            if tiempo == "corto":
                query = query.outerjoin(subq, MediaItem.id == subq.c.item_id).filter(func.coalesce(subq.c.ep_count, 0) < 10)
            elif tiempo == "medio":
                query = query.outerjoin(subq, MediaItem.id == subq.c.item_id).filter(func.coalesce(subq.c.ep_count, 0) >= 10, func.coalesce(subq.c.ep_count, 0) <= 30)
            elif tiempo == "largo":
                query = query.outerjoin(subq, MediaItem.id == subq.c.item_id).filter(func.coalesce(subq.c.ep_count, 0) > 30)
        elif mt == MediaType.VIDEOJUEGO:
            if tiempo == "corto":
                query = query.filter(MediaItem.hltb_hours < 10)
            elif tiempo == "medio":
                query = query.filter(MediaItem.hltb_hours >= 10, MediaItem.hltb_hours <= 30)
            elif tiempo == "largo":
                query = query.filter(MediaItem.hltb_hours > 30, MediaItem.hltb_hours <= 60)
            elif tiempo == "muy_largo":
                query = query.filter(MediaItem.hltb_hours > 60)
        elif mt == MediaType.PODCAST:
            if tiempo == "corto":
                query = query.filter(MediaItem.runtime_minutes < 30)
            elif tiempo == "medio":
                query = query.filter(MediaItem.runtime_minutes >= 30, MediaItem.runtime_minutes <= 60)
            elif tiempo == "largo":
                query = query.filter(MediaItem.runtime_minutes > 60)

    # Ordenamiento
    orden = orden if orden in ORDERINGS else "recientes"
    query = ORDERINGS[orden][1](query)

    # Paginación
    total = query.count()
    total_paginas = max(1, ceil(total / PER_PAGE))
    pagina = min(max(1, pagina), total_paginas)
    items = query.offset((pagina - 1) * PER_PAGE).limit(PER_PAGE).all()

    # Contado solo del tipo que se está viendo: si no, el botón "Buscar
    # portadas" de la pestaña de Películas mostraría un número que en
    # realidad son libros sin portada, sin relación con lo que se ve en pantalla.
    sin_portada_query = db.query(MediaItem).filter(MediaItem.cover_url.is_(None))
    if mt:
        sin_portada_query = sin_portada_query.filter(MediaItem.media_type == mt)
    sin_portada = sin_portada_query.count()

    # 3. Obtener géneros únicos para poblar el filtro
    generos_disponibles = set()
    if mt:
        items_generos = db.query(MediaItem.genres).filter(
            MediaItem.media_type == mt,
            MediaItem.genres.is_not(None)
        ).all()
        for row in items_generos:
            if row[0]:
                for g in row[0].split(","):
                    g_clean = g.strip().capitalize()
                    if g_clean:
                        generos_disponibles.add(g_clean)
    generos_lista = sorted(generos_disponibles)

    # 4. Obtener opciones de duración/tiempo correspondientes
    tiempos_disponibles = []
    if mt == MediaType.LIBRO:
        tiempos_disponibles = [
            ("corto", "< 150 págs"),
            ("medio", "150 - 300 págs"),
            ("largo", "300 - 500 págs"),
            ("muy_largo", "> 500 págs")
        ]
    elif mt == MediaType.PELICULA:
        tiempos_disponibles = [
            ("corto", "< 90 mins"),
            ("medio", "90 - 150 mins"),
            ("largo", "> 150 mins")
        ]
    elif mt == MediaType.SERIE:
        tiempos_disponibles = [
            ("corto", "< 10 caps"),
            ("medio", "10 - 30 caps"),
            ("largo", "> 30 caps")
        ]
    elif mt == MediaType.VIDEOJUEGO:
        tiempos_disponibles = [
            ("corto", "< 10 h (HLTB)"),
            ("medio", "10 - 30 h (HLTB)"),
            ("largo", "30 - 60 h (HLTB)"),
            ("muy_largo", "> 60 h (HLTB)")
        ]
    elif mt == MediaType.PODCAST:
        tiempos_disponibles = [
            ("corto", "< 30 mins"),
            ("medio", "30 - 60 mins"),
            ("largo", "> 60 mins")
        ]

    # 5. Mapeo personalizado de etiquetas de estado
    # WISHLIST tiene que estar en los 5 diccionarios: `statuses` (abajo) incluye
    # los 5 valores del enum, y a status_labels.get() le faltaba justo este, así
    # que Jinja imprimía literalmente "None" en el desplegable del catálogo.
    status_labels_raw = {
        MediaStatus.WISHLIST: "Wishlist",
        MediaStatus.PENDIENTE: "Pendiente",
        MediaStatus.EN_PROGRESO: "En progreso",
        MediaStatus.COMPLETADO: "Completado",
        MediaStatus.ABANDONADO: "Abandonado"
    }
    if mt == MediaType.LIBRO:
        status_labels_raw = {
            MediaStatus.WISHLIST: "Lo quiero",
            MediaStatus.PENDIENTE: "Por leer",
            MediaStatus.EN_PROGRESO: "Leyendo",
            MediaStatus.COMPLETADO: "Leído",
            MediaStatus.ABANDONADO: "Abandonado"
        }
    elif mt in (MediaType.PELICULA, MediaType.SERIE):
        status_labels_raw = {
            MediaStatus.WISHLIST: "Lo quiero ver",
            MediaStatus.PENDIENTE: "Por ver",
            MediaStatus.EN_PROGRESO: "Viendo",
            MediaStatus.COMPLETADO: "Visto",
            MediaStatus.ABANDONADO: "Abandonado"
        }
    elif mt == MediaType.VIDEOJUEGO:
        status_labels_raw = {
            MediaStatus.WISHLIST: "Lo quiero jugar",
            MediaStatus.PENDIENTE: "Por jugar",
            MediaStatus.EN_PROGRESO: "Jugando",
            MediaStatus.COMPLETADO: "Terminado/Jugado",
            MediaStatus.ABANDONADO: "Abandonado"
        }
    elif mt == MediaType.PODCAST:
        status_labels_raw = {
            MediaStatus.WISHLIST: "Lo quiero escuchar",
            MediaStatus.PENDIENTE: "Por escuchar",
            MediaStatus.EN_PROGRESO: "Escuchando",
            MediaStatus.COMPLETADO: "Escuchado",
            MediaStatus.ABANDONADO: "Abandonado"
        }
    status_labels = {s.value: label for s, label in status_labels_raw.items()}

    # Solo para mostrar la selección actual en el desplegable de filtro sin
    # repetir esta búsqueda en la plantilla.
    tiempo_label = next((label for val, label in tiempos_disponibles if val == tiempo), None)
    orden_label = ORDERINGS[orden][0]

    return templates.TemplateResponse(request, "catalog.html", {
        "items": items,
        "media_types": list(MediaType),
        "statuses": list(MediaStatus),
        "priorities": list(Priority),
        "tipo_filtro": mt.value if mt else None,
        "estado_filtro": ms.value if ms else None,
        "genero_filtro": genero,
        "tiempo_filtro": tiempo,
        "orden": orden,
        "ordenes": [(k, v[0]) for k, v in ORDERINGS.items()],
        "pagina": pagina,
        "total_paginas": total_paginas,
        "total": total,
        "sin_portada": sin_portada,
        "generos_disponibles": generos_lista,
        "tiempos_disponibles": tiempos_disponibles,
        "status_labels": status_labels,
        "tiempo_label": tiempo_label,
        "orden_label": orden_label,
    })


@router.post("/catalogo/completar-portadas")
def catalog_fill_covers(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    # BATCH_SIZE=30 ítems x SLEEP_BETWEEN=0.7s son ya 21s mínimo, y hasta más de
    # 2 minutos con las APIs lentas: hecho dentro de la propia petición HTTP,
    # cualquier proxy inverso delante corta por timeout antes de que termine.
    # Se lanza en segundo plano y se avisa de que ha empezado; el contador
    # "sin portada" de la página ya se recalcula solo en la siguiente carga.
    from ..services.enrich import enrich_missing_covers_en_segundo_plano, estado_actual

    if estado_actual()["corriendo"]:
        msg = "Ya hay una búsqueda de portadas en marcha; espera a que termine."
    else:
        background_tasks.add_task(enrich_missing_covers_en_segundo_plano, SessionLocal)
        msg = "Buscando portadas en segundo plano. Vuelve en un minuto y recarga para ver el resultado."

    # La cabecera Referer la controla el cliente: no se usa tal cual como
    # destino de la redirección (open redirect). Solo se conserva la ruta
    # interna, rechazando "//host" (protocol-relative) además de URLs absolutas.
    referer = request.headers.get("referer") or ""
    path = urlparse(referer).path or "/catalogo"
    destino = path if path.startswith("/") and not path.startswith("//") else "/catalogo"
    return redirect_flash(destino, msg)


@router.get("/buscar")
def search_external(tipo: str, request: Request, q: str = "", idioma: str = "es", db: Session = Depends(get_db)):
    idioma = idioma if idioma in ("es", "en") else "es"
    results = []
    source = None
    if q and len(q.strip()) >= 2:
        if tipo == MediaType.LIBRO.value:
            results = googlebooks.search_books(q, idioma=idioma)
            if results:
                source = "googlebooks"
            else:
                results = openlibrary.search_books(q, idioma=idioma)
                source = "openlibrary"
        elif tipo == MediaType.PELICULA.value:
            results = tmdb.search_movies(q)
            source = "tmdb"
        elif tipo == MediaType.SERIE.value:
            results = tmdb.search_tv(q)
            source = "tmdb"
        elif tipo == MediaType.VIDEOJUEGO.value:
            results = rawg.search_games(q)
            source = "rawg"
        elif tipo == MediaType.PODCAST.value:
            results = itunes.search_podcasts(q)
            source = "itunes"
            
    if not source:
        source = {"libro": "googlebooks", "pelicula": "tmdb", "serie": "tmdb",
                  "videojuego": "rawg", "podcast": "itunes"}.get(tipo)
                  
    return templates.TemplateResponse(request, "search_results.html",
                                      {"results": results, "tipo": tipo, "source": source, "idioma": idioma})


@router.get("/sugerencia")
def suggest_random(request: Request, tipo: str | None = None, db: Session = Depends(get_db)):
    """Un ítem pendiente al azar (fragmento HTMX para el panel de sugerencia)."""
    query = db.query(MediaItem).filter(MediaItem.status == MediaStatus.PENDIENTE)
    mt = _enum_or_none(MediaType, tipo)
    if mt:
        query = query.filter(MediaItem.media_type == mt)
    item = query.order_by(func.random()).first()
    return templates.TemplateResponse(request, "_suggestion.html", {"item": item, "tipo": tipo})


@router.post("/agregar")
def add_item(
    media_type: MediaType = Form(...),
    title: str = Form(...),
    status: MediaStatus = Form(MediaStatus.PENDIENTE),
    external_id: str = Form(""),
    external_source: str = Form(""),
    cover_url: str = Form(""),
    year: str = Form(""),
    creator: str = Form(""),
    overview: str = Form(""),
    genres: str = Form(""),
    page_count: str = Form(""),
    release_date: str = Form(""),
    db: Session = Depends(get_db),
):
    item = MediaItem(
        media_type=media_type,
        title=title,
        status=status,
        external_id=external_id or None,
        external_source=external_source or None,
        cover_url=safe_external_url(cover_url),
        year=_parse_optional(year, int),
        creator=creator or None,
        overview=overview,
        genres=genres.strip() or None,
        page_count=_parse_optional(page_count, int),
        release_date=_parse_optional(release_date, lambda v: date.fromisoformat(v[:10])),
        completed_at=date.today() if status == MediaStatus.COMPLETADO else None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    # Enriquecer (duración/reparto/saga) y, en series, traer episodios
    metadata.enrich_item(db, item)
    db.commit()

    destino = "wishlist" if status == MediaStatus.WISHLIST else "el catálogo"
    extra = ""
    if item.is_episodic and item.episodes:
        extra = " (%d episodios)" % len(item.episodes)
    return redirect_flash("/catalogo", '"%s" añadido a %s%s' % (title, destino, extra))


@router.get("/item/{item_id}")
def item_detail(item_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.get(MediaItem, item_id)
    if not item:
        return redirect_flash("/catalogo", "El ítem ya no existe", "error")

    # Episodios agrupados por temporada (para series/podcasts)
    seasons = []
    if item.is_episodic:
        by_season: dict[int, list[Episode]] = {}
        for e in item.episodes:
            by_season.setdefault(e.season_number, []).append(e)
        for sn in sorted(by_season):
            eps = by_season[sn]
            watched = sum(1 for e in eps if e.watched)
            seasons.append({"number": sn, "episodes": eps, "watched": watched, "total": len(eps)})

    stats = item.episode_stats() if item.is_episodic else None

    # Misma saga: por colección de TMDB (automático) o por nombre de saga (manual)
    related = []
    conds = []
    if item.tmdb_collection_id:
        conds.append(MediaItem.tmdb_collection_id == item.tmdb_collection_id)
    if item.saga:
        conds.append(MediaItem.saga == item.saga)
    if conds:
        related = (
            db.query(MediaItem)
            .filter(or_(*conds), MediaItem.id != item.id)
            .order_by(MediaItem.year)
            .all()
        )

    all_lists = db.query(Lista).filter(Lista.filtro_estado.is_(None)).order_by(Lista.name).all()
    item_lists = [lista for lista in all_lists if item in lista.items]

    return templates.TemplateResponse(request, "detail.html", {
        "item": item,
        "seasons": seasons,
        "stats": stats,
        "related": related,
        "all_lists": all_lists,
        "item_lists": item_lists,
        "statuses": list(MediaStatus),
        "priorities": list(Priority),
    })


@router.post("/item/{item_id}/actualizar")
def update_item(
    item_id: int,
    title: str = Form(...),
    status: MediaStatus = Form(...),
    priority: Priority = Form(Priority.MEDIA),
    year: str = Form(""),
    creator: str = Form(""),
    cover_url: str = Form(""),
    genres: str = Form(""),
    saga: str = Form(""),
    rating: str = Form(""),
    notes: str = Form(""),
    progress_current: str = Form(""),
    progress_total: str = Form(""),
    progress_unit: str = Form(""),
    runtime_minutes: str = Form(""),
    page_count: str = Form(""),
    hltb_hours: str = Form(""),
    tags: str = Form(""),
    db: Session = Depends(get_db),
):
    item = db.get(MediaItem, item_id)
    if not item:
        return redirect_flash("/catalogo", "El ítem ya no existe", "error")

    item.title = title.strip() or item.title
    item.year = _parse_optional(year, int)
    item.creator = creator.strip() or None
    # safe_external_url y no strip() a secas: el campo es editable a mano y se
    # autorrellena desde seis APIs, y su valor acaba en el src de un <img>.
    item.cover_url = safe_external_url(cover_url)
    item.genres = genres.strip() or None
    item.saga = saga.strip() or None
    item.priority = priority
    item.runtime_minutes = _parse_optional(runtime_minutes, int)
    item.page_count = _parse_optional(page_count, int)
    item.hltb_hours = _parse_optional(hltb_hours, float)
    if item.status != MediaStatus.COMPLETADO and status == MediaStatus.COMPLETADO and item.completed_at is None:
        item.completed_at = date.today()  # primera vez que se marca completado
    item.status = status
    item.rating = _parse_rating(rating)
    item.notes = notes
    item.progress_current = _parse_optional(progress_current, float)
    item.progress_total = _parse_optional(progress_total, float)
    item.progress_unit = progress_unit.strip() or None
    item.updated_at = _utcnow()

    tag_names = [t.strip() for t in tags.split(",") if t.strip()]
    tag_objs = []
    for name in tag_names:
        tag = db.query(Tag).filter(Tag.name == name).first()
        if not tag:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
        tag_objs.append(tag)
    item.tags = tag_objs
    db.commit()
    return redirect_flash("/item/%d" % item.id, '"%s" actualizado' % item.title)


@router.post("/item/{item_id}/eliminar")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = db.get(MediaItem, item_id)
    if item:
        db.delete(item)
        db.commit()
    return redirect_flash("/catalogo", "Ítem eliminado", "info")


@router.post("/item/{item_id}/episodio/{ep_id}/toggle")
def toggle_episode(item_id: int, ep_id: int, db: Session = Depends(get_db)):
    item = db.get(MediaItem, item_id)
    ep = db.get(Episode, ep_id)
    if item and ep and ep.item_id == item.id:
        metadata.toggle_episode(item, ep)
        item.updated_at = _utcnow()
        db.commit()
    return redirect_flash("/item/%d" % item_id, "Episodio actualizado", "info")


@router.post("/item/{item_id}/marcar-hasta/{ep_id}")
def mark_through_episode(item_id: int, ep_id: int, db: Session = Depends(get_db)):
    item = db.get(MediaItem, item_id)
    ep = db.get(Episode, ep_id)
    if item and ep and ep.item_id == item.id:
        metadata.mark_through(item, ep)
        item.updated_at = _utcnow()
        db.commit()
        return redirect_flash("/item/%d" % item_id, "Marcado hasta %s" % ep.code)
    return redirect_flash("/item/%d" % item_id, "No se pudo marcar", "error")


@router.get("/estadisticas")
def stats(request: Request, db: Session = Depends(get_db)):
    year_now = date.today().year

    total = db.query(MediaItem).count()
    por_estado = dict(
        db.query(MediaItem.status, func.count(MediaItem.id)).group_by(MediaItem.status).all()
    )
    por_tipo = dict(
        db.query(MediaItem.media_type, func.count(MediaItem.id)).group_by(MediaItem.media_type).all()
    )

    completados_este_año = (
        db.query(MediaItem)
        .filter(MediaItem.completed_at.isnot(None), extract("year", MediaItem.completed_at) == year_now)
        .count()
    )

    por_mes = [0] * 12
    rows = (
        db.query(extract("month", MediaItem.completed_at), func.count(MediaItem.id))
        .filter(MediaItem.completed_at.isnot(None), extract("year", MediaItem.completed_at) == year_now)
        .group_by(extract("month", MediaItem.completed_at))
        .all()
    )
    for month, count in rows:
        por_mes[int(month) - 1] = count

    genre_counts: dict[str, int] = {}
    for (genres_str,) in db.query(MediaItem.genres).filter(MediaItem.genres.isnot(None)).all():
        for g in [x.strip() for x in genres_str.split(",") if x.strip()]:
            genre_counts[g] = genre_counts.get(g, 0) + 1
    top_generos = sorted(genre_counts.items(), key=lambda kv: kv[1], reverse=True)[:8]

    ratings = [0] * 10
    for rating, count in (
        db.query(MediaItem.rating, func.count(MediaItem.id))
        .filter(MediaItem.rating.isnot(None))
        .group_by(MediaItem.rating)
        .all()
    ):
        if 1 <= rating <= 10:
            ratings[rating - 1] = count

    # Tiempo total consumido (minutos): pelis + juegos + libros completados + episodios vistos.
    # Las tres primeras sumas las hace SQLite (func.sum) en vez de traer todos
    # los MediaItem completos solo para sumar un campo. La de episodios sí
    # necesita los objetos (usa ep.item.runtime_minutes de fallback), pero con
    # joinedload en la misma consulta en vez de un SELECT por episodio (N+1):
    # con 30 series x 10 episodios vistos eran ~44 sentencias SQL; con esto, ~14.
    minutos_pelis = db.query(func.sum(MediaItem.runtime_minutes)).filter(
        MediaItem.media_type == MediaType.PELICULA, MediaItem.status == MediaStatus.COMPLETADO
    ).scalar() or 0
    horas_juegos = db.query(func.sum(MediaItem.hltb_hours)).filter(
        MediaItem.media_type == MediaType.VIDEOJUEGO, MediaItem.status == MediaStatus.COMPLETADO
    ).scalar() or 0
    paginas_libros = db.query(func.sum(MediaItem.page_count)).filter(
        MediaItem.media_type == MediaType.LIBRO, MediaItem.status == MediaStatus.COMPLETADO
    ).scalar() or 0

    tiempo_min = minutos_pelis + horas_juegos * 60 + paginas_libros * 1.5
    episodios_vistos = (
        db.query(Episode).join(MediaItem)
        .options(joinedload(Episode.item))
        .filter(Episode.watched.is_(True))
    )
    for ep in episodios_vistos:
        tiempo_min += ep.runtime_minutes or ep.item.runtime_minutes or 45
    tiempo_horas = round(tiempo_min / 60)

    # Colección por década (según el año del ítem)
    dec_counts: dict[int, int] = {}
    for (yr,) in db.query(MediaItem.year).filter(MediaItem.year.isnot(None)).all():
        dec = (int(yr) // 10) * 10
        dec_counts[dec] = dec_counts.get(dec, 0) + 1
    por_decada = sorted(dec_counts.items())

    # "Año en cifras": comparación con el año anterior + mejores del año
    completados_prev = (
        db.query(MediaItem)
        .filter(MediaItem.completed_at.isnot(None), extract("year", MediaItem.completed_at) == year_now - 1)
        .count()
    )
    mejores = (
        db.query(MediaItem)
        .filter(
            MediaItem.completed_at.isnot(None), extract("year", MediaItem.completed_at) == year_now,
            MediaItem.rating.isnot(None),
        )
        .order_by(MediaItem.rating.desc(), MediaItem.completed_at.desc())
        .limit(5).all()
    )

    return templates.TemplateResponse(request, "stats.html", {
        "total": total,
        "año": year_now,
        "año_prev": year_now - 1,
        "completados_este_año": completados_este_año,
        "completados_prev": completados_prev,
        "en_progreso": por_estado.get(MediaStatus.EN_PROGRESO, 0),
        "pendientes": por_estado.get(MediaStatus.PENDIENTE, 0),
        "completados": por_estado.get(MediaStatus.COMPLETADO, 0),
        "abandonados": por_estado.get(MediaStatus.ABANDONADO, 0),
        "wishlist": por_estado.get(MediaStatus.WISHLIST, 0),
        "tiempo_horas": tiempo_horas,
        "mejores": mejores,
        "por_tipo": {t.value: por_tipo.get(t, 0) for t in MediaType},
        "por_mes": por_mes,
        "por_decada": por_decada,
        "top_generos": top_generos,
        "ratings": ratings,
    })
