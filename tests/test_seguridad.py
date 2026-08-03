"""Tests de los hallazgos de seguridad que no tenían un xfail previo: M1 (CSRF),
M7 (SSRF vía feed de podcast) y A3 (API keys filtradas en los logs).

A1 y A2 sí tenían xfail y ya se movieron (sin marca) a test_fallos_conocidos.py.
"""
import httpx
import pytest

from app.csrf import _es_peticion_cruzada
from app.services._logging_utils import log_fallo_api
from app.services.itunes import _es_url_publica


class TestProteccionCSRF:
    def test_rechaza_post_cross_site(self, client):
        r = client.post("/item/1/eliminar", headers={"sec-fetch-site": "cross-site"})
        assert r.status_code == 403

    def test_permite_post_same_origin(self, client, crear_item):
        item = crear_item()
        r = client.post(f"/item/{item.id}/eliminar", headers={"sec-fetch-site": "same-origin"},
                        follow_redirects=False)
        assert r.status_code == 303

    def test_permite_navegacion_directa_none(self, client, crear_item):
        """sec-fetch-site: none = el usuario llegó tecleando la URL o un
        marcador, no siguiendo un enlace de otra página."""
        item = crear_item()
        r = client.post(f"/item/{item.id}/eliminar", headers={"sec-fetch-site": "none"},
                        follow_redirects=False)
        assert r.status_code == 303

    def test_rechaza_origin_distinto_del_host(self, client):
        r = client.post("/item/1/eliminar", headers={"origin": "https://sitio-malicioso.example"})
        assert r.status_code == 403

    def test_permite_origin_igual_al_host(self, client, crear_item):
        item = crear_item()
        r = client.post(f"/item/{item.id}/eliminar",
                        headers={"origin": "http://testserver", "host": "testserver"},
                        follow_redirects=False)
        assert r.status_code == 303

    def test_sin_ninguna_cabecera_se_deja_pasar(self, client, crear_item):
        """Fallo abierto a propósito: sin Sec-Fetch-Site ni Origin (curl,
        scripts, la propia suite de tests) no hay evidencia de cruce."""
        item = crear_item()
        r = client.post(f"/item/{item.id}/eliminar", follow_redirects=False)
        assert r.status_code == 303

    def test_los_get_nunca_se_bloquean(self, client):
        r = client.get("/catalogo?tipo=libro", headers={"sec-fetch-site": "cross-site"})
        assert r.status_code == 200

    @pytest.mark.parametrize("sec_fetch_site,esperado", [
        ("cross-site", True), ("same-origin", False), ("same-site", False), ("none", False),
    ])
    def test_funcion_de_deteccion_directamente(self, sec_fetch_site, esperado):
        class PeticionFalsa:
            headers = {"sec-fetch-site": sec_fetch_site}
        assert _es_peticion_cruzada(PeticionFalsa()) is esperado


class TestSSRFEnFeedDePodcast:
    @pytest.mark.parametrize("url", [
        "http://169.254.169.254/latest/meta-data/",  # metadata de nube (AWS/GCP)
        "http://127.0.0.1:8002/salud",
        "http://localhost/admin",
        "http://192.168.1.1/",
        "http://10.0.0.5/",
        "ftp://ejemplo.com/feed.xml",  # esquema no http/https
        "file:///etc/passwd",
        "no-es-una-url",
    ])
    def test_rechaza_hosts_no_publicos(self, url):
        assert _es_url_publica(url) is False

    def test_acepta_un_host_publico(self, monkeypatch):
        # Se fija la resolución DNS para no depender de la red en el test.
        monkeypatch.setattr("socket.getaddrinfo", lambda host, port: [
            (2, 1, 6, "", ("93.184.216.34", 0)),
        ])
        assert _es_url_publica("https://ejemplo.com/feed.xml") is True

    def test_un_host_que_no_resuelve_se_rechaza(self, monkeypatch):
        import socket as socket_mod

        def _falla(*a, **k):
            raise socket_mod.gaierror("no resuelve")
        monkeypatch.setattr("socket.getaddrinfo", _falla)
        assert _es_url_publica("https://dominio-inventado.invalid/feed.xml") is False

    def test_fetch_podcast_episodes_no_llega_a_pedir_una_url_privada(self, monkeypatch):
        """Si _es_url_publica rechaza la URL, ni siquiera se debe intentar
        el httpx.get -- verificado interceptando la llamada de red."""
        from app.services import itunes

        def _no_deberia_llamarse(*a, **k):
            raise AssertionError("fetch_podcast_episodes llamó a httpx.get con un host privado")
        monkeypatch.setattr(itunes.httpx, "get", _no_deberia_llamarse)

        assert itunes.fetch_podcast_episodes("http://169.254.169.254/") == []


class TestLogsSinApiKeys:
    def test_log_fallo_api_no_incluye_la_url_ni_la_clave(self, caplog):
        url_con_clave = "https://api.themoviedb.org/3/search/movie?api_key=SUPER_SECRETA&query=dune"
        excepcion = httpx.HTTPStatusError(
            f"Client error '401 Unauthorized' for url '{url_con_clave}'",
            request=httpx.Request("GET", url_con_clave),
            response=httpx.Response(401, request=httpx.Request("GET", url_con_clave)),
        )
        import logging
        logger = logging.getLogger("test-tmdb")

        with caplog.at_level(logging.WARNING):
            log_fallo_api(logger, "Fallo al buscar %s", "dune", exc=excepcion)

        assert "SUPER_SECRETA" not in caplog.text
        assert "api_key" not in caplog.text
        assert "401" in caplog.text

    def test_log_fallo_api_indica_el_tipo_de_error_sin_respuesta_http(self, caplog):
        """Un timeout no tiene .response: debe seguir siendo informativo sin
        reventar buscando un status_code que no existe."""
        import logging
        logger = logging.getLogger("test-tmdb")
        excepcion = httpx.ConnectTimeout("timeout")

        with caplog.at_level(logging.WARNING):
            log_fallo_api(logger, "Fallo al buscar %s", "dune", exc=excepcion)

        assert "ConnectTimeout" in caplog.text
