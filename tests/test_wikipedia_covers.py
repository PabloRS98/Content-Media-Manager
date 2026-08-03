"""Tests del fallback de portadas vía Wikipedia, en particular la verificación
de autor que evita aceptar páginas que comparten título por casualidad.

Caso real que motiva esto: "Seda" de Alessandro Baricco es también la
palabra para la tela. Antes de este fix, `enrich_missing_covers` le puso al
libro la foto de un telar (la página de Wikipedia sobre la fibra), porque
`_fetch_page_image` solo comprobaba que el título coincidiera, no que la
página fuera realmente sobre el libro.
"""
from app.services import wikipedia_covers


def _respuesta_falsa(titulo, thumb, extract):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"query": {"pages": {"1": {
                "title": titulo,
                "thumbnail": {"source": thumb} if thumb else {},
                "extract": extract,
            }}}}
    return Resp()


class TestVerificacionDeAutor:
    def test_rechaza_la_pagina_si_el_autor_no_aparece_en_el_extracto(self, monkeypatch):
        """Reproduce el caso real: la página de Wikipedia sobre la tela no
        menciona a Alessandro Baricco en ningún sitio."""
        monkeypatch.setattr(wikipedia_covers.httpx, "get", lambda *a, **k: _respuesta_falsa(
            "Seda", "https://ejemplo/telar.jpg",
            "El hilo de seda es una fibra natural formada por proteínas...",
        ))

        assert wikipedia_covers.search_book_cover("Seda", author="Alessandro Baricco") == []

    def test_acepta_la_pagina_si_el_autor_si_aparece(self, monkeypatch):
        monkeypatch.setattr(wikipedia_covers.httpx, "get", lambda *a, **k: _respuesta_falsa(
            "Seda (novela)", "https://ejemplo/portada.jpg",
            "Seda es una novela de Alessandro Baricco publicada en 1996...",
        ))

        resultado = wikipedia_covers.search_book_cover("Seda", author="Alessandro Baricco")
        assert resultado and resultado[0]["cover_url"] == "https://ejemplo/portada.jpg"

    def test_sin_autor_conocido_no_se_puede_verificar_y_se_deja_pasar(self, monkeypatch):
        """Comportamiento anterior conservado cuando no hay autor con el que
        comparar (algunos ítems no lo tienen)."""
        monkeypatch.setattr(wikipedia_covers.httpx, "get", lambda *a, **k: _respuesta_falsa(
            "Seda", "https://ejemplo/telar.jpg", "El hilo de seda es una fibra natural...",
        ))

        resultado = wikipedia_covers.search_book_cover("Seda", author="")
        assert resultado and resultado[0]["cover_url"] == "https://ejemplo/telar.jpg"

    def test_reconoce_el_autor_aunque_el_extracto_solo_cite_el_apellido(self, monkeypatch):
        monkeypatch.setattr(wikipedia_covers.httpx, "get", lambda *a, **k: _respuesta_falsa(
            "Seda (novela)", "https://ejemplo/portada.jpg",
            "Novela de Baricco publicada en 1996, ambientada en el siglo XIX...",
        ))

        resultado = wikipedia_covers.search_book_cover("Seda", author="Alessandro Baricco")
        assert resultado != []

    def test_sin_extracto_se_rechaza_si_se_conoce_el_autor(self, monkeypatch):
        """Sin extracto no hay forma de verificar: mejor no arriesgar una
        portada mala que dejarla pasar sin comprobar nada."""
        monkeypatch.setattr(wikipedia_covers.httpx, "get", lambda *a, **k: _respuesta_falsa(
            "Seda", "https://ejemplo/telar.jpg", None,
        ))

        assert wikipedia_covers.search_book_cover("Seda", author="Alessandro Baricco") == []

    def test_una_pagina_marcada_como_missing_se_ignora(self, monkeypatch):
        class RespuestaMissing:
            def raise_for_status(self):
                pass

            def json(self):
                return {"query": {"pages": {"-1": {"missing": "", "title": "Algo"}}}}

        monkeypatch.setattr(wikipedia_covers.httpx, "get", lambda *a, **k: RespuestaMissing())
        assert wikipedia_covers.search_book_cover("Algo Que No Existe", author="Nadie") == []
