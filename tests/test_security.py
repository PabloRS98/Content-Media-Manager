"""Regresiones de los hallazgos de seguridad de la auditoría."""
import httpx
import pytest

from app.security import safe_redirect_path
from app.services.http_errors import describe, safe_url
from app.services.netguard import UnsafeURLError, ensure_public_url


class TestXSSEnEstadisticas:
    """A1: el filtro tojson de Jinja escapa `<` para poder incrustar datos en
    un <script>. Sobreescribirlo con json.dumps abría un XSS almacenado."""

    def test_genero_malicioso_no_cierra_la_etiqueta_script(self, client, db):
        from app.models import MediaItem, MediaStatus, MediaType

        payload = "</script><img src=x onerror=alert(1)>"
        db.add(MediaItem(media_type=MediaType.LIBRO, title="XSS", genres=payload,
                         status=MediaStatus.COMPLETADO))
        db.commit()

        html = client.get("/estadisticas").text
        assert payload not in html
        assert "</script><img" not in html
        assert "\\u003c" in html  # escapado como secuencia unicode

    def test_el_dato_sigue_llegando_al_grafico(self, client, db):
        from app.models import MediaItem, MediaStatus, MediaType

        db.add(MediaItem(media_type=MediaType.LIBRO, title="L", genres="Fantasía",
                         status=MediaStatus.COMPLETADO))
        db.commit()
        assert "Fantas" in client.get("/estadisticas").text


class TestRedireccionAbierta:
    """A2: el Referer lo controla el cliente; no puede ser destino de un 303."""

    @pytest.mark.parametrize("referer", [
        "https://evil.example.com/phish",
        "//evil.example.com/phish",
        "http://127.0.0.1:9999/admin",
    ])
    def test_referer_externo_cae_al_fallback(self, client, referer):
        r = client.post("/catalogo/completar-portadas",
                        headers={"referer": referer}, follow_redirects=False)
        assert r.headers["location"] == "/catalogo"

    def test_referer_interno_se_respeta(self, client):
        r = client.post("/catalogo/completar-portadas",
                        headers={"referer": "http://testserver/catalogo?tipo=libro"},
                        follow_redirects=False)
        assert r.headers["location"] == "/catalogo?tipo=libro"

    @pytest.mark.parametrize("entrada,esperado", [
        ("https://evil.com/x", "/"),
        ("//evil.com/x", "/"),
        ("/catalogo?tipo=serie", "/catalogo?tipo=serie"),
        ("", "/"),
        (None, "/"),
        ("https://evil.com/catalogo", "/"),   # quedarse con el path enmascararía el ataque
    ])
    def test_safe_redirect_path(self, entrada, esperado):
        assert safe_redirect_path(entrada, host="testserver") == esperado

    def test_absoluta_del_propio_host_se_acepta(self):
        assert safe_redirect_path("http://testserver/catalogo?tipo=libro",
                                  host="testserver") == "/catalogo?tipo=libro"

    def test_absoluta_sin_host_conocido_cae_al_fallback(self):
        assert safe_redirect_path("http://testserver/catalogo") == "/"


class TestSecretosEnLogs:
    """A3: httpx mete la URL completa en el mensaje de raise_for_status, y las
    keys de TMDB/RAWG/Google Books viajan en la query string."""

    def test_describe_omite_la_query_string(self):
        url = "https://api.themoviedb.org/3/search/movie?api_key=SECRETO&query=dune"
        req = httpx.Request("GET", url)
        exc = httpx.HTTPStatusError("x", request=req, response=httpx.Response(401, request=req))
        assert "SECRETO" not in describe(exc)
        assert "api.themoviedb.org/3/search/movie" in describe(exc)

    def test_describe_omite_el_token_de_telegram_del_path(self):
        url = "https://api.telegram.org/bot123456:AAH-SECRETO/sendMessage"
        req = httpx.Request("POST", url)
        exc = httpx.ConnectError("boom", request=req)
        assert "SECRETO" not in describe(exc)

    def test_describe_nunca_lanza(self):
        """Se llama dentro de un `except`: si lanzara, sería un 500 para el usuario.
        `exc.request` en httpx lanza RuntimeError si no se le asignó petición."""
        assert describe(httpx.ConnectError("sin request")) == "ConnectError"
        assert describe(ValueError("otra cosa")) == "ValueError"
        assert describe(httpx.ReadTimeout("timeout")) == "ReadTimeout"

    def test_safe_url_sin_credenciales(self):
        assert safe_url("https://x.test/a/b?key=SECRETO") == "https://x.test/a/b"

    def test_tmdb_no_loguea_la_key(self, app_env, monkeypatch, caplog):
        import app.services.tmdb as tmdb
        from app.config import settings

        monkeypatch.setattr(settings, "tmdb_api_key", "KEY_SECRETA_123")

        def falla(*a, **k):
            raise httpx.ConnectError(
                "down", request=httpx.Request("GET", "https://api.themoviedb.org/3/search/movie"
                                                     "?api_key=KEY_SECRETA_123&query=x"))
        monkeypatch.setattr(tmdb.httpx, "get", falla)

        with caplog.at_level("DEBUG"):
            assert tmdb.search_movies("dune") == []
        assert "KEY_SECRETA_123" not in caplog.text


class TestCSRF:
    """M1: con HTTP Basic el navegador reenvía credenciales cross-site."""

    def test_post_cross_site_bloqueado(self, app_env):
        from fastapi.testclient import TestClient

        c = TestClient(app_env.app)
        r = c.post("/listas", data={"name": "x"},
                   headers={"sec-fetch-site": "cross-site"}, follow_redirects=False)
        assert r.status_code == 403

    def test_post_con_origin_ajeno_bloqueado(self, app_env):
        from fastapi.testclient import TestClient

        c = TestClient(app_env.app)
        r = c.post("/listas", data={"name": "x"},
                   headers={"origin": "https://evil.example.com"}, follow_redirects=False)
        assert r.status_code == 403

    def test_post_same_origin_permitido(self, client):
        r = client.post("/listas", data={"name": "mi lista"}, follow_redirects=False)
        assert r.status_code == 303

    def test_get_nunca_se_bloquea(self, app_env):
        from fastapi.testclient import TestClient

        c = TestClient(app_env.app)
        assert c.get("/salud", headers={"sec-fetch-site": "cross-site"}).status_code == 200


class TestSSRF:
    """M7: el external_id de un podcast es la URL del feed y llega por formulario."""

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:8000/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/x",
        "file:///etc/passwd",
        "gopher://x/1",
        "http://10.0.0.5/feed.xml",
    ])
    def test_destinos_internos_rechazados(self, url):
        with pytest.raises(UnsafeURLError):
            ensure_public_url(url)

    def test_fetch_no_hace_peticion_a_destino_interno(self, app_env, monkeypatch):
        import app.services.itunes as itunes

        llamadas = []
        monkeypatch.setattr(itunes.httpx, "get",
                            lambda *a, **k: llamadas.append(a) or pytest.fail("no debe pedirse"))
        assert itunes.fetch_podcast_episodes("http://169.254.169.254/") == []
        assert llamadas == []


class TestLimiteDeSubida:
    """M8: UploadFile.read() sin argumentos carga el fichero entero en memoria."""

    def test_csv_demasiado_grande_devuelve_413(self, client, app_env):
        from app.config import settings

        settings.max_upload_mb = 1
        grande = "Const,Title,Title Type,Year\n" + ("tt1,X,movie,2001\n" * 200_000)
        r = client.post("/importar", files={"archivo": ("big.csv", grande, "text/csv")})
        assert r.status_code == 413

    def test_csv_normal_pasa(self, client):
        csv_ok = "Const,Title,Title Type,Year\ntt0000001,Duna,movie,2021\n"
        r = client.post("/importar", files={"archivo": ("ok.csv", csv_ok, "text/csv")})
        assert r.status_code == 200


class TestAuth:
    """B4: compare_digest sobre str exige ASCII y lanza TypeError."""

    def test_password_no_ascii_no_provoca_500(self, app_env):
        from fastapi.testclient import TestClient

        from app.config import settings

        settings.enable_auth = True
        settings.auth_username = "admin"
        settings.auth_password = "contraseña"
        try:
            c = TestClient(app_env.app)
            r = c.get("/catalogo?tipo=libro", auth=("admin", "otra"))
            assert r.status_code == 401  # 401 limpio, no un 500 por TypeError
        finally:
            settings.enable_auth = False

    def test_credenciales_correctas(self, app_env):
        from fastapi.testclient import TestClient

        from app.config import settings

        settings.enable_auth = True
        settings.auth_username = "admin"
        settings.auth_password = "s3cret"
        try:
            c = TestClient(app_env.app)
            assert c.get("/catalogo?tipo=libro", auth=("admin", "s3cret")).status_code == 200
            assert c.get("/catalogo?tipo=libro", auth=("admin", "mal")).status_code == 401
            assert c.get("/catalogo?tipo=libro").status_code == 401
        finally:
            settings.enable_auth = False
