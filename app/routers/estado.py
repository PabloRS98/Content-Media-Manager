"""Panel de diagnóstico: qué está configurado, qué corrió y qué falta.

Existe para convertir "no me llegan los avisos de episodios" en un diagnóstico
de un vistazo. Ese síntoma puede ser: TMDB caída, `TMDB_API_KEY` inválida,
token de Telegram revocado, `chat_id` mal, el job caído, o simplemente que no
hay episodios nuevos. Sin panel, distinguirlos exige leer los logs del
contenedor.

Nunca muestra el VALOR de una credencial, solo si está puesta: la página va
detrás de la misma autenticación que el resto, pero un panel de estado es
justo lo que uno acaba enseñando en una captura para pedir ayuda.
"""
import os

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..auth import verify_auth
from ..config import settings
from ..cuentas import items_de, usuario_actual
from ..database import get_db, revision_pendiente
from ..models import EPISODIC_TYPES, Episode, MediaItem, Usuario
from ..services import telegram
from ..services.scheduler import JOB_STATUS
from ..templating import templates

router = APIRouter(tags=["estado"], dependencies=[Depends(verify_auth)])

# id del job -> nombre legible y para qué sirve.
JOBS = {
    "media_alerts": ("Avisos", "Episodios nuevos y estrenos de la wishlist (09:00)"),
    "daily_backup": ("Backup", "Copia diaria de la base de datos (04:45)"),
}


def _fuentes_de_metadatos() -> list[dict]:
    """Presencia, no valor. Open Library e iTunes no llevan clave: van como
    disponibles siempre, que es la verdad y evita que parezcan rotas."""
    return [
        {"nombre": "TMDB", "para": "Películas, series y episodios",
         "configurada": bool(settings.tmdb_api_key), "necesita_clave": True},
        {"nombre": "RAWG", "para": "Videojuegos",
         "configurada": bool(settings.rawg_api_key), "necesita_clave": True},
        {"nombre": "Google Books", "para": "Libros (respaldo)",
         "configurada": bool(settings.google_books_api_key), "necesita_clave": True},
        {"nombre": "Open Library", "para": "Libros",
         "configurada": True, "necesita_clave": False},
        {"nombre": "iTunes", "para": "Podcasts",
         "configurada": True, "necesita_clave": False},
    ]


def _ultimo_backup() -> dict | None:
    """Fecha y tamaño del backup más reciente, leídos del disco.

    Del disco y no de `JOB_STATUS` a propósito: el job solo deja rastro desde
    que el proceso arrancó, y lo que interesa saber es si hay una copia
    reciente, la hiciera quien la hiciera."""
    carpeta = os.path.join(os.path.dirname(settings.db_path), "backups")
    if not os.path.isdir(carpeta):
        return None
    copias = [f for f in os.listdir(carpeta) if f.startswith("media-") and f.endswith(".db")]
    if not copias:
        return None
    ultima = max(copias)
    ruta = os.path.join(carpeta, ultima)
    return {
        "nombre": ultima,
        "mb": round(os.path.getsize(ruta) / (1024 * 1024), 2),
        "total": len(copias),
    }


def _huecos_en_los_datos(db: Session, usuario: Usuario) -> list[dict]:
    """Lo que la app no puede completar sola y explica cosas que se ven raras.

    Solo del catálogo de quien mira. Aunque sean números y no títulos, decir
    "hay 40 ítems sin portada" contando los de otra persona es contar algo que
    no es suyo -- y le haría pulsar un botón que no le corresponde."""
    mios = items_de(db, usuario)

    sin_portada = mios.filter(MediaItem.cover_url.is_(None)).count()

    series_sin_episodios = mios.filter(
        MediaItem.media_type.in_(EPISODIC_TYPES),
        ~MediaItem.id.in_(db.query(Episode.item_id).distinct()),
    ).count()

    # Un ítem con fuente declarada pero sin id no se puede enriquecer nunca:
    # metadata.enrich_item lo ignora en silencio.
    fuente_sin_id = mios.filter(
        MediaItem.external_source.isnot(None),
        MediaItem.external_id.is_(None),
    ).count()

    return [
        {"que": "Ítems sin portada", "cuantos": sin_portada,
         "arreglo": "Usa «Completar portadas» en Importar"},
        {"que": "Series o podcasts sin episodios", "cuantos": series_sin_episodios,
         "arreglo": "Requiere TMDB o el feed del podcast"},
        {"que": "Ítems con fuente pero sin identificador", "cuantos": fuente_sin_id,
         "arreglo": "No se pueden enriquecer; edítalos y vuelve a buscarlos"},
    ]


@router.get("/estado")
def estado(request: Request, db: Session = Depends(get_db),
           usuario: Usuario = Depends(usuario_actual)):
    jobs = []
    for job_id, (nombre, descripcion) in JOBS.items():
        ultima = JOB_STATUS.get(job_id)
        jobs.append({
            "id": job_id, "nombre": nombre, "descripcion": descripcion,
            "ultima": ultima,
        })

    try:
        revision_actual, revision_head = revision_pendiente(db.get_bind())
    except Exception:
        revision_actual, revision_head = None, None

    return templates.TemplateResponse(request, "estado.html", {
        "jobs": jobs,
        "scheduler_activo": settings.enable_scheduler,
        "fuentes": _fuentes_de_metadatos(),
        "telegram_configurado": telegram.is_configured(),
        "autenticacion": settings.enable_auth,
        "backup": _ultimo_backup(),
        "huecos": _huecos_en_los_datos(db, usuario),
        "revision_actual": revision_actual,
        "revision_head": revision_head,
    })
