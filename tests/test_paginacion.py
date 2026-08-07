"""Los enlaces de "Anterior"/"Siguiente" tienen que conservar los filtros.

El caso que rompe no es exótico: `Sci-Fi & Fantasy`, `Action & Adventure` y
`War & Politics` son géneros reales de TMDB para series. Con el catálogo
paginando de 24 en 24, basta con tener 25 series de ciencia ficción.
"""
import html as html_mod
import re
from urllib.parse import parse_qs, urlparse

import pytest

from app.models import MediaStatus, MediaType

GENERO_CON_AMPERSAND = "Sci-Fi & Fantasy"


def enlace_siguiente(html: str) -> str | None:
    """Devuelve el href de "Siguiente" tal y como lo vería el navegador.

    Jinja escribe `&amp;` entre parámetros, que es lo correcto dentro de un
    atributo HTML; hay que deshacer ese escapado antes de parsear la query, o
    `parse_qs` ve claves llamadas `amp;pagina`.
    """
    m = re.search(r'href="([^"]*)"[^>]*>\s*Siguiente', html)
    return html_mod.unescape(m.group(1)) if m else None


def enlaces_de_paginacion(html: str) -> list[str]:
    """Los hrefs de dentro del bloque de paginación, y solo esos."""
    bloque = re.search(r'<div class="pagination">(.*?)</div>', html, re.S)
    if not bloque:
        return []
    return [html_mod.unescape(h) for h in re.findall(r'href="([^"]*)"', bloque.group(1))]


def cuenta_tarjetas(html: str) -> int:
    return len(re.findall(r'class="card media-card"', html))


@pytest.fixture
def catalogo_paginado(crear_item):
    """25 series del mismo género: una página completa y una segunda con 1."""
    def _crear(**extra):
        for n in range(25):
            crear_item(
                title="Serie %02d" % n,
                media_type=MediaType.SERIE,
                status=MediaStatus.PENDIENTE,
                genres=GENERO_CON_AMPERSAND,
                **extra,
            )
    return _crear


def test_la_paginacion_conserva_un_genero_con_ampersand(client, catalogo_paginado):
    """El `&` sin codificar cortaba el parámetro: llegaba `genero=Sci-Fi ` y
    aparecía un parámetro fantasma `Fantasy`, así que el filtro se perdía justo
    al pasar de página."""
    catalogo_paginado()

    r = client.get("/catalogo", params={"genero": GENERO_CON_AMPERSAND})
    assert r.status_code == 200
    href = enlace_siguiente(r.text)
    assert href, "no se pintó el enlace de 'Siguiente'"

    # El & del género va codificado, no suelto en la URL.
    assert "Sci-Fi+%26+Fantasy" in href or "Sci-Fi%20%26%20Fantasy" in href
    assert parse_qs(urlparse(href).query)["genero"] == [GENERO_CON_AMPERSAND]

    # Y al seguirlo, el filtro sigue puesto: 25 ítems del género, 24 en la
    # primera página y 1 en la segunda. Antes, al perderse el filtro, la
    # segunda página volvía con el catálogo entero.
    assert cuenta_tarjetas(r.text) == 24
    r2 = client.get(href)
    assert r2.status_code == 200
    assert cuenta_tarjetas(r2.text) == 1
    assert "Página 2 de 2" in r2.text


@pytest.mark.parametrize(
    "filtro, valor",
    [
        ("tipo", "serie"),
        ("estado", "pendiente"),
        ("genero", GENERO_CON_AMPERSAND),
        ("orden", "alfabetico"),
    ],
)
def test_la_paginacion_conserva_todos_los_filtros(client, catalogo_paginado, filtro, valor):
    catalogo_paginado()
    r = client.get("/catalogo", params={filtro: valor})
    href = enlace_siguiente(r.text)
    assert href, "no se pintó el enlace de 'Siguiente'"
    assert parse_qs(urlparse(href).query)[filtro] == [valor]
    assert parse_qs(urlparse(href).query)["pagina"] == ["2"]


def test_los_botones_de_filtro_siguen_volviendo_a_la_pagina_1(client, catalogo_paginado):
    """La otra mitad del comportamiento, y la que es fácil romper al arreglar
    esto: cambiar de filtro invalida la paginación anterior, así que los
    enlaces de los filtros no deben arrastrar `pagina`."""
    catalogo_paginado()
    r = client.get("/catalogo", params={"genero": GENERO_CON_AMPERSAND, "pagina": 2})
    assert r.status_code == 200

    # Todos los enlaces a /catalogo de la página, menos los de la propia
    # paginación: esos son los únicos que deben llevar `pagina`.
    todos = [html_mod.unescape(h) for h in re.findall(r'href="(/catalogo\?[^"]*)"', r.text)]
    de_paginacion = set(enlaces_de_paginacion(r.text))
    assert de_paginacion, "no se pintó el bloque de paginación"

    de_filtro = [h for h in todos if h not in de_paginacion]
    assert de_filtro, "no se encontró ningún enlace de filtro que comprobar"
    for href in de_filtro:
        assert "pagina=" not in href, (
            "el enlace de filtro %s arrastra la página: cambiar de filtro tiene "
            "que devolver a la 1" % href
        )
