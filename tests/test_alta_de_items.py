"""Añadir un ítem no debe esperar a las peticiones HTTP de enriquecimiento.

Para una serie de TMDB la cadena es: 1 petición de detalles + una petición POR
TEMPORADA. *Los Simpson* tiene 36 temporadas: 37 peticiones secuenciales con
10 s de timeout cada una, dentro del POST. Si el proxy corta antes, el usuario
ve un error mientras el trabajo sigue por detrás y el commit final no llega a
ejecutarse.
"""
import time
from urllib.parse import unquote

import pytest

from app.models import Episode, MediaItem, MediaType
from app.services import metadata, tmdb


@pytest.fixture
def orden_de_ejecucion(monkeypatch, db):
    """Registra en qué orden ocurren la respuesta y el enriquecimiento.

    Es la forma de distinguir "en segundo plano" de "síncrono" desde aquí:
    `TestClient` ejecuta los `BackgroundTasks` dentro de la propia llamada
    `client.post(...)`, así que cronometrar la respuesta no sirve -- pero el
    ORDEN sí cambia. Síncrono: enriquece y luego responde. En segundo plano:
    responde y luego enriquece.
    """
    from app.routers import catalog

    pasos: list[str] = []
    flash_original = catalog.redirect_flash

    def _enrich(db, item):
        pasos.append("enriquecer")
        # Como haría el enriquecimiento real de una serie de TMDB.
        item.episodes.append(Episode(season_number=1, episode_number=1))
        item.episodes.append(Episode(season_number=1, episode_number=2))

    def _flash(*a, **k):
        pasos.append("responder")
        return flash_original(*a, **k)

    monkeypatch.setattr(metadata, "enrich_item", _enrich)
    monkeypatch.setattr(catalog, "redirect_flash", _flash)
    # La tarea de fondo abre su propia sesión (la de la petición ya está
    # cerrada cuando corre). En el test se le da la del test, y se le impide
    # cerrarla porque los asserts la siguen usando después.
    monkeypatch.setattr(catalog, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    return pasos


def alta_de_serie(client, titulo="Serie larga"):
    return client.post("/agregar", data={
        "media_type": "serie", "title": titulo,
        "external_source": "tmdb", "external_id": "1234",
    }, follow_redirects=False)


class TestElAltaNoEspera:
    def test_alta_de_serie_responde_sin_esperar_a_los_episodios(self, client, orden_de_ejecucion):
        alta_de_serie(client)
        assert orden_de_ejecucion == ["responder", "enriquecer"], (
            "el enriquecimiento sigue ocurriendo dentro de la petición: %s"
            % orden_de_ejecucion
        )

    def test_el_item_se_crea_aunque_el_enriquecimiento_tarde(self, client, db, monkeypatch):
        def _lento(db_, item):
            time.sleep(0.3)

        monkeypatch.setattr(metadata, "enrich_item", _lento)
        alta_de_serie(client, "Serie lenta")
        assert db.query(MediaItem).filter(MediaItem.title == "Serie lenta").count() == 1

    def test_el_enriquecimiento_llega_a_ejecutarse(self, client, orden_de_ejecucion):
        """Mandarlo al fondo no puede significar no hacerlo."""
        alta_de_serie(client)
        assert "enriquecer" in orden_de_ejecucion

    def test_el_mensaje_ya_no_promete_un_recuento_de_episodios(self, client, orden_de_ejecucion):
        """Antes decía "'X' añadido (24 episodios)". Con el enriquecimiento en
        segundo plano ese número no existe todavía cuando se responde, así que
        el mensaje tiene que decir otra cosa en vez de mentir."""
        r = alta_de_serie(client, "Serie con aviso")
        # El mensaje viaja en la cookie 'flash', urlencoded dentro de un JSON.
        cookie = unquote(r.headers.get("set-cookie", ""))
        assert "episodios)" not in cookie, cookie


class TestTemporadasEnParalelo:
    def test_las_temporadas_se_piden_en_paralelo(self, monkeypatch):
        """10 temporadas a 0,2 s cada una: en serie son 2 s, en paralelo <1 s."""
        monkeypatch.setattr(tmdb.settings, "tmdb_api_key", "clave")

        def _get_lento(url, **kwargs):
            time.sleep(0.2)

            class Respuesta:
                @staticmethod
                def raise_for_status():
                    return None

                @staticmethod
                def json():
                    return {"episodes": [{"episode_number": 1, "season_number": 1}]}

            return Respuesta()

        monkeypatch.setattr(tmdb.httpx, "get", _get_lento)

        inicio = time.monotonic()
        episodios = tmdb.fetch_tv_episodes("1234", list(range(1, 11)))
        transcurrido = time.monotonic() - inicio

        assert len(episodios) == 10
        assert transcurrido < 1.0, "%.2f s: las temporadas se siguen pidiendo en serie" % transcurrido

    def test_una_temporada_que_falla_no_tumba_las_demas(self, monkeypatch):
        """El comportamiento de antes: cada temporada tiene su try/except."""
        monkeypatch.setattr(tmdb.settings, "tmdb_api_key", "clave")

        def _get(url, **kwargs):
            if "/season/3" in url:
                raise RuntimeError("TMDB caída")

            class Respuesta:
                @staticmethod
                def raise_for_status():
                    return None

                @staticmethod
                def json():
                    return {"episodes": [{"episode_number": 1, "season_number": 1}]}

            return Respuesta()

        monkeypatch.setattr(tmdb.httpx, "get", _get)
        assert len(tmdb.fetch_tv_episodes("1234", [1, 2, 3, 4])) == 3

    def test_los_episodios_salen_ordenados(self, monkeypatch):
        """En paralelo las respuestas llegan desordenadas: el resultado no
        puede depender de cuál conteste antes."""
        monkeypatch.setattr(tmdb.settings, "tmdb_api_key", "clave")

        def _get(url, **kwargs):
            temporada = int(url.rsplit("/", 1)[1])
            # Las temporadas altas contestan antes.
            time.sleep(0.05 * (5 - temporada))

            class Respuesta:
                @staticmethod
                def raise_for_status():
                    return None

                @staticmethod
                def json():
                    return {"episodes": [
                        {"episode_number": n, "season_number": temporada} for n in (1, 2)
                    ]}

            return Respuesta()

        monkeypatch.setattr(tmdb.httpx, "get", _get)
        episodios = tmdb.fetch_tv_episodes("1234", [1, 2, 3, 4])
        orden = [(e["season_number"], e["episode_number"]) for e in episodios]
        assert orden == sorted(orden)


def test_alta_de_libro_sin_fuente_externa_sigue_funcionando(client, db, orden_de_ejecucion):
    """El enriquecimiento se encola siempre, pero solo hace algo si hay fuente:
    esto fija que el alta simple sigue funcionando igual."""
    r = client.post("/agregar", data={
        "media_type": "libro", "title": "Libro a mano",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert db.query(MediaItem).filter(MediaItem.media_type == MediaType.LIBRO).count() == 1
