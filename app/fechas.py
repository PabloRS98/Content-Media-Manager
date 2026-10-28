"""Nombres de días y meses en español, sin depender del locale.

`strftime("%A")` devuelve lo que diga el locale del proceso, y el del contenedor
es el de por defecto: "Thursday". La app está entera en español, así que el
calendario y el diario ponían el único texto en inglés de toda la interfaz.

Fijar el locale no vale: `es_ES.UTF-8` no está generado en la imagen slim, y
`setlocale` es global al proceso -- cambiarlo afectaría también a cómo se
formatean los números en cualquier otro sitio.

Y qué día es en casa [R3-M11]: el contenedor va en UTC, pero la cabecera, el
saludo y el planificador usan `TIMEZONE`. Con `date.today()` repartido por el
código, un episodio marcado a las 00:30 en Madrid se guardaba con la fecha del
día anterior. `hoy()` y `ahora()` son la única fuente.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import settings

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def nombre_del_mes(fecha: date) -> str:
    return MESES[fecha.month - 1]


def dia_y_mes(fecha: date) -> str:
    """'jueves 6 de agosto'. Para agrupar por días dentro de un mes."""
    return "%s %d de %s" % (DIAS[fecha.weekday()], fecha.day, nombre_del_mes(fecha))


def fecha_larga(fecha: date) -> str:
    """'jueves 6 de agosto de 2026'. Cuando el año no se sobreentiende."""
    return "%s de %d" % (dia_y_mes(fecha), fecha.year)


def mes_y_año(fecha: date) -> str:
    return "%s de %d" % (nombre_del_mes(fecha), fecha.year)


def mes_corto(fecha: date) -> str:
    """'ago', 'sep'... Para las fechas apiladas de la portada; `%b` daría "Aug"."""
    return nombre_del_mes(fecha)[:3]


def _zona() -> ZoneInfo | None:
    try:
        return ZoneInfo(settings.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def ahora() -> datetime:
    """La hora local de la casa, con zona (la del sistema si `TIMEZONE` no vale)."""
    zona = _zona()
    return datetime.now(zona) if zona else datetime.now()


def hoy() -> date:
    return ahora().date()
