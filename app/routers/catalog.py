"""Catálogo de medios: búsqueda con autocompletado, alta, edición completa,
ficha de detalle con episodios, orden + paginación y estadísticas."""
import logging
from datetime import UTC, date, datetime
from math import ceil
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from sqlalchemy import extract, func, or_, select
from sqlalchemy.orm import Session, joinedload

from ..auth import verify_auth
from ..catalogo_config import etiquetas_de, etiquetas_de_duracion
from ..cuentas import item_de, items_de, listas_de, usuario_actual
from ..database import SessionLocal, get_db
from ..flash import redirect_flash
from ..models import (
    Episode,
    Lista,
    MediaItem,
    MediaStatus,
    MediaType,
    Priority,
    Tag,
    Usuario,
    media_item_tags,
)
from ..security import safe_external_url
from ..services import (
    catalogo,
    episodios,
    googlebooks,
    itunes,
    metadata,
    openlibrary,
    rawg,
    tmdb,
)
from ..templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["catalogo"], dependencies=[Depends(verify_auth)])

PER_PAGE = 24

# Las claves VAN EN LA URL, así que son ASCII: mezclaban acentuadas con sin
# acentuar (`añadido` y `año` junto a `alfabetico`), y una clave acentuada hay
# que percent-encodearla --`?orden=a%C3%B1o`-- para escribirla a mano o
# copiarla. Las etiquetas visibles sí llevan tilde: es lo que se lee. [MC-B7]
ORDERINGS = {
    "recientes": ("Actividad reciente", lambda q: q.order_by(MediaItem.updated_at.desc())),
    "anadido": ("Fecha de añadido", lambda q: q.order_by(MediaItem.created_at.desc())),
    "alfabetico": ("Alfabético", lambda q: q.order_by(func.lower(MediaItem.title))),
    "rating": ("Mejor valorados", lambda q: q.order_by(MediaItem.rating.is_(None), MediaItem.rating.desc())),
    "anio": ("Año", lambda q: q.order_by(MediaItem.year.is_(None), MediaItem.year.desc())),
}

# Las claves viejas están en los marcadores de quien ya usa la app. Sin esto,
# `?orden=año` no sería un orden conocido y caería al de por defecto EN
# SILENCIO: el usuario vería otra cosa sin entender por qué.
ALIAS_DE_ORDEN = {"añadido": "anadido", "año": "anio"}


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


def _opciones_de_filtro(mt: MediaType | None, tiempo: str | None, orden: str) -> dict:
    """Lo que necesitan los desplegables de filtro: las opciones y la etiqueta
    de la selección actual, para no repetir la búsqueda en la plantilla."""
    tiempos = etiquetas_de_duracion(mt)
    return {
        # Las etiquetas de estado salen de la tabla de catalogo_config.py, la
        # misma que usa la macro de las tarjetas: estaban duplicadas y ya
        # habían divergido (a la de la plantilla le faltaba wishlist, así que
        # un ítem deseado se veía distinto en cada sitio).
        "status_labels": {s.value: label for s, label in etiquetas_de(mt).items()},
        "tiempos_disponibles": tiempos,
        "tiempo_label": next((label for val, label in tiempos if val == tiempo), None),
        "orden_label": ORDERINGS[orden][0],
    }


@router.get("/catalogo")
def list_catalog(
    request: Request,
    tipo: str | None = None,
    estado: str | None = None,
    genero: str | None = None,
    tiempo: str | None = None,
    orden: str = "recientes",
    pagina: int = 1,
    # El buscador del catálogo. Ojo, no confundir con el `q` de `/buscar`, que
    # consulta las APIs externas para AÑADIR: este busca en lo que ya tienes.
    buscar: str | None = None,
    usuario: Usuario = Depends(usuario_actual),
    db: Session = Depends(get_db),
):
    mt = _enum_or_none(MediaType, tipo)
    ms = _enum_or_none(MediaStatus, estado)
    query = catalogo.aplicar_filtros(db, items_de(db, usuario), mt, ms, genero, tiempo, buscar)

    orden = ALIAS_DE_ORDEN.get(orden, orden)
    orden = orden if orden in ORDERINGS else "recientes"
    query = ORDERINGS[orden][1](query)

    total = query.count()
    total_paginas = max(1, ceil(total / PER_PAGE))
    pagina = min(max(1, pagina), total_paginas)
    items = query.offset((pagina - 1) * PER_PAGE).limit(PER_PAGE).all()
    # Los recuentos de episodios de toda la página, de una vez: cada tarjeta
    # los pedía por su cuenta y eran una consulta por serie (MC-X2).
    episodios.precalcular(db, items)

    sin_portada = catalogo.contar_sin_portada(db, usuario, mt)
    generos_lista = catalogo.generos_de(db, usuario, mt)

    return templates.TemplateResponse(request, "catalog.html", {
        **_opciones_de_filtro(mt, tiempo, orden),
        "items": items,
        "media_types": list(MediaType),
        "statuses": list(MediaStatus),
        "priorities": list(Priority),
        "tipo_filtro": mt.value if mt else None,
        "estado_filtro": ms.value if ms else None,
        "genero_filtro": genero,
        "tiempo_filtro": tiempo,
        "buscar": buscar or "",
        "orden": orden,
        "ordenes": [(k, v[0]) for k, v in ORDERINGS.items()],
        "pagina": pagina,
        "total_paginas": total_paginas,
        "total": total,
        "sin_portada": sin_portada,
        "generos_disponibles": generos_lista,
    })


def borrar_etiquetas_huerfanas(db: Session) -> int:
    """Borra las etiquetas que ya no usa ningún ítem. Devuelve cuántas.

    Nada las borraba: al quitar la última "documental" de todos los ítems, la
    fila seguía ahí para siempre. La tabla crecía monótonamente y un día una
    nube de etiquetas o un autocompletado ofrecería etiquetas que ya no usa
    nadie. Se hace aquí, tras guardar, porque son pocas filas y es el único
    momento en el que una etiqueta puede quedarse sin uso.
    """
    huerfanas = db.query(Tag).filter(~Tag.id.in_(select(media_item_tags.c.tag_id))).all()
    for tag in huerfanas:
        db.delete(tag)
    if huerfanas:
        db.commit()
    return len(huerfanas)


def enriquecer_en_segundo_plano(item_id: int) -> None:
    """Enriquece un ítem recién creado, fuera del ciclo petición-respuesta.

    Sesión propia: la de la petición original ya está cerrada cuando esto se
    ejecuta. Mismo patrón que `enrich_missing_covers_en_segundo_plano`.
    """
    db = SessionLocal()
    try:
        # Aquí sí se busca por id a secas: el ítem lo acaba de crear esta misma
        # petición, así que no hay nada que acotar -- y no hay usuario en
        # sesión, porque esto corre después de que la respuesta se haya ido.
        item = db.get(MediaItem, item_id)
        if item:
            metadata.enrich_item(db, item)
            db.commit()
    except Exception:
        logger.exception("Fallo enriqueciendo el ítem %s en segundo plano", item_id)
    finally:
        db.close()


@router.post("/catalogo/completar-portadas")
def catalog_fill_covers(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(usuario_actual),
):
    # BATCH_SIZE=30 ítems x SLEEP_BETWEEN=0.7s son ya 21s mínimo, y hasta más de
    # 2 minutos con las APIs lentas: hecho dentro de la propia petición HTTP,
    # cualquier proxy inverso delante corta por timeout antes de que termine.
    # Se lanza en segundo plano y se avisa de que ha empezado; el contador
    # "sin portada" de la página ya se recalcula solo en la siguiente carga.
    from ..services.enrich import enrich_missing_covers_en_segundo_plano, reservar_lote

    # reservar_lote y no "consultar y luego marcar": los dos endpoints que
    # lanzan lotes corren en el threadpool, así que comprobar y reservar tiene
    # que ser una sola operación.
    if reservar_lote():
        background_tasks.add_task(
            enrich_missing_covers_en_segundo_plano, SessionLocal, ya_reservado=True
        )
        msg = "Buscando portadas en segundo plano. Vuelve en un minuto y recarga para ver el resultado."
    else:
        msg = "Ya hay una búsqueda de portadas en marcha; espera a que termine."

    # La cabecera Referer la controla el cliente: no se usa tal cual como
    # destino de la redirección (open redirect). Solo se conserva la ruta
    # interna, rechazando "//host" (protocol-relative) además de URLs absolutas.
    referer = request.headers.get("referer") or ""
    path = urlparse(referer).path or "/catalogo"
    destino = path if path.startswith("/") and not path.startswith("//") else "/catalogo"
    return redirect_flash(destino, msg)


@router.get("/buscar")
def search_external(
    tipo: str,
    request: Request,
    q: str = "",
    idioma: str = "es",
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(usuario_actual),
):
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
def suggest_random(
    request: Request,
    tipo: str | None = None,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(usuario_actual),
):
    """Un ítem pendiente al azar (fragmento HTMX para el panel de sugerencia)."""
    query = items_de(db, usuario).filter(MediaItem.status == MediaStatus.PENDIENTE)
    mt = _enum_or_none(MediaType, tipo)
    if mt:
        query = query.filter(MediaItem.media_type == mt)
    item = query.order_by(func.random()).first()
    return templates.TemplateResponse(request, "_suggestion.html", {"item": item, "tipo": tipo})


@router.post("/agregar")
def add_item(
    background_tasks: BackgroundTasks,
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
    usuario: Usuario = Depends(usuario_actual),
    db: Session = Depends(get_db),
):
    item = MediaItem(
        usuario_id=usuario.id,
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

    # Enriquecer (duración/reparto/saga) y, en series, traer episodios: en
    # segundo plano. `metadata.enrich_item` hace peticiones HTTP síncronas con
    # timeout de 10 s, y para una serie de TMDB la cadena es 1 petición de
    # detalles + una POR TEMPORADA. Los Simpson tiene 36 temporadas: hasta 37
    # peticiones secuenciales dentro del POST, con el navegador bloqueado. Y si
    # el proxy corta antes, el usuario ve un error mientras el trabajo sigue
    # por detrás y el commit final no llega a ejecutarse: el ítem se queda
    # creado pero sin episodios.
    background_tasks.add_task(enriquecer_en_segundo_plano, item.id)

    destino = "wishlist" if status == MediaStatus.WISHLIST else "el catálogo"
    # Ya no se puede prometer un número de episodios: todavía no existen. Antes
    # decía "(24 episodios)"; decir "(0 episodios)" sería peor que no decirlo.
    extra = "; trayendo episodios…" if item.is_episodic and external_source else ""
    return redirect_flash("/catalogo", '"%s" añadido a %s%s' % (title, destino, extra))


@router.get("/item/{item_id}")
def item_detail(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(usuario_actual),
):
    item = item_de(db, usuario, item_id)
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
            items_de(db, usuario)
            .filter(or_(*conds), MediaItem.id != item.id)
            .order_by(MediaItem.year)
            .all()
        )
        # La saga se pinta con las mismas tarjetas que el catálogo (MC-X2).
        episodios.precalcular(db, related)

    all_lists = listas_de(db, usuario).filter(Lista.filtro_estado.is_(None)).order_by(Lista.name).all()
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
    usuario: Usuario = Depends(usuario_actual),
    db: Session = Depends(get_db),
):
    item = item_de(db, usuario, item_id)
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

    # Una sola consulta para todas las etiquetas, no una por etiqueta.
    # `Tag.name` es unique, así que cada una era rápida; el problema no era el
    # coste sino que fueran N round-trips.
    tag_names = [t.strip() for t in tags.split(",") if t.strip()]
    existentes = {
        t.name: t for t in db.query(Tag).filter(Tag.name.in_(tag_names)).all()
    } if tag_names else {}
    tag_objs = [existentes.get(n) or Tag(name=n) for n in tag_names]
    db.add_all([t for t in tag_objs if t.id is None])
    item.tags = tag_objs
    db.commit()

    borrar_etiquetas_huerfanas(db)
    return redirect_flash("/item/%d" % item.id, '"%s" actualizado' % item.title)


@router.post("/item/{item_id}/eliminar")
def delete_item(item_id: int, db: Session = Depends(get_db), usuario: Usuario = Depends(usuario_actual)):
    item = item_de(db, usuario, item_id)
    if item:
        db.delete(item)
        db.commit()
    return redirect_flash("/catalogo", "Ítem eliminado", "info")


@router.post("/item/{item_id}/episodio/{ep_id}/toggle")
def toggle_episode(item_id: int, ep_id: int, db: Session = Depends(get_db), usuario: Usuario = Depends(usuario_actual)):
    item = item_de(db, usuario, item_id)
    ep = db.get(Episode, ep_id)
    if item and ep and ep.item_id == item.id:
        metadata.toggle_episode(item, ep)
        item.updated_at = _utcnow()
        db.commit()
    return redirect_flash("/item/%d" % item_id, "Episodio actualizado", "info")


@router.post("/item/{item_id}/marcar-hasta/{ep_id}")
def mark_through_episode(
    item_id: int,
    ep_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(usuario_actual),
):
    item = item_de(db, usuario, item_id)
    ep = db.get(Episode, ep_id)
    if item and ep and ep.item_id == item.id:
        metadata.mark_through(item, ep)
        item.updated_at = _utcnow()
        db.commit()
        return redirect_flash("/item/%d" % item_id, "Marcado hasta %s" % ep.code)
    return redirect_flash("/item/%d" % item_id, "No se pudo marcar", "error")


@router.get("/estadisticas")
def stats(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(usuario_actual)):
    year_now = date.today().year

    total = items_de(db, usuario).count()
    por_estado = dict(
        db.query(MediaItem.status, func.count(MediaItem.id)).group_by(MediaItem.status).all()
    )
    por_tipo = dict(
        db.query(MediaItem.media_type, func.count(MediaItem.id)).group_by(MediaItem.media_type).all()
    )

    completados_este_año = (
        items_de(db, usuario)
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

    # Colección por década (según el año del ítem). Lo agrega SQLite con un
    # GROUP BY, igual que las sumas de tiempo de más arriba: antes se traía una
    # fila por ítem del catálogo entero solo para contarlas en Python.
    # La división entera de SQLite sobre enteros ya trunca, así que year/10*10
    # da la década sin más.
    decada = (MediaItem.year / 10 * 10).label("decada")
    por_decada = sorted(
        (int(d), n) for d, n in
        db.query(decada, func.count(MediaItem.id))
        .filter(MediaItem.year.isnot(None))
        .group_by(decada)
        .all()
    )

    # "Año en cifras": comparación con el año anterior + mejores del año
    completados_prev = (
        items_de(db, usuario)
        .filter(MediaItem.completed_at.isnot(None), extract("year", MediaItem.completed_at) == year_now - 1)
        .count()
    )
    mejores = (
        items_de(db, usuario)
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
