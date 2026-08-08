"""Panel `/estado`.

Existe para convertir "no me llegan los avisos" en un diagnóstico de un
vistazo: ese síntoma puede ser TMDB caída, clave inválida, token de Telegram
revocado, chat_id mal, el job caído, o que no haya episodios nuevos.
"""
from datetime import UTC, datetime

import pytest

from app.models import Episode, MediaItem, MediaStatus, MediaType
from app.services import scheduler


@pytest.fixture(autouse=True)
def sin_ejecuciones(monkeypatch):
    """`JOB_STATUS` es un global del módulo: se aísla por test."""
    monkeypatch.setattr(scheduler, "JOB_STATUS", {})
    from app.routers import estado as estado_router
    monkeypatch.setattr(estado_router, "JOB_STATUS", scheduler.JOB_STATUS)


class TestLosJobs:
    def test_estado_muestra_los_jobs(self, client):
        r = client.get("/estado")
        assert r.status_code == 200
        assert "Avisos" in r.text
        assert "Backup" in r.text

    def test_sin_ejecuciones_lo_dice(self, client):
        assert "Sin ejecuciones desde el arranque" in client.get("/estado").text

    def test_una_ejecucion_correcta_se_ve(self, client, monkeypatch):
        monkeypatch.setitem(scheduler.JOB_STATUS, "media_alerts", {
            "cuando": datetime(2026, 8, 7, 9, 0, tzinfo=UTC).replace(tzinfo=None),
            "ok": True, "detalle": "3 episodios, 1 estrenos",
        })
        html = client.get("/estado").text
        assert "07/08/2026 09:00" in html
        assert "3 episodios, 1 estrenos" in html

    def test_un_job_que_fallo_se_ve_como_fallo(self, client, monkeypatch):
        """Es el caso entero por el que existe el panel."""
        monkeypatch.setitem(scheduler.JOB_STATUS, "media_alerts", {
            "cuando": datetime.now(UTC).replace(tzinfo=None),
            "ok": False, "detalle": "ConnectTimeout",
        })
        html = client.get("/estado").text
        assert "Falló" in html
        assert "ConnectTimeout" in html


class TestSecretos:
    def test_estado_no_filtra_las_api_keys(self, client, monkeypatch):
        """La presencia sí, el valor nunca: esta página es justo la que uno
        acaba enseñando en una captura para pedir ayuda."""
        from app.config import settings

        secretos = {
            "tmdb_api_key": "TMDB-SUPER-SECRETA",
            "rawg_api_key": "RAWG-SUPER-SECRETA",
            "google_books_api_key": "GOOGLE-SUPER-SECRETA",
            "telegram_bot_token": "TOKEN-SUPER-SECRETO",
            "telegram_chat_id": "12345",
            "auth_password": "CONTRASENA-SUPER-SECRETA",
        }
        for clave, valor in secretos.items():
            monkeypatch.setattr(settings, clave, valor)

        html = client.get("/estado").text
        for valor in secretos.values():
            if valor != "12345":
                assert valor not in html, valor

    def test_estado_dice_que_fuentes_estan_configuradas(self, client, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "tmdb_api_key", "algo")
        monkeypatch.setattr(settings, "rawg_api_key", "")
        html = client.get("/estado").text
        assert "TMDB" in html and "RAWG" in html
        assert "Sin clave" in html  # la de RAWG

    def test_las_fuentes_sin_clave_no_salen_como_rotas(self, client):
        """Open Library e iTunes no necesitan clave: marcarlas como "sin
        configurar" mandaría a buscar una clave que no existe."""
        html = client.get("/estado").text
        assert "Open Library" in html and "iTunes" in html
        assert "Sin clave necesaria" in html


class TestHuecosEnLosDatos:
    def test_cuenta_los_items_sin_portada(self, client, db, crear_item):
        crear_item(title="Sin portada", cover_url=None)
        crear_item(title="Con portada", cover_url="https://ejemplo.test/a.jpg")
        assert "Ítems sin portada" in client.get("/estado").text

    def test_cuenta_las_series_sin_episodios(self, usuario, client, db):
        db.add(MediaItem(usuario_id=usuario.id, title="Serie vacía", media_type=MediaType.SERIE,
                         status=MediaStatus.PENDIENTE))
        con = MediaItem(usuario_id=usuario.id, title="Serie con episodios", media_type=MediaType.SERIE,
                        status=MediaStatus.EN_PROGRESO)
        con.episodes.append(Episode(season_number=1, episode_number=1))
        db.add(con)
        db.commit()
        assert "Series o podcasts sin episodios" in client.get("/estado").text

    def test_cuenta_los_items_con_fuente_pero_sin_id(self, client, db, crear_item):
        """Un ítem así no se puede enriquecer nunca: metadata.enrich_item lo
        ignora en silencio."""
        crear_item(title="Huérfano", external_source="tmdb", external_id=None)
        assert "Ítems con fuente pero sin identificador" in client.get("/estado").text


def test_estado_dice_si_el_esquema_esta_al_dia(client):
    html = client.get("/estado").text
    assert "Esquema" in html
    assert "Al día" in html


def test_estado_pide_autenticacion_como_el_resto(client):
    """No es una ruta pública: `/salud` sí lo es, esta no."""
    from app.routers import estado as estado_router

    dependencias = [d.dependency.__name__ for d in estado_router.router.dependencies]
    assert "verify_auth" in dependencias
