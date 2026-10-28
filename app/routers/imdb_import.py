"""Rutas de importación: los CSV (IMDb, Goodreads/StoryGraph, Letterboxd, Trakt,
Backloggd), la biblioteca de Steam y el lote de portadas para ítems sin cover.

La lógica de cada formato vive en `services/imports.py`; aquí solo se recibe
el fichero, se importa fuera del bucle de eventos y se pinta el resultado."""
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..auth import verify_auth
from ..config import settings
from ..cuentas import items_de, usuario_actual
from ..database import SessionLocal, get_db
from ..flash import redirect_flash
from ..models import MediaItem, Usuario
from ..services import imports, steam
from ..services.enrich import (
    enrich_missing_covers_en_segundo_plano,
    estado_actual,
    reservar_lote,
)
from ..templating import templates

router = APIRouter(tags=["importar-imdb"], dependencies=[Depends(verify_auth)])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


async def _leer_csv_limitado(archivo: UploadFile) -> str:
    """Lee el fichero por trozos con un tope de tamaño, en vez de cargarlo
    entero de una sola vez con `archivo.read()`. Sin límite, un fichero de 1 GB
    (p.ej. arrastrar el equivocado por accidente) consume 2-3 GB de RSS -- el
    tamaño original más la copia decodificada en memoria -- y tumba el proceso
    entero en vez de fallar de forma controlada."""
    trozos = []
    total = 0
    while True:
        trozo = await archivo.read(1024 * 1024)
        if not trozo:
            break
        total += len(trozo)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="El fichero supera el límite de 20 MB")
        trozos.append(trozo)
    return b"".join(trozos).decode("utf-8-sig", errors="ignore")

@router.get("/importar")
def import_form(request: Request, db: Session = Depends(get_db), usuario: Usuario = Depends(usuario_actual)):
    sin_portada = items_de(db, usuario).filter(MediaItem.cover_url.is_(None)).count()
    return templates.TemplateResponse(request, "import.html", {
        "sin_portada": sin_portada,
        # El bloque de Steam solo se ofrece si se puede usar: un botón que
        # siempre falla es peor que una explicación de qué falta.
        "steam_configurado": bool(settings.steam_api_key and settings.steam_id),
    })


def _resultado(request: Request, db: Session, usuario: Usuario, res: dict):
    sin_portada = items_de(db, usuario).filter(MediaItem.cover_url.is_(None)).count()
    return templates.TemplateResponse(request, "import_result.html", {
        "creados": res["creados"], "duplicados": res["duplicados"],
        "actualizados": res.get("actualizados", 0), "omitidos": res.get("omitidos", 0),
        "sin_portada": sin_portada,
    })


async def _importar(request: Request, archivo: UploadFile, db: Session, usuario: Usuario,
                    importador, **opciones):
    """Lo común a todos los CSV: leer con tope, importar y pintar el resultado.

    El importador va al threadpool: son funciones síncronas (SQLAlchemy lo
    es) y se llamaban desde un `async def`, así que un CSV de 5 000 filas
    bloqueaba el bucle de eventos y la app entera dejaba de responder unos
    segundos, a todas las pestañas y cuentas. [R3-M20]
    """
    texto = await _leer_csv_limitado(archivo)
    try:
        res = await run_in_threadpool(importador, db, texto, usuario.id, **opciones)
    except imports.CsvIlegible as e:
        db.rollback()
        return redirect_flash(
            "/importar", "No se ha importado nada: el fichero no se puede leer como CSV (%s)" % e, "error")
    return _resultado(request, db, usuario, res)


@router.post("/importar")
async def import_imdb(
    request: Request,
    archivo: UploadFile = File(...),
    usuario: Usuario = Depends(usuario_actual),
    db: Session = Depends(get_db),
):
    """Importa un export de IMDb ("Your Ratings" o la Watchlist)."""
    return await _importar(request, archivo, db, usuario, imports.import_imdb_csv)


@router.post("/importar/libros")
async def import_books(
    request: Request,
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(usuario_actual),
):
    """Importa libros desde un CSV de Goodreads o StoryGraph."""
    return await _importar(request, archivo, db, usuario, imports.import_books_csv)


@router.post("/importar/juegos")
async def import_games(
    request: Request,
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(usuario_actual),
):
    """Importa juegos desde un CSV de Backloggd o genérico."""
    return await _importar(request, archivo, db, usuario, imports.import_games_csv)


@router.post("/importar/letterboxd")
async def import_letterboxd(
    request: Request,
    archivo: UploadFile = File(...),
    pendientes: str = Form(""),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(usuario_actual),
):
    """Importa películas desde un CSV de Letterboxd."""
    return await _importar(request, archivo, db, usuario, imports.import_letterboxd_csv,
                           pendientes=bool(pendientes))


@router.post("/importar/trakt")
async def import_trakt(
    request: Request,
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(usuario_actual),
):
    """Importa el historial de Trakt: películas y series en el mismo fichero."""
    return await _importar(request, archivo, db, usuario, imports.import_trakt_csv)


@router.post("/importar/steam")
def import_steam(
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(usuario_actual),
):
    """Trae la biblioteca de Steam. No sube nada: la pide a su API."""
    res = steam.importar_biblioteca(
        db, usuario.id, settings.steam_api_key, settings.steam_id
    )
    if res.get("error"):
        return redirect_flash("/importar", res["error"], "error")
    return _resultado(request, db, usuario, res)


@router.post("/importar/completar-portadas")
def fill_covers(
    request: Request,
    background_tasks: BackgroundTasks,
    usuario: Usuario = Depends(usuario_actual),
):
    """Lanza un lote de ítems sin portada y devuelve el fragmento de estado.

    Antes procesaba el lote entero aquí dentro: BATCH_SIZE=30 x
    SLEEP_BETWEEN=0.7s son 21 s mínimo, y más de 2 minutos con las APIs lentas.
    Como es un `def` y no un `async def`, FastAPI lo ejecuta en el threadpool,
    así que `time.sleep` bloqueaba un hilo del pool todo ese rato mientras el
    usuario veía la petición colgada. `/catalogo/completar-portadas` ya lo había
    resuelto; este endpoint, que hace exactamente lo mismo, se quedó fuera.

    Tampoco consultaba el estado, así que se podía lanzar en paralelo con el
    lote del otro endpoint: dos tandas sobre los mismos ítems, duplicando
    peticiones a APIs gratuitas con cuota y arriesgando un 429.

    Pide cuenta y trabaja solo sobre la suya: antes respondía sin pasar por el
    selector y recorría el catálogo de todas. [R3-M5]
    """
    if reservar_lote():
        background_tasks.add_task(
            enrich_missing_covers_en_segundo_plano, SessionLocal,
            ya_reservado=True, usuario_id=usuario.id,
        )
    return templates.TemplateResponse(request, "_enrich_result.html", estado_actual())


@router.get("/importar/estado-portadas")
def covers_status(request: Request, usuario: Usuario = Depends(usuario_actual)):
    """Estado del lote, para que el fragmento se refresque solo."""
    return templates.TemplateResponse(request, "_enrich_result.html", estado_actual())
