"""Buscar dentro de lo que ya tienes.

Era la laguna funcional más grande de la app: el único buscador consultaba las
APIs externas para AÑADIR ítems, y no había forma de encontrar entre los que ya
estaban. Con varios miles importados, "aquel libro de Sanderson que dejé a
medias" exigía navegar por filtros y pasar páginas.
"""
import pytest

from app.models import MediaStatus, MediaType
from app.services.catalogo import filtrar_por_busqueda


@pytest.fixture
def biblioteca(crear_item):
    """Un puñado de ítems con datos repartidos entre las cuatro columnas."""
    crear_item(title="El imperio final", creator="Brandon Sanderson",
               saga="Nacidos de la bruma", genres="Fantasía", media_type=MediaType.LIBRO)
    crear_item(title="El pozo de la ascensión", creator="Brandon Sanderson",
               saga="Nacidos de la bruma", genres="Fantasía", media_type=MediaType.LIBRO)
    crear_item(title="Dune", creator="Frank Herbert",
               saga="Dune", genres="Ciencia ficción", media_type=MediaType.LIBRO)
    crear_item(title="Blade Runner", creator="Ridley Scott",
               genres="Ciencia ficción", media_type=MediaType.PELICULA,
               status=MediaStatus.COMPLETADO)


def titulos(html: str, biblioteca_titulos) -> set[str]:
    return {t for t in biblioteca_titulos if t in html}


TODOS = {"El imperio final", "El pozo de la ascensión", "Dune", "Blade Runner"}


class TestPorDondeBusca:
    def test_busca_por_titulo(self, client, biblioteca):
        html = client.get("/catalogo", params={"buscar": "dune"}).text
        assert titulos(html, TODOS) == {"Dune"}

    def test_busca_por_autor(self, client, biblioteca):
        html = client.get("/catalogo", params={"buscar": "sanderson"}).text
        assert titulos(html, TODOS) == {"El imperio final", "El pozo de la ascensión"}

    def test_busca_por_genero(self, client, biblioteca):
        html = client.get("/catalogo", params={"buscar": "ciencia"}).text
        assert titulos(html, TODOS) == {"Dune", "Blade Runner"}

    def test_busca_por_saga(self, client, biblioteca):
        html = client.get("/catalogo", params={"buscar": "bruma"}).text
        assert titulos(html, TODOS) == {"El imperio final", "El pozo de la ascensión"}

    def test_no_distingue_mayusculas(self, client, biblioteca):
        assert titulos(client.get("/catalogo", params={"buscar": "SANDERSON"}).text, TODOS) == \
            titulos(client.get("/catalogo", params={"buscar": "sanderson"}).text, TODOS)


class TestPalabrasEnAND:
    def test_las_palabras_pueden_estar_en_columnas_distintas(self, client, biblioteca):
        """Es como se busca de verdad: uno recuerda media cosa de cada sitio."""
        html = client.get("/catalogo", params={"buscar": "sanderson pozo"}).text
        assert titulos(html, TODOS) == {"El pozo de la ascensión"}

    def test_todas_las_palabras_tienen_que_aparecer(self, client, biblioteca):
        html = client.get("/catalogo", params={"buscar": "sanderson herbert"}).text
        assert titulos(html, TODOS) == set()

    def test_los_espacios_de_mas_no_estorban(self, client, biblioteca):
        html = client.get("/catalogo", params={"buscar": "  sanderson   pozo  "}).text
        assert titulos(html, TODOS) == {"El pozo de la ascensión"}


class TestConLosDemasFiltros:
    def test_la_busqueda_se_combina_con_el_tipo(self, client, biblioteca):
        html = client.get("/catalogo", params={"buscar": "ciencia", "tipo": "libro"}).text
        assert titulos(html, TODOS) == {"Dune"}

    def test_la_busqueda_se_combina_con_el_estado(self, client, biblioteca):
        html = client.get("/catalogo", params={"buscar": "ciencia", "estado": "completado"}).text
        assert titulos(html, TODOS) == {"Blade Runner"}

    def test_los_filtros_conservan_la_busqueda(self, client, biblioteca):
        """Pulsar un filtro no puede tirar lo que estabas buscando."""
        html = client.get("/catalogo", params={"buscar": "sanderson"}).text
        assert "buscar=sanderson" in html

    def test_la_paginacion_conserva_la_busqueda(self, client, crear_item):
        for n in range(25):
            crear_item(title="Sanderson %02d" % n, creator="Brandon Sanderson")
        html = client.get("/catalogo", params={"buscar": "sanderson"}).text
        assert "buscar=sanderson" in html
        assert "Página 1 de 2" in html


class TestCasosBorde:
    def test_sin_busqueda_sale_todo(self, client, biblioteca):
        assert titulos(client.get("/catalogo").text, TODOS) == TODOS

    def test_una_busqueda_vacia_no_filtra(self, client, biblioteca):
        assert titulos(client.get("/catalogo", params={"buscar": "   "}).text, TODOS) == TODOS

    def test_los_comodines_de_like_se_escapan(self, client, biblioteca):
        """Sin escapar, buscar "%" devolvía el catálogo entero -- el mismo fallo
        que ya se arregló una vez en el filtro de género."""
        assert titulos(client.get("/catalogo", params={"buscar": "%"}).text, TODOS) == set()
        assert titulos(client.get("/catalogo", params={"buscar": "_"}).text, TODOS) == set()

    def test_una_busqueda_sin_resultados_no_rompe(self, client, biblioteca):
        r = client.get("/catalogo", params={"buscar": "noexisteestapalabra"})
        assert r.status_code == 200
        assert titulos(r.text, TODOS) == set()

    def test_los_items_sin_autor_ni_saga_no_estorban(self, client, crear_item):
        """`creator` y `saga` son nulables: un NULL en un OR no puede tumbar la
        consulta ni hacer desaparecer al ítem de una búsqueda por título."""
        crear_item(title="Suelto", creator=None, saga=None, genres=None)
        assert "Suelto" in client.get("/catalogo", params={"buscar": "suelto"}).text


class TestLaConsultaEsDeVerdad:
    def test_el_filtro_se_aplica_en_sql(self, db, crear_item):
        """En SQL y no en Python: con varios miles de ítems, traérselos todos
        para filtrarlos en memoria es justo lo que la Fase 3 quitó."""
        from app.models import MediaItem

        crear_item(title="Uno", creator="Alguien")
        query = filtrar_por_busqueda(db.query(MediaItem), "alguien")
        assert "LIKE" in str(query).upper()
        assert query.count() == 1

    def test_sin_texto_devuelve_la_consulta_intacta(self, db):
        from app.models import MediaItem

        original = db.query(MediaItem)
        assert filtrar_por_busqueda(original, None) is original
        assert filtrar_por_busqueda(original, "") is original


def test_el_buscador_aparece_en_la_pagina(client):
    html = client.get("/catalogo").text
    assert 'name="buscar"' in html
    assert "Buscar en mi catálogo" in html


def test_el_formulario_de_busqueda_arrastra_los_filtros(client, biblioteca):
    """Buscar con un tipo activo no puede devolverte al catálogo entero."""
    html = client.get("/catalogo", params={"tipo": "libro"}).text
    assert '<input type="hidden" name="tipo" value="libro">' in html
