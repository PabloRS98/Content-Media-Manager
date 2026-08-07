"""Punto de entrada: Catálogo de Libros, Películas, Series y Videojuegos."""
import logging
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .config import ENV_FILE, settings
from .csrf import CSRFProtectionMiddleware
from .database import (
    CLAVE_BACKFILL_V2,
    SessionLocal,
    escribir_meta,
    get_db,
    leer_meta,
    revision_pendiente,
)
from .routers import catalog, estado, home, imdb_import, lists

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

    Se dice si el fichero existe o no, que es el dato que hace falta para
    distinguir los dos casos. Nombrar la ruta a secas manda a mirar donde
    normalmente no hay nada: en Docker la configuración llega como variables de
    entorno desde el `env_file` del compose, y `/app/.env` no existe dentro del
    contenedor porque el Dockerfile no lo copia.
    """
    if not enable_auth:
        logger.warning(
            "ENABLE_AUTH está desactivado: la aplicación no pide credenciales. "
            "Si no era la intención, revisa la configuración: %s %s, y en Docker "
            "los valores llegan por el env_file del compose, no de ese fichero.",
            ENV_FILE,
            "existe" if ENV_FILE.exists() else "NO existe",
        )


def backfill_v2_columns() -> None:
    """Rellena las columnas nuevas en BDs que ya tenían datos, UNA sola vez:
    - completed_at: aproximado con updated_at para ítems ya completados.
    - genres: extraído de las notas de importaciones antiguas de IMDB ("Genero: X, Y.").

    Antes decía "una sola vez" pero nada lo garantizaba: lo idempotente era el
    resultado, no la ejecución, y corría entera en cada arranque. Sus dos
    consultas son caras --la segunda hace `LIKE '%Genero:%'` sobre una columna
    Text, que no puede usar índice ni aunque existiera porque el comodín va
    delante--, así que con un catálogo de 5 000 ítems importados eran 10 000
    filas leídas y 5 000 búsquedas de subcadena en cada `docker compose
    restart`, antes de que el healthcheck pudiera responder siquiera.

    Ahora se marca en `app_meta` al terminar. Es una migración puntual de la v1
    a la v2, no una regla permanente: una base ya marcada no se vuelve a tocar.
    """
    from .models import MediaItem, MediaStatus

    if leer_meta(CLAVE_BACKFILL_V2):
        return

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

    # Solo si llegó hasta aquí: si el backfill revienta a mitad, la excepción
    # sube y la marca no se escribe, así que el arranque siguiente lo reintenta.
    escribir_meta(CLAVE_BACKFILL_V2, datetime.now(UTC).isoformat(timespec="seconds"))


def avisar_si_el_esquema_esta_desactualizado() -> None:
    """Comprueba la revisión del esquema, sin aplicarla.

    Las migraciones NO se aplican aquí: las corre el entrypoint del contenedor
    antes de levantar uvicorn. Ejecutar `alembic upgrade` dentro del lifespan
    puede quedarse esperando un lock de SQLite y dejar el arranque colgado sin
    decir por qué; en el entrypoint no hay servidor ni scheduler tocando la
    base todavía. Aquí solo se lee `alembic_version`, que no bloquea.
    """
    try:
        actual, head = revision_pendiente()
    except Exception:
        logger.exception("No se pudo comprobar la revisión del esquema")
        return
    if actual == head:
        return
    logger.error(
        "La base de datos está en la revisión %s y el código espera %s. "
        "Arranca con el entrypoint del contenedor o ejecuta 'alembic upgrade head'; "
        "hasta entonces habrá errores de columna inexistente.",
        actual or "sin marcar", head,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    avisar_si_no_hay_autenticacion(settings.enable_auth)
    avisar_si_el_esquema_esta_desactualizado()
    # Los dos bloques de abajo capturan cualquier excepción y siguen a
    # propósito: son trabajos de puesta a punto, y ninguno debe impedir que la
    # app arranque y sirva el catálogo. Lo que sí hacen es dejar constancia
    # distinguible, porque las consecuencias no son iguales:
    #
    # - Si el backfill falla, la marca no se escribe y se reintenta al
    #   arrancar de nuevo (ver `backfill_v2_columns`).
    # - Si el sembrado falla, la app se queda sin las listas automáticas. La
    #   portada lo tolera desde MC-M16 (pinta los números sin enlace en vez de
    #   enlazar a ninguna parte), pero el usuario pierde esas cuatro vistas.
    try:
        backfill_v2_columns()
    except Exception:
        logger.exception("Fallo en el backfill de columnas v2: se reintentará al arrancar")
    try:
        db = SessionLocal()
        try:
            lists.seed_smart_lists(db)
        finally:
            db.close()
    except Exception:
        logger.exception(
            "Fallo al sembrar las listas automáticas: la app arranca sin ellas y "
            "los accesos de la portada se quedan sin enlace"
        )

    app.state.scheduler = None
    if settings.enable_scheduler:
        from .services.scheduler import start_scheduler
        app.state.scheduler = start_scheduler()
    yield
    if app.state.scheduler:
        app.state.scheduler.shutdown(wait=False)


app = FastAPI(title="Catálogo de Medios", lifespan=lifespan)

app.add_middleware(CSRFProtectionMiddleware)


@app.middleware("http")
async def cabeceras_de_seguridad(request: Request, call_next):
    """Cabeceras que el navegador aplica aunque la app se equivoque en algo.

    Se registra después del middleware CSRF a propósito: en Starlette el último
    middleware añadido es el más externo, así que estas cabeceras salen también
    en las respuestas que el CSRF rechaza y en los estáticos.

    `setdefault` y no asignación directa: si algún día una vista necesita su
    propia política (un `frame-ancestors` distinto para un embebido, por
    ejemplo), la suya gana y esto solo rellena lo que falte.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        # img-src abierto a https: es un compromiso consciente. Las portadas
        # vienen de TMDB, Google Books, Open Library, Wikipedia, RAWG e iTunes,
        # y `cover_url` es además editable a mano en la ficha del ítem, así que
        # una lista blanca de dominios dejaría sin portada cualquier fuente
        # nueva. Lo que sí cierra: `default-src 'self'` evita que una portada
        # con esquema raro sirva de canal para otra cosa, y `frame-ancestors`
        # impide embeber la app (que aquí pesa más que en las apps hermanas,
        # porque la protección CSRF de este proyecto falla abierta -- MC-A6).
        "default-src 'self'; img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    return response

app.include_router(home.router)
app.include_router(catalog.router)
app.include_router(lists.router)
app.include_router(imdb_import.router)
app.include_router(estado.router)

# `.resolve()` igual que en templating.py: la ruta no depende del cwd y aguanta
# que el proyecto esté detrás de un enlace simbólico.
app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")


def _problemas_para_servir(db: Session) -> list[str]:
    """Lo que impediría a la app pintar páginas, comprobado de verdad.

    No basta con "el proceso responde": el fallo que este healthcheck existe
    para cazar es un esquema desactualizado, que deja la app viva devolviendo
    500 en todas las vistas. Por eso también se consulta un `MediaItem` con el
    ORM, que emite un SELECT con todas las columnas mapeadas y revienta igual
    que reventarían las páginas.
    """
    problemas = []
    try:
        actual, head = revision_pendiente(db.get_bind())
        if actual != head:
            problemas.append("esquema desactualizado")
    except Exception:
        logger.exception("Healthcheck: no se pudo leer la revisión del esquema")
        problemas.append("esquema ilegible")

    try:
        from .models import MediaItem
        db.query(MediaItem).limit(1).all()
    except Exception:
        logger.exception("Healthcheck: la consulta de prueba falló")
        problemas.append("consulta de prueba fallida")
    return problemas


@app.get("/salud")
def health(response: Response, db: Session = Depends(get_db)):
    """Healthcheck del contenedor: sin auth a propósito (Docker no manda credenciales).

    Devuelve 503 si la app no puede servir páginas, para que Docker marque el
    contenedor como `unhealthy` en vez de dar por bueno un proceso vivo que
    responde 500 a todo. El detalle va al log, no a la respuesta: esta ruta no
    pide credenciales.
    """
    problemas = _problemas_para_servir(db)
    if problemas:
        response.status_code = 503
        return {"status": "degradado", "problemas": problemas}
    return {"status": "ok"}
