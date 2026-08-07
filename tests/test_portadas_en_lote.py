"""El lote de portadas no se procesa dentro de la petición HTTP.

BATCH_SIZE=30 x SLEEP_BETWEEN=0.7s son 21 s mínimo, y más de 2 minutos con las
APIs lentas. `/catalogo/completar-portadas` ya lo había resuelto;
`/importar/completar-portadas` hacía lo mismo de forma síncrona.

Nota sobre la medición: `TestClient` ejecuta los `BackgroundTasks` **dentro**
de la llamada `client.post(...)`, así que desde aquí no se puede cronometrar la
respuesta. Lo que sí se puede comprobar --y es lo que de verdad importa-- es
que el manejador devuelve el fragmento de "en marcha" en vez de esperar al
resultado. El tiempo real se verificó contra el contenedor.
"""
import pytest

from app.services import enrich


@pytest.fixture(autouse=True)
def lote_parado(monkeypatch):
    """Estado limpio por test: el lote es un global del módulo."""
    monkeypatch.setattr(enrich, "_estado_lote", {"corriendo": False, "resultado": None})


@pytest.fixture
def lote_espia(monkeypatch):
    """Sustituye el trabajo real del lote y cuenta cuántas veces se lanza."""
    llamadas = []

    def _falso(db):
        llamadas.append(1)
        return {"procesados": 3, "encontrados": 2, "restantes": 1}

    monkeypatch.setattr(enrich, "enrich_missing_covers", _falso)
    return llamadas


class TestNoBloquearLaPeticion:
    def test_completar_portadas_no_procesa_dentro_de_la_peticion(self, client, lote_espia):
        r = client.post("/importar/completar-portadas")
        assert r.status_code == 200
        # El fragmento que vuelve es el de "en marcha", no el del resultado.
        assert "portadas encontradas" not in r.text
        assert "/importar/estado-portadas" in r.text
        assert "every" in r.text  # hx-trigger de refresco

    def test_el_lote_llega_a_ejecutarse(self, client, lote_espia):
        """La otra mitad: mandarlo al fondo no puede significar no hacerlo.
        TestClient corre los BackgroundTasks al terminar la petición."""
        client.post("/importar/completar-portadas")
        assert len(lote_espia) == 1


class TestUnSoloLoteALaVez:
    def test_no_se_lanzan_dos_lotes_a_la_vez(self, client, lote_espia, monkeypatch):
        monkeypatch.setattr(enrich, "_estado_lote", {"corriendo": True, "resultado": None})
        r = client.post("/importar/completar-portadas")
        assert r.status_code == 200
        assert lote_espia == [], "se lanzó un segundo lote con uno ya en marcha"

    def test_los_dos_endpoints_comparten_el_mismo_candado(self, client, lote_espia, monkeypatch):
        """`/importar/completar-portadas` no consultaba el estado, así que se
        podía lanzar en paralelo con el lote de `/catalogo`: dos tandas sobre
        los mismos ítems, duplicando peticiones a APIs gratuitas con cuota."""
        monkeypatch.setattr(enrich, "_estado_lote", {"corriendo": True, "resultado": None})
        client.post("/catalogo/completar-portadas", follow_redirects=False)
        client.post("/importar/completar-portadas")
        assert lote_espia == []


class TestPanelDeEstado:
    def test_el_estado_dice_que_esta_en_marcha(self, client, monkeypatch):
        monkeypatch.setattr(enrich, "_estado_lote", {"corriendo": True, "resultado": None})
        r = client.get("/importar/estado-portadas")
        assert r.status_code == 200
        assert "/importar/estado-portadas" in r.text  # sigue refrescándose

    def test_el_estado_muestra_el_resultado_al_terminar(self, client, monkeypatch):
        monkeypatch.setattr(enrich, "_estado_lote", {
            "corriendo": False,
            "resultado": {"procesados": 30, "encontrados": 12, "restantes": 5},
        })
        r = client.get("/importar/estado-portadas")
        assert "12 portadas encontradas" in r.text
        assert "5" in r.text

    def test_el_fragmento_deja_de_refrescarse_cuando_termina(self, client, monkeypatch):
        """Sin esto el navegador seguiría pidiendo el estado para siempre."""
        monkeypatch.setattr(enrich, "_estado_lote", {
            "corriendo": False,
            "resultado": {"procesados": 30, "encontrados": 12, "restantes": 0},
        })
        r = client.get("/importar/estado-portadas")
        assert "hx-trigger" not in r.text

    def test_saber_si_termino_es_lo_que_antes_no_se_podia(self, client, monkeypatch):
        """El mensaje viejo era "vuelve en un minuto y recarga", que es una
        forma educada de no saberlo."""
        monkeypatch.setattr(enrich, "_estado_lote", {
            "corriendo": False,
            "resultado": {"procesados": 0, "encontrados": 0, "restantes": 0},
        })
        assert "No queda ningún ítem sin portada" in client.get("/importar/estado-portadas").text


class TestReservaDelLote:
    def test_reservar_dos_veces_seguidas_falla_la_segunda(self):
        assert enrich.reservar_lote() is True
        assert enrich.reservar_lote() is False

    def test_la_reserva_se_libera_al_terminar(self, db, lote_espia):
        assert enrich.reservar_lote() is True
        enrich.enrich_missing_covers_en_segundo_plano(lambda: db, ya_reservado=True)
        assert enrich.estado_actual()["corriendo"] is False
        assert enrich.estado_actual()["resultado"] is not None
