"""Leer números que vienen de fuera: formularios y CSV. [R3-B1, R3-B2]

`float()` acepta "inf", "nan" y "1e999", e `int()` cualquier cantidad de
dígitos. Un progreso infinito o un año de treinta cifras se guardaban sin más,
y a partir de ahí la portada, el catálogo, la ficha y las estadísticas daban
500 cada vez que lo pintaban: no un error al guardar, sino una página rota de
forma persistente hasta editar la base a mano.

Aquí está la única forma de leerlos: número finito y dentro de un rango que
tenga sentido para el campo, o `None`. Lo que no cabe se descarta en lugar de
recortarse, porque un año 30000 no es "casi 9999", es un dato malo.
"""
from math import isfinite

# Rangos por campo. Holgados a propósito: solo tienen que dejar fuera lo
# absurdo, no decidir qué es razonable.
AÑO = (1, 9999)
PAGINAS = (1, 100_000)
MINUTOS = (1, 100_000)
HORAS = (0, 100_000)
PROGRESO = (0, 1_000_000_000)
NOTA = (1, 10)


def decimal(valor, rango: tuple[float, float] | None = None) -> float | None:
    """Número finito (acepta coma decimal) o None."""
    if valor is None:
        return None
    if not isinstance(valor, int | float):
        texto = str(valor).strip().replace(",", ".")
        if not texto or len(texto) > 32:
            return None
        try:
            valor = float(texto)
        except ValueError:
            return None
    try:
        valor = float(valor)
    except OverflowError:  # un int de mil cifras
        return None
    if not isfinite(valor):
        return None
    if rango and not (rango[0] <= valor <= rango[1]):
        return None
    return valor


def entero(valor, rango: tuple[int, int] | None = None) -> int | None:
    """Entero (trunca "300.7" a 300, como siempre hicieron los importadores) o None."""
    d = decimal(valor)
    if d is None:
        return None
    n = int(d)
    if rango and not (rango[0] <= n <= rango[1]):
        return None
    return n


def nota(valor) -> int | None:
    """La nota es 1-10. El min/max del HTML es solo del lado del cliente: sin
    esto, un rating de 99 se guardaba y desaparecía silenciosamente del
    histograma de /estadisticas (que sí filtra 1 <= rating <= 10)."""
    return entero(valor, NOTA)
