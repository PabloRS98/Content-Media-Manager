"""Excepciones propias que la app traduce a una respuesta concreta."""


class SinCuenta(Exception):
    """No hay ninguna cuenta abierta en esta sesión.

    La lanza la dependencia `usuario_actual` y la recoge un manejador en
    `main.py`, que redirige al selector de cuentas. Es una excepción y no un
    `None` a propósito: así una vista no puede olvidarse de comprobarlo y
    acabar sirviendo el catálogo de nadie -- o peor, el de todos.
    """
