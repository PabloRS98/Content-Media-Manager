"""Flujos de extremo a extremo: alta, edición, episodios, listas e importadores."""
import pytest


class TestPaginasBasicas:
    @pytest.mark.parametrize("ruta", [
        "/", "/salud", "/catalogo?tipo=libro", "/catalogo?tipo=pelicula",
        "/catalogo?tipo=serie", "/catalogo?tipo=videojuego", "/catalogo?tipo=podcast",
        "/listas", "/estadisticas", "/importar", "/calendario", "/tengo-tiempo?minutos=60",
    ])
    def test_responden_200_con_catalogo_vacio(self, client, ruta):
        assert client.get(ruta).status_code == 200

    def test_catalogo_sin_tipo_redirige(self, client):
        r = client.get("/catalogo", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/catalogo?tipo=libro"

    @pytest.mark.parametrize("ruta", [
        "/catalogo?tipo=inexistente", "/catalogo?tipo=libro&estado=inventado",
        "/catalogo?tipo=libro&orden=nope", "/catalogo?tipo=libro&pagina=99999",
        "/catalogo?tipo=libro&pagina=-5",
    ])
    def test_parametros_invalidos_no_rompen(self, client, ruta):
        assert client.get(ruta).status_code == 200

    def test_item_inexistente_redirige(self, client):
        r = client.get("/item/999999", follow_redirects=False)
        assert r.status_code == 303


class TestCicloDeVidaDeUnItem:
    def test_alta_edicion_y_borrado(self, client, db):
        from app.models import MediaItem

        r = client.post("/agregar", data={"media_type": "libro", "title": "Duna",
                                          "status": "pendiente", "year": "1965",
                                          "creator": "Frank Herbert"},
                        follow_redirects=False)
        assert r.status_code == 303
        item = db.query(MediaItem).filter(MediaItem.title == "Duna").one()
        assert item.year == 1965 and item.creator == "Frank Herbert"

        client.post(f"/item/{item.id}/actualizar",
                    data={"title": "Duna", "status": "completado", "rating": "9",
                          "tags": "clasico, sci-fi"}, follow_redirects=False)
        db.expire_all()
        item = db.get(MediaItem, item.id)
        assert item.rating == 9
        assert {t.name for t in item.tags} == {"clasico", "sci-fi"}
        assert item.completed_at is not None

        assert client.get(f"/item/{item.id}").status_code == 200

        client.post(f"/item/{item.id}/eliminar", follow_redirects=False)
        db.expunge_all()
        assert db.get(MediaItem, item.id) is None

    def test_ano_absurdo_se_descarta(self, client, db):
        from app.models import MediaItem

        client.post("/agregar", data={"media_type": "libro", "title": "X",
                                      "status": "pendiente", "year": "999999"},
                    follow_redirects=False)
        assert db.query(MediaItem).filter(MediaItem.title == "X").one().year is None


class TestEpisodios:
    @pytest.fixture()
    def serie(self, db):
        from app.models import Episode, MediaItem, MediaStatus, MediaType

        item = MediaItem(media_type=MediaType.SERIE, title="Serie", status=MediaStatus.PENDIENTE)
        item.episodes = [Episode(season_number=1, episode_number=n) for n in range(1, 4)]
        db.add(item)
        db.commit()
        return item

    def test_marcar_un_episodio_pone_la_serie_en_progreso(self, client, db, serie):
        from app.models import MediaItem, MediaStatus

        ep = serie.episodes[0]
        client.post(f"/item/{serie.id}/episodio/{ep.id}/toggle", follow_redirects=False)
        db.expire_all()
        assert db.get(MediaItem, serie.id).status == MediaStatus.EN_PROGRESO

    def test_marcar_todos_completa_la_serie(self, client, db, serie):
        from app.models import MediaItem, MediaStatus

        client.post(f"/item/{serie.id}/marcar-hasta/{serie.episodes[-1].id}",
                    follow_redirects=False)
        db.expire_all()
        item = db.get(MediaItem, serie.id)
        assert item.status == MediaStatus.COMPLETADO
        assert item.completed_at is not None

    def test_desmarcar_vuelve_a_pendiente(self, client, db, serie):
        from app.models import MediaItem, MediaStatus

        ep = serie.episodes[0]
        for _ in range(2):
            client.post(f"/item/{serie.id}/episodio/{ep.id}/toggle", follow_redirects=False)
        db.expire_all()
        assert db.get(MediaItem, serie.id).status == MediaStatus.PENDIENTE

    def test_episodio_de_otra_serie_no_se_marca(self, client, db, serie):
        from app.models import Episode, MediaItem, MediaType

        otra = MediaItem(media_type=MediaType.SERIE, title="Otra")
        otra.episodes = [Episode(season_number=1, episode_number=1)]
        db.add(otra)
        db.commit()
        ajeno = otra.episodes[0]

        client.post(f"/item/{serie.id}/episodio/{ajeno.id}/toggle", follow_redirects=False)
        db.expire_all()
        assert db.get(Episode, ajeno.id).watched is False


class TestListas:
    def test_crear_anadir_y_borrar(self, client, db):
        from app.models import Lista, MediaItem, MediaType

        db.add(MediaItem(media_type=MediaType.LIBRO, title="Libro"))
        db.commit()
        item_id = db.query(MediaItem).one().id

        client.post("/listas", data={"name": "Favoritos"}, follow_redirects=False)
        lista = db.query(Lista).filter(Lista.name == "Favoritos").one()

        client.post(f"/item/{item_id}/anadir-lista", data={"list_id": str(lista.id)},
                    follow_redirects=False)
        db.expire_all()
        assert len(db.get(Lista, lista.id).items) == 1
        assert client.get(f"/listas/{lista.id}").status_code == 200

        client.post(f"/listas/{lista.id}/quitar/{item_id}", follow_redirects=False)
        db.expire_all()
        assert len(db.get(Lista, lista.id).items) == 0

        lista_id = lista.id
        client.post(f"/listas/{lista_id}/eliminar", follow_redirects=False)
        db.expunge_all()
        assert db.get(Lista, lista_id) is None

    def test_nombre_duplicado_se_rechaza(self, client, db):
        from app.models import Lista

        for _ in range(2):
            client.post("/listas", data={"name": "Unica"}, follow_redirects=False)
        assert db.query(Lista).filter(Lista.name == "Unica").count() == 1

    def test_nombre_vacio_se_rechaza(self, client, db):
        from app.models import Lista

        client.post("/listas", data={"name": "   "}, follow_redirects=False)
        assert db.query(Lista).count() == 0


class TestImportadores:
    def test_goodreads(self, client, db):
        from app.models import MediaItem, MediaStatus, MediaType

        csv_data = ("Title,Author,My Rating,Number of Pages,Exclusive Shelf,Date Read\n"
                    "Duna,Frank Herbert,5,600,read,2024/03/15\n"
                    "Otro,Alguien,0,100,to-read,\n")
        r = client.post("/importar/libros", files={"archivo": ("g.csv", csv_data, "text/csv")})
        assert r.status_code == 200

        duna = db.query(MediaItem).filter(MediaItem.title == "Duna").one()
        assert duna.media_type == MediaType.LIBRO
        assert duna.rating == 10 and duna.page_count == 600
        assert duna.status == MediaStatus.COMPLETADO and duna.completed_at is not None

        otro = db.query(MediaItem).filter(MediaItem.title == "Otro").one()
        assert otro.status == MediaStatus.PENDIENTE and otro.rating is None

    def test_goodreads_no_duplica_al_reimportar(self, client, db):
        from app.models import MediaItem

        csv_data = "Title,Author,Exclusive Shelf\nDuna,Frank Herbert,read\n"
        for _ in range(2):
            client.post("/importar/libros", files={"archivo": ("g.csv", csv_data, "text/csv")})
        assert db.query(MediaItem).filter(MediaItem.title == "Duna").count() == 1

    def test_juegos(self, client, db):
        from app.models import MediaItem, MediaStatus, MediaType

        csv_data = ("Title,Status,Hours,Rating\n"
                    "Hollow Knight,completed,40,4.5\n"
                    "Otro juego,backlog,,\n")
        client.post("/importar/juegos", files={"archivo": ("b.csv", csv_data, "text/csv")})

        hk = db.query(MediaItem).filter(MediaItem.title == "Hollow Knight").one()
        assert hk.media_type == MediaType.VIDEOJUEGO
        assert hk.hltb_hours == 40 and hk.rating == 9
        assert hk.status == MediaStatus.COMPLETADO

    def test_imdb_mapea_tipos_y_valoraciones(self, client, db):
        from app.models import MediaItem, MediaStatus, MediaType

        csv_data = ("Const,Title,Title Type,Year,Your Rating,Genres\n"
                    "tt1,Peli,movie,2001,8,Drama\n"
                    "tt2,Serie,tvSeries,2005,,Comedy\n"
                    "tt3,Juego,videoGame,2010,7,Action\n"
                    "tt4,Raro,podcastSeries,2020,,\n")
        client.post("/importar", files={"archivo": ("i.csv", csv_data, "text/csv")})

        assert db.query(MediaItem).filter(MediaItem.title == "Peli").one().media_type == MediaType.PELICULA
        assert db.query(MediaItem).filter(MediaItem.title == "Serie").one().media_type == MediaType.SERIE
        assert db.query(MediaItem).filter(MediaItem.title == "Juego").one().media_type == MediaType.VIDEOJUEGO
        assert db.query(MediaItem).filter(MediaItem.title == "Raro").count() == 0  # tipo no soportado

        peli = db.query(MediaItem).filter(MediaItem.title == "Peli").one()
        assert peli.rating == 8 and peli.status == MediaStatus.COMPLETADO
        serie = db.query(MediaItem).filter(MediaItem.title == "Serie").one()
        assert serie.status == MediaStatus.PENDIENTE

    def test_csv_vacio_no_rompe(self, client):
        r = client.post("/importar", files={"archivo": ("v.csv", "", "text/csv")})
        assert r.status_code == 200


class TestSugerencias:
    def test_sugerencia_con_catalogo_vacio(self, client):
        assert client.get("/sugerencia?tipo=libro").status_code == 200

    def test_sugerencia_devuelve_un_pendiente(self, client, db):
        from app.models import MediaItem, MediaStatus, MediaType

        db.add(MediaItem(media_type=MediaType.LIBRO, title="Pendiente",
                         status=MediaStatus.PENDIENTE))
        db.commit()
        assert "Pendiente" in client.get("/sugerencia?tipo=libro").text

    def test_tengo_tiempo_filtra_por_duracion(self, client, db):
        from app.models import MediaItem, MediaStatus, MediaType

        db.add(MediaItem(media_type=MediaType.PELICULA, title="Corta",
                         status=MediaStatus.PENDIENTE, runtime_minutes=80))
        db.add(MediaItem(media_type=MediaType.PELICULA, title="Larga",
                         status=MediaStatus.PENDIENTE, runtime_minutes=200))
        db.commit()

        html = client.get("/tengo-tiempo?minutos=90").text
        assert "Corta" in html and "Larga" not in html
