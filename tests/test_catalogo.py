"""Tests del ciclo de vida de un ítem y de la pantalla de catálogo, contra la
app real vía TestClient. Cubren lo que un usuario hace de verdad: dar de alta,
editar, filtrar, paginar y borrar.
"""
from datetime import date

from app.models import MediaItem, MediaStatus, MediaType, Priority


class TestAltaEdicionYBorrado:
    def test_alta_crea_el_item_y_redirige_al_catalogo(self, client, db):
        r = client.post("/agregar", data={
            "media_type": "libro", "title": "Duna", "status": "pendiente",
            "year": "1965", "creator": "Frank Herbert", "genres": "Ciencia ficción",
        }, follow_redirects=False)

        assert r.status_code == 303
        assert r.headers["location"] == "/catalogo"
        item = db.query(MediaItem).filter(MediaItem.title == "Duna").one()
        assert item.year == 1965
        assert item.creator == "Frank Herbert"
        assert item.status == MediaStatus.PENDIENTE

    def test_alta_ignora_los_numeros_mal_escritos_en_vez_de_reventar(self, client, db):
        r = client.post("/agregar", data={
            "media_type": "libro", "title": "Con año raro",
            "status": "pendiente", "year": "mil novecientos", "page_count": "muchas",
        }, follow_redirects=False)

        assert r.status_code == 303
        item = db.query(MediaItem).filter(MediaItem.title == "Con año raro").one()
        assert item.year is None
        assert item.page_count is None

    def test_edicion_actualiza_los_campos(self, client, crear_item, db):
        item = crear_item(title="Titulo viejo", media_type=MediaType.PELICULA)

        r = client.post(f"/item/{item.id}/actualizar", data={
            "title": "Titulo nuevo", "status": "en_progreso", "priority": "alta",
            "year": "1999", "rating": "8", "notes": "Una nota",
        }, follow_redirects=False)

        assert r.status_code == 303
        db.refresh(item)
        assert item.title == "Titulo nuevo"
        assert item.status == MediaStatus.EN_PROGRESO
        assert item.priority == Priority.ALTA
        assert item.rating == 8

    def test_marcar_como_completado_registra_la_fecha(self, client, crear_item, db):
        item = crear_item(status=MediaStatus.EN_PROGRESO)
        assert item.completed_at is None

        client.post(f"/item/{item.id}/actualizar", data={
            "title": item.title, "status": "completado", "priority": "media",
        }, follow_redirects=False)

        db.refresh(item)
        assert item.completed_at == date.today()

    def test_no_se_pisa_la_fecha_de_completado_al_reeditar(self, client, crear_item, db):
        """Editar una nota de algo ya completado no debe mover su fecha: la
        página de estadísticas se apoya en ella."""
        antigua = date(2020, 1, 1)
        item = crear_item(status=MediaStatus.COMPLETADO, completed_at=antigua)

        client.post(f"/item/{item.id}/actualizar", data={
            "title": item.title, "status": "completado", "priority": "media",
            "notes": "releída",
        }, follow_redirects=False)

        db.refresh(item)
        assert item.completed_at == antigua

    def test_las_etiquetas_se_crean_y_se_reutilizan(self, client, crear_item, db):
        uno = crear_item(title="Uno")
        otro = crear_item(title="Otro")
        datos = {"status": "pendiente", "priority": "media"}

        client.post(f"/item/{uno.id}/actualizar", data={**datos, "title": "Uno", "tags": "clásico, relectura"})
        client.post(f"/item/{otro.id}/actualizar", data={**datos, "title": "Otro", "tags": "clásico"})

        db.refresh(uno)
        db.refresh(otro)
        assert {t.name for t in uno.tags} == {"clásico", "relectura"}
        # La etiqueta compartida es la misma fila, no un duplicado
        assert otro.tags[0].id in {t.id for t in uno.tags}

    def test_borrar_elimina_el_item(self, client, crear_item, db):
        item = crear_item()
        r = client.post(f"/item/{item.id}/eliminar", follow_redirects=False)

        assert r.status_code == 303
        assert db.get(MediaItem, item.id) is None

    def test_operar_sobre_un_item_inexistente_no_da_500(self, client):
        assert client.get("/item/999999", follow_redirects=False).status_code == 303
        assert client.post("/item/999999/eliminar", follow_redirects=False).status_code == 303


class TestListadoDelCatalogo:
    def test_sin_tipo_muestra_todos_los_tipos(self, client, crear_item):
        """Antes /catalogo sin `tipo` redirigía forzando libros, lo que rompía
        los enlaces de inicio "en progreso/pendientes/completados/wishlist"
        (deberían filtrar por estado en TODO el catálogo, no solo libros)."""
        crear_item(title="Un libro", media_type=MediaType.LIBRO)
        crear_item(title="Una peli", media_type=MediaType.PELICULA)

        r = client.get("/catalogo", follow_redirects=False)
        assert r.status_code == 200
        assert "Un libro" in r.text
        assert "Una peli" in r.text

    def test_sin_tipo_el_filtro_de_estado_cruza_todos_los_tipos(self, client, crear_item):
        crear_item(title="Libro pendiente", media_type=MediaType.LIBRO, status=MediaStatus.PENDIENTE)
        crear_item(title="Peli completada", media_type=MediaType.PELICULA, status=MediaStatus.COMPLETADO)

        html = client.get("/catalogo?estado=completado").text
        assert "Peli completada" in html
        assert "Libro pendiente" not in html

    def test_filtra_por_tipo(self, client, crear_item):
        crear_item(title="Un libro", media_type=MediaType.LIBRO)
        crear_item(title="Una peli", media_type=MediaType.PELICULA)

        html = client.get("/catalogo?tipo=libro").text
        assert "Un libro" in html
        assert "Una peli" not in html

    def test_filtra_por_estado(self, client, crear_item):
        crear_item(title="Pendiente uno", status=MediaStatus.PENDIENTE)
        crear_item(title="Completado uno", status=MediaStatus.COMPLETADO)

        html = client.get("/catalogo?tipo=libro&estado=completado").text
        assert "Completado uno" in html
        assert "Pendiente uno" not in html

    def test_un_tipo_o_estado_invalido_no_rompe_la_pagina(self, client):
        """Los valores llegan por querystring: un enum inválido no debe dar 500."""
        assert client.get("/catalogo?tipo=inventado").status_code == 200
        assert client.get("/catalogo?tipo=libro&estado=inventado").status_code == 200

    def test_pagina_de_24_en_24(self, client, crear_item):
        for i in range(30):
            crear_item(title=f"Libro {i:02d}")

        primera = client.get("/catalogo?tipo=libro&pagina=1").text
        segunda = client.get("/catalogo?tipo=libro&pagina=2").text
        assert primera.count('class="card media-card"') == 24
        assert segunda.count('class="card media-card"') == 6

    def test_una_pagina_fuera_de_rango_se_ajusta_a_la_ultima(self, client, crear_item):
        crear_item(title="Unico")
        assert client.get("/catalogo?tipo=libro&pagina=999").status_code == 200
        assert client.get("/catalogo?tipo=libro&pagina=-5").status_code == 200

    def test_el_desplegable_de_generos_sale_de_los_datos(self, client, crear_item):
        crear_item(title="Uno", genres="Ciencia ficción, Aventura")
        crear_item(title="Otro", genres="Ensayo")

        html = client.get("/catalogo?tipo=libro").text
        assert "Ciencia ficción" in html
        assert "Ensayo" in html

    def test_las_etiquetas_de_estado_se_adaptan_al_tipo(self, client):
        """Un libro se 'lee' y una película se 've': la interfaz lo refleja."""
        assert "Por leer" in client.get("/catalogo?tipo=libro").text
        assert "Por ver" in client.get("/catalogo?tipo=pelicula").text
        assert "Por jugar" in client.get("/catalogo?tipo=videojuego").text

    def test_ordena_por_valoracion(self, client, crear_item):
        crear_item(title="Floja", rating=3)
        crear_item(title="Buenisima", rating=10)

        html = client.get("/catalogo?tipo=libro&orden=rating").text
        assert html.index("Buenisima") < html.index("Floja")

    def test_un_orden_desconocido_cae_al_por_defecto(self, client, crear_item):
        crear_item(title="Algo")
        assert client.get("/catalogo?tipo=libro&orden=inventado").status_code == 200


class TestClavesDeOrden:
    """[MC-B7] Las claves van en la URL y mezclaban acentuadas con sin acentuar
    (`añadido` y `año` frente a `alfabetico`). Se unifican en ASCII, que es lo
    que no hay que percent-encodear, pero las viejas no pueden dejar de
    funcionar: están en los marcadores de quien ya usa la app."""

    def test_todas_las_claves_son_ascii(self):
        from app.routers.catalog import ORDERINGS

        no_ascii = [k for k in ORDERINGS if not k.isascii()]
        assert no_ascii == [], "claves que hay que percent-encodear: %s" % no_ascii

    def test_las_claves_nuevas_ordenan(self, client, crear_item):
        crear_item(title="Vieja", year=1980)
        crear_item(title="Nueva", year=2024)

        html = client.get("/catalogo?tipo=libro&orden=anio").text
        assert html.index("Nueva") < html.index("Vieja")

    def test_una_url_guardada_con_la_clave_vieja_sigue_ordenando_igual(self, client, crear_item):
        """`?orden=año` estaba en cualquier marcador: no puede caer al orden por
        defecto en silencio, que es lo que haría sin el alias."""
        crear_item(title="Vieja", year=1980)
        crear_item(title="Nueva", year=2024)

        html = client.get("/catalogo?tipo=libro&orden=a%C3%B1o").text
        assert html.index("Nueva") < html.index("Vieja")
        # Y el botón tiene que quedar marcado como activo, no huérfano.
        assert 'orden=anio" class="active"' in html

    def test_el_alias_de_anadido_tambien(self, client, crear_item):
        assert client.get("/catalogo?tipo=libro&orden=a%C3%B1adido").status_code == 200
        assert 'orden=anadido" class="active"' in client.get(
            "/catalogo?tipo=libro&orden=a%C3%B1adido"
        ).text


class TestFiltroDeDuracion:
    def test_filtra_libros_por_numero_de_paginas(self, client, crear_item):
        crear_item(title="Cortito", page_count=100)
        crear_item(title="Ladrillo", page_count=800)

        corto = client.get("/catalogo?tipo=libro&tiempo=corto").text
        assert "Cortito" in corto
        assert "Ladrillo" not in corto

    def test_filtra_peliculas_por_duracion(self, client, crear_item):
        crear_item(title="Corta", media_type=MediaType.PELICULA, runtime_minutes=80)
        crear_item(title="Larga", media_type=MediaType.PELICULA, runtime_minutes=200)

        largo = client.get("/catalogo?tipo=pelicula&tiempo=largo").text
        assert "Larga" in largo
        assert "Corta" not in largo

    def test_filtra_juegos_por_horas_de_hltb(self, client, crear_item):
        crear_item(title="Rapidito", media_type=MediaType.VIDEOJUEGO, hltb_hours=5)
        crear_item(title="Eterno", media_type=MediaType.VIDEOJUEGO, hltb_hours=90)

        muy_largo = client.get("/catalogo?tipo=videojuego&tiempo=muy_largo").text
        assert "Eterno" in muy_largo
        assert "Rapidito" not in muy_largo


class TestSugerencia:
    def test_sugiere_solo_entre_los_pendientes(self, client, crear_item):
        crear_item(title="Pendiente", status=MediaStatus.PENDIENTE)
        crear_item(title="Ya completado", status=MediaStatus.COMPLETADO)

        html = client.get("/sugerencia").text
        assert "Pendiente" in html
        assert "Ya completado" not in html

    def test_sin_candidatos_no_rompe(self, client):
        assert client.get("/sugerencia").status_code == 200
