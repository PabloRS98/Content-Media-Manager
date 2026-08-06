"""Punto de entrada: Catálogo de Libros, Películas, Series y Videojuegos."""
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import ENV_FILE, settings
from .csrf import CSRFProtectionMiddleware
from .database import SessionLocal, init_db
from .routers import catalog, home, imdb_import, lists

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_GENRE_NOTES_RE = re.compile(r"Genero:\s*([^.]+)\.")


def avisar_si_no_hay_autenticacion(enable_auth: bool) -> None:
    """Deja constancia en el log de que la app queda sin credenciales.

    Arrancar sin autenticación puede ser deliberado (localhost), pero también es
    lo que pasa cuando el `.env` no se lee: `ENABLE_AUTH` vuelve a su valor por
    defecto (false) sin que nada falle. Los síntomas visibles de ese caso son
    otros --"no encuentra películas", "no llegan los avisos"--, así que sin este
    aviso la causa real no aparece por ninguna parte.
    """
    if not enable_auth:
        logger.warning(
            "ENABLE_AUTH está desactivado: la aplicación no pide credenciales. "
            "Si no era la intención, comprueba que se está leyendo el .env (%s).",
            ENV_FILE,
        )


def backfill_v2_columns() -> None:
    """Rellena (una sola vez, idempotente) las columnas nuevas en BDs que ya tenían datos:
    - completed_at: aproximado con updated_at para ítems ya completados.
    - genres: extraído de las notas de importaciones antiguas de IMDB ("Genero: X, Y.")."""
    from .models import MediaItem, MediaStatus

    db = SessionLocal()
    try:
        changed = 0
        for item in db.query(MediaItem).filter(
            MediaItem.status == MediaStatus.COMPLETADO, MediaItem.completed_at.is_(None)
        ).all():
            item.completed_at = item.updated_at.date() if item.updated_at else None
            changed += 1
        for item in db.query(MediaItem).filter(
            MediaItem.genres.is_(None), MediaItem.notes.like("%Genero:%")
        ).all():
            match = _GENRE_NOTES_RE.search(item.notes)
            if match:
                item.genres = match.group(1).strip()
                # Limpia la nota para no duplicar información
                item.notes = _GENRE_NOTES_RE.sub("", item.notes).strip()
                changed += 1
        if changed:
            db.commit()
            logger.info("Backfill de columnas v2: %d ítems actualizados", changed)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    avisar_si_no_hay_autenticacion(settings.enable_auth)
    init_db()
    try:
        backfill_v2_columns()
    except Exception:
        logger.exception("Fallo en el backfill de columnas v2")
    try:
        db = SessionLocal()
        try:
            lists.seed_smart_lists(db)
        finally:
            db.close()
    except Exception:
        logger.exception("Fallo al sembrar las listas automáticas")

    app.state.scheduler = None
    if settings.enable_scheduler:
        from .services.scheduler import start_scheduler
        app.state.scheduler = start_scheduler()
    yield
    if app.state.scheduler:
        app.state.scheduler.shutdown(wait=False)


app = FastAPI(title="Catálogo de Medios", lifespan=lifespan)

app.add_middleware(CSRFProtectionMiddleware)

app.include_router(home.router)
app.include_router(catalog.router)
app.include_router(lists.router)
app.include_router(imdb_import.router)

# `.resolve()` igual que en templating.py: la ruta no depende del cwd y aguanta
# que el proyecto esté detrás de un enlace simbólico.
app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")


@app.get("/salud")
def health():
    return {"status": "ok"}
