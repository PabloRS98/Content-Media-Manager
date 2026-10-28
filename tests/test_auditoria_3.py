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
from app.models import Episode, MediaItem, MediaStatus, MediaType
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
