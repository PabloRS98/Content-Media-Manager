"""Funciones puras: parseo de CSV, duraciones, estimaciones y matching.

No tocan red ni BD, así que corren en milisegundos."""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routers.imdb_import import _parse_date as imdb_parse_date  # noqa: E402
from app.routers.imdb_import import _parse_optional_int  # noqa: E402
from app.services.imports import _int, _parse_date, _rating5_to_10  # noqa: E402
from app.services.itunes import _duration_minutes  # noqa: E402


class TestDuracionDePodcast:
    @pytest.mark.parametrize("texto,esperado", [
        ("3600", 60),
        ("01:30:00", 90),
        ("30:00", 30),
        ("00:45:40", 46),   # 40 s redondea hacia arriba
        ("00:45:20", 45),
        ("", None),
        (None, None),
        ("no-es-un-numero", None),
    ])
    def test_duracion(self, texto, esperado):
        assert _duration_minutes(texto) == esperado


class TestConversionDeNotas:
    @pytest.mark.parametrize("entrada,esperado", [
        ("5", 10), ("4.5", 9), ("3", 6), ("0.5", 1),
        ("0", None), ("", None), ("abc", None),
    ])
    def test_escala_5_a_10(self, entrada, esperado):
        assert _rating5_to_10(entrada) == esperado

    def test_nunca_sale_del_rango(self):
        assert _rating5_to_10("99") == 10


class TestParseoDeFechas:
    @pytest.mark.parametrize("entrada,esperado", [
        ("2024-03-15", date(2024, 3, 15)),
        ("2024/03/15", date(2024, 3, 15)),
        ("15/03/2024", date(2024, 3, 15)),
        ("2024", date(2024, 1, 1)),
        ("", None),
        ("basura", None),
    ])
    def test_import_parse_date(self, entrada, esperado):
        assert _parse_date(entrada) == esperado

    @pytest.mark.parametrize("entrada,esperado", [
        ("2024-03-15", date(2024, 3, 15)),
        ("15-03-2024", date(2024, 3, 15)),
        ("", None),
        ("nope", None),
    ])
    def test_imdb_parse_date(self, entrada, esperado):
        resultado = imdb_parse_date(entrada)
        assert (resultado.date() if resultado else None) == esperado


class TestEnterosOpcionales:
    @pytest.mark.parametrize("entrada,esperado", [
        ("300", 300), ("300.0", 300), ("", None), (None, None), ("abc", None),
    ])
    def test_parse_optional_int(self, entrada, esperado):
        assert _parse_optional_int(entrada) == esperado
        assert _int(entrada or "") == esperado


class TestEstimacionDeTiempo:
    def _item(self, **kwargs):
        from app.models import MediaItem

        return MediaItem(**kwargs)

    def test_pelicula_usa_su_duracion(self, app_env):
        from app.models import MediaType
        from app.services.metadata import estimated_minutes

        assert estimated_minutes(self._item(media_type=MediaType.PELICULA,
                                           title="x", runtime_minutes=120)) == 120

    def test_libro_estima_por_paginas(self, app_env):
        from app.models import MediaType
        from app.services.metadata import estimated_minutes

        assert estimated_minutes(self._item(media_type=MediaType.LIBRO,
                                            title="x", page_count=200)) == 300

    def test_juego_convierte_horas(self, app_env):
        from app.models import MediaType
        from app.services.metadata import estimated_minutes

        assert estimated_minutes(self._item(media_type=MediaType.VIDEOJUEGO,
                                            title="x", hltb_hours=2.5)) == 150

    def test_sin_dato_devuelve_none(self, app_env):
        from app.models import MediaType
        from app.services.metadata import estimated_minutes

        assert estimated_minutes(self._item(media_type=MediaType.LIBRO, title="x")) is None


class TestSeleccionDeCoincidencia:
    def _item(self, titulo, tipo=None):
        from app.models import MediaItem, MediaType

        return MediaItem(media_type=tipo or MediaType.LIBRO, title=titulo)

    def test_descarta_resultados_sin_portada(self, app_env):
        from app.services.enrich import _pick_match

        assert _pick_match(self._item("Duna"), [{"title": "Duna", "cover_url": None}]) is None

    def test_descarta_titulos_incompatibles(self, app_env):
        from app.services.enrich import _pick_match

        resultados = [{"title": "Otra cosa", "cover_url": "https://x.test/a.jpg"}]
        assert _pick_match(self._item("Duna"), resultados) is None

    def test_acepta_titulo_compatible(self, app_env):
        from app.services.enrich import _pick_match

        resultados = [{"title": "Duna (edición especial)", "cover_url": "https://x.test/a.jpg"}]
        assert _pick_match(self._item("Duna"), resultados) is not None

    def test_prefiere_el_resultado_en_espanol_para_libros(self, app_env):
        from app.services.enrich import _pick_match

        resultados = [
            {"title": "Dune", "cover_url": "https://x.test/en.jpg", "language": "en"},
            {"title": "Dune", "cover_url": "https://x.test/es.jpg", "language": "es"},
        ]
        assert _pick_match(self._item("Dune"), resultados)["cover_url"] == "https://x.test/es.jpg"


class TestCodigoDeEpisodio:
    def test_formato(self, app_env):
        from app.models import Episode

        assert Episode(season_number=2, episode_number=5).code == "S02E05"
        assert Episode(season_number=10, episode_number=12).code == "S10E12"
