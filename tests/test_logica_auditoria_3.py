"""Sesiones, SSRF, diario, fechas, consultas y búsqueda (tercera auditoría)."""
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event

from app import cuentas, fechas
from app.config import settings
from app.models import Episode, Lista, MediaStatus, MediaType
from app.services import actividad, itunes, metadata, recomendaciones


class TestSesiones:
    """R3-M4."""

    def _peticion(self, sesion=None):
        return SimpleNamespace(session=dict(sesion or {}))

    def test_cerrar_todas_invalida_las_demas_cookies(self, db, usuario):
        otro_navegador = self._peticion()
        cuentas.abrir_sesion(otro_navegador, usuario)
        este = self._peticion()
        cuentas.abrir_sesion(este, usuario)
        assert cuentas.cuenta_en_sesion(otro_navegador, db) == usuario

        cuentas.cerrar_todas_las_sesiones(este, usuario)
        db.commit()

        assert cuentas.cuenta_en_sesion(otro_navegador, db) is None
        assert cuentas.cuenta_en_sesion(este, db) == usuario

    def test_una_cookie_de_antes_de_la_version_vale_mientras_no_se_cierre(self, db, usuario):
        vieja = self._peticion({cuentas.CLAVE_SESION: usuario.id})
        assert cuentas.cuenta_en_sesion(vieja, db) == usuario

    def test_el_boton_sube_la_version(self, client, db, usuario):
        r = client.post("/cuentas/ajustes/cerrar-sesiones", follow_redirects=False)
        assert r.status_code == 303
        db.refresh(usuario)
        assert usuario.sesion_version == 1

    def test_la_cookie_caduca_y_va_segura_si_se_pide(self):
        from app.main import app
        (mw,) = [m for m in app.user_middleware if m.cls.__name__ == "SessionMiddleware"]
        assert mw.kwargs["max_age"] == 90 * 24 * 3600
        assert mw.kwargs["https_only"] == settings.secure_cookies


class _Resp:
    def __init__(self, location=None, content=b""):
        self.is_redirect = location is not None
        self.headers = {"location": location} if location else {}
        self.content = content


class TestSSRF:
    """R3-M6."""

    @pytest.fixture
    def red(self, monkeypatch):
        ips = {"feed.ejemplo.com": "93.184.216.34", "interno.ejemplo.com": "192.168.1.1",
               "cgnat.ejemplo.com": "100.64.0.1", "mapeada.ejemplo.com": "::ffff:127.0.0.1"}
        monkeypatch.setattr(itunes.socket, "getaddrinfo",
                            lambda host, port: [(None, None, None, None, (ips[host], 0))])
        pedidas: list[str] = []
        respuestas: dict[str, _Resp] = {}

        def _get(url, **kw):
            pedidas.append(url)
            return respuestas[url]
        monkeypatch.setattr(itunes.httpx, "get", _get)
        return SimpleNamespace(pedidas=pedidas, respuestas=respuestas)

    @pytest.mark.parametrize("host", ["interno", "cgnat", "mapeada"])
    def test_rechaza_lo_que_no_es_global(self, host):
        assert not itunes._es_url_publica("http://%s.ejemplo.com/feed" % host)

    def test_una_redireccion_a_la_red_interna_no_se_sigue(self, red):
        red.respuestas["https://feed.ejemplo.com/rss"] = _Resp("http://interno.ejemplo.com/admin")
        with pytest.raises(ValueError):
            itunes._get_publico("https://feed.ejemplo.com/rss")
        assert red.pedidas == ["https://feed.ejemplo.com/rss"]

    def test_una_redireccion_publica_si(self, red):
        red.respuestas["http://feed.ejemplo.com/rss"] = _Resp("/nuevo")
        red.respuestas["http://feed.ejemplo.com/nuevo"] = _Resp(content=b"ok")
        assert itunes._get_publico("http://feed.ejemplo.com/rss").content == b"ok"

    def test_un_bucle_de_redirecciones_se_corta(self, red):
        red.respuestas["http://feed.ejemplo.com/a"] = _Resp("/a")
        with pytest.raises(ValueError):
            itunes._get_publico("http://feed.ejemplo.com/a")
        assert len(red.pedidas) == itunes.MAX_REDIRECCIONES + 1


def test_el_diario_muestra_en_marzo_lo_empezado_en_marzo_y_acabado_en_abril(db, usuario, crear_item):
    """R3-M10."""
    crear_item(title="Largo", status=MediaStatus.COMPLETADO,
               started_at=date(2026, 3, 10), completed_at=date(2026, 4, 2))
    crear_item(title="De un tirón", status=MediaStatus.COMPLETADO,
               started_at=date(2026, 3, 12), completed_at=date(2026, 3, 12))
    marzo = actividad.diario(db, usuario, date(2026, 3, 1), date(2026, 3, 31))
    entradas = [(e.tipo, e.titulo) for d in marzo for e in d.entradas]
    assert ("empezado", "Largo") in entradas
    assert entradas.count(("terminado", "De un tirón")) == 1
    assert ("empezado", "De un tirón") not in entradas


def test_lo_que_no_gusto_no_recomienda(db, usuario, crear_item):
    """R3-M18."""
    crear_item(title="Malo 1", saga="Saga", status=MediaStatus.COMPLETADO, rating=3)
    crear_item(title="Malo 2", saga="Saga", status=MediaStatus.PENDIENTE)
    assert all(r.item.title != "Malo 2" or not r.motivos
               for r in recomendaciones.recomendar(db, usuario))
    sagas, _, _ = recomendaciones._gustos(db, usuario)
    assert "saga" not in sagas


class TestFechas:
    """R3-M11."""

    def test_hoy_es_el_de_la_zona_configurada(self, monkeypatch):
        monkeypatch.setattr(settings, "timezone", "Pacific/Kiritimati")
        assert fechas.hoy() == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()

    def test_una_zona_que_no_existe_no_rompe(self, monkeypatch):
        monkeypatch.setattr(settings, "timezone", "Marte/Olimpo")
        assert isinstance(fechas.hoy(), date)

    def test_ver_un_episodio_usa_el_dia_de_casa(self, db, crear_item, monkeypatch):
        monkeypatch.setattr(fechas, "hoy", lambda: date(2030, 1, 1))
        serie = crear_item(title="S", media_type=MediaType.SERIE)
        ep = Episode(season_number=1, episode_number=1)
        serie.episodes.append(ep)
        metadata.toggle_episode(serie, ep)
        assert ep.watched_at == date(2030, 1, 1)


class TestConsultas:
    """R3-M19: el número de SELECT no crece con las listas ni con las series."""

    @pytest.fixture
    def contar(self, db):
        motor = db.get_bind()
        cuenta = {"n": 0}

        def _anotar(*_):
            cuenta["n"] += 1
        event.listen(motor, "before_cursor_execute", _anotar)
        yield cuenta
        event.remove(motor, "before_cursor_execute", _anotar)

    def _selects(self, client, url, contar):
        contar["n"] = 0
        assert client.get(url).status_code == 200
        return contar["n"]

    def test_la_ficha_no_hace_una_consulta_por_lista(self, client, db, usuario, crear_item, contar):
        item = crear_item(title="Ficha")
        db.add(Lista(usuario_id=usuario.id, name="L0", items=[item]))
        db.commit()
        pocas = self._selects(client, "/item/%d" % item.id, contar)
        for i in range(1, 10):
            db.add(Lista(usuario_id=usuario.id, name="L%d" % i, items=[item]))
        db.commit()
        assert self._selects(client, "/item/%d" % item.id, contar) == pocas

    def test_el_calendario_no_hace_una_consulta_por_serie(self, client, db, crear_item, contar):
        def _serie(n):
            s = crear_item(title="S%d" % n, media_type=MediaType.SERIE, status=MediaStatus.EN_PROGRESO)
            s.episodes.append(Episode(season_number=1, episode_number=1, air_date=date(2099, 1, n)))
            db.commit()
        _serie(1)
        pocas = self._selects(client, "/calendario", contar)
        for n in range(2, 8):
            _serie(n)
        assert self._selects(client, "/calendario", contar) == pocas


class TestBusqueda:
    """R3-B3 y R3-B17."""

    def test_mil_palabras_no_dan_500(self, client):
        assert client.get("/catalogo", params={"q": "palabra " * 1000}).status_code == 200

    def test_dice_que_falta_la_clave(self, client, monkeypatch):
        monkeypatch.setattr(settings, "tmdb_api_key", "")
        r = client.get("/buscar", params={"tipo": "pelicula", "q": "Dune"})
        assert "TMDB_API_KEY" in r.text

    def test_dice_que_es_demasiado_corta(self, client):
        r = client.get("/buscar", params={"tipo": "libro", "q": "a"})
        assert "TMDB_API_KEY" not in r.text
        assert "2" in r.text


def test_la_decada_de_1999_es_los_90(client, crear_item):
    """Década con división entera: `/` daba 199.9 → 1999.0 y no agrupaba."""
    crear_item(title="Matrix", media_type=MediaType.PELICULA, year=1999)
    crear_item(title="Fight Club", media_type=MediaType.PELICULA, year=1990)
    r = client.get("/estadisticas")
    assert "1990s 2" in r.text
