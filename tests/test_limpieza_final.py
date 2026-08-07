"""Los hallazgos sueltos de la Fase 5: MC-M15, MC-M13, MC-M14 y MC-M17."""
import ast
from pathlib import Path

import pytest
from sqlalchemy import event

from app.models import MediaItem, MediaStatus, MediaType
from app.services import enrich

RAIZ = Path(__file__).resolve().parent.parent


class TestListasSinMutarElORM:
    """[MC-M15] `item_count` se inyectaba en el `__dict__` de objetos del ORM."""

    def test_no_se_inyecta_item_count_en_los_modelos(self):
        fuente = (RAIZ / "app" / "routers" / "lists.py").read_text(encoding="utf-8")
        assert "lista.item_count" not in fuente

    def test_listas_automaticas_cuentan_con_una_sola_consulta(self, client, db, listas_dinamicas):
        """Eran cuatro COUNT, uno por lista automática."""
        contador = []

        def _antes(conn, cursor, sentencia, parametros, contexto, muchos):
            if "count(" in sentencia.lower() and "media_items" in sentencia.lower():
                contador.append(sentencia)

        motor = db.get_bind()
        event.listen(motor, "before_cursor_execute", _antes)
        try:
            assert client.get("/listas").status_code == 200
        finally:
            event.remove(motor, "before_cursor_execute", _antes)

        assert len(contador) <= 1, "%d consultas de recuento\n%s" % (
            len(contador), "\n".join(s.replace("\n", " ")[:90] for s in contador)
        )

    def test_los_recuentos_siguen_siendo_correctos(self, client, db, crear_item, listas_dinamicas):
        crear_item(title="Uno", status=MediaStatus.PENDIENTE)
        crear_item(title="Dos", status=MediaStatus.PENDIENTE)
        crear_item(title="Tres", status=MediaStatus.COMPLETADO)

        html = client.get("/listas").text
        assert "2 ítems" in html
        assert "1 ítem<" in html or "1 ítem " in html


class TestEnriquecimientoRobusto:
    """[MC-M13] Un solo commit al final de un lote de 30."""

    @pytest.fixture
    def cinco_sin_portada(self, db):
        for n in range(5):
            db.add(MediaItem(title="Sin portada %d" % n, media_type=MediaType.LIBRO,
                             status=MediaStatus.PENDIENTE, cover_url=None))
        db.commit()

    def test_un_fallo_a_mitad_de_lote_conserva_lo_anterior(self, db, cinco_sin_portada, monkeypatch):
        """Con el 5º ítem reventando, los 4 primeros quedan con portada."""
        monkeypatch.setattr(enrich, "SLEEP_BETWEEN", 0)

        def _buscar(item):
            if item.title.endswith("4"):
                raise RuntimeError("API caída")
            return [{"title": item.title, "cover_url": "https://ejemplo.test/%s.jpg" % item.title}]

        monkeypatch.setattr(enrich, "_search_for", _buscar)

        enrich.enrich_missing_covers(db)

        con_portada = db.query(MediaItem).filter(MediaItem.cover_url.isnot(None)).count()
        assert con_portada == 4

    def test_un_fallo_al_aplicar_tampoco_tumba_el_lote(self, db, cinco_sin_portada, monkeypatch):
        """El try solo envolvía la búsqueda: un fallo en `metadata.enrich_item`
        rompía el lote entero."""
        monkeypatch.setattr(enrich, "SLEEP_BETWEEN", 0)
        monkeypatch.setattr(
            enrich, "_search_for",
            lambda item: [{"title": item.title, "cover_url": "https://ejemplo.test/a.jpg"}],
        )

        original = enrich._aplicar_coincidencia

        def _revienta(db_, item, match):
            if item.title.endswith("2"):
                raise RuntimeError("fallo al aplicar")
            return original(db_, item, match)

        monkeypatch.setattr(enrich, "_aplicar_coincidencia", _revienta)

        resultado = enrich.enrich_missing_covers(db)

        assert resultado["procesados"] == 5
        assert db.query(MediaItem).filter(MediaItem.cover_url.isnot(None)).count() == 4


class TestClientesDeApiHonestos:
    """[MC-M14] Google Books se llamaba suplantando el UA de Chrome 120."""

    def test_google_books_se_identifica(self):
        from app.services import googlebooks

        assert "MediaCatalog" in googlebooks.USER_AGENT
        assert "Mozilla" not in googlebooks.USER_AGENT
        assert "Chrome" not in googlebooks.USER_AGENT

    def test_los_clientes_de_api_se_identifican(self):
        """`hltb.py` es la excepción documentada: HowLongToBeat no tiene API
        pública y el propio módulo se declara frágil a propósito."""
        excepciones = {"hltb.py"}
        for fichero in (RAIZ / "app" / "services").glob("*.py"):
            if fichero.name in excepciones:
                continue
            texto = fichero.read_text(encoding="utf-8")
            if "User-Agent" not in texto:
                continue
            assert "Mozilla/5.0" not in texto, fichero.name

    def test_hltb_sigue_siendo_la_excepcion_documentada(self):
        texto = (RAIZ / "app" / "services" / "hltb.py").read_text(encoding="utf-8")
        assert "Mozilla" in texto  # suplanta, y está justificado
        assert "FRÁGIL" in texto  # el módulo lo declara en su docstring


class TestImportsYRegex:
    """[MC-M17] `import re` y `import time` dentro de funciones."""

    @pytest.mark.parametrize("fichero", ["enrich.py", "googlebooks.py"])
    def test_no_hay_imports_dentro_de_funciones(self, fichero):
        arbol = ast.parse((RAIZ / "app" / "services" / fichero).read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.FunctionDef):
                dentro = [n for n in ast.walk(nodo) if isinstance(n, ast.Import | ast.ImportFrom)]
                assert dentro == [], "%s: %s" % (fichero, nodo.name)

    def test_los_patrones_estan_compilados_a_nivel_de_modulo(self):
        assert hasattr(enrich, "_RE_PARENTESIS")
        assert hasattr(enrich, "_RE_CORCHETES")

    @pytest.mark.parametrize("entrada, esperado", [
        ("The Well of Ascension (Mistborn, #2)", "The Well of Ascension"),
        ("Duna [edición ilustrada]", "Duna"),
        ("Sin adornos", "Sin adornos"),
        ("", ""),
        (None, ""),
    ])
    def test_la_limpieza_de_titulos_es_la_misma_en_los_dos_sitios(self, entrada, esperado):
        """`_search_for` y `_pick_match.clean` repetían el mismo regex escrito
        dos veces cada uno."""
        assert enrich._limpiar_titulo(entrada) == esperado
