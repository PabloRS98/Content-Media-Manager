"""Las etiquetas de estado tienen que decir lo mismo en todas partes.

Estaban duplicadas: cinco diccionarios en el router y el mismo mapeo otra vez
en `_card.html` como ternario anidado de 13 niveles. Y ya habían divergido --
a la copia de la plantilla le faltaba `wishlist`.
"""
import pytest

from app.catalogo_config import ETIQUETAS_ESTADO, etiqueta_estado, etiquetas_de
from app.models import MediaStatus, MediaType

COMBINACIONES = [(t, e) for t in MediaType for e in MediaStatus]


@pytest.mark.parametrize("media_type, estado", COMBINACIONES)
def test_las_25_combinaciones_tienen_etiqueta(media_type, estado):
    """Ninguna puede caer a un `.get()` que devuelva None: eso es lo que hacía
    que Jinja imprimiera literalmente "None" en el desplegable."""
    etiqueta = etiquetas_de(media_type)[estado]
    assert etiqueta and etiqueta != "None"


@pytest.mark.parametrize("media_type", list(MediaType) + [None])
def test_ninguna_tabla_se_deja_un_estado(media_type):
    assert set(etiquetas_de(media_type)) == set(MediaStatus)


def test_sin_tipo_se_usan_las_neutras():
    assert etiquetas_de(None)[MediaStatus.PENDIENTE] == "Pendiente"


@pytest.mark.parametrize("media_type, estado", COMBINACIONES)
def test_la_etiqueta_de_la_tarjeta_coincide_con_la_del_desplegable(
    client, crear_item, media_type, estado
):
    """El bug concreto: un ítem en wishlist se veía como "Wishlist" en la
    tarjeta y como "Lo quiero ver" en el filtro."""
    item = crear_item(title="Ítem de prueba", media_type=media_type, status=estado)

    de_la_tarjeta = etiqueta_estado(item)
    del_desplegable = etiquetas_de(media_type)[estado]
    assert de_la_tarjeta == del_desplegable


def test_la_tarjeta_pinta_la_etiqueta_del_tipo(client, crear_item):
    """Antes, la plantilla calculaba la suya por su cuenta."""
    crear_item(title="Libro deseado", media_type=MediaType.LIBRO,
               status=MediaStatus.WISHLIST)
    html = client.get("/catalogo?tipo=libro").text
    assert "Lo quiero" in html
    assert ">Wishlist<" not in html


def test_el_desplegable_del_filtro_usa_la_misma_tabla(client, crear_item):
    crear_item(title="Peli deseada", media_type=MediaType.PELICULA,
               status=MediaStatus.WISHLIST)
    html = client.get("/catalogo?tipo=pelicula").text
    assert "Lo quiero ver" in html


def test_la_plantilla_ya_no_tiene_su_propia_copia():
    """Si vuelve a aparecer un mapeo en la plantilla, esto lo caza."""
    from pathlib import Path

    card = Path(__file__).resolve().parent.parent / "app" / "templates" / "_card.html"
    contenido = card.read_text(encoding="utf-8")
    assert "Por leer" not in contenido
    assert "Escuchando" not in contenido
    assert "etiqueta_estado(item)" in contenido


def test_todas_las_tablas_estan_declaradas():
    """Que no se añada un MediaType sin su tabla."""
    declarados = {t for t in ETIQUETAS_ESTADO if t is not None}
    assert declarados == set(MediaType)
