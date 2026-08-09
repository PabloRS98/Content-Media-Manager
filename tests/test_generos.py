"""[N4] Los géneros dejan de ser una cadena separada por comas.

Guardarlos como texto obligaba a cuatro cosas malas:

1. Filtrar con `LIKE '%...%'`, que además de escapar comodines a mano arrastra
   géneros distintos: pedir "Acción" devolvía también "Acción y aventura".
2. Agregar en Python para las estadísticas.
3. Recorrer todas las filas para saber qué géneros hay.
4. No poder renombrar: "Sci-Fi" y "Ciencia ficción" convivían para siempre.

La app ya tenía el patrón resuelto con `Tag`; esto aplica el mismo a géneros.
"""
from app.models import Genero, MediaItem, MediaStatus, MediaType


class TestFiltrarPorGenero:
    def test_un_genero_no_arrastra_a_otro_que_lo_contenga(self, client, crear_item, db):
        """Con LIKE '%Acción%', pedir "Acción" traía también "Acción y
        aventura", que es un género distinto de TMDB."""
        crear_item(title="La de tiros", media_type=MediaType.PELICULA, generos=["Acción"])
        crear_item(title="La de exploradores", media_type=MediaType.PELICULA,
                   generos=["Acción y aventura"])

        html = client.get("/catalogo?tipo=pelicula&genero=Acci%C3%B3n").text

        # El nombre del otro género sale igualmente en el desplegable del
        # filtro; lo que no puede salir es su película.
        assert "La de tiros" in html
        assert "La de exploradores" not in html

    def test_filtra_por_uno_de_varios_generos(self, client, crear_item):
        crear_item(title="Con dos", media_type=MediaType.PELICULA,
                   generos=["Drama", "Ciencia ficción"])
        crear_item(title="Con otro", media_type=MediaType.PELICULA, generos=["Comedia"])

        html = client.get("/catalogo?tipo=pelicula&genero=Drama").text

        assert "Con dos" in html
        assert "Con otro" not in html

    def test_el_desplegable_sale_de_la_base(self, client, crear_item):
        crear_item(title="A", media_type=MediaType.PELICULA, generos=["Drama", "Comedia"])
        crear_item(title="B", media_type=MediaType.PELICULA, generos=["Drama"])

        html = client.get("/catalogo?tipo=pelicula").text

        assert "genero=Drama" in html
        assert "genero=Comedia" in html

    def test_solo_los_generos_del_tipo_que_se_mira(self, client, crear_item):
        crear_item(title="Peli", media_type=MediaType.PELICULA, generos=["Cine negro"])
        crear_item(title="Libro", media_type=MediaType.LIBRO, generos=["Ensayo"])

        html = client.get("/catalogo?tipo=pelicula").text

        assert "Cine negro" in html
        assert "Ensayo" not in html

    def test_no_ofrece_los_generos_de_otra_cuenta(self, client, crear_item, otro_usuario, db):
        crear_item(title="Mía", media_type=MediaType.PELICULA, generos=["Western"])
        ajena = MediaItem(usuario_id=otro_usuario.id, title="Suya",
                          media_type=MediaType.PELICULA, status=MediaStatus.PENDIENTE)
        ajena.generos.append(Genero(nombre="Telenovela"))
        db.add(ajena)
        db.commit()

        html = client.get("/catalogo?tipo=pelicula").text

        assert "Western" in html
        assert "Telenovela" not in html


class TestElVocabularioNoSeDuplica:
    def test_dos_items_con_el_mismo_genero_comparten_fila(self, client, crear_item, db):
        crear_item(title="A", generos=["Fantasía"])
        crear_item(title="B", generos=["Fantasía"])

        assert db.query(Genero).filter(Genero.nombre == "Fantasía").count() == 1

    def test_el_mismo_genero_escrito_distinto_es_el_mismo(self, client, db, crear_item):
        """Escribir "  fantasía " no puede crear un género nuevo."""
        item = crear_item(title="A")

        client.post("/item/%d/actualizar" % item.id, data={
            "title": "A", "status": "pendiente", "genres": "  fantasía ",
        })

        nombres = [g.nombre for g in db.query(Genero).all()]
        assert nombres == ["Fantasía"], nombres

    def test_un_genero_que_se_queda_sin_items_desaparece(self, client, db, crear_item):
        """Igual que las etiquetas: si no, la lista crece para siempre y el
        desplegable acaba ofreciendo géneros que ya no usa nadie."""
        item = crear_item(title="A", generos=["Efímero"])

        client.post("/item/%d/actualizar" % item.id, data={
            "title": "A", "status": "pendiente", "genres": "Otro",
        })

        assert db.query(Genero).filter(Genero.nombre == "Efímero").count() == 0


class TestRenombrar:
    """El cuarto problema del informe: con la cadena, "Sci-Fi" y "Ciencia
    ficción" convivían para siempre porque no había dónde renombrar."""

    def test_renombrar_lo_cambia_en_todos_los_items(self, client, db, crear_item):
        crear_item(title="A", generos=["Sci-Fi"])
        crear_item(title="B", generos=["Sci-Fi"])
        genero = db.query(Genero).filter(Genero.nombre == "Sci-Fi").one()

        client.post("/generos/%d/renombrar" % genero.id, data={"nombre": "Ciencia ficción"})

        db.expire_all()
        assert db.query(Genero).filter(Genero.nombre == "Sci-Fi").count() == 0
        assert [i.title for i in db.query(Genero)
                .filter(Genero.nombre == "Ciencia ficción").one().items] == ["A", "B"]

    def test_renombrar_a_uno_que_ya_existe_los_fusiona(self, client, db, crear_item):
        """Es el caso de verdad: los dos nombres ya están en la base, y lo que
        uno quiere es que pasen a ser uno solo."""
        crear_item(title="A", generos=["Sci-Fi"])
        crear_item(title="B", generos=["Ciencia ficción"])
        viejo = db.query(Genero).filter(Genero.nombre == "Sci-Fi").one()

        client.post("/generos/%d/renombrar" % viejo.id, data={"nombre": "Ciencia ficción"})

        db.expire_all()
        assert db.query(Genero).filter(Genero.nombre == "Sci-Fi").count() == 0
        fusionado = db.query(Genero).filter(Genero.nombre == "Ciencia ficción").one()
        assert sorted(i.title for i in fusionado.items) == ["A", "B"]

    def test_la_pagina_lista_los_generos_con_su_cuenta(self, client, crear_item):
        crear_item(title="A", generos=["Drama"])
        crear_item(title="B", generos=["Drama"])
        crear_item(title="C", generos=["Comedia"])

        html = client.get("/generos").text

        assert "Drama" in html and "Comedia" in html

    def test_no_se_puede_renombrar_a_vacio(self, client, db, crear_item):
        crear_item(title="A", generos=["Drama"])
        genero = db.query(Genero).filter(Genero.nombre == "Drama").one()

        client.post("/generos/%d/renombrar" % genero.id, data={"nombre": "   "})

        db.expire_all()
        assert db.query(Genero).filter(Genero.nombre == "Drama").count() == 1


class TestLaBusquedaSigueEncontrandoPorGenero:
    def test_buscar_una_palabra_del_genero(self, client, crear_item):
        crear_item(title="Sin pistas en el título", generos=["Documental"])
        crear_item(title="Otra cosa", generos=["Comedia"])

        html = client.get("/catalogo", params={"buscar": "documental"}).text

        assert "Sin pistas en el título" in html
        assert "Otra cosa" not in html


class TestEstadisticas:
    def test_el_grafico_de_generos_cuenta_bien(self, client, crear_item):
        crear_item(title="A", generos=["Drama"], status=MediaStatus.COMPLETADO)
        crear_item(title="B", generos=["Drama"], status=MediaStatus.COMPLETADO)
        crear_item(title="C", generos=["Comedia"], status=MediaStatus.COMPLETADO)

        html = client.get("/estadisticas").text

        assert "Drama" in html
        assert "[2, 1]" in html or "[2,1]" in html
