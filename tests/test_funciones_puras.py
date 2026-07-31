"""Tests de las funciones sin estado ni red: parseo de CSV, conversión de notas,
duraciones y selección de coincidencias. Son las más baratas de cubrir y donde
más fácil se cuelan regresiones al tocar los importadores.
"""
from datetime import date

import pytest

from app.models import MediaItem, MediaType
from app.routers.imdb_import import _get as imdb_get, _parse_date as imdb_parse_date, _parse_optional_int
from app.services.enrich import _pick_match
from app.services.imports import _get, _int, _parse_date, _rating5_to_10
from app.services.itunes import _duration_minutes
from app.services.metadata import estimated_minutes


class TestLecturaDeColumnas:
    def test_encuentra_la_columna_sea_cual_sea_la_mayuscula(self):
        assert _get({"TÍTULO": "Duna"}, "título") == "Duna"

    def test_prueba_los_nombres_alternativos_en_orden(self):
        assert _get({"Author": "Herbert"}, "Authors", "Author") == "Herbert"

    def test_devuelve_cadena_vacia_si_no_hay_ninguna(self):
        assert _get({"Otra": "x"}, "Title") == ""

    def test_se_salta_las_columnas_vacias(self):
        """Un CSV bilingüe trae 'Title' y 'Título'; si la primera viene vacía
        hay que seguir buscando, no quedarse con el hueco."""
        assert _get({"Title": "", "Título": "Duna"}, "Title", "Título") == "Duna"


class TestNotas:
    @pytest.mark.parametrize("entrada,esperado", [
        ("5", 10), ("4.5", 9), ("3", 6), ("0.5", 1),
        ("0", None), ("", None), ("no es un numero", None),
    ])
    def test_convierte_de_escala_5_a_escala_10(self, entrada, esperado):
        assert _rating5_to_10(entrada) == esperado

    def test_nunca_se_sale_del_rango_1_10(self):
        assert _rating5_to_10("99") == 10
        assert _rating5_to_10("-3") is None


class TestFechas:
    @pytest.mark.parametrize("entrada,esperado", [
        ("2024/03/15", date(2024, 3, 15)),
        ("2024-03-15", date(2024, 3, 15)),
        ("15/03/2024", date(2024, 3, 15)),
        ("2024", date(2024, 1, 1)),
    ])
    def test_acepta_los_formatos_de_los_distintos_exports(self, entrada, esperado):
        assert _parse_date(entrada) == esperado

    def test_devuelve_none_si_no_reconoce_el_formato(self):
        assert _parse_date("marzo de 2024") is None
        assert _parse_date("") is None

    def test_el_parser_de_imdb_acepta_sus_formatos(self):
        assert imdb_parse_date("2024-03-15").date() == date(2024, 3, 15)
        assert imdb_parse_date("15-03-2024").date() == date(2024, 3, 15)
        assert imdb_parse_date("basura") is None


class TestEnteros:
    @pytest.mark.parametrize("entrada,esperado", [
        ("300", 300), ("300.7", 300), ("", None), ("x", None),
    ])
    def test_tolera_decimales_y_basura(self, entrada, esperado):
        assert _int(entrada) == esperado
        assert _parse_optional_int(entrada) == esperado


class TestDuracionDePodcast:
    @pytest.mark.parametrize("entrada,esperado", [
        ("1:30:00", 90),      # h:m:s
        ("45:00", 45),        # m:s
        ("30:45", 31),        # los segundos >= 30 redondean hacia arriba
        ("3600", 60),         # segundos sueltos
        (None, None), ("", None), ("abc", None),
    ])
    def test_normaliza_los_formatos_del_rss(self, entrada, esperado):
        assert _duration_minutes(entrada) == esperado


class TestMinutosEstimados:
    def test_pelicula_usa_su_duracion(self):
        item = MediaItem(media_type=MediaType.PELICULA, title="x", runtime_minutes=120)
        assert estimated_minutes(item) == 120

    def test_libro_estima_a_partir_de_las_paginas(self):
        item = MediaItem(media_type=MediaType.LIBRO, title="x", page_count=200)
        assert estimated_minutes(item) == 300  # 200 * 1.5

    def test_juego_convierte_las_horas_de_hltb(self):
        item = MediaItem(media_type=MediaType.VIDEOJUEGO, title="x", hltb_hours=2.5)
        assert estimated_minutes(item) == 150

    def test_serie_cae_a_45_minutos_si_no_hay_dato(self, crear_serie):
        serie = crear_serie(temporadas=1, por_temporada=1)
        serie.episodes[0].runtime_minutes = None
        assert estimated_minutes(serie) == 45

    @pytest.mark.parametrize("tipo,campo", [
        (MediaType.PELICULA, "runtime_minutes"),
        (MediaType.LIBRO, "page_count"),
        (MediaType.VIDEOJUEGO, "hltb_hours"),
    ])
    def test_devuelve_none_cuando_falta_el_dato(self, tipo, campo):
        item = MediaItem(media_type=tipo, title="x", **{campo: None})
        assert estimated_minutes(item) is None


class TestSeleccionDeCoincidencia:
    def _resultado(self, titulo, **extra):
        return {"title": titulo, "cover_url": "https://ejemplo/portada.jpg", **extra}

    def test_descarta_los_resultados_sin_portada(self):
        item = MediaItem(media_type=MediaType.LIBRO, title="Duna")
        assert _pick_match(item, [{"title": "Duna", "cover_url": None}]) is None

    def test_devuelve_none_si_ningun_titulo_encaja(self):
        item = MediaItem(media_type=MediaType.LIBRO, title="Duna")
        assert _pick_match(item, [self._resultado("Los Pilares de la Tierra")]) is None

    def test_ignora_mayusculas_y_puntuacion(self):
        item = MediaItem(media_type=MediaType.LIBRO, title="El Señor de los Anillos")
        elegido = _pick_match(item, [self._resultado("el señor de los anillos")])
        assert elegido is not None

    def test_en_libros_prefiere_la_edicion_en_espanol(self):
        item = MediaItem(media_type=MediaType.LIBRO, title="Dune")
        elegido = _pick_match(item, [
            self._resultado("Dune", language="en"),
            self._resultado("Dune", language="es"),
        ])
        assert elegido["language"] == "es"


class TestLecturaDeColumnasIMDb:
    def test_encuentra_la_columna_ignorando_mayusculas(self):
        assert imdb_get({"Title Type": "movie"}, "title type") == "movie"

    def test_devuelve_cadena_vacia_si_falta(self):
        assert imdb_get({"Otra": "x"}, "Title") == ""
