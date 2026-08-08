"""Tests de los hallazgos de robustez/infra que no tenían un xfail previo:
M6 (N+1 en estadísticas), M8 (subida de CSV sin límite), M9 (enriquecimiento
bloqueante), B7 (rutas relativas), B8 (scheduler duplicado), B9 y B10
(Dockerfile/compose).

M4, B3 y B4 sí tenían xfail y ya se movieron (sin marca) a
test_fallos_conocidos.py.
"""
from pathlib import Path

from sqlalchemy import event

from app.models import Episode, MediaItem, MediaStatus, MediaType
from app.routers.imdb_import import MAX_UPLOAD_BYTES


class TestTiempoTotalSinNMasUno:
    def test_las_sumas_por_tipo_son_correctas(self, client, crear_item, db):
        crear_item(title="Peli", media_type=MediaType.PELICULA,
                   status=MediaStatus.COMPLETADO, runtime_minutes=120)
        crear_item(title="Juego", media_type=MediaType.VIDEOJUEGO,
                   status=MediaStatus.COMPLETADO, hltb_hours=10)
        crear_item(title="Libro", media_type=MediaType.LIBRO,
                   status=MediaStatus.COMPLETADO, page_count=200)

        html = client.get("/estadisticas").text
        # 120 + 10*60 + 200*1.5 = 1020 min = 17h
        assert '<p class="big-number">17<small> h</small></p>' in html

    def test_el_joinedload_evita_una_consulta_por_episodio(self, crear_serie, db):
        """Test unitario, no vía HTTP: /estadisticas dispara muchas otras
        consultas que también mencionan `media_items` en su SQL (por_tipo,
        por_estado, mejores...), así que contar "SELECTs con media_items" en
        toda la respuesta no aislaría el patrón N+1 real. Se reproduce
        directamente la consulta de catalog.py: sin el joinedload, acceder a
        `ep.item` dispara un SELECT por episodio (5 aquí); con él, cero."""
        from sqlalchemy.orm import joinedload

        crear_serie(temporadas=1, por_temporada=5, vistos=5)
        db.expire_all()

        consultas = []

        def _contador(conn, cursor, statement, *a, **k):
            consultas.append(statement)

        event.listen(db.get_bind(), "before_cursor_execute", _contador)
        try:
            episodios = (
                db.query(Episode).join(MediaItem)
                .options(joinedload(Episode.item))
                .filter(Episode.watched.is_(True))
                .all()
            )
            for ep in episodios:
                _ = ep.item.runtime_minutes  # no debe disparar una consulta nueva
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", _contador)

        assert len(episodios) == 5
        assert len(consultas) == 1


class TestLimiteDeSubida:
    def test_un_csv_dentro_del_limite_se_importa(self, client, db):
        csv = "Const,Title,Title Type,Year\ntt1,Peli,movie,2020\n"
        r = client.post("/importar", files={"archivo": ("r.csv", csv, "text/csv")})
        assert r.status_code == 200
        assert db.query(MediaItem).count() == 1

    def test_un_fichero_que_supera_el_limite_se_rechaza_con_413(self, client, db):
        demasiado_grande = "x" * (MAX_UPLOAD_BYTES + 1)
        r = client.post("/importar", files={"archivo": ("enorme.csv", demasiado_grande, "text/csv")})
        assert r.status_code == 413
        assert db.query(MediaItem).count() == 0


class TestEnriquecimientoEnSegundoPlano:
    def test_la_peticion_no_espera_a_que_termine_el_lote(self, client, crear_item, monkeypatch):
        """El endpoint debe devolver de inmediato (lanza el trabajo con
        BackgroundTasks), no bloquear hasta que enrich_missing_covers
        termine."""
        from app.services import enrich

        def _lento(db):
            raise AssertionError("no debería ejecutarse de forma síncrona en la request")

        monkeypatch.setattr(enrich, "enrich_missing_covers", _lento)
        crear_item(title="Sin portada", cover_url=None)

        r = client.post("/catalogo/completar-portadas", follow_redirects=False)
        assert r.status_code == 303

    def test_no_se_lanzan_dos_lotes_en_paralelo(self, client, monkeypatch):
        from app.services import enrich

        monkeypatch.setattr(enrich, "_estado_lote", {"corriendo": True, "resultado": None})
        r = client.post("/catalogo/completar-portadas", follow_redirects=False)

        assert r.status_code == 303
        # El toast avisa de que ya hay uno en marcha (comprobado vía la cookie
        # flash, que redirect_flash siempre deja).
        assert "marcha" in r.cookies.get("flash", "")

    def test_enrich_missing_covers_en_segundo_plano_usa_su_propia_sesion(self, usuario, db):
        """Simula lo que hace BackgroundTasks: pasa una factoría de sesión, no
        la `db` de la request (que ya estaría cerrada para cuando esto corra)."""
        from app.services import enrich

        enrich._estado_lote["corriendo"] = False
        enrich._estado_lote["resultado"] = None

        db.add(MediaItem(usuario_id=usuario.id, media_type=MediaType.LIBRO, title="x", cover_url="ya tiene"))
        db.commit()

        enrich.enrich_missing_covers_en_segundo_plano(lambda: db)

        assert enrich.estado_actual()["corriendo"] is False
        assert enrich.estado_actual()["resultado"] is not None


class TestRutasAbsolutas:
    def test_static_y_templates_se_resuelven_desde_el_paquete(self):
        """Antes eran "app/static" y "app/templates": solo funcionaban si el
        proceso arrancaba desde la raíz del repo. Deben ser absolutas."""
        import app.main as main_mod
        import app.templating as templating_mod

        assert Path(templating_mod.templates.env.loader.searchpath[0]).is_absolute()

        # getattr y no r.path: desde FastAPI 0.141 `app.routes` incluye objetos
        # `_IncludedRouter` que no tienen ese atributo.
        static_mount = next(
            r for r in main_mod.app.routes if getattr(r, "path", None) == "/static"
        )
        assert Path(static_mount.app.directory).is_absolute()


class TestSchedulerConfigurable:
    def test_enable_scheduler_false_no_arranca_el_scheduler(self):
        from fastapi.testclient import TestClient

        from app import config
        from app.main import app

        original = config.settings.enable_scheduler
        config.settings.enable_scheduler = False
        try:
            with TestClient(app) as c:
                assert c.app.state.scheduler is None
        finally:
            config.settings.enable_scheduler = original


class TestImagenYCompose:
    def test_el_dockerfile_arranca_sin_privilegios_via_entrypoint(self):
        """No hay un "USER appuser" a secas: un volumen ya existente (creado
        bajo la imagen anterior, que corría como root) tendría sus ficheros a
        nombre de root, y el proceso non-root no podría ni abrir media.db. El
        entrypoint arregla el dueño de /data en cada arranque y luego baja
        privilegios con gosu -- necesario también para actualizaciones, no
        solo instalaciones nuevas."""
        contenido = (Path(__file__).parent.parent / "Dockerfile").read_text()
        assert "gosu" in contenido
        assert "docker-entrypoint.sh" in contenido
        assert "USER appuser" not in contenido

        entrypoint = (Path(__file__).parent.parent / "docker-entrypoint.sh").read_text()
        assert "chown" in entrypoint and "/data" in entrypoint
        assert "gosu appuser" in entrypoint

    def test_el_dockerfile_declara_healthcheck(self):
        contenido = (Path(__file__).parent.parent / "Dockerfile").read_text()
        assert "HEALTHCHECK" in contenido

    def test_el_compose_no_exige_env_de_forma_obligatoria(self):
        contenido = (Path(__file__).parent.parent / "docker-compose.yml").read_text()
        assert "required: false" in contenido


class TestBackupKeepCero:
    def test_con_cero_se_conserva_al_menos_el_ultimo(self, tmp_path, monkeypatch):
        from app.services import scheduler

        backups = tmp_path / "backups"
        backups.mkdir()
        origen = tmp_path / "media.db"
        import sqlite3
        sqlite3.connect(origen).close()
        for dia in ("20200101", "20200102", "20200103"):
            (backups / f"media-{dia}.db").write_bytes(b"")

        monkeypatch.setattr(scheduler.settings, "db_path", str(origen))
        monkeypatch.setattr(scheduler.settings, "backup_keep", 0)
        scheduler.backup_database()

        # backup_database() crea primero el backup de HOY antes de rotar, así
        # que no se compara contra una fecha fija: con backup_keep=0 debe
        # quedar solo 1 fichero, y no debe ser ninguno de los 3 antiguos.
        restantes = sorted(p.name for p in backups.glob("media-*.db"))
        assert len(restantes) == 1
        assert restantes[0] not in {"media-20200101.db", "media-20200102.db", "media-20200103.db"}
