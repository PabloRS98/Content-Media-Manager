"""Seguimiento de series y podcasts (tercera auditoría, bloque 3).

- R3-A3: una serie al día pasaba a «completada» y el job no la volvía a mirar.
- R3-M12: los episodios ya guardados no recibían la fecha que TMDB fija después.
- R3-M13: los podcasts no se refrescaban, y numerar por posición los descolocaba.
- R3-B11: Telegram avisaba de lo ya visto, un mensaje por episodio, sin 429.
"""
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.models import Episode, MediaItem, MediaStatus, MediaType, Usuario
from app.services import itunes, metadata, scheduler, telegram, tmdb

HOY = date.today()
AYER = HOY - timedelta(days=1)


@pytest.fixture
def fabrica(db):
    return sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)


@pytest.fixture
def envios(monkeypatch):
    enviados: list[str] = []

    def _enviar(texto):
        enviados.append(texto)
        return True

    monkeypatch.setattr(scheduler.telegram, "send_message", _enviar)
    return enviados


def _serie(db, usuario, episodios, status=MediaStatus.EN_PROGRESO, completed_at=None,
           tipo=MediaType.SERIE, fuente="tmdb", titulo="Serie"):
    item = MediaItem(
        usuario_id=usuario.id, title=titulo, media_type=tipo, status=status,
        external_source=fuente, external_id="1", completed_at=completed_at,
    )
    for ep in episodios:
        item.episodes.append(ep)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _ep_tmdb(sn, en, air=None, name=None):
    return {"season_number": sn, "episode_number": en, "name": name, "overview": "",
            "air_date": air.isoformat() if air else None, "runtime_minutes": None}


class TestEpisodiosExistentesSeActualizan:
    """R3-M12."""

    def test_la_fecha_que_fija_tmdb_despues_llega(self, db, usuario, monkeypatch):
        item = _serie(db, usuario, [Episode(season_number=1, episode_number=1, name="Episodio 1")])
        futura = HOY + timedelta(days=10)
        monkeypatch.setattr(tmdb, "fetch_tv_episodes",
                            lambda ext, ts: [_ep_tmdb(1, 1, futura, "El piloto")])

        assert metadata.load_episodes(db, item, [1]) == 0
        ep = item.episodes[0]
        assert ep.air_date == futura
        assert ep.name == "El piloto"
        assert ep.notified is False

    def test_un_retraso_a_futuro_vuelve_a_dejar_el_aviso_pendiente(self, db, usuario, monkeypatch):
        item = _serie(db, usuario, [Episode(season_number=1, episode_number=1,
                                            air_date=AYER, notified=True)])
        monkeypatch.setattr(tmdb, "fetch_tv_episodes",
                            lambda ext, ts: [_ep_tmdb(1, 1, HOY + timedelta(days=7))])
        metadata.load_episodes(db, item, [1])
        assert item.episodes[0].notified is False

    def test_un_dato_vacio_no_borra_lo_que_habia(self, db, usuario, monkeypatch):
        item = _serie(db, usuario, [Episode(season_number=1, episode_number=1,
                                            name="Piloto", air_date=AYER)])
        monkeypatch.setattr(tmdb, "fetch_tv_episodes", lambda ext, ts: [_ep_tmdb(1, 1)])
        metadata.load_episodes(db, item, [1])
        assert item.episodes[0].name == "Piloto"
        assert item.episodes[0].air_date == AYER

    def test_en_un_refresco_lo_recien_emitido_se_avisa(self, db, usuario, monkeypatch):
        """TMDB a veces da de alta el episodio el mismo día que sale."""
        item = _serie(db, usuario, [Episode(season_number=1, episode_number=1, air_date=AYER - timedelta(days=7))])
        monkeypatch.setattr(tmdb, "fetch_tv_episodes", lambda ext, ts: [_ep_tmdb(1, 2, AYER)])
        metadata.load_episodes(db, item, [1])
        nuevo = next(e for e in item.episodes if e.episode_number == 2)
        assert nuevo.notified is False

    def test_en_la_primera_carga_lo_emitido_no_se_avisa(self, db, usuario, monkeypatch):
        item = _serie(db, usuario, [])
        monkeypatch.setattr(tmdb, "fetch_tv_episodes", lambda ext, ts: [_ep_tmdb(1, 1, AYER)])
        metadata.load_episodes(db, item, [1])
        assert item.episodes[0].notified is True


class TestSeriesCompletadasSeReabren:
    """R3-A3."""

    def test_un_episodio_emitido_tras_completarla_la_reabre(self, db, usuario):
        hace_un_mes = HOY - timedelta(days=30)
        item = _serie(db, usuario, [
            Episode(season_number=1, episode_number=1, air_date=hace_un_mes - timedelta(days=7), watched=True),
            Episode(season_number=1, episode_number=2, air_date=AYER),
        ], status=MediaStatus.COMPLETADO, completed_at=hace_un_mes)

        assert scheduler.reabrir_completadas(db) == 1
        db.refresh(item)
        assert item.status == MediaStatus.EN_PROGRESO

    def test_lo_anunciado_y_sin_emitir_no_la_reabre(self, db, usuario):
        item = _serie(db, usuario, [
            Episode(season_number=1, episode_number=1, air_date=HOY - timedelta(days=60), watched=True),
            Episode(season_number=2, episode_number=1, air_date=HOY + timedelta(days=30)),
        ], status=MediaStatus.COMPLETADO, completed_at=HOY - timedelta(days=50))

        assert scheduler.reabrir_completadas(db) == 0
        db.refresh(item)
        assert item.status == MediaStatus.COMPLETADO

    def test_una_completada_a_mano_con_episodios_viejos_sin_marcar_no_se_toca(self, db, usuario):
        """Datos de antes de R3-M8: completada con episodios sin marcar."""
        item = _serie(db, usuario, [
            Episode(season_number=1, episode_number=1, air_date=HOY - timedelta(days=900), watched=True),
            Episode(season_number=1, episode_number=2, air_date=HOY - timedelta(days=890)),
        ], status=MediaStatus.COMPLETADO, completed_at=HOY - timedelta(days=10))

        assert scheduler.reabrir_completadas(db) == 0
        db.refresh(item)
        assert item.status == MediaStatus.COMPLETADO

    def test_el_job_refresca_tambien_las_completadas(self, db, usuario, monkeypatch, fabrica):
        monkeypatch.setattr(scheduler.settings, "tmdb_api_key", "clave")
        monkeypatch.setattr(tmdb, "get_tv_details", lambda ext: {"seasons": [1, 2]})
        hace_un_mes = HOY - timedelta(days=30)
        item = _serie(db, usuario, [
            Episode(season_number=1, episode_number=1, air_date=hace_un_mes - timedelta(days=7), watched=True),
        ], status=MediaStatus.COMPLETADO, completed_at=hace_un_mes)
        monkeypatch.setattr(tmdb, "fetch_tv_episodes",
                            lambda ext, ts: [_ep_tmdb(1, 1, hace_un_mes - timedelta(days=7)),
                                             _ep_tmdb(2, 1, AYER, "Vuelve")])

        scheduler.refresh_following_episodes(db, session_factory=fabrica)

        db.expire_all()
        item = db.get(MediaItem, item.id)
        assert len(item.episodes) == 2
        assert item.status == MediaStatus.EN_PROGRESO

    def test_proximamente_incluye_las_completadas(self, db, usuario):
        from app.routers.home import _upcoming

        _serie(db, usuario, [
            Episode(season_number=1, episode_number=1, air_date=HOY - timedelta(days=60), watched=True),
            Episode(season_number=2, episode_number=1, air_date=HOY + timedelta(days=30)),
        ], status=MediaStatus.COMPLETADO, completed_at=HOY - timedelta(days=50))
        assert [e["episodio"].code for e in _upcoming(db, usuario)] == ["S02E01"]


class TestPodcasts:
    """R3-M13."""

    def _feed(self, monkeypatch, episodios):
        monkeypatch.setattr(itunes, "fetch_podcast_episodes", lambda url, limit=50: list(episodios))

    def _e(self, nombre, fecha):
        return {"name": nombre, "air_date": fecha, "runtime_minutes": 30}

    def test_un_episodio_nuevo_no_descoloca_los_numeros(self, db, usuario, monkeypatch):
        d = [HOY - timedelta(days=n) for n in (30, 20, 10, 1)]
        item = _serie(db, usuario, [], tipo=MediaType.PODCAST, fuente="itunes")
        self._feed(monkeypatch, [self._e("A", d[0]), self._e("B", d[1]), self._e("C", d[2])])
        metadata.load_podcast_episodes(item)
        # El feed solo trae los últimos 3: A cae por la cola y aparece D.
        self._feed(monkeypatch, [self._e("B", d[1]), self._e("C", d[2]), self._e("D", d[3])])
        assert metadata.load_podcast_episodes(item) == 1

        por_nombre = {e.name: e.episode_number for e in item.episodes}
        assert por_nombre == {"A": 1, "B": 2, "C": 3, "D": 4}
        nuevo = next(e for e in item.episodes if e.name == "D")
        assert nuevo.notified is False  # recién salido: se avisa

    def test_el_job_refresca_podcasts_sin_clave_de_tmdb(self, db, usuario, monkeypatch, fabrica):
        monkeypatch.setattr(scheduler.settings, "tmdb_api_key", "")
        item = _serie(db, usuario, [Episode(season_number=1, episode_number=1, name="A",
                                            air_date=HOY - timedelta(days=9))],
                      tipo=MediaType.PODCAST, fuente="itunes")
        self._feed(monkeypatch, [self._e("A", HOY - timedelta(days=9)), self._e("B", AYER)])

        scheduler.refresh_following_episodes(db, session_factory=fabrica)

        db.expire_all()
        assert [e.name for e in db.get(MediaItem, item.id).episodes] == ["A", "B"]


class TestAvisos:
    """R3-B11."""

    def test_lo_ya_visto_no_se_avisa_pero_se_marca(self, db, usuario, envios):
        item = _serie(db, usuario, [Episode(season_number=1, episode_number=1, air_date=AYER,
                                            watched=True, notified=False)])
        assert scheduler.check_new_episodes(db) == 0
        assert envios == []
        db.refresh(item)
        assert item.episodes[0].notified is True

    def test_varios_episodios_de_una_serie_van_en_un_mensaje(self, db, usuario, envios):
        _serie(db, usuario, [
            Episode(season_number=1, episode_number=n, air_date=AYER, notified=False)
            for n in range(1, 9)
        ], titulo="Maratón")
        assert scheduler.check_new_episodes(db) == 8
        assert len(envios) == 1
        assert "8 episodios nuevos" in envios[0]
        assert "y 3 más" in envios[0]

    def test_con_varias_cuentas_se_dice_de_quien_es(self, db, usuario, envios):
        db.add(Usuario(nombre="Otra"))
        db.commit()
        _serie(db, usuario, [Episode(season_number=1, episode_number=1, air_date=AYER, notified=False)])
        scheduler.check_new_episodes(db)
        assert "(Titular)" in envios[0]

    def test_un_429_se_reintenta_tras_la_espera(self, monkeypatch):
        monkeypatch.setattr(telegram.settings, "telegram_bot_token", "t")
        monkeypatch.setattr(telegram.settings, "telegram_chat_id", "1")
        esperas: list[float] = []
        monkeypatch.setattr(telegram.time, "sleep", esperas.append)
        peticion = httpx.Request("POST", "https://api.telegram.org/bott/sendMessage")
        respuestas = [
            httpx.Response(429, json={"parameters": {"retry_after": 3}}, request=peticion),
            httpx.Response(200, json={"ok": True}, request=peticion),
        ]
        monkeypatch.setattr(telegram.httpx, "post", lambda *a, **k: respuestas.pop(0))

        assert telegram.send_message("hola") is True
        assert esperas == [3]

    def test_un_429_con_espera_larga_no_duerme_el_job(self, monkeypatch):
        monkeypatch.setattr(telegram.settings, "telegram_bot_token", "t")
        monkeypatch.setattr(telegram.settings, "telegram_chat_id", "1")
        monkeypatch.setattr(telegram.time, "sleep", lambda s: pytest.fail("no debía esperar"))
        peticion = httpx.Request("POST", "https://api.telegram.org/bott/sendMessage")
        monkeypatch.setattr(telegram.httpx, "post", lambda *a, **k: httpx.Response(
            429, json={"parameters": {"retry_after": 600}}, request=peticion))

        assert telegram.send_message("hola") is False
