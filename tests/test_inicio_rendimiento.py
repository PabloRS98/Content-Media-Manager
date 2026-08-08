"""La portada no debe traerse el catálogo entero para enseñar doce fichas.

Con 3 000 pendientes se materializaban 3 000 objetos ORM --cada uno con todas
sus columnas, incluida `overview`, que es Text-- para descartar 2 988.
"""
import pytest

from app.models import Episode, MediaItem, MediaStatus, MediaType, Priority


@pytest.fixture
def contador_de_filas(db):
    """Cuenta las filas que devuelve cada SELECT sobre media_items."""
    from sqlalchemy import event

    motor = db.get_bind()
    sentencias: list[str] = []

    def _antes(conn, cursor, sentencia, parametros, contexto, muchos):
        if sentencia.lstrip().upper().startswith("SELECT"):
            sentencias.append(sentencia.replace("\n", " "))

    event.listen(motor, "before_cursor_execute", _antes)
    try:
        yield sentencias
    finally:
        event.remove(motor, "before_cursor_execute", _antes)


@pytest.fixture
def muchos_pendientes(usuario, db):
    def _crear(n=200, estado=MediaStatus.PENDIENTE):
        prioridades = [Priority.BAJA, Priority.MEDIA, Priority.ALTA]
        for i in range(n):
            db.add(MediaItem(
                usuario_id=usuario.id,
                title="Pendiente %03d" % i,
                media_type=MediaType.LIBRO,
                status=estado,
                priority=prioridades[i % 3],
            ))
        db.commit()
    return _crear


def selects_sin_limite(sentencias: list[str], tabla: str) -> list[str]:
    """SELECT de FILAS COMPLETAS sin LIMIT sobre `tabla`, que es lo que MC-M5
    prohíbe en la portada.

    Se excluyen dos cosas a propósito:

    - Los `count(*)`: los recuentos del resumen recorren la tabla entera y
      deben hacerlo.
    - Las proyecciones de unas pocas columnas. El problema de MC-M5 era
      materializar objetos ORM completos --con `overview`, que es Text-- para
      descartar casi todos; leer cuatro columnas cortas de todas las filas para
      agregarlas es otra cosa, y es lo que hace el perfil de gustos de las
      recomendaciones. Se detecta por `overview`, que es la columna cara y solo
      aparece cuando se piden filas enteras.
    """
    return [
        s for s in sentencias
        if " FROM %s" % tabla in s
        and "LIMIT" not in s.upper()
        and "count(" not in s
        and "%s.overview" % tabla in s
    ]


def test_inicio_no_materializa_todos_los_pendientes(client, muchos_pendientes, contador_de_filas):
    """Los recuentos (`count(*)`) sí recorren la tabla entera y deben hacerlo;
    lo que no puede haber es un SELECT de filas completas sin LIMIT."""
    muchos_pendientes(200)
    contador_de_filas.clear()

    assert client.get("/").status_code == 200

    sin_limite = selects_sin_limite(contador_de_filas, "media_items")
    assert sin_limite == [], (
        "la portada trae filas completas sin LIMIT:\n%s"
        % "\n".join(s[:120] for s in sin_limite)
    )


def test_el_orden_por_prioridad_se_conserva(client, db, muchos_pendientes):
    """El orden lo hacía Python; ahora lo hace SQL con un CASE. El resultado
    visible tiene que ser el mismo: primero prioridad, luego actividad."""
    muchos_pendientes(30)
    alta = db.query(MediaItem).filter(MediaItem.priority == Priority.ALTA).all()

    html = client.get("/").text
    posiciones = [html.find(">%s<" % i.title) for i in alta if html.find(">%s<" % i.title) >= 0]
    baja = db.query(MediaItem).filter(MediaItem.priority == Priority.BAJA).all()
    pos_baja = [html.find(">%s<" % i.title) for i in baja if html.find(">%s<" % i.title) >= 0]

    assert posiciones, "no salió ningún pendiente de prioridad alta en la portada"
    if pos_baja:
        assert max(posiciones) < min(pos_baja), "un pendiente de prioridad baja adelanta a uno alto"


def test_la_portada_sigue_mostrando_doce_pendientes(client, muchos_pendientes):
    muchos_pendientes(50)
    html = client.get("/").text
    assert html.count('class="card media-card"') >= 12


def test_proximamente_no_trae_todos_los_episodios(usuario, db, contador_de_filas):
    """`_upcoming` juntaba TODOS los episodios futuros y TODA la wishlist con
    fecha para quedarse con 6.

    Se prueba la función y no la página entera: al renderizar, cada tarjeta
    consulta sus propios episodios para `episode_stats()`, y ese es un N+1
    distinto y anterior (anotado como MC-X2) que ensuciaría la medición.
    """
    from datetime import date, timedelta

    from app.routers.home import _upcoming

    manana = date.today() + timedelta(days=1)
    for n in range(40):
        serie = MediaItem(usuario_id=usuario.id, title="Serie %02d" % n, media_type=MediaType.SERIE,
                          status=MediaStatus.EN_PROGRESO)
        serie.episodes.append(Episode(season_number=1, episode_number=1,
                                      air_date=manana + timedelta(days=n)))
        db.add(serie)
    db.commit()
    contador_de_filas.clear()

    entradas = _upcoming(db, usuario, limit=6)
    assert len(entradas) == 6

    sin_limite = selects_sin_limite(contador_de_filas, "episodes")
    assert sin_limite == [], (
        "trae todos los episodios futuros:\n%s"
        % "\n".join(s[:120] for s in sin_limite)
    )


class TestTarjetasEpisodicas:
    """[MC-X2] Cada tarjeta de serie o podcast llamaba a `episode_stats()`, que
    lee la relación `episodes`. Como es perezosa, pintar una página de catálogo
    con 24 series eran 24 consultas más, una por tarjeta, y crecían con lo que
    tuvieras: el caso malo no es el catálogo de prueba, es el de verdad."""

    @staticmethod
    def _consultas_de_episodios(sentencias: list[str]) -> list[str]:
        return [s for s in sentencias if " FROM episodes" in s]

    @pytest.fixture
    def series(self, usuario, db):
        def _crear(n, episodios=5):
            for i in range(n):
                serie = MediaItem(usuario_id=usuario.id, title="Serie %02d" % i,
                                  media_type=MediaType.SERIE,
                                  status=MediaStatus.EN_PROGRESO)
                for e in range(1, episodios + 1):
                    serie.episodes.append(Episode(season_number=1, episode_number=e,
                                                  name="Ep %d" % e, watched=e <= 2))
                db.add(serie)
            db.commit()
        return _crear

    def test_el_catalogo_no_consulta_una_vez_por_tarjeta(self, client, series, contador_de_filas):
        """El número de consultas no puede depender de cuántas series haya."""
        series(12)
        contador_de_filas.clear()

        assert client.get("/catalogo?tipo=serie").status_code == 200

        consultas = self._consultas_de_episodios(contador_de_filas)
        assert len(consultas) <= 2, (
            "%d consultas a episodes para 12 tarjetas:\n%s"
            % (len(consultas), "\n".join(s[:120] for s in consultas))
        )

    def test_los_recuentos_los_hace_la_base(self, client, series, contador_de_filas):
        """Arreglarlo con un `selectinload` quitaría el N+1, pero seguiría
        trayendo los 480 episodios de las 12 series para contar dos números por
        tarjeta. Los recuentos se piden agregados; de la tabla solo salen las
        filas de los 'próximos', una por serie como mucho."""
        series(12, episodios=40)
        contador_de_filas.clear()

        client.get("/catalogo?tipo=serie")

        consultas = self._consultas_de_episodios(contador_de_filas)
        assert any("count(" in s for s in consultas), (
            "ninguna consulta agrega: los episodios se están contando en Python\n%s"
            % "\n".join(s[:160] for s in consultas)
        )

    def test_las_tarjetas_siguen_diciendo_lo_mismo(self, client, series):
        """El dato pintado no cambia: 2 de 5 vistos y el próximo es el S01E03."""
        series(1)

        html = client.get("/catalogo?tipo=serie").text

        assert "2/5 ep" in html
        assert "próx. S01E03" in html

    def test_tambien_en_la_portada_y_en_las_listas(self, client, series, contador_de_filas):
        """La portada pinta tarjetas igual que el catálogo, y las listas
        automáticas también: arreglarlo en un sitio y no en los otros deja el
        problema donde estaba."""
        series(10)
        for ruta in ("/", "/listas"):
            contador_de_filas.clear()
            assert client.get(ruta).status_code == 200
            consultas = self._consultas_de_episodios(contador_de_filas)
            assert len(consultas) <= 3, (
                "%s: %d consultas a episodes" % (ruta, len(consultas))
            )


def test_el_calendario_sigue_trayendolo_todo(usuario, client, db):
    """`/calendario` sí necesita el listado completo: el LIMIT es solo de la
    portada, y meterlo aquí recortaría la vista en silencio."""
    from datetime import date, timedelta

    manana = date.today() + timedelta(days=1)
    for n in range(20):
        serie = MediaItem(usuario_id=usuario.id, title="Serie cal %02d" % n, media_type=MediaType.SERIE,
                          status=MediaStatus.EN_PROGRESO)
        serie.episodes.append(Episode(season_number=1, episode_number=1,
                                      air_date=manana + timedelta(days=n)))
        db.add(serie)
    db.commit()

    html = client.get("/calendario").text
    assert "Serie cal 00" in html
    assert "Serie cal 19" in html
