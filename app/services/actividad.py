"""El diario: qué hiciste y cuándo, mes a mes. [N5]

Las estadísticas anuales solo tienen sentido en diciembre. Esto es lo mismo
durante el año.

Solo se leen fechas que registran un hecho -- `completed_at`, `started_at` y
`watched_at` de los episodios. `updated_at` NO se usa aunque parezca la más
completa: cambia también al corregir una errata, y un diario que diga que
empezaste un libro el día que le arreglaste el título no vale nada.

Nada de esto son objetos del ORM completos: se piden las columnas que se
pintan, que es lo que evita traerse el catálogo entero para listar treinta
líneas (mismo criterio que MC-M5 en la portada).
"""
import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Episode, MediaItem, Usuario

# Cuántos meses hacia atrás ofrece la navegación antes de dejar de tener
# sentido. No limita lo que se puede pedir a mano por la URL.
FORMATO_MES = "%Y-%m"


@dataclass
class Entrada:
    """Una línea del diario."""

    tipo: str  # "terminado" | "empezado" | "episodios"
    item_id: int
    titulo: str
    media_type: str
    cover_url: str | None
    episodios: int = 0  # solo para tipo "episodios"


@dataclass
class Dia:
    fecha: date
    entradas: list[Entrada] = field(default_factory=list)


def rango_del_mes(mes: str | None) -> tuple[date, date]:
    """(primer día, último día) del mes `AAAA-MM`. Si no se entiende, el actual.

    El parámetro va en la URL y se puede escribir a mano, así que cualquier
    basura cae al mes en curso en vez de reventar la página.
    """
    hoy = date.today()
    primero = hoy.replace(day=1)
    if mes:
        try:
            año, numero = mes.split("-")
            primero = date(int(año), int(numero), 1)
        # OverflowError además de ValueError: `date(9999999999, 1, 1)` no es un
        # valor inválido para Python, es un entero que no cabe en el C long que
        # usa por dentro, y lanza otra excepción distinta.
        except (ValueError, TypeError, OverflowError):
            primero = hoy.replace(day=1)
    ultimo = primero.replace(day=calendar.monthrange(primero.year, primero.month)[1])
    return primero, ultimo


def mes_anterior(primero: date) -> str:
    return (primero - timedelta(days=1)).strftime(FORMATO_MES)


def mes_siguiente(ultimo: date) -> str:
    return (ultimo + timedelta(days=1)).strftime(FORMATO_MES)


def diario(db: Session, usuario: Usuario, desde: date, hasta: date) -> list[Dia]:
    """Los días con actividad del rango, del más reciente al más antiguo."""
    por_dia: dict[date, list[Entrada]] = {}

    def añadir(fecha: date, entrada: Entrada) -> None:
        por_dia.setdefault(fecha, []).append(entrada)

    columnas = (MediaItem.id, MediaItem.title, MediaItem.media_type, MediaItem.cover_url)

    terminados = db.query(MediaItem.completed_at, *columnas).filter(
        MediaItem.usuario_id == usuario.id,
        MediaItem.completed_at.isnot(None),
        MediaItem.completed_at >= desde,
        MediaItem.completed_at <= hasta,
    ).all()
    for fecha, id_item, titulo, tipo, portada in terminados:
        añadir(fecha, Entrada("terminado", id_item, titulo, tipo.value, portada))

    empezados = db.query(MediaItem.started_at, *columnas).filter(
        MediaItem.usuario_id == usuario.id,
        MediaItem.started_at.isnot(None),
        MediaItem.started_at >= desde,
        MediaItem.started_at <= hasta,
        # Lo que se empezó y se terminó el mismo día sale como terminado y ya:
        # dos líneas para el mismo hecho leen peor que una.
        MediaItem.completed_at.is_(None),
    ).all()
    for fecha, id_item, titulo, tipo, portada in empezados:
        añadir(fecha, Entrada("empezado", id_item, titulo, tipo.value, portada))

    # Los episodios se agrupan por (serie, día) en SQL: una tarde de maratón son
    # cinco filas idénticas o una que dice "5 episodios", y la segunda es la que
    # se lee. Los que no tienen `watched_at` --marcados antes de que existiera
    # la columna-- se quedan fuera en vez de amontonarse en un día inventado.
    vistos = db.query(
        Episode.watched_at, *columnas, func.count(Episode.id),
    ).join(MediaItem, Episode.item_id == MediaItem.id).filter(
        MediaItem.usuario_id == usuario.id,
        Episode.watched.is_(True),
        Episode.watched_at.isnot(None),
        Episode.watched_at >= desde,
        Episode.watched_at <= hasta,
    ).group_by(Episode.watched_at, MediaItem.id).all()
    for fecha, id_item, titulo, tipo, portada, cuantos in vistos:
        añadir(fecha, Entrada("episodios", id_item, titulo, tipo.value, portada, cuantos))

    return [Dia(fecha, por_dia[fecha]) for fecha in sorted(por_dia, reverse=True)]


def resumen(dias: list[Dia]) -> dict[str, int]:
    """Los totales del mes que se pintan arriba."""
    return {
        "terminados": sum(1 for d in dias for e in d.entradas if e.tipo == "terminado"),
        "empezados": sum(1 for d in dias for e in d.entradas if e.tipo == "empezado"),
        "episodios": sum(e.episodios for d in dias for e in d.entradas),
    }


def hay_algo_antes(db: Session, usuario: Usuario, desde: date) -> bool:
    """Si no hay nada anterior, el enlace de "mes anterior" no lleva a ningún
    sitio y es mejor no ofrecerlo."""
    primera = db.query(func.min(MediaItem.completed_at)).filter(
        MediaItem.usuario_id == usuario.id
    ).scalar()
    primer_comienzo = db.query(func.min(MediaItem.started_at)).filter(
        MediaItem.usuario_id == usuario.id
    ).scalar()
    primer_episodio = db.query(func.min(Episode.watched_at)).join(
        MediaItem, Episode.item_id == MediaItem.id
    ).filter(MediaItem.usuario_id == usuario.id).scalar()
    fechas = [f for f in (primera, primer_comienzo, primer_episodio) if f]
    return bool(fechas) and min(fechas) < desde
