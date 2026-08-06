"""Ninguna credencial debe acabar en los logs del contenedor.

`test_seguridad.py` ya prueba la función `log_fallo_api` por dentro. Aquí se
prueban las *llamadas*: que cada cliente de API la use de verdad en el camino
que se recorre cuando la petición falla. Es la diferencia entre tener el
mecanismo y aplicarlo, que es justo donde estaba el fallo.

Los `docker-compose.yml` usan el driver `json-file`, así que estos logs
persisten en disco y acaban fácilmente pegados en un issue.
"""
import logging

import httpx
import pytest

from app.services import googlebooks, rawg, tmdb

CLAVE_FICTICIA = "CLAVE-RECONOCIBLE-DE-TEST"


def respuesta_401(url_con_clave: str):
    """Devuelve un `httpx.Response` real de 401.

    Se construye completo, con su `request`, para que `raise_for_status()`
    genere el mensaje auténtico de httpx: el que incluye la URL entera y es la
    razón de que exista `_logging_utils`.
    """
    peticion = httpx.Request("GET", url_con_clave)
    return httpx.Response(401, request=peticion)


@pytest.mark.parametrize(
    "modulo, ajuste, url, llamada",
    [
        (
            googlebooks,
            "google_books_api_key",
            "https://www.googleapis.com/books/v1/volumes?q=dune&key=" + CLAVE_FICTICIA,
            lambda: googlebooks.search_books("dune"),
        ),
        (
            tmdb,
            "tmdb_api_key",
            "https://api.themoviedb.org/3/search/movie?api_key=" + CLAVE_FICTICIA,
            lambda: tmdb.search_movies("dune"),
        ),
        (
            rawg,
            "rawg_api_key",
            "https://api.rawg.io/api/games?key=" + CLAVE_FICTICIA,
            lambda: rawg.search_games("doom"),
        ),
    ],
    ids=["googlebooks", "tmdb", "rawg"],
)
def test_un_401_no_escribe_la_api_key_en_el_log(
    modulo, ajuste, url, llamada, monkeypatch, caplog
):
    monkeypatch.setattr(modulo.settings, ajuste, CLAVE_FICTICIA, raising=False)
    monkeypatch.setattr(modulo.httpx, "get", lambda *a, **k: respuesta_401(url))

    with caplog.at_level(logging.DEBUG):
        assert llamada() == []

    assert CLAVE_FICTICIA not in caplog.text
    # El log tiene que seguir sirviendo para diagnosticar: sin el código HTTP,
    # quitar la clave lo dejaría mudo.
    assert "401" in caplog.text
