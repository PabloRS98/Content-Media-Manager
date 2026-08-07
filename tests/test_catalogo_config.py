"""Los buckets de duración: rango y etiqueta en el mismo sitio.

Estaban duplicados en dos bloques de `list_catalog` separados por 40 líneas,
uno para filtrar y otro para pintar el desplegable. Cambiar "< 150 págs" por
"< 200 págs" exigía tocar los dos y nada garantizaba que se hiciera.
"""
import ast
from pathlib import Path

import pytest

from app.catalogo_config import (
    BUCKETS_DURACION,
    condicion_de_duracion,
    etiquetas_de_duracion,
)
from app.models import MediaStatus, MediaType

RAIZ = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("media_type", list(BUCKETS_DURACION))
def test_los_buckets_de_duracion_tienen_etiqueta(media_type):
    _, buckets = BUCKETS_DURACION[media_type]
    assert buckets
    for entrada in buckets:
        clave, etiqueta, condicion = entrada
        assert clave and etiqueta
        assert callable(condicion)


@pytest.mark.parametrize("media_type", list(BUCKETS_DURACION))
def test_las_claves_no_se_repiten(media_type):
    claves = [c for c, _, _ in BUCKETS_DURACION[media_type][1]]
    assert len(claves) == len(set(claves))


@pytest.mark.parametrize("media_type", list(BUCKETS_DURACION))
def test_las_etiquetas_del_desplegable_salen_de_los_buckets(media_type):
    """Es el punto entero del cambio: una sola fuente."""
    del_desplegable = etiquetas_de_duracion(media_type)
    de_la_tabla = [(c, e) for c, e, _ in BUCKETS_DURACION[media_type][1]]
    assert del_desplegable == de_la_tabla


def test_un_tipo_sin_buckets_no_ofrece_duraciones():
    assert etiquetas_de_duracion(None) == []


def test_una_clave_inventada_no_filtra_nada():
    """`?tiempo=loquesea` no puede reventar la página."""
    assert condicion_de_duracion(MediaType.LIBRO, "inexistente", None) is None
    assert condicion_de_duracion(None, "corto", None) is None


class TestElFiltroSigueFiltrandoIgual:
    """Los límites originales mezclan `<` con `<=` de forma no uniforme. Estos
    tests fijan los bordes exactos para que la extracción no los cambie."""

    @pytest.mark.parametrize("paginas, bucket", [
        (149, "corto"), (150, "medio"), (300, "medio"),
        (301, "largo"), (500, "largo"), (501, "muy_largo"),
    ])
    def test_los_bordes_de_los_libros(self, client, crear_item, paginas, bucket):
        crear_item(title="Libro de %d págs" % paginas, media_type=MediaType.LIBRO,
                   page_count=paginas, status=MediaStatus.PENDIENTE)
        html = client.get("/catalogo?tipo=libro&tiempo=%s" % bucket).text
        assert "Libro de %d págs" % paginas in html

    @pytest.mark.parametrize("horas, bucket", [
        (9.5, "corto"), (10.0, "medio"), (30.0, "medio"),
        (30.5, "largo"), (60.0, "largo"), (60.5, "muy_largo"),
    ])
    def test_los_bordes_de_los_juegos_con_decimales(self, client, crear_item, horas, bucket):
        """`hltb_hours` es FLOAT: reescribir los rangos como enteros cerrados
        habría cambiado en silencio dónde cae un juego de 10,5 horas."""
        crear_item(title="Juego de %s h" % horas, media_type=MediaType.VIDEOJUEGO,
                   hltb_hours=horas, status=MediaStatus.PENDIENTE)
        html = client.get("/catalogo?tipo=videojuego&tiempo=%s" % bucket).text
        assert "Juego de %s h" % horas in html

    def test_las_series_se_miden_en_episodios(self, client, crear_serie):
        crear_serie(temporadas=1, por_temporada=5, title="Serie corta")
        crear_serie(temporadas=4, por_temporada=10, title="Serie larga")

        corto = client.get("/catalogo?tipo=serie&tiempo=corto").text
        assert "Serie corta" in corto and "Serie larga" not in corto

        largo = client.get("/catalogo?tipo=serie&tiempo=largo").text
        assert "Serie larga" in largo and "Serie corta" not in largo


def test_list_catalog_mide_menos_de_60_lineas():
    """Criterio de aceptación del informe. Era de 210."""
    fuente = (RAIZ / "app" / "routers" / "catalog.py").read_text(encoding="utf-8")
    for nodo in ast.walk(ast.parse(fuente)):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == "list_catalog":
            lineas = nodo.end_lineno - nodo.lineno + 1
            assert lineas <= 60, "list_catalog tiene %d líneas" % lineas
            return
    pytest.fail("no se encontró list_catalog")


def test_no_quedan_imports_dentro_de_list_catalog():
    """Había un `from sqlalchemy import func` y un `from ..models import
    Episode` en mitad de la función, ya importados arriba (MC-B3)."""
    fuente = (RAIZ / "app" / "routers" / "catalog.py").read_text(encoding="utf-8")
    for nodo in ast.walk(ast.parse(fuente)):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == "list_catalog":
            internos = [n for n in ast.walk(nodo) if isinstance(n, ast.Import | ast.ImportFrom)]
            assert internos == []
