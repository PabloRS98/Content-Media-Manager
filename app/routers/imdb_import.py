"""Importador del CSV que IMDB permite exportar desde 'Your Ratings' o tu Watchlist,
más el enriquecedor de portadas para ítems sin cover (cruza TMDB/Open Library/RAWG).

No existe una API oficial gratuita de IMDB, así que esta vía de importación puntual
es la forma más simple y 100% local de traer tu historial existente."""
import csv
import io
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from ..auth import verify_auth
from ..database import SessionLocal, get_db
from ..models import MediaItem, MediaStatus, MediaType
from ..services.enrich import (
    enrich_missing_covers_en_segundo_plano,
    estado_actual,
    reservar_lote,
)
from ..services.imports import import_books_csv, import_games_csv
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

# IMDB usa sus propios "Title Type"; los mapeamos a nuestras 4 categorías.
# Mapeados en minúsculas para búsquedas seguras tolerantes a mayúsculas/minúsculas.
TITLE_TYPE_MAP = {
    "movie": MediaType.PELICULA,
    "tvmovie": MediaType.PELICULA,
    "short": MediaType.PELICULA,
    "tvshort": MediaType.PELICULA,
    "tvspecial": MediaType.PELICULA,
    "video": MediaType.PELICULA,
    "película": MediaType.PELICULA,
    "pelicula": MediaType.PELICULA,
    "película de televisión": MediaType.PELICULA,
    "pelicula de television": MediaType.PELICULA,
    "cortometraje": MediaType.PELICULA,
    "cortometraje de televisión": MediaType.PELICULA,
    "cortometraje de television": MediaType.PELICULA,
    "especial de televisión": MediaType.PELICULA,
    "especial de television": MediaType.PELICULA,

    "tvseries": MediaType.SERIE,
    "tvminiseries": MediaType.SERIE,
    "tvpilot": MediaType.SERIE,
    "serie de televisión": MediaType.SERIE,
    "serie de television": MediaType.SERIE,
    "miniserie de televisión": MediaType.SERIE,
    "miniserie de television": MediaType.SERIE,
    "serie": MediaType.SERIE,
    "miniserie": MediaType.SERIE,

    "videogame": MediaType.VIDEOJUEGO,
    "videojuego": MediaType.VIDEOJUEGO,
}


def _get(row: dict, *names: str) -> str:
    """Obtiene un valor de una fila (row) del CSV buscando entre varios nombres alternativos
    de forma case-insensitive."""
    lowered = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
    for n in names:
        v = lowered.get(n.strip().lower())
        # "is not None" deja ganar a una columna PRESENTE pero vacía sobre una
        # alternativa poblada (un CSV bilingüe con "Title" y "Título" descarta
        # filas buenas como "omitidas"). services/imports.py ya usa "if v:".
        if v:
            return v
    return ""


def _parse_optional_int(value: str | None):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _parse_date(value: str | None) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


@router.get("/importar")
def import_form(request: Request, db: Session = Depends(get_db)):
    sin_portada = db.query(MediaItem).filter(MediaItem.cover_url.is_(None)).count()
    return templates.TemplateResponse(request, "import.html", {"sin_portada": sin_portada})


@router.post("/importar")
async def import_imdb_csv(
    request: Request,
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    contenido = await _leer_csv_limitado(archivo)
    reader = csv.DictReader(io.StringIO(contenido))
    fieldnames = reader.fieldnames or []

    # Comprobar de forma case-insensitive si existe columna de valoración
    lowered_fields = {(f or "").strip().lower() for f in fieldnames}
    tiene_rating = any(r in lowered_fields for r in ["your rating", "tu calificación", "tu calificacion", "rating"])

    creados = 0
    omitidos = 0
    duplicados = 0
    # Un solo SELECT con los external_id que ya existen, en vez de uno por fila
    # del CSV. Un export de "Your Ratings" tiene fácilmente 2 000-5 000 filas, y
    # eran 5 000 consultas secuenciales dentro de una sola petición HTTP: la
    # importación tardaba minutos y cualquier proxy inverso delante cortaba por
    # timeout. Es el patrón que services/imports.py (Goodreads, Backloggd) ya
    # usaba con `_existing_keys`; solo el importador de IMDb, el más antiguo y
    # el que recibe los ficheros más grandes, se había quedado con el N+1.
    #
    # El mismo set absorbe los external_id vistos en ESTE fichero: SessionLocal
    # tiene autoflush=False, así que los db.add() de filas anteriores del bucle
    # no estarían en la BD aunque se consultara. Sin eso, un CSV con la misma
    # película repetida (p. ej. en "Ratings" y en "Watchlist") crearía un
    # duplicado por cada repetición en vez de detectarlas entre sí.
    ya_conocidos: set[str] = {
        eid for (eid,) in db.query(MediaItem.external_id)
        .filter(MediaItem.external_id.isnot(None)).all()
    }

    for row in reader:
        # Busca el tipo de título de forma case-insensitive y tolerante a idiomas
        title_type = _get(row, "Title Type", "Tipo de título", "Tipo de titulo", "Type").strip().lower()
        media_type = TITLE_TYPE_MAP.get(title_type)
        
        title = _get(row, "Title", "Título", "Titulo").strip()

        if media_type is None or not title:
            omitidos += 1
            continue

        imdb_id = _get(row, "Const", "ID", "id", "Constante").strip()
        external_id = f"imdb:{imdb_id}" if imdb_id else None

        if external_id:
            if external_id in ya_conocidos:
                duplicados += 1
                continue
            ya_conocidos.add(external_id)

        year = _parse_optional_int(_get(row, "Year", "Año", "Ano"))
        directors = _get(row, "Directors", "Directores", "Director").strip()
        imdb_rating = _get(row, "IMDb Rating", "Calificación de IMDb", "Calificacion de IMDb", "IMDB Rating").strip()
        genres = _get(row, "Genres", "Géneros", "Generos").strip()
        num_votes = _get(row, "Num Votes", "Número de votos", "Numero de votos").strip()
        
        # Obtener valoración del usuario si existe la columna
        your_rating_str = _get(row, "Your Rating", "Tu calificación", "Tu calificacion", "Rating")
        your_rating = _parse_optional_int(your_rating_str) if tiene_rating else None

        # Fecha de valoración
        date_rated_str = _get(row, "Date Rated", "Fecha de calificación", "Fecha de calificacion", "Date")
        fecha = _parse_date(date_rated_str) or datetime.now()

        notas = ["Importado de IMDB."]
        if imdb_rating:
            sufijo_votos = f" ({num_votes} votos)" if num_votes else ""
            notas.append(f"Rating IMDb: {imdb_rating}{sufijo_votos}.")

        db.add(MediaItem(
            media_type=media_type,
            title=title,
            external_id=external_id,
            external_source="imdb",
            year=year,
            creator=directors or None,
            overview="",
            genres=genres or None,
            status=MediaStatus.COMPLETADO if your_rating is not None else MediaStatus.PENDIENTE,
            rating=your_rating,
            notes=" ".join(notas),
            completed_at=fecha.date() if your_rating is not None else None,
            created_at=fecha,
            updated_at=fecha,
        ))
        creados += 1
        # Un flush cada 500 filas para no acumular 5 000 objetos en la sesión
        # antes del único commit final. No abre transacción propia: si algo
        # falla después, el rollback sigue deshaciendo el fichero entero.
        if creados % 500 == 0:
            db.flush()

    db.commit()
    sin_portada = db.query(MediaItem).filter(MediaItem.cover_url.is_(None)).count()
    return templates.TemplateResponse(request, "import_result.html", {
        "creados": creados,
        "omitidos": omitidos,
        "duplicados": duplicados,
        "sin_portada": sin_portada,
    })


@router.post("/importar/libros")
async def import_books(request: Request, archivo: UploadFile = File(...), db: Session = Depends(get_db)):
    """Importa libros desde un CSV de Goodreads o StoryGraph."""
    text = await _leer_csv_limitado(archivo)
    res = import_books_csv(db, text)
    sin_portada = db.query(MediaItem).filter(MediaItem.cover_url.is_(None)).count()
    return templates.TemplateResponse(request, "import_result.html", {**res, "sin_portada": sin_portada})


@router.post("/importar/juegos")
async def import_games(request: Request, archivo: UploadFile = File(...), db: Session = Depends(get_db)):
    """Importa juegos desde un CSV de Backloggd o genérico."""
    text = await _leer_csv_limitado(archivo)
    res = import_games_csv(db, text)
    sin_portada = db.query(MediaItem).filter(MediaItem.cover_url.is_(None)).count()
    return templates.TemplateResponse(request, "import_result.html", {**res, "sin_portada": sin_portada})


@router.post("/importar/completar-portadas")
def fill_covers(request: Request, background_tasks: BackgroundTasks):
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
    """
    if reservar_lote():
        background_tasks.add_task(
            enrich_missing_covers_en_segundo_plano, SessionLocal, ya_reservado=True
        )
    return templates.TemplateResponse(request, "_enrich_result.html", estado_actual())


@router.get("/importar/estado-portadas")
def covers_status(request: Request):
    """Estado del lote, para que el fragmento se refresque solo."""
    return templates.TemplateResponse(request, "_enrich_result.html", estado_actual())
