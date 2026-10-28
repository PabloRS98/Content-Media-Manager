"""Freno a probar contraseñas a lo bruto. [R3-M3]

Sin esto, quien llegara a la red podía probar contraseñas de una cuenta sin
límite: unos 60 ms de scrypt por intento y ningún bloqueo, así que una
contraseña corta caía en minutos -- y cada intento ocupaba además un hilo del
servidor haciendo scrypt.

Los primeros fallos son gratis (la gente se equivoca escribiendo); a partir
de ahí cada fallo dobla la espera, hasta un máximo. Mientras dura la espera ni
siquiera se comprueba la contraseña, así que tampoco cuesta CPU. Un acierto lo
pone todo a cero.

Vive en memoria a propósito: es un servidor de casa con un solo proceso, y que
un reinicio lo olvide no da ventaja real (reiniciar el contenedor no está al
alcance de quien está probando contraseñas desde fuera).
"""
import threading
import time

FALLOS_LIBRES = 5
ESPERA_INICIAL = 2      # segundos tras el primer fallo de más
ESPERA_MAXIMA = 15 * 60
# Tope de claves recordadas, para que llenar el diccionario con IPs inventadas
# no sea una forma de gastar memoria.
MAX_CLAVES = 10_000


class Limitador:
    def __init__(self, reloj=time.monotonic):
        self._reloj = reloj
        self._fallos: dict[tuple, tuple[int, float]] = {}
        self._candado = threading.Lock()

    def espera(self, clave: tuple) -> int:
        """Segundos que faltan para poder volver a intentarlo (0 = adelante)."""
        with self._candado:
            fallos = self._fallos.get(clave)
            if not fallos:
                return 0
            restante = fallos[1] - self._reloj()
            return max(0, int(restante + 0.999))

    def fallo(self, clave: tuple) -> None:
        with self._candado:
            if len(self._fallos) >= MAX_CLAVES and clave not in self._fallos:
                self._purgar()
            cuantos = self._fallos.get(clave, (0, 0.0))[0] + 1
            de_mas = cuantos - FALLOS_LIBRES
            espera = 0 if de_mas <= 0 else min(ESPERA_MAXIMA, ESPERA_INICIAL * 2 ** (de_mas - 1))
            self._fallos[clave] = (cuantos, self._reloj() + espera)

    def acierto(self, clave: tuple) -> None:
        with self._candado:
            self._fallos.pop(clave, None)

    def _purgar(self) -> None:
        """Olvida las claves cuya espera ya pasó; si no basta, las más viejas."""
        ahora = self._reloj()
        for clave in [c for c, (_, hasta) in self._fallos.items() if hasta <= ahora]:
            del self._fallos[clave]
        if len(self._fallos) >= MAX_CLAVES:
            for clave in sorted(self._fallos, key=lambda c: self._fallos[c][1])[: MAX_CLAVES // 2]:
                del self._fallos[clave]


# Uno para las cuentas y otro para HTTP Basic: son puertas distintas.
cuentas = Limitador()
basic = Limitador()


def ip_de(request) -> str:
    return request.client.host if request.client else "?"
