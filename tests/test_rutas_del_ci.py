"""El script de rutas del CI tiene que seguir cubriendo la app.

`comprobar-rutas.sh` existe para cazar lo que ningún test unitario ve: una
columna que falte en el esquema deja el proceso vivo y respondiendo, pero
devuelve 500 en cada vista. Solo se detecta pidiendo las páginas.

La regla escrita en el script ("cuando se añada una ruta GET nueva, se añade
aquí") depende de que alguien se acuerde. Esto la comprueba.
"""
from pathlib import Path

import pytest

from app.main import app

RAIZ = Path(__file__).resolve().parent.parent
SCRIPT = RAIZ / ".github" / "comprobar-rutas.sh"

# Rutas GET sin parámetros de ruta que NO van en el script, con su motivo.
FUERA_A_PROPOSITO = {
    # Exige el parámetro `tipo` y sale a las APIs externas: en el CI no hay
    # claves, y pedirla comprobaría la red de terceros, no esta app.
    "/buscar",
}


def rutas_get_sin_parametros() -> set[str]:
    """Del esquema OpenAPI y no de `app.routes`: desde FastAPI 0.141 las rutas
    de los routers incluidos quedan anidadas dentro de objetos
    `_IncludedRouter` y no aparecen sueltas en esa lista."""
    esquema = app.openapi()
    return {
        camino for camino, metodos in esquema["paths"].items()
        if "get" in metodos and "{" not in camino
    }


@pytest.fixture(scope="module")
def script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_el_script_existe_y_es_ejecutable():
    assert SCRIPT.exists()
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")


def test_el_script_cubre_todas_las_rutas_get(script):
    listadas = {
        linea.strip().split("?")[0]
        for linea in script.splitlines()
        if linea.strip().startswith("/")
    }
    sin_cubrir = rutas_get_sin_parametros() - listadas - FUERA_A_PROPOSITO
    assert sin_cubrir == set(), (
        "rutas GET nuevas que no están en comprobar-rutas.sh: %s" % sorted(sin_cubrir)
    )


def test_el_script_no_lista_rutas_que_ya_no_existen(script):
    listadas = {
        linea.strip().split("?")[0]
        for linea in script.splitlines()
        if linea.strip().startswith("/")
    }
    # `/static` lo sirve un mount, no una ruta.
    fantasmas = listadas - rutas_get_sin_parametros() - {"/static"}
    assert fantasmas == set(), "el script pide rutas que no existen: %s" % sorted(fantasmas)


@pytest.mark.parametrize("ruta", sorted(rutas_get_sin_parametros() - FUERA_A_PROPOSITO))
def test_todas_las_rutas_del_script_responden_200(client, ruta):
    """Lo mismo que hace el CI contra el contenedor, pero contra la app."""
    assert client.get(ruta).status_code == 200, ruta


def test_esperar_salud_existe():
    esperar = RAIZ / ".github" / "esperar-salud.sh"
    assert esperar.exists()
    # Mira el healthcheck de Docker, no solo el código HTTP: es lo que de
    # verdad se quiere probar desde que /salud puede devolver 503.
    assert "State.Health.Status" in esperar.read_text(encoding="utf-8")
