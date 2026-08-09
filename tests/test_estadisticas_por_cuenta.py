"""Las estadísticas son de tu catálogo, no del de toda la casa.

Al añadir las cuentas, `/estadisticas` se acotó donde se listaban ítems --y ahí
se quedó. Los agregados (recuentos por estado y tipo, géneros, notas, décadas,
tiempo total, episodios vistos) siguieron sumando el catálogo de TODAS las
cuentas, que es justo lo contrario de lo que promete tener una cuenta propia.

No se veía ningún título ajeno, que es lo único que comprobaba el test que
había: se veían sus números. Un gráfico de géneros con los de tu pareja dice de
ella tanto como una lista de títulos.
"""
import json
import re

import pytest

from app.models import Episode, MediaItem, MediaStatus, MediaType
from app.services import generos


@pytest.fixture
def catalogo_ajeno(db, otro_usuario):  # noqa: C901
    """Cuatro ítems de otra cuenta, uno de cada tipo, con datos que se agregan:
    género, nota, año, duración y un episodio visto."""
    peli = MediaItem(
        usuario_id=otro_usuario.id, title="Peli suya", media_type=MediaType.PELICULA,
        status=MediaStatus.COMPLETADO, rating=9, year=1974,
        runtime_minutes=600,
    )
    libro = MediaItem(
        usuario_id=otro_usuario.id, title="Libro suyo", media_type=MediaType.LIBRO,
        status=MediaStatus.COMPLETADO, rating=8, year=1965,
        page_count=1000,
    )
    juego = MediaItem(
        usuario_id=otro_usuario.id, title="Juego suyo", media_type=MediaType.VIDEOJUEGO,
        status=MediaStatus.COMPLETADO, hltb_hours=50, year=1985,
    )
    serie = MediaItem(
        usuario_id=otro_usuario.id, title="Serie suya", media_type=MediaType.SERIE,
        status=MediaStatus.EN_PROGRESO, year=1995,
    )
    serie.episodes.append(Episode(season_number=1, episode_number=1,
                                  watched=True, runtime_minutes=300))
    db.add_all([peli, libro, juego, serie])
    for item, genero in ((peli, "Documental"), (libro, "Poesia"),
                         (juego, "Puzzle"), (serie, "Telenovela")):
        generos.asignar(db, item, genero)
    db.commit()
    return [peli, libro, juego, serie]


def _numeros_de(html: str, etiqueta: str) -> list:
    """Saca la serie de datos del gráfico cuyas etiquetas contengan `etiqueta`.

    Se lee el `<script>` renderizado en vez de la página visible porque los
    números de esta página SOLO están ahí: los gráficos los pinta Chart.js en un
    canvas, así que no hay texto que buscar.
    """
    patron = r"labels: (\[[^\]]*\]).*?data: (\[[^\]]*\])"
    for etiquetas, datos in re.findall(patron, html, re.S):
        if etiqueta in etiquetas:
            return json.loads(datos)
    return []


class TestLosAgregadosSonDeLaCuenta:
    def test_los_generos_no_incluyen_los_de_otra_cuenta(self, client, crear_item, catalogo_ajeno):
        # Nombres sin tildes a propósito: el gráfico va dentro de un <script> y
        # el `tojson` de Jinja escapa los no-ASCII, así que buscar "Fantasía"
        # en el HTML no encontraría nada aunque estuviera.
        crear_item(title="Mío", genres="Fantasia", status=MediaStatus.COMPLETADO)

        html = client.get("/estadisticas").text

        assert "Fantasia" in html
        for ajeno in ("Documental", "Poesia", "Puzzle", "Telenovela"):
            assert ajeno not in html, ajeno

    def test_el_reparto_por_tipo_no_cuenta_los_ajenos(self, client, crear_item, catalogo_ajeno):
        crear_item(title="Mi libro", media_type=MediaType.LIBRO)

        html = client.get("/estadisticas").text

        # [libros, películas, series, juegos, podcasts]
        por_tipo = _numeros_de(html, "Libros")
        assert por_tipo[:4] == [1, 0, 0, 0], por_tipo

    def test_las_notas_no_incluyen_las_ajenas(self, client, crear_item, catalogo_ajeno):
        crear_item(title="Mío", rating=5, status=MediaStatus.COMPLETADO)

        notas = _numeros_de(client.get("/estadisticas").text, '"5"')

        assert sum(notas) == 1, notas

    def test_las_decadas_no_incluyen_las_ajenas(self, client, crear_item, catalogo_ajeno):
        crear_item(title="Mío", year=2020)

        decadas = _numeros_de(client.get("/estadisticas").text, "2020")

        # Una sola década, la del ítem propio, con un solo ítem dentro.
        assert decadas == [1], decadas

    def test_el_tiempo_total_no_suma_el_ajeno(self, client, crear_item, catalogo_ajeno):
        """El catálogo ajeno son 10 h de película, 50 h de juego, 1000 páginas y
        un episodio de 5 h: si se colara, el número sería enorme."""
        crear_item(title="Mi peli", media_type=MediaType.PELICULA,
                   status=MediaStatus.COMPLETADO, runtime_minutes=120)

        html = client.get("/estadisticas").text
        horas = re.search(r'class="big-number">(\d+)<small> h', html)

        assert horas is not None
        assert int(horas.group(1)) == 2, horas.group(1)

    def test_los_episodios_vistos_ajenos_no_suman(self, client, crear_item, catalogo_ajeno):
        """Sin ítems propios el tiempo tiene que ser cero, no las 5 h del
        episodio que vio la otra cuenta."""
        html = client.get("/estadisticas").text
        horas = re.search(r'class="big-number">(\d+)<small> h', html)

        assert horas is not None
        assert int(horas.group(1)) == 0, horas.group(1)

    def test_lo_completado_por_mes_no_incluye_lo_ajeno(self, client, crear_item, catalogo_ajeno, db):
        """Los cuatro ajenos están completados hoy; el propio, ninguno."""
        from datetime import date

        for item in catalogo_ajeno:
            item.completed_at = date.today()
        db.commit()

        por_mes = _numeros_de(client.get("/estadisticas").text, "Ene")

        assert sum(por_mes) == 0, por_mes
