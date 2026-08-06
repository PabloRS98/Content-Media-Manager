"""Cabeceras de seguridad HTTP.

La CSP de esta app tiene un compromiso deliberado (`img-src ... https:`) que se
prueba a propósito: sin un test que lo fije, endurecerla parece una mejora
evidente y rompe todas las portadas del catálogo.
"""
import pytest

RUTAS = ["/", "/catalogo", "/listas", "/estadisticas"]


@pytest.mark.parametrize("ruta", RUTAS)
def test_las_paginas_llevan_cabeceras_de_seguridad(client, ruta):
    r = client.get(ruta)
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "same-origin"
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp
    assert "base-uri 'self'" in csp


def test_la_csp_permite_imagenes_https_externas(client):
    """`cover_url` se rellena desde TMDB, Google Books, Open Library, Wikipedia,
    RAWG e iTunes, y además es editable a mano en la ficha. Una lista blanca de
    dominios dejaría sin portada cualquier fuente nueva, así que `img-src` está
    abierto a https: a propósito. Este test existe para que nadie lo cierre sin
    enterarse de lo que rompe."""
    csp = client.get("/").headers["content-security-policy"]
    directiva = next(d for d in csp.split(";") if d.strip().startswith("img-src"))
    assert "https:" in directiva
    assert "data:" in directiva


def test_los_estaticos_tambien_llevan_las_cabeceras(client):
    """El middleware va por fuera de todo, no solo de las vistas HTML."""
    r = client.get("/static/css/style.css")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
