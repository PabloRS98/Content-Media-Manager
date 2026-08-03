"""Tests del selector de idioma en la búsqueda de libros.

El problema que arreglan: Google Books y Open Library no tienen ningún concepto
de idioma por defecto, así que buscar "Duna" podía devolver la portada o el
título en inglés sin forma de pedir la edición en español. `/buscar` ahora
acepta `idioma` ("es"/"en").

Verificado contra la API real de Google Books antes de dar esto por bueno:
`langRestrict` NO filtra de forma fiable — la misma consulta con
`langRestrict=es` y `langRestrict=en` puede devolver exactamente el mismo
listado mixto (confirmado en vivo con "the hobbit"). El filtro real que
importa es el que se hace en `googlebooks.py` sobre el campo `language` de
cada resultado, después de pedir un pool de candidatos más ancho que `limit`.

Las otras fuentes (TMDB, RAWG, iTunes) no se tocan: TMDB ya fuerza `es-ES` en
todas sus llamadas, y juegos/podcasts no tienen esta ambigüedad de idioma.
"""
from app.services import googlebooks, openlibrary


class TestGoogleBooksRestringePorIdioma:
    def _stub(self, monkeypatch, items=None):
        capturados = {}

        class RespuestaFalsa:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"items": items or []}

        def _get(url, params=None, **kwargs):
            capturados["params"] = params
            return RespuestaFalsa()

        monkeypatch.setattr(googlebooks.httpx, "get", _get)
        return capturados

    def _volumen(self, titulo, idioma):
        return {"id": titulo, "volumeInfo": {"title": titulo, "language": idioma}}

    def test_pide_langrestrict_es_cuando_se_pide_espanol(self, monkeypatch):
        capturados = self._stub(monkeypatch)
        googlebooks.search_books("duna", idioma="es")
        assert capturados["params"]["langRestrict"] == "es"

    def test_pide_langrestrict_en_cuando_se_pide_ingles(self, monkeypatch):
        capturados = self._stub(monkeypatch)
        googlebooks.search_books("dune", idioma="en")
        assert capturados["params"]["langRestrict"] == "en"

    def test_sin_idioma_no_restringe_nada(self, monkeypatch):
        """El enriquecimiento automático (enrich.py) no conoce el idioma del
        ítem: debe conservar el comportamiento de antes, sin restricción."""
        capturados = self._stub(monkeypatch)
        googlebooks.search_books("duna")
        assert "langRestrict" not in capturados["params"]

    def test_filtra_por_idioma_aunque_google_devuelva_una_mezcla(self, monkeypatch):
        """Reproduce lo verificado en vivo: Google ignora langRestrict y mezcla
        idiomas en la misma respuesta. El filtro tiene que hacerse aquí."""
        mixto = [
            self._volumen("The Hobbit", "en"),
            self._volumen("El hobbit", "es"),
            self._volumen("El arte de El hobbit", "es"),
        ]
        self._stub(monkeypatch, items=mixto)

        solo_es = googlebooks.search_books("hobbit", idioma="es")
        solo_en = googlebooks.search_books("hobbit", idioma="en")

        assert [r["title"] for r in solo_es] == ["El hobbit", "El arte de El hobbit"]
        assert [r["title"] for r in solo_en] == ["The Hobbit"]

    def test_sin_coincidencias_en_ese_idioma_devuelve_vacio_no_una_mezcla(self, monkeypatch):
        """Si no hay ninguna edición en el idioma pedido, el resultado correcto
        es una lista vacía (que activa el aviso de 'prueba a cambiar de
        idioma'), no colar silenciosamente una del otro idioma."""
        self._stub(monkeypatch, items=[self._volumen("Only in English", "en")])

        assert googlebooks.search_books("algo", idioma="es") == []

    def test_pide_mas_candidatos_de_los_que_muestra_cuando_hay_idioma(self, monkeypatch):
        """El filtrado ocurre después de traer los resultados: hace falta un
        pool más ancho que `limit`, o se descartaría casi todo."""
        capturados = self._stub(monkeypatch)
        googlebooks.search_books("duna", limit=8, idioma="es")
        assert capturados["params"]["maxResults"] == 32

    def test_no_pide_de_mas_si_no_hay_idioma(self, monkeypatch):
        capturados = self._stub(monkeypatch)
        googlebooks.search_books("duna", limit=8)
        assert capturados["params"]["maxResults"] == 8

    def test_el_pool_de_candidatos_no_supera_el_maximo_de_google(self, monkeypatch):
        capturados = self._stub(monkeypatch)
        googlebooks.search_books("duna", limit=20, idioma="es")
        assert capturados["params"]["maxResults"] == 40

    def test_respeta_el_limite_tras_filtrar(self, monkeypatch):
        mixto = [self._volumen(f"Libro {i}", "es") for i in range(10)]
        self._stub(monkeypatch, items=mixto)

        resultados = googlebooks.search_books("algo", limit=3, idioma="es")
        assert len(resultados) == 3


class TestOpenLibraryRestringePorIdioma:
    def _stub(self, monkeypatch):
        capturados = {}

        class RespuestaFalsa:
            def raise_for_status(self):
                pass

            status_code = 200

            def json(self):
                return {"docs": []}

        def _get(url, params=None, **kwargs):
            capturados["params"] = params
            return RespuestaFalsa()

        monkeypatch.setattr(openlibrary.httpx, "get", _get)
        return capturados

    def test_traduce_es_al_codigo_iso_639_2(self, monkeypatch):
        capturados = self._stub(monkeypatch)
        openlibrary.search_books("duna", idioma="es")
        assert capturados["params"]["language"] == "spa"

    def test_traduce_en_al_codigo_iso_639_2(self, monkeypatch):
        capturados = self._stub(monkeypatch)
        openlibrary.search_books("dune", idioma="en")
        assert capturados["params"]["language"] == "eng"

    def test_sin_idioma_no_filtra(self, monkeypatch):
        capturados = self._stub(monkeypatch)
        openlibrary.search_books("duna")
        assert "language" not in capturados["params"]


class TestEndpointDeBusqueda:
    def test_pasa_el_idioma_elegido_a_google_books(self, client, monkeypatch):
        recibido = {}

        def _stub(query, limit=8, year=None, idioma=None):
            recibido["idioma"] = idioma
            return []

        monkeypatch.setattr("app.routers.catalog.googlebooks.search_books", _stub)
        monkeypatch.setattr("app.routers.catalog.openlibrary.search_books",
                            lambda *a, **k: [])

        client.get("/buscar?tipo=libro&q=duna&idioma=en")
        assert recibido["idioma"] == "en"

    def test_por_defecto_busca_en_espanol(self, client, monkeypatch):
        recibido = {}
        monkeypatch.setattr("app.routers.catalog.googlebooks.search_books",
                            lambda query, limit=8, year=None, idioma=None: recibido.setdefault("idioma", idioma) or [])
        monkeypatch.setattr("app.routers.catalog.openlibrary.search_books",
                            lambda *a, **k: [])

        client.get("/buscar?tipo=libro&q=duna")
        assert recibido["idioma"] == "es"

    def test_un_idioma_desconocido_cae_a_espanol(self, client, monkeypatch):
        recibido = {}
        monkeypatch.setattr("app.routers.catalog.googlebooks.search_books",
                            lambda query, limit=8, year=None, idioma=None: recibido.setdefault("idioma", idioma) or [])
        monkeypatch.setattr("app.routers.catalog.openlibrary.search_books",
                            lambda *a, **k: [])

        client.get("/buscar?tipo=libro&q=duna&idioma=fr")
        assert recibido["idioma"] == "es"

    def test_el_fallback_a_open_library_hereda_el_mismo_idioma(self, client, monkeypatch):
        recibido = {}
        monkeypatch.setattr("app.routers.catalog.googlebooks.search_books",
                            lambda *a, **k: [])  # Google Books sin resultados
        monkeypatch.setattr("app.routers.catalog.openlibrary.search_books",
                            lambda query, limit=8, year=None, idioma=None: recibido.setdefault("idioma", idioma) or [])

        client.get("/buscar?tipo=libro&q=duna&idioma=en")
        assert recibido["idioma"] == "en"

    def test_el_selector_de_idioma_solo_aparece_para_libros(self, client):
        assert 'id="idioma-toggle"' in client.get("/catalogo?tipo=libro").text
        assert 'id="idioma-toggle"' not in client.get("/catalogo?tipo=pelicula").text
