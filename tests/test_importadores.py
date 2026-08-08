"""Tests de los tres importadores de CSV (IMDb, Goodreads/StoryGraph, juegos).

Es la vía por la que entra el grueso del catálogo, y cada servicio exporta con
sus propios nombres de columna, así que la tolerancia a formatos es justo lo
que hay que blindar.
"""
from datetime import date

from app.models import MediaItem, MediaStatus, MediaType
from app.services.imports import import_books_csv, import_games_csv


def subir(client, ruta, texto, nombre="export.csv"):
    return client.post(ruta, files={"archivo": (nombre, texto, "text/csv")})


class TestImportadorIMDb:
    def test_importa_peliculas_y_series(self, client, db):
        csv = (
            "Const,Title,Title Type,Year,Directors,Genres\n"
            "tt0133093,The Matrix,movie,1999,Lana Wachowski,Action\n"
            "tt0903747,Breaking Bad,tvSeries,2008,Vince Gilligan,Drama\n"
        )
        subir(client, "/importar", csv)

        peli = db.query(MediaItem).filter(MediaItem.title == "The Matrix").one()
        serie = db.query(MediaItem).filter(MediaItem.title == "Breaking Bad").one()
        assert peli.media_type == MediaType.PELICULA
        assert peli.year == 1999
        assert peli.external_id == "tt0133093" or peli.external_id == "imdb:tt0133093"
        assert serie.media_type == MediaType.SERIE

    def test_una_valoracion_propia_marca_el_titulo_como_completado(self, client, db):
        csv = (
            "Const,Title,Title Type,Year,Your Rating,Date Rated\n"
            "tt0111161,Cadena perpetua,movie,1994,10,2024-05-20\n"
        )
        subir(client, "/importar", csv)

        item = db.query(MediaItem).one()
        assert item.status == MediaStatus.COMPLETADO
        assert item.rating == 10
        assert item.completed_at == date(2024, 5, 20)

    def test_sin_valoracion_queda_como_pendiente(self, client, db):
        csv = "Const,Title,Title Type,Year\ntt0111161,Cadena perpetua,movie,1994\n"
        subir(client, "/importar", csv)

        item = db.query(MediaItem).one()
        assert item.status == MediaStatus.PENDIENTE
        assert item.completed_at is None

    def test_acepta_las_cabeceras_en_espanol(self, client, db):
        csv = (
            "Constante,Título,Tipo de título,Año,Directores\n"
            "tt0133093,Matrix,película,1999,Lana Wachowski\n"
        )
        subir(client, "/importar", csv)
        assert db.query(MediaItem).filter(MediaItem.title == "Matrix").count() == 1

    def test_omite_los_tipos_que_no_manejamos(self, client, db):
        csv = (
            "Const,Title,Title Type,Year\n"
            "tt1,Un episodio suelto,tvEpisode,2020\n"
            "tt2,Una peli,movie,2020\n"
        )
        r = subir(client, "/importar", csv)

        assert db.query(MediaItem).count() == 1
        assert "1" in r.text  # el resumen informa de 1 omitido

    def test_no_reimporta_lo_que_ya_esta_en_la_base(self, client, db):
        csv = "Const,Title,Title Type,Year\ntt0133093,The Matrix,movie,1999\n"
        subir(client, "/importar", csv)
        subir(client, "/importar", csv)

        assert db.query(MediaItem).filter(MediaItem.title == "The Matrix").count() == 1

    def test_un_csv_vacio_no_rompe(self, client, db):
        r = subir(client, "/importar", "Const,Title,Title Type,Year\n")
        assert r.status_code == 200
        assert db.query(MediaItem).count() == 0

    def test_tolera_el_bom_de_excel(self, client, db):
        csv = "﻿Const,Title,Title Type,Year\ntt1,Con BOM,movie,2020\n"
        subir(client, "/importar", csv)
        assert db.query(MediaItem).filter(MediaItem.title == "Con BOM").count() == 1


class TestImportadorDeLibros:
    def test_importa_un_export_de_goodreads(self, usuario, db):
        csv = (
            "Title,Author,My Rating,Number of Pages,Original Publication Year,"
            "Exclusive Shelf,Date Read\n"
            "Dune,Frank Herbert,5,412,1965,read,2024/03/15\n"
            "Neuromancer,William Gibson,0,271,1984,to-read,\n"
        )
        res = import_books_csv(db, csv, usuario.id)

        assert res["creados"] == 2
        dune = db.query(MediaItem).filter(MediaItem.title == "Dune").one()
        assert dune.creator == "Frank Herbert"
        assert dune.rating == 10          # 5 estrellas -> 10
        assert dune.page_count == 412
        assert dune.status == MediaStatus.COMPLETADO
        assert dune.completed_at == date(2024, 3, 15)

        neuro = db.query(MediaItem).filter(MediaItem.title == "Neuromancer").one()
        assert neuro.status == MediaStatus.PENDIENTE
        assert neuro.rating is None       # 0 estrellas no es una nota

    def test_importa_un_export_de_storygraph(self, usuario, db):
        csv = (
            "Title,Authors,Star Rating,Read Status,Last Date Read\n"
            "Piranesi,Susanna Clarke,4.5,read,2024-01-10\n"
        )
        res = import_books_csv(db, csv, usuario.id)

        assert res["creados"] == 1
        libro = db.query(MediaItem).one()
        assert libro.rating == 9
        assert libro.status == MediaStatus.COMPLETADO

    def test_no_duplica_dentro_del_mismo_fichero(self, usuario, db):
        csv = (
            "Title,Author,Exclusive Shelf\n"
            "Dune,Frank Herbert,read\n"
            "Dune,Frank Herbert,read\n"
        )
        res = import_books_csv(db, csv, usuario.id)

        assert res["creados"] == 1
        assert res["duplicados"] == 1

    def test_no_duplica_entre_importaciones(self, usuario, db):
        csv = "Title,Author,Exclusive Shelf\nDune,Frank Herbert,read\n"
        import_books_csv(db, csv, usuario.id)
        res = import_books_csv(db, csv, usuario.id)

        assert res["creados"] == 0
        assert res["duplicados"] == 1

    def test_una_fila_sin_titulo_se_omite(self, usuario, db):
        csv = "Title,Author\n,Sin titulo\nDune,Frank Herbert\n"
        res = import_books_csv(db, csv, usuario.id)

        assert res["creados"] == 1
        assert res["omitidos"] == 1

    def test_el_mismo_titulo_de_otro_autor_no_es_duplicado(self, usuario, db):
        csv = (
            "Title,Author\n"
            "Ulises,James Joyce\n"
            "Ulises,Otro Autor\n"
        )
        res = import_books_csv(db, csv, usuario.id)
        assert res["creados"] == 2


class TestImportadorDeJuegos:
    def test_importa_un_csv_generico(self, usuario, db):
        csv = (
            "Title,Status,Hours,Rating,Developer,Year\n"
            "Hollow Knight,completed,40.5,5,Team Cherry,2017\n"
            "Hades,playing,12,4,Supergiant,2020\n"
        )
        res = import_games_csv(db, csv, usuario.id)

        assert res["creados"] == 2
        hk = db.query(MediaItem).filter(MediaItem.title == "Hollow Knight").one()
        assert hk.media_type == MediaType.VIDEOJUEGO
        assert hk.hltb_hours == 40.5
        assert hk.rating == 10
        assert hk.status == MediaStatus.COMPLETADO

        hades = db.query(MediaItem).filter(MediaItem.title == "Hades").one()
        assert hades.status == MediaStatus.EN_PROGRESO

    def test_traduce_los_estados_de_backloggd(self, usuario, db):
        csv = (
            "Name,Status\n"
            "Uno,backlog\n"
            "Dos,abandoned\n"
            "Tres,wishlist\n"
        )
        import_games_csv(db, csv, usuario.id)

        por_titulo = {i.title: i.status for i in db.query(MediaItem).all()}
        assert por_titulo["Uno"] == MediaStatus.PENDIENTE
        assert por_titulo["Dos"] == MediaStatus.ABANDONADO
        assert por_titulo["Tres"] == MediaStatus.WISHLIST

    def test_unas_horas_ilegibles_no_rompen_la_importacion(self, usuario, db):
        csv = "Title,Hours\nJuego raro,muchisimas\n"
        res = import_games_csv(db, csv, usuario.id)

        assert res["creados"] == 1
        assert db.query(MediaItem).one().hltb_hours is None
