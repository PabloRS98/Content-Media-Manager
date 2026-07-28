"""Regresiones de los bugs de lógica encontrados en la auditoría."""
import httpx
import pytest


class TestImportadorIMDB:
    """M2: la sesión usa autoflush=False, así que las filas pendientes no son
    visibles para la consulta de dedupe."""

    def test_mismo_id_repetido_en_el_csv_crea_una_sola_fila(self, client, db):
        from app.models import MediaItem

        csv_data = ("Const,Title,Title Type,Year\n"
                    "tt0000001,Dup Movie,movie,2001\n"
                    "tt0000001,Dup Movie,movie,2001\n")
        r = client.post("/importar", files={"archivo": ("r.csv", csv_data, "text/csv")})
        assert r.status_code == 200
        assert db.query(MediaItem).filter(MediaItem.external_id == "imdb:tt0000001").count() == 1

    def test_reimportar_el_mismo_fichero_no_duplica(self, client, db):
        from app.models import MediaItem

        csv_data = "Const,Title,Title Type,Year\ntt0000002,Duna,movie,2021\n"
        for _ in range(2):
            client.post("/importar", files={"archivo": ("r.csv", csv_data, "text/csv")})
        assert db.query(MediaItem).filter(MediaItem.external_id == "imdb:tt0000002").count() == 1

    def test_columna_vacia_no_gana_a_la_alternativa_poblada(self):
        """B2: `is not None` hacía que 'Title' vacío tapara a 'Título'."""
        from app.routers.imdb_import import _get

        assert _get({"Title": "", "Título": "Duna"}, "Title", "Título") == "Duna"
        assert _get({"Title": "Dune", "Título": "Duna"}, "Title", "Título") == "Dune"
        assert _get({"Otra": "x"}, "Title") == ""

    def test_csv_bilingue_se_importa(self, client, db):
        from app.models import MediaItem

        csv_data = ("Const,Title,Título,Title Type,Tipo de título,Year\n"
                    "tt0000003,,Duna,,película,2021\n")
        client.post("/importar", files={"archivo": ("r.csv", csv_data, "text/csv")})
        assert db.query(MediaItem).filter(MediaItem.title == "Duna").count() == 1


class TestEnriquecimientoDePortadas:
    """M3: `restantes` restaba el tamaño del lote, no las portadas encontradas."""

    def test_restantes_cuenta_los_que_siguen_sin_portada(self, app_env, db, monkeypatch):
        from app.models import MediaItem, MediaType
        from app.services import enrich

        monkeypatch.setattr(enrich, "_search_for", lambda item: [])
        monkeypatch.setattr(enrich, "SLEEP_BETWEEN", 0)
        for i in range(5):
            db.add(MediaItem(media_type=MediaType.LIBRO, title=f"Sin portada {i}"))
        db.commit()

        res = enrich.enrich_missing_covers(db)
        siguen_sin_portada = db.query(MediaItem).filter(MediaItem.cover_url.is_(None)).count()

        assert res["encontrados"] == 0
        assert res["restantes"] == siguen_sin_portada == 5

    def test_restantes_baja_cuando_se_encuentran_portadas(self, app_env, db, monkeypatch):
        from app.models import MediaItem, MediaType
        from app.services import enrich

        monkeypatch.setattr(enrich, "SLEEP_BETWEEN", 0)
        monkeypatch.setattr(enrich, "_search_for", lambda item: [
            {"title": item.title, "cover_url": "https://x.test/c.jpg"}])
        for i in range(3):
            db.add(MediaItem(media_type=MediaType.LIBRO, title=f"Libro {i}"))
        db.commit()

        res = enrich.enrich_missing_covers(db)
        assert res["encontrados"] == 3
        assert res["restantes"] == 0

    def test_respeta_el_presupuesto_de_tiempo(self, app_env, db, monkeypatch):
        """M9: el lote podía bloquear la petición durante minutos."""
        from app.models import MediaItem, MediaType
        from app.services import enrich

        monkeypatch.setattr(enrich, "SLEEP_BETWEEN", 0)
        monkeypatch.setattr(enrich, "TIME_BUDGET_SECONDS", 0)
        for i in range(10):
            db.add(MediaItem(media_type=MediaType.LIBRO, title=f"L{i}"))
        db.commit()

        res = enrich.enrich_missing_covers(db)
        assert res["procesados"] == 0  # se para antes de empezar


class TestGoogleBooks:
    """M4: un fallo de red se propagaba y devolvía un 500 al usuario."""

    def test_fallo_de_red_devuelve_lista_vacia(self, app_env, monkeypatch):
        import app.services.googlebooks as gb

        def falla(*a, **k):
            raise httpx.ConnectError("down", request=httpx.Request("GET", gb.SEARCH_URL))
        monkeypatch.setattr(gb.httpx, "get", falla)
        assert gb.search_books("dune") == []

    def test_error_http_devuelve_lista_vacia(self, app_env, monkeypatch):
        import app.services.googlebooks as gb

        req = httpx.Request("GET", gb.SEARCH_URL)
        monkeypatch.setattr(gb.httpx, "get", lambda *a, **k: httpx.Response(500, request=req))
        assert gb.search_books("dune") == []

    def test_buscar_no_devuelve_500_si_la_api_cae(self, client, app_env, monkeypatch):
        import app.services.googlebooks as gb
        import app.services.openlibrary as ol

        def falla(*a, **k):
            raise httpx.ConnectError("down", request=httpx.Request("GET", "https://x.test"))
        monkeypatch.setattr(gb.httpx, "get", falla)
        monkeypatch.setattr(ol.httpx, "get", falla)

        r = client.get("/buscar?tipo=libro&q=dune")
        assert r.status_code == 200
        assert "Sin resultados" in r.text


class TestCompletadoAlDarDeAlta:
    """M5: /agregar nunca ponía completed_at, dejando el ítem fuera de las stats."""

    def test_alta_como_completado_registra_la_fecha(self, client, db):
        from datetime import date

        from app.models import MediaItem

        client.post("/agregar", data={"media_type": "libro", "title": "Ya leido",
                                      "status": "completado"}, follow_redirects=False)
        item = db.query(MediaItem).filter(MediaItem.title == "Ya leido").one()
        assert item.completed_at == date.today()

    def test_alta_pendiente_no_registra_fecha(self, client, db):
        from app.models import MediaItem

        client.post("/agregar", data={"media_type": "libro", "title": "Por leer",
                                      "status": "pendiente"}, follow_redirects=False)
        assert db.query(MediaItem).filter(MediaItem.title == "Por leer").one().completed_at is None

    def test_aparece_en_las_estadisticas_del_ano(self, client, db):
        from datetime import date

        from sqlalchemy import extract

        from app.models import MediaItem

        client.post("/agregar", data={"media_type": "pelicula", "title": "Vista ya",
                                      "status": "completado"}, follow_redirects=False)
        completados_este_ano = (
            db.query(MediaItem)
            .filter(MediaItem.completed_at.isnot(None),
                    extract("year", MediaItem.completed_at) == date.today().year)
            .count()
        )
        assert completados_este_ano == 1
        assert client.get("/estadisticas").status_code == 200


class TestConsultasEstadisticas:
    """M6: cada `ep.item` disparaba su propio SELECT."""

    def test_sin_n_mas_1(self, client, db, app_env):
        from sqlalchemy import event

        from app.database import engine
        from app.models import Episode, MediaItem, MediaStatus, MediaType

        for i in range(30):
            it = MediaItem(media_type=MediaType.SERIE, title=f"S{i}", status=MediaStatus.COMPLETADO)
            it.episodes = [Episode(season_number=1, episode_number=j, watched=True)
                           for j in range(1, 11)]
            db.add(it)
        db.commit()

        consultas = []
        @event.listens_for(engine, "before_cursor_execute")
        def _contar(conn, cur, stmt, params, ctx, many):
            consultas.append(stmt)

        try:
            assert client.get("/estadisticas").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", _contar)

        # Antes eran 44 (una por serie). El número no debe crecer con el catálogo.
        assert len(consultas) < 20, "posible N+1: %d consultas" % len(consultas)


class TestFiltroDeGenero:
    """B1: '%' y '_' del usuario se interpretaban como comodines de LIKE."""

    @pytest.fixture()
    def con_generos(self, db):
        from app.models import MediaItem, MediaType

        db.add(MediaItem(media_type=MediaType.LIBRO, title="Fantasia", genres="Fantasía"))
        db.add(MediaItem(media_type=MediaType.LIBRO, title="Terror", genres="Terror"))
        db.commit()
        return {i.title: i.id for i in db.query(MediaItem).all()}

    def _tarjetas(self, html, ids):
        """Ítems realmente listados (el <select> de géneros los nombra todos)."""
        return {t for t, i in ids.items() if ('href="/item/%d"' % i) in html}

    def test_porcentaje_no_hace_de_comodin(self, client, con_generos):
        html = client.get("/catalogo?tipo=libro&genero=%25").text
        assert self._tarjetas(html, con_generos) == set()

    def test_guion_bajo_no_hace_de_comodin(self, client, con_generos):
        html = client.get("/catalogo?tipo=libro&genero=_").text
        assert self._tarjetas(html, con_generos) == set()

    def test_filtro_normal_sigue_funcionando(self, client, con_generos):
        html = client.get("/catalogo?tipo=libro&genero=Terror").text
        assert self._tarjetas(html, con_generos) == {"Terror"}


class TestValidacionDeRangos:
    """B5: rating y year se guardaban sin comprobar, y luego desaparecían."""

    def test_rating_fuera_de_rango_se_descarta(self, client, db):
        from app.models import MediaItem, MediaType

        db.add(MediaItem(media_type=MediaType.LIBRO, title="L"))
        db.commit()
        item_id = db.query(MediaItem).one().id

        client.post(f"/item/{item_id}/actualizar",
                    data={"title": "L", "status": "completado", "rating": "99"},
                    follow_redirects=False)
        db.expire_all()
        assert db.get(MediaItem, item_id).rating is None

    def test_rating_valido_se_guarda(self, client, db):
        from app.models import MediaItem, MediaType

        db.add(MediaItem(media_type=MediaType.LIBRO, title="L"))
        db.commit()
        item_id = db.query(MediaItem).one().id

        client.post(f"/item/{item_id}/actualizar",
                    data={"title": "L", "status": "completado", "rating": "8"},
                    follow_redirects=False)
        db.expire_all()
        assert db.get(MediaItem, item_id).rating == 8


class TestRotacionDeBackups:
    """B3: `existing[:-0]` es la lista vacía, así que keep=0 no rotaba nada."""

    def test_keep_cero_conserva_solo_el_ultimo(self, app_env, tmp_path, monkeypatch):
        from app.config import settings
        from app.services import scheduler

        backups = tmp_path / "backups"
        backups.mkdir()
        for n in ("media-20250101.db", "media-20250102.db"):
            (backups / n).touch()

        monkeypatch.setattr(settings, "backup_keep", 0)
        scheduler.backup_database(str(backups / "media-20250103.db"))

        assert sorted(p.name for p in backups.iterdir()) == ["media-20250103.db"]

    def test_keep_normal_rota(self, app_env, tmp_path, monkeypatch):
        from app.config import settings
        from app.services import scheduler

        backups = tmp_path / "backups"
        backups.mkdir()
        for d in range(1, 6):
            (backups / f"media-2025010{d}.db").touch()

        monkeypatch.setattr(settings, "backup_keep", 2)
        scheduler.backup_database(str(backups / "media-20250106.db"))

        assert sorted(p.name for p in backups.iterdir()) == ["media-20250105.db", "media-20250106.db"]


class TestContadorSinPortada:
    """B6: el contador ignoraba la pestaña de tipo activa."""

    def test_respeta_el_tipo_filtrado(self, client, db):
        from app.models import MediaItem, MediaType

        db.add(MediaItem(media_type=MediaType.LIBRO, title="Libro sin portada"))
        db.add(MediaItem(media_type=MediaType.PELICULA, title="Peli sin portada"))
        db.commit()

        # Solo debe contar el libro, no los 2 ítems del catálogo entero
        assert "Buscar portadas (1 pendientes)" in client.get("/catalogo?tipo=libro").text
        assert "Buscar portadas (1 pendientes)" in client.get("/catalogo?tipo=pelicula").text
