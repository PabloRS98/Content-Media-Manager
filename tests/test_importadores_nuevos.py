"""[N6] Tres sitios más de donde traerse el catálogo.

Letterboxd y Trakt exportan CSV; Steam no exporta nada, pero tiene una API
pública con la que se puede leer la biblioteca. Los tres siguen el mismo
criterio que los tres importadores que ya había: tolerantes con los nombres de
columna, deduplicando contra lo que ya tienes, y sin tocar nada de otra cuenta.
"""
from app.models import MediaItem, MediaStatus, MediaType
from app.services import imports, steam

LETTERBOXD = (
    "Date,Name,Year,Letterboxd URI,Rating\n"
    "2026-01-05,Blade Runner,1982,https://boxd.it/aaa,4.5\n"
    "2026-02-01,Arrival,2016,https://boxd.it/bbb,5\n"
)

TRAKT = (
    "watched_at,type,title,year,imdb_id,season,episode\n"
    "2026-01-05T20:00:00Z,movie,Dune,2021,tt1160419,,\n"
    "2026-01-06T21:00:00Z,episode,Severance,2022,tt11280740,1,3\n"
    "2026-01-06T22:00:00Z,episode,Severance,2022,tt11280740,1,4\n"
)


class TestLetterboxd:
    def test_importa_peliculas_vistas(self, db, usuario):
        resultado = imports.import_letterboxd_csv(db, LETTERBOXD, usuario.id)

        assert resultado["creados"] == 2
        pelis = db.query(MediaItem).filter(MediaItem.media_type == MediaType.PELICULA).all()
        assert {p.title for p in pelis} == {"Blade Runner", "Arrival"}
        assert all(p.status == MediaStatus.COMPLETADO for p in pelis)

    def test_la_nota_pasa_de_cinco_a_diez(self, db, usuario):
        """Letterboxd puntúa sobre 5 con medias estrellas; aquí es sobre 10."""
        imports.import_letterboxd_csv(db, LETTERBOXD, usuario.id)

        notas = {i.title: i.rating for i in db.query(MediaItem).all()}
        assert notas == {"Blade Runner": 9, "Arrival": 10}

    def test_la_watchlist_entra_como_pendiente(self, db, usuario):
        """El fichero de watchlist tiene las MISMAS columnas que el de vistas,
        así que no hay forma de distinguirlos leyéndolos: lo dice quien importa."""
        watchlist = "Date,Name,Year,Letterboxd URI\n2026-01-05,Dune,2021,https://boxd.it/ccc\n"

        imports.import_letterboxd_csv(db, watchlist, usuario.id, pendientes=True)

        assert db.query(MediaItem).one().status == MediaStatus.PENDIENTE

    def test_no_duplica_lo_que_ya_tienes(self, db, usuario, crear_item):
        crear_item(title="Blade Runner", media_type=MediaType.PELICULA)

        resultado = imports.import_letterboxd_csv(db, LETTERBOXD, usuario.id)

        assert resultado["creados"] == 1
        assert resultado["duplicados"] == 1

    def test_una_fila_sin_titulo_se_omite_sin_romper(self, db, usuario):
        csv = "Date,Name,Year\n2026-01-05,,1982\n2026-01-06,Alien,1979\n"

        resultado = imports.import_letterboxd_csv(db, csv, usuario.id)

        assert resultado["creados"] == 1
        assert resultado["omitidos"] == 1


class TestTrakt:
    def test_separa_peliculas_de_series(self, db, usuario):
        imports.import_trakt_csv(db, TRAKT, usuario.id)

        por_tipo = {i.title: i.media_type for i in db.query(MediaItem).all()}
        assert por_tipo == {"Dune": MediaType.PELICULA, "Severance": MediaType.SERIE}

    def test_los_episodios_de_una_serie_no_crean_un_item_cada_uno(self, db, usuario):
        """Dos episodios de la misma serie son una serie, no dos entradas."""
        imports.import_trakt_csv(db, TRAKT, usuario.id)

        assert db.query(MediaItem).filter(MediaItem.title == "Severance").count() == 1

    def test_una_serie_con_episodios_vistos_queda_en_progreso(self, db, usuario):
        imports.import_trakt_csv(db, TRAKT, usuario.id)

        serie = db.query(MediaItem).filter(MediaItem.title == "Severance").one()
        assert serie.status == MediaStatus.EN_PROGRESO
        # Y se guarda hasta dónde llegaste, para poder marcarlo cuando TMDB
        # traiga la lista de episodios.
        assert "S01E04" in (serie.notes or "")

    def test_guarda_el_id_de_imdb_para_poder_enriquecer(self, db, usuario):
        imports.import_trakt_csv(db, TRAKT, usuario.id)

        peli = db.query(MediaItem).filter(MediaItem.title == "Dune").one()
        assert peli.external_id == "tt1160419"
        assert peli.external_source == "imdb"

    def test_no_duplica_contra_lo_que_ya_tienes(self, db, usuario, crear_item):
        crear_item(title="Dune", media_type=MediaType.PELICULA)

        resultado = imports.import_trakt_csv(db, TRAKT, usuario.id)

        assert resultado["duplicados"] == 1
        assert db.query(MediaItem).filter(MediaItem.title == "Dune").count() == 1


class TestSteam:
    JUEGOS = {
        "response": {
            "games": [
                {"appid": 367520, "name": "Hollow Knight", "playtime_forever": 1800},
                {"appid": 620, "name": "Portal 2", "playtime_forever": 0},
            ]
        }
    }

    def test_trae_la_biblioteca(self, db, usuario, monkeypatch):
        monkeypatch.setattr(steam, "_pedir_biblioteca", lambda clave, sid: self.JUEGOS)

        resultado = steam.importar_biblioteca(db, usuario.id, "clave", "76561198000000000")

        assert resultado["creados"] == 2
        juegos = db.query(MediaItem).all()
        assert {j.title for j in juegos} == {"Hollow Knight", "Portal 2"}

    def test_las_horas_jugadas_llegan_como_horas(self, db, usuario, monkeypatch):
        """Steam las da en minutos."""
        monkeypatch.setattr(steam, "_pedir_biblioteca", lambda clave, sid: self.JUEGOS)

        steam.importar_biblioteca(db, usuario.id, "clave", "76561198000000000")

        hk = db.query(MediaItem).filter(MediaItem.title == "Hollow Knight").one()
        assert hk.hltb_hours == 30

    def test_lo_no_jugado_queda_pendiente(self, db, usuario, monkeypatch):
        monkeypatch.setattr(steam, "_pedir_biblioteca", lambda clave, sid: self.JUEGOS)

        steam.importar_biblioteca(db, usuario.id, "clave", "76561198000000000")

        estados = {j.title: j.status for j in db.query(MediaItem).all()}
        assert estados == {
            "Hollow Knight": MediaStatus.EN_PROGRESO,
            "Portal 2": MediaStatus.PENDIENTE,
        }

    def test_todo_queda_marcado_como_de_steam(self, db, usuario, monkeypatch):
        """Es lo que hace que "¿qué tengo pendiente en Steam?" funcione [N7]."""
        monkeypatch.setattr(steam, "_pedir_biblioteca", lambda clave, sid: self.JUEGOS)

        steam.importar_biblioteca(db, usuario.id, "clave", "76561198000000000")

        assert {j.plataforma for j in db.query(MediaItem).all()} == {"Steam"}

    def test_sin_clave_no_se_intenta_siquiera(self, db, usuario):
        resultado = steam.importar_biblioteca(db, usuario.id, "", "76561198000000000")

        assert resultado["error"]
        assert db.query(MediaItem).count() == 0

    def test_si_steam_falla_lo_dice_en_vez_de_reventar(self, db, usuario, monkeypatch):
        def _falla(clave, sid):
            raise RuntimeError("Steam caída")

        monkeypatch.setattr(steam, "_pedir_biblioteca", _falla)

        resultado = steam.importar_biblioteca(db, usuario.id, "clave", "76561198000000000")

        assert resultado["error"]
        assert db.query(MediaItem).count() == 0

    def test_no_duplica_al_reimportar(self, db, usuario, monkeypatch):
        monkeypatch.setattr(steam, "_pedir_biblioteca", lambda clave, sid: self.JUEGOS)
        steam.importar_biblioteca(db, usuario.id, "clave", "76561198000000000")

        resultado = steam.importar_biblioteca(db, usuario.id, "clave", "76561198000000000")

        assert resultado["creados"] == 0
        assert resultado["duplicados"] == 2
        assert db.query(MediaItem).count() == 2
