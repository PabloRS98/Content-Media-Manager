"""Nombres de días y meses en español, sin depender del locale.

`strftime("%A")` devuelve lo que diga el locale del proceso, y el del contenedor
es el de por defecto: "Thursday". La app está entera en español, así que el
calendario y el diario ponían el único texto en inglés de toda la interfaz.

Fijar el locale no vale: `es_ES.UTF-8` no está generado en la imagen slim, y
`setlocale` es global al proceso -- cambiarlo afectaría también a cómo se
formatean los números en cualquier otro sitio.
"""
from datetime import date

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
