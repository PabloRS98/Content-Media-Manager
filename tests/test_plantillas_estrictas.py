"""Con `StrictUndefined`, una variable ausente revienta en vez de callarse.

Sin él, referenciar algo que el router no pasa da `Undefined`, que es falsy:
un `{% if %}` sobre ella no entra y el bloque simplemente no se pinta. Un fallo
así puede pasar meses sin verse, y es el mismo tipo de bug que ya ocurrió aquí
con `status_labels.get()` devolviendo None.

Activarlo destapó dos cosas, y las dos están cubiertas abajo.
"""
import pytest
from jinja2 import UndefinedError

from app.models import MediaStatus, MediaType
from app.templating import templates

VISTAS = ["/", "/catalogo", "/listas", "/estadisticas", "/calendario",
          "/importar", "/estado", "/sugerencia", "/tengo-tiempo"]


def test_una_variable_ausente_lanza_error():
    plantilla = templates.env.from_string("{{ no_existe }}")
    with pytest.raises(UndefinedError):
        plantilla.render()


def test_is_defined_sigue_funcionando():
    """El patrón correcto para lo opcional: `base.html` ya lo usaba."""
    plantilla = templates.env.from_string(
        "{% if opcional is defined and opcional %}sí{% else %}no{% endif %}"
    )
    assert plantilla.render() == "no"
    assert plantilla.render(opcional=True) == "sí"


@pytest.mark.parametrize("ruta", VISTAS)
def test_las_vistas_siguen_respondiendo_200(client, ruta):
    assert client.get(ruta).status_code == 200


@pytest.mark.parametrize("ruta", VISTAS)
def test_las_vistas_responden_200_con_datos(client, crear_item, crear_serie, ruta):
    """Vacías es el caso fácil: muchos bloques solo se pintan si hay algo."""
    crear_item(title="Un libro", media_type=MediaType.LIBRO,
               status=MediaStatus.COMPLETADO, rating=8, year=1999,
               genres="Drama", cover_url="https://ejemplo.test/a.jpg")
    crear_serie(temporadas=1, por_temporada=2, vistos=1)
    assert client.get(ruta).status_code == 200


def test_la_ficha_de_un_item_responde_200(client, crear_item):
    item = crear_item(title="Con ficha")
    assert client.get("/item/%d" % item.id).status_code == 200


def test_la_ficha_de_una_lista_responde_200(client, db, listas_dinamicas):
    for lista in listas_dinamicas.values():
        assert client.get("/listas/%d" % lista.id).status_code == 200


class TestAccesosDeLaPortada:
    """El fallo real que destapó `StrictUndefined`.

    Si el sembrado de las listas automáticas falla, `lifespan` registra el
    error y la app sigue arrancando -- a propósito, no queremos que un fallo
    ahí impida usar el catálogo. Pero entonces la portada pintaba
    `href="/listas/"`: un enlace roto con el mismo aspecto que uno bueno.
    """

    def test_sin_listas_automaticas_los_numeros_no_enlazan(self, client, crear_item):
        crear_item(title="Algo", status=MediaStatus.EN_PROGRESO)
        html = client.get("/").text
        assert 'href="/listas/"' not in html
        assert "en progreso" in html  # el dato sigue estando

    def test_con_listas_automaticas_los_numeros_enlazan(
        self, client, crear_item, listas_dinamicas
    ):
        crear_item(title="Algo", status=MediaStatus.EN_PROGRESO)
        html = client.get("/").text
        esperado = 'href="/listas/%d"' % listas_dinamicas["en_progreso"].id
        assert esperado in html
