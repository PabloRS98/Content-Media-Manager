"""Primer bloque de la tercera auditoría (docs/AUDITORIA-3.md): los arreglos
de una línea con impacto alto. Cada clase lleva el identificador que cierra.
"""
import logging
import re
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import Episode, MediaItem, MediaStatus, MediaType, Usuario
from app.services import enrich, metadata

RAIZ = Path(__file__).resolve().parent.parent


class TestClavesFueraDelLog:
    """[R3-A1] httpx escribía en INFO la URL completa de cada petición
    correcta, con `api_key=` dentro."""

    def test_httpx_no_registra_las_peticiones_correctas(self):
        import app.main  # noqa: F401  -- configura los loggers al importarse
        assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
        assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING

    def test_una_busqueda_correcta_no_deja_la_clave_en_el_log(self, caplog):
        caplog.set_level(logging.INFO)
        transporte = httpx.MockTransport(lambda r: httpx.Response(200, json={"results": []}))
        with httpx.Client(transport=transporte) as cliente:
            cliente.get("https://api.themoviedb.org/3/search/movie",
                        params={"api_key": "CLAVE-SECRETA-123", "query": "dune"})
        assert "CLAVE-SECRETA-123" not in caplog.text


class TestSalirDeCompletado:
    """[R3-M7] La fecha de completado se quedaba al volver a "por ver"."""

    def test_volver_a_pendiente_quita_la_fecha(self, client, db, crear_item):
        item = crear_item(title="Por error", status=MediaStatus.PENDIENTE)
        client.post("/item/%d/estado" % item.id, data={"status": "completado"})
        db.refresh(item)
        assert item.completed_at == date.today()

        client.post("/item/%d/estado" % item.id, data={"status": "pendiente"})
        db.refresh(item)
        assert item.completed_at is None

    def test_deja_de_contar_en_estadisticas(self, client, crear_item):
        item = crear_item(title="Deshecho", status=MediaStatus.PENDIENTE, rating=9)
        client.post("/item/%d/estado" % item.id, data={"status": "completado"})
        client.post("/item/%d/estado" % item.id, data={"status": "pendiente"})
        assert "Deshecho" not in client.get("/estadisticas").text

    def test_el_formulario_completo_sigue_la_misma_regla(self, client, db, crear_item):
        item = crear_item(title="Por formulario", status=MediaStatus.COMPLETADO,
                          completed_at=date(2025, 3, 1))
        client.post("/item/%d/actualizar" % item.id,
                    data={"title": item.title, "status": "en_progreso"})
        db.refresh(item)
        assert item.completed_at is None


class TestSerieVista:
    """[R3-M8] "Visto" en una serie la dejaba completada con 0/N episodios."""

    def test_marca_los_emitidos_y_no_los_futuros(self, client, db, usuario):
        hoy = date.today()
        serie = MediaItem(usuario_id=usuario.id, title="Serie", media_type=MediaType.SERIE,
                          status=MediaStatus.EN_PROGRESO)
        serie.episodes = [
            Episode(season_number=1, episode_number=1, air_date=hoy - timedelta(days=30)),
            Episode(season_number=1, episode_number=2, air_date=None),
            Episode(season_number=1, episode_number=3, air_date=hoy + timedelta(days=7)),
        ]
        db.add(serie)
        db.commit()

        client.post("/item/%d/estado" % serie.id, data={"status": "completado"})
        db.refresh(serie)

        assert [e.watched for e in serie.episodes] == [True, True, False]
        # Se sabe que los viste, no cuándo: el diario no se llena de golpe hoy.
        assert all(e.watched_at is None for e in serie.episodes)

    def test_no_toca_lo_que_no_es_episodico(self, db, crear_item):
        libro = crear_item(title="Libro", status=MediaStatus.EN_PROGRESO)
        metadata.cambiar_estado(libro, MediaStatus.COMPLETADO)
        assert libro.status == MediaStatus.COMPLETADO


@pytest.fixture
def cliente_sin_cuenta(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app, headers={"origin": "http://testserver"})
    finally:
        app.dependency_overrides.clear()


class TestPortadasConCuenta:
    """[R3-M5] Los dos endpoints de portadas respondían sin cuenta y
    recorrían el catálogo de todas."""

    @pytest.mark.parametrize("metodo,ruta", [
        ("post", "/importar/completar-portadas"),
        ("get", "/importar/estado-portadas"),
    ])
    def test_sin_cuenta_llevan_al_selector(self, cliente_sin_cuenta, metodo, ruta):
        r = getattr(cliente_sin_cuenta, metodo)(ruta, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/cuentas"

    def test_el_lote_solo_toca_la_cuenta_propia(self, db, usuario, otro_usuario, monkeypatch):
        mio = MediaItem(usuario_id=usuario.id, title="Mío", media_type=MediaType.LIBRO,
                        status=MediaStatus.PENDIENTE)
        ajeno = MediaItem(usuario_id=otro_usuario.id, title="Ajeno", media_type=MediaType.LIBRO,
                          status=MediaStatus.PENDIENTE)
        db.add_all([mio, ajeno])
        db.commit()
        vistos = []
        monkeypatch.setattr(enrich, "_search_for", lambda item: vistos.append(item.title) or [])
        monkeypatch.setattr(enrich, "SLEEP_BETWEEN", 0)

        resultado = enrich.enrich_missing_covers(db, usuario.id)

        assert vistos == ["Mío"]
        assert resultado["restantes"] == 1

    def test_el_boton_lanza_el_lote_de_su_cuenta(self, client, usuario, monkeypatch):
        cuentas = []
        monkeypatch.setattr(enrich, "enrich_missing_covers",
                            lambda db, usuario_id=None: cuentas.append(usuario_id) or {})
        client.post("/importar/completar-portadas")
        assert cuentas == [usuario.id]


class TestSinDocumentacionDeLaApi:
    """[R3-B4] /docs y compañía publicaban el mapa de rutas sin cuenta."""

    @pytest.mark.parametrize("ruta", ["/docs", "/redoc", "/openapi.json"])
    def test_no_existen(self, cliente_sin_cuenta, ruta):
        assert cliente_sin_cuenta.get(ruta).status_code == 404


class TestMesesExtremos:
    """[R3-B8] Calcular el enlace al mes anterior o siguiente desbordaba."""

    @pytest.mark.parametrize("mes", ["0001-01", "9999-12"])
    def test_no_revientan(self, client, mes):
        assert client.get("/actividad?mes=%s" % mes).status_code == 200


def _luminancia(hexa: str) -> float:
    canales = [int(hexa[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lineal = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canales]
    return 0.2126 * lineal[0] + 0.7152 * lineal[1] + 0.0722 * lineal[2]


def _contraste(a: str, b: str) -> float:
    la, lb = sorted((_luminancia(a), _luminancia(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class TestContrasteDelPapel:
    """[R3-A6] Texto terciario, aviso, positivo y el número de la nota daban
    entre 2,9 y 3,7:1 en el tema claro. Se lee de style.css para que un cambio
    de paleta que lo rompa falle aquí y no en una auditoría."""

    @pytest.fixture(scope="class")
    def claro(self):
        css = (RAIZ / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
        bloque = css[css.index("/* ---------- Tokens: claro"):css.index("/* ---------- Tokens: oscuro")]
        return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", bloque))

    @pytest.mark.parametrize("texto", ["text", "text-2", "text-3", "positive", "negative",
                                       "warning", "star-text", "accent-soft", "stamp"])
    @pytest.mark.parametrize("fondo", ["bg", "bg-soft", "card", "card-2", "sidebar"])
    def test_pasa_aa(self, claro, texto, fondo):
        ratio = _contraste(claro[texto], claro[fondo])
        assert ratio >= 4.5, "--%s sobre --%s: %.2f:1" % (texto, fondo, ratio)


# ---------------------------------------------------------------------------
# Bloque 2: aislamiento y acceso
# ---------------------------------------------------------------------------

class TestGenerosPorCuenta:
    """[R3-A2] Renombrar un género lo cambiaba en el catálogo de todas."""

    def _con_genero(self, db, usuario_id, titulo, genero):
        from app.services import generos
        item = MediaItem(usuario_id=usuario_id, title=titulo, media_type=MediaType.PELICULA,
                         status=MediaStatus.PENDIENTE)
        db.add(item)
        db.flush()
        generos.asignar(db, item, genero)
        db.commit()
        return item

    def test_renombrar_no_toca_a_la_otra_cuenta(self, client, db, usuario, otro_usuario):
        mio = self._con_genero(db, usuario.id, "Mío", "Drama")
        suyo = self._con_genero(db, otro_usuario.id, "Suyo", "Drama")

        client.post("/generos/%d/renombrar" % mio.generos[0].id, data={"nombre": "Dramón"})

        db.refresh(mio)
        db.refresh(suyo)
        assert [g.nombre for g in mio.generos] == ["Dramón"]
        assert [g.nombre for g in suyo.generos] == ["Drama"]

    def test_fusionar_tampoco(self, client, db, usuario, otro_usuario):
        mio = self._con_genero(db, usuario.id, "Mío", "Sci-Fi")
        self._con_genero(db, usuario.id, "Otro mío", "Ciencia ficción")
        suyo = self._con_genero(db, otro_usuario.id, "Suyo", "Sci-Fi")

        client.post("/generos/%d/renombrar" % mio.generos[0].id, data={"nombre": "Ciencia ficción"})

        db.refresh(mio)
        db.refresh(suyo)
        assert [g.nombre for g in mio.generos] == ["Ciencia ficción"]
        assert [g.nombre for g in suyo.generos] == ["Sci-Fi"]

    def test_si_solo_era_tuyo_no_queda_la_fila_vieja(self, client, db, usuario):
        from app.models import Genero
        mio = self._con_genero(db, usuario.id, "Mío", "Efímero")
        client.post("/generos/%d/renombrar" % mio.generos[0].id, data={"nombre": "Duradero"})
        assert db.query(Genero).filter(Genero.nombre == "Efímero").count() == 0


class TestHostPermitido:
    """[R3-M2] Con DNS rebinding el `Host` es el del atacante; era lo único que
    lo delataba y no se miraba."""

    @pytest.mark.parametrize("host", [
        "localhost", "localhost:8002", "127.0.0.1:8002", "[::1]:8002", "192.168.1.10",
        "nas", "nas:8002", "catalogo.local", "media.home.arpa", "casa.lan:8002",
        "mi-pc.tailnet-1234.ts.net",
    ])
    def test_se_admiten_los_que_no_se_pueden_secuestrar(self, host):
        from app.hosts import host_admitido
        assert host_admitido(host)

    @pytest.mark.parametrize("host", ["evil.example", "evil.example:8002", "catalogo.midominio.es", ""])
    def test_un_dominio_publico_no_declarado_se_rechaza(self, host):
        from app.hosts import host_admitido
        assert not host_admitido(host)

    def test_el_rebinding_se_corta_antes_de_entrar(self, cliente_sin_cuenta, usuario):
        r = cliente_sin_cuenta.post(
            "/cuentas/entrar/%d" % usuario.id,
            headers={"host": "evil.example:8002", "origin": "http://evil.example:8002",
                     "sec-fetch-site": "same-origin"},
            follow_redirects=False,
        )
        assert r.status_code == 400
        assert "ALLOWED_HOSTS" in r.text

    def test_un_dominio_declarado_entra(self, monkeypatch):
        from app.config import settings
        from app.hosts import host_admitido
        monkeypatch.setattr(settings, "allowed_hosts", "catalogo.midominio.es")
        assert host_admitido("catalogo.midominio.es:443")
        assert not host_admitido("otro.midominio.es")

    def test_trusted_origins_tambien_cuenta(self, monkeypatch):
        from app.config import settings
        from app.hosts import host_admitido
        monkeypatch.setattr(settings, "trusted_origins", "media.ejemplo.org:8443")
        assert host_admitido("media.ejemplo.org:8443")

    def test_asterisco_lo_desactiva(self, monkeypatch):
        from app.config import settings
        from app.hosts import host_admitido
        monkeypatch.setattr(settings, "allowed_hosts", "*")
        assert host_admitido("evil.example")

    def test_el_healthcheck_sigue_funcionando(self, cliente_sin_cuenta):
        r = cliente_sin_cuenta.get("/salud", headers={"host": "localhost:8000"})
        assert r.status_code in (200, 503)


class TestLimiteDeIntentos:
    """[R3-M3] Sin límite, una contraseña corta caía a base de probar."""

    @pytest.fixture
    def con_password(self, db, usuario):
        from app.cuentas import cifrar_password
        usuario.password_hash = cifrar_password("buena-contrasena")
        db.commit()
        return usuario

    def _entrar(self, cliente, usuario, password):
        return cliente.post("/cuentas/entrar/%d" % usuario.id, data={"password": password},
                            follow_redirects=False)

    def test_tras_cinco_fallos_ni_la_buena_entra_de_momento(self, cliente_sin_cuenta, con_password):
        for _ in range(6):
            self._entrar(cliente_sin_cuenta, con_password, "mala")
        r = self._entrar(cliente_sin_cuenta, con_password, "buena-contrasena")
        assert r.headers["location"] == "/cuentas"
        assert cliente_sin_cuenta.get("/estadisticas", follow_redirects=False).status_code == 303

    def test_durante_la_espera_no_se_calcula_scrypt(self, cliente_sin_cuenta, con_password, monkeypatch):
        for _ in range(6):
            self._entrar(cliente_sin_cuenta, con_password, "mala")
        from app.routers import cuentas as router_cuentas
        llamadas = []
        monkeypatch.setattr(router_cuentas, "comprobar_password",
                            lambda *a: llamadas.append(1) or False)
        self._entrar(cliente_sin_cuenta, con_password, "otra")
        assert llamadas == []

    def test_los_primeros_fallos_no_bloquean(self, cliente_sin_cuenta, con_password):
        for _ in range(4):
            self._entrar(cliente_sin_cuenta, con_password, "mala")
        r = self._entrar(cliente_sin_cuenta, con_password, "buena-contrasena")
        assert r.headers["location"] == "/"

    def test_la_espera_crece_y_tiene_tope(self):
        from app.intentos import ESPERA_MAXIMA, Limitador
        ahora = [1000.0]
        lim = Limitador(reloj=lambda: ahora[0])
        esperas = []
        for _ in range(30):
            lim.fallo(("ip", 1))
            esperas.append(lim.espera(("ip", 1)))
        assert esperas[:5] == [0] * 5
        assert esperas[5] < esperas[7] < esperas[9]
        assert max(esperas) == ESPERA_MAXIMA

    def test_un_acierto_lo_pone_a_cero(self):
        from app.intentos import Limitador
        lim = Limitador(reloj=lambda: 0.0)
        for _ in range(8):
            lim.fallo(("ip", 1))
        lim.acierto(("ip", 1))
        assert lim.espera(("ip", 1)) == 0

    def test_la_memoria_tiene_tope(self, monkeypatch):
        from app import intentos
        monkeypatch.setattr(intentos, "MAX_CLAVES", 100)
        lim = intentos.Limitador(reloj=lambda: 0.0)
        for n in range(1000):
            lim.fallo(("ip-%d" % n,))
        assert len(lim._fallos) <= 100

    def test_contrasenas_nuevas_de_al_menos_ocho(self, cliente_sin_cuenta, db):
        cliente_sin_cuenta.post("/cuentas", data={"nombre": "Corta", "password": "1234567"})
        assert db.query(Usuario).filter(Usuario.nombre == "Corta").count() == 0
        cliente_sin_cuenta.post("/cuentas", data={"nombre": "Larga", "password": "12345678"})
        assert db.query(Usuario).filter(Usuario.nombre == "Larga").count() == 1

    def test_http_basic_tambien_frena(self, monkeypatch):
        import base64

        from fastapi import HTTPException

        from app.auth import verify_auth
        from app.config import settings
        monkeypatch.setattr(settings, "enable_auth", True)
        monkeypatch.setattr(settings, "auth_password", "la-buena-de-verdad")

        class Peticion:
            client = type("C", (), {"host": "10.0.0.5"})()
            def __init__(self, password):
                cred = base64.b64encode(("admin:" + password).encode()).decode()
                self.headers = {"authorization": "Basic " + cred}

        codigos = []
        for _ in range(7):
            try:
                verify_auth(Peticion("mala"))
            except HTTPException as e:
                codigos.append(e.status_code)
        assert codigos[:5] == [401] * 5
        assert 429 in codigos
