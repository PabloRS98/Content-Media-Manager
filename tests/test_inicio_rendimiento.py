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
def muchos_pendientes(db):
    def _crear(n=200, estado=MediaStatus.PENDIENTE):
        prioridades = [Priority.BAJA, Priority.MEDIA, Priority.ALTA]
        for i in range(n):
            db.add(MediaItem(
                title="Pendiente %03d" % i,
                media_type=MediaType.LIBRO,
                status=estado,
                priority=prioridades[i % 3],
            ))
        db.commit()
    return _crear


def selects_sin_limite(sentencias: list[str], tabla: str) -> list[str]:
    return [
        s for s in sentencias
        if " FROM %s" % tabla in s and "LIMIT" not in s.upper() and "count(" not in s
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


def test_proximamente_no_trae_todos_los_episodios(db, contador_de_filas):
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
        serie = MediaItem(title="Serie %02d" % n, media_type=MediaType.SERIE,
                          status=MediaStatus.EN_PROGRESO)
        serie.episodes.append(Episode(season_number=1, episode_number=1,
                                      air_date=manana + timedelta(days=n)))
        db.add(serie)
    db.commit()
    contador_de_filas.clear()

    entradas = _upcoming(db, limit=6)
    assert len(entradas) == 6

    sin_limite = selects_sin_limite(contador_de_filas, "episodes")
    assert sin_limite == [], (
        "trae todos los episodios futuros:\n%s"
        % "\n".join(s[:120] for s in sin_limite)
    )


def test_el_calendario_sigue_trayendolo_todo(client, db):
    """`/calendario` sí necesita el listado completo: el LIMIT es solo de la
    portada, y meterlo aquí recortaría la vista en silencio."""
    from datetime import date, timedelta

    manana = date.today() + timedelta(days=1)
    for n in range(20):
        serie = MediaItem(title="Serie cal %02d" % n, media_type=MediaType.SERIE,
                          status=MediaStatus.EN_PROGRESO)
        serie.episodes.append(Episode(season_number=1, episode_number=1,
                                      air_date=manana + timedelta(days=n)))
        db.add(serie)
    db.commit()

    html = client.get("/calendario").text
    assert "Serie cal 00" in html
    assert "Serie cal 19" in html
