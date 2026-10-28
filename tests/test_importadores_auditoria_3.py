"""Importadores y números de entrada (tercera auditoría, bloque 5)."""
from datetime import date

import pytest

from app import numeros
from app.models import MediaItem, MediaStatus, MediaType
from app.services import imports, steam


def _items(db, tipo=None):
    q = db.query(MediaItem)
    if tipo:
        q = q.filter(MediaItem.media_type == tipo)
    return q.order_by(MediaItem.id).all()


class TestNumeros:
    """R3-B1 / R3-B2."""

    @pytest.mark.parametrize("valor", ["inf", "-inf", "nan", "1e999", "9" * 40, "", "x", None])
    def test_lo_no_finito_o_absurdo_es_none(self, valor):
        assert numeros.decimal(valor) is None
        assert numeros.entero(valor, numeros.AÑO) is None

    def test_acepta_coma_decimal_y_trunca_enteros(self):
        assert numeros.decimal("2,5") == 2.5
        assert numeros.entero("300.7") == 300

    def test_la_nota_es_de_1_a_10(self):
        assert numeros.nota("99") is None
        assert numeros.nota("0") is None
        assert numeros.nota("8") == 8

    def test_un_post_con_inf_no_rompe_la_ficha_ni_la_portada(self, client, crear_item):
        item = crear_item(title="Libro", media_type=MediaType.LIBRO)
        r = client.post("/item/%d/actualizar" % item.id, data={
            "title": "Libro", "status": "en_progreso", "year": "9" * 30,
            "progress_current": "inf", "progress_total": "1e999", "hltb_hours": "nan",
        }, follow_redirects=False)
        assert r.status_code == 303
        for url in ("/", "/catalogo", "/item/%d" % item.id, "/estadisticas"):
            assert client.get(url).status_code == 200, url


class TestDuplicados:
    """R3-M14 y R3-M15."""

    def test_un_remake_no_es_un_duplicado(self, db, usuario, crear_item):
        crear_item(title="Dune", media_type=MediaType.PELICULA, year=1984)
        res = imports.import_letterboxd_csv(db, "Name,Year,Rating\nDune,2021,4.5\n", usuario.id)
        assert res["creados"] == 1
        assert sorted(i.year for i in _items(db, MediaType.PELICULA)) == [1984, 2021]

    def test_el_mismo_titulo_con_tildes_o_mayusculas_si(self, db, usuario, crear_item):
        crear_item(title="Amélie", media_type=MediaType.PELICULA, year=2001,
                   status=MediaStatus.COMPLETADO, completed_at=date(2020, 1, 1))
        res = imports.import_letterboxd_csv(db, "Name,Year\nAMELIE,2001\n", usuario.id)
        assert res == {"creados": 0, "duplicados": 1, "actualizados": 0, "omitidos": 0}

    def test_imdb_y_trakt_reconocen_la_misma_pelicula(self, db, usuario):
        imdb = ("Const,Your Rating,Date Rated,Title,Title Type,Year\n"
                "tt1160419,8,2024-01-02,Dune,movie,2021\n")
        trakt = "type,title,year,imdb_id,watched_at\nmovie,Dune: Part One,2021,tt1160419,2024-01-01\n"
        imports.import_imdb_csv(db, imdb, usuario.id)
        res = imports.import_trakt_csv(db, trakt, usuario.id)
        assert res["creados"] == 0 and res["duplicados"] == 1
        assert len(_items(db)) == 1

    def test_dos_ids_de_imdb_distintos_con_el_mismo_titulo_son_dos(self, db, usuario):
        csv = ("Const,Title,Title Type,Year\n"
               "tt0000001,Hamlet,movie,\n"
               "tt0000002,Hamlet,movie,\n")
        assert imports.import_imdb_csv(db, csv, usuario.id)["creados"] == 2

    def test_alta_manual_avisa_del_posible_duplicado(self, client, crear_item):
        crear_item(title="Dune", media_type=MediaType.PELICULA, year=2021)
        r = client.post("/agregar", data={"media_type": "pelicula", "title": "Dune", "year": "2021"},
                        follow_redirects=False)
        assert r.status_code == 303
        assert "ya%20ten" in r.headers["set-cookie"] or "ya ten" in r.headers["set-cookie"]


class TestCompletarAlExistente:
    """R3-M16."""

    def test_ratings_despues_de_watched_trae_las_notas(self, db, usuario):
        imports.import_letterboxd_csv(db, "Name,Year,Watched Date\nAlien,1979,2024-02-03\n", usuario.id)
        res = imports.import_letterboxd_csv(db, "Name,Year,Rating\nAlien,1979,4.5\n", usuario.id)
        assert res["actualizados"] == 1
        (alien,) = _items(db)
        assert alien.rating == 9
        assert alien.completed_at == date(2024, 2, 3)

    def test_no_pisa_lo_escrito_a_mano_ni_retrocede_el_estado(self, db, usuario, crear_item):
        crear_item(title="Alien", media_type=MediaType.PELICULA, year=1979, rating=6,
                   status=MediaStatus.COMPLETADO, completed_at=date(2020, 1, 1))
        imports.import_letterboxd_csv(db, "Name,Year,Rating\nAlien,1979,5\n", usuario.id, pendientes=True)
        (alien,) = _items(db)
        assert alien.rating == 6
        assert alien.status == MediaStatus.COMPLETADO

    def test_lo_pendiente_pasa_a_visto(self, db, usuario, crear_item):
        crear_item(title="Alien", media_type=MediaType.PELICULA, status=MediaStatus.PENDIENTE)
        imports.import_letterboxd_csv(db, "Name,Year,Watched Date\nAlien,1979,2024-02-03\n", usuario.id)
        (alien,) = _items(db)
        assert alien.status == MediaStatus.COMPLETADO
        assert alien.completed_at == date(2024, 2, 3)
        assert alien.year == 1979

    def test_reimportar_steam_pone_al_dia_las_horas(self, db, usuario, monkeypatch):
        juegos = [{"appid": 620, "name": "Portal 2", "playtime_forever": 60}]
        monkeypatch.setattr(steam, "_pedir_biblioteca",
                            lambda clave, sid: {"response": {"games": juegos}})
        steam.importar_biblioteca(db, usuario.id, "k", "1")
        juegos[0]["playtime_forever"] = 600
        res = steam.importar_biblioteca(db, usuario.id, "k", "1")
        assert res["actualizados"] == 1
        (portal,) = _items(db)
        assert portal.hltb_hours == 10.0


class TestFechas:
    """R3-M17 y R3-B12."""

    def test_storygraph_rango_de_fechas(self, db, usuario):
        csv = "Title,Authors,Read Status,Dates Read\nDune,Frank Herbert,read,2022/01/05-2022/02/10\n"
        imports.import_books_csv(db, csv, usuario.id)
        (dune,) = _items(db)
        assert dune.started_at == date(2022, 1, 5)
        assert dune.completed_at == date(2022, 2, 10)

    def test_goodreads_leido_sin_fecha_usa_la_de_alta(self, db, usuario):
        csv = "Title,Author,Exclusive Shelf,Date Read,Date Added\nDune,Frank Herbert,read,,2019/05/06\n"
        imports.import_books_csv(db, csv, usuario.id)
        assert _items(db)[0].completed_at == date(2019, 5, 6)

    def test_letterboxd_diary_usa_watched_date(self, db, usuario):
        csv = "Date,Name,Year,Watched Date\n2024-03-10,Alien,1979,2024-03-01\n"
        imports.import_letterboxd_csv(db, csv, usuario.id)
        assert _items(db)[0].completed_at == date(2024, 3, 1)


class TestFicherosRaros:
    """R3-B2 y R3-B13."""

    def test_csv_con_punto_y_coma(self, db, usuario):
        res = imports.import_letterboxd_csv(db, "Name;Year;Rating\nAlien;1979;4\n", usuario.id)
        assert res["creados"] == 1
        assert _items(db)[0].year == 1979

    def test_un_campo_enorme_no_da_500(self, client):
        csv = "Title,Author\n" + "x" * 200_000 + ",Alguien\n"
        r = client.post("/importar/libros", files={"archivo": ("l.csv", csv, "text/csv")},
                        follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/importar"

    def test_una_nota_de_99_en_imdb_no_se_guarda(self, db, usuario):
        csv = "Const,Your Rating,Title,Title Type,Year\ntt0000009,99,Peli,movie,2000\n"
        imports.import_imdb_csv(db, csv, usuario.id)
        assert _items(db)[0].rating is None

    def test_el_resultado_dice_cuantos_se_completaron(self, client, crear_item):
        crear_item(title="Alien", media_type=MediaType.PELICULA, year=1979)
        r = client.post("/importar/letterboxd",
                        files={"archivo": ("r.csv", "Name,Year,Rating\nAlien,1979,4\n", "text/csv")})
        assert r.status_code == 200
        assert "completados con datos nuevos" in r.text


def test_alta_en_progreso_fija_la_fecha_de_comienzo(client, db):
    """R3-M9."""
    client.post("/agregar", data={"media_type": "libro", "title": "Nuevo", "status": "en_progreso"})
    (item,) = _items(db)
    assert item.started_at == date.today()
