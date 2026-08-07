"""La importación de IMDb no debe hacer una consulta por fila del CSV.

Un export de "Your Ratings" de un usuario con historial largo tiene 2 000-5 000
filas. Una consulta por fila son miles de round-trips secuenciales dentro de una
sola petición HTTP, y hasta [MC-M1] cada una era además un escaneo completo.
"""
import csv
import io

import pytest
from sqlalchemy import event

from app.models import MediaItem


@pytest.fixture
def contador_sql(db):
    """Cuenta las sentencias SELECT que salen al motor del test."""
    motor = db.get_bind()
    lecturas: list[str] = []

    def _antes(conn, cursor, sentencia, parametros, contexto, muchos):
        if sentencia.lstrip().upper().startswith("SELECT"):
            lecturas.append(sentencia)

    event.listen(motor, "before_cursor_execute", _antes)
    try:
        yield lecturas
    finally:
        event.remove(motor, "before_cursor_execute", _antes)


def csv_de_imdb(filas: int, desde: int = 0) -> bytes:
    salida = io.StringIO()
    escritor = csv.writer(salida)
    escritor.writerow(["Const", "Title", "Title Type", "Year", "Your Rating"])
    for n in range(desde, desde + filas):
        escritor.writerow(["tt%07d" % n, "Peli %04d" % n, "movie", 2020, 8])
    return salida.getvalue().encode("utf-8")


def importar(client, contenido: bytes):
    return client.post(
        "/importar",
        files={"archivo": ("ratings.csv", contenido, "text/csv")},
    )


def test_importar_csv_grande_no_hace_una_consulta_por_fila(client, db, contador_sql):
    r = importar(client, csv_de_imdb(200))
    assert r.status_code == 200
    assert db.query(MediaItem).count() == 200

    assert len(contador_sql) < 10, (
        "%d consultas de lectura para 200 filas: sigue habiendo un N+1.\n%s"
        % (len(contador_sql), "\n".join(contador_sql[:5]))
    )


def test_importar_sigue_detectando_duplicados_contra_la_base(client, db):
    """La precarga sustituye a la consulta por fila: tiene que seguir viendo
    lo que ya estaba en la base antes de esta importación."""
    importar(client, csv_de_imdb(5))
    assert db.query(MediaItem).count() == 5

    r = importar(client, csv_de_imdb(5))
    assert db.query(MediaItem).count() == 5
    assert "5" in r.text  # 5 duplicados


def test_importar_sigue_detectando_duplicados_dentro_del_mismo_csv(client, db):
    """El set `vistos_en_este_csv` existía porque autoflush=False: los db.add()
    de filas anteriores no están en la base cuando se consulta. Al fusionarlo
    con la precarga, esa propiedad tiene que conservarse."""
    repetido = csv_de_imdb(3) + csv_de_imdb(3).split(b"\n", 1)[1]
    importar(client, repetido)
    assert db.query(MediaItem).count() == 3


def test_una_importacion_incremental_solo_crea_lo_nuevo(client, db):
    importar(client, csv_de_imdb(5))               # tt0000000 .. tt0000004
    importar(client, csv_de_imdb(5, desde=3))      # tt0000003 .. tt0000007
    # Se solapan dos (003 y 004), así que entran tres nuevos.
    assert db.query(MediaItem).count() == 8
