"""Las décadas se agregan en SQL, no trayendo la columna entera a Python.

Es la misma función `stats` que **sí** hace lo correcto con las sumas de
tiempo, y que lo comenta: "las tres primeras sumas las hace SQLite (func.sum)
en vez de traer todos los MediaItem completos solo para sumar un campo".

Los géneros se quedan en Python a propósito: `genres` es una cadena separada
por comas, no una relación, así que no hay forma de agregarlos en SQL. Ese es
el problema de fondo, y su solución es normalizarlos a tabla (N4 del informe).
"""
import pytest
from sqlalchemy import event

from app.models import MediaItem, MediaType


@pytest.fixture
def filas_leidas(db):
    """Cuenta las filas que devuelve cada SELECT sobre la columna `year`."""
    motor = db.get_bind()
    consultas: list[str] = []

    def _antes(conn, cursor, sentencia, parametros, contexto, muchos):
        limpia = " ".join(sentencia.split())
        # La consulta de décadas menciona `year` y no trae la fila entera: un
        # `SELECT *` del ORM también lleva `year`, pero incluye `title`.
        if ("media_items.year" in limpia
                and "media_items.title" not in limpia
                and limpia.upper().startswith("SELECT")):
            consultas.append(limpia)

    event.listen(motor, "before_cursor_execute", _antes)
    try:
        yield consultas
    finally:
        event.remove(motor, "before_cursor_execute", _antes)


@pytest.fixture
def catalogo_por_decadas(usuario, db):
    """60 ítems repartidos en 3 décadas."""
    for i in range(60):
        db.add(MediaItem(
            usuario_id=usuario.id,
            title="Peli %02d" % i,
            media_type=MediaType.PELICULA,
            year=1990 + (i % 3) * 10 + (i % 5),
        ))
    db.commit()


def test_la_grafica_de_decadas_se_agrega_en_sql(client, catalogo_por_decadas, filas_leidas):
    """Antes se traía una fila por ítem para contarlas en Python."""
    assert client.get("/estadisticas").status_code == 200

    assert filas_leidas, "no se consultó `year` en ninguna parte"
    for consulta in filas_leidas:
        assert "GROUP BY" in consulta.upper(), (
            "la consulta de décadas trae la columna entera:\n%s" % consulta
        )


def test_las_decadas_salen_bien_agrupadas(client, db, catalogo_por_decadas):
    html = client.get("/estadisticas").text
    # 1990, 2000 y 2010 con 20 ítems cada una.
    for decada in (1990, 2000, 2010):
        assert str(decada) in html


def test_un_catalogo_sin_anos_no_rompe_la_grafica(usuario, client, db):
    db.add(MediaItem(usuario_id=usuario.id, title="Sin año", media_type=MediaType.LIBRO, year=None))
    db.commit()
    assert client.get("/estadisticas").status_code == 200


def test_los_generos_siguen_contandose(usuario, client, db):
    """Los géneros no se pueden agregar en SQL mientras sean una cadena: este
    test fija que la parte que NO cambia sigue funcionando."""
    for n in range(3):
        db.add(MediaItem(usuario_id=usuario.id, title="Con género %d" % n, media_type=MediaType.PELICULA,
                         genres="Drama, Crimen"))
    db.commit()
    html = client.get("/estadisticas").text
    assert "Drama" in html
