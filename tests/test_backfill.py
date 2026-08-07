"""El backfill de la v2 solo tiene sentido la primera vez.

Su docstring decía "una sola vez, idempotente", pero lo idempotente era el
*resultado*, no la *ejecución*: corría entero en cada arranque del lifespan.
Y sus dos consultas son caras -- una filtra por dos columnas y la otra hace
`LIKE '%Genero:%'` sobre una columna Text, que no puede usar índice ni aunque
exista porque el comodín va delante.
"""
import pytest
from sqlalchemy import event, text

from app.database import CLAVE_BACKFILL_V2, SessionLocal, engine, escribir_meta, init_db, leer_meta
from app.main import backfill_v2_columns
from app.models import MediaItem, MediaStatus, MediaType


@pytest.fixture
def base_limpia():
    """Base real del test (conftest apunta DB_PATH a un temporal) sin la marca."""
    init_db()
    with engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM app_meta")
        conn.exec_driver_sql("DELETE FROM media_items")
    yield
    with engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM media_items")


@pytest.fixture
def consultas_del_backfill():
    """Cuenta los SELECT sobre media_items que emite el backfill."""
    vistas: list[str] = []

    def _antes(conn, cursor, sentencia, parametros, contexto, muchos):
        s = sentencia.lstrip().upper()
        if s.startswith("SELECT") and "MEDIA_ITEMS" in s:
            vistas.append(sentencia)

    event.listen(engine, "before_cursor_execute", _antes)
    try:
        yield vistas
    finally:
        event.remove(engine, "before_cursor_execute", _antes)


def item_pendiente_de_migrar():
    """Un ítem como los que dejaba la v1: completado sin `completed_at` y con
    el género escondido en las notas de una importación de IMDb."""
    return MediaItem(
        media_type=MediaType.PELICULA,
        title="Importada en la v1",
        status=MediaStatus.COMPLETADO,
        completed_at=None,
        genres=None,
        notes="Importado de IMDB. Genero: Drama, Crimen. Rating IMDb: 9,3.",
    )


def test_el_backfill_sigue_funcionando_en_una_base_v1(base_limpia):
    db = SessionLocal()
    try:
        db.add(item_pendiente_de_migrar())
        db.commit()
    finally:
        db.close()

    backfill_v2_columns()

    db = SessionLocal()
    try:
        item = db.query(MediaItem).one()
        assert item.completed_at is not None
        assert item.genres == "Drama, Crimen"
        assert "Genero:" not in item.notes
    finally:
        db.close()


def test_el_backfill_solo_corre_una_vez(base_limpia, consultas_del_backfill):
    db = SessionLocal()
    try:
        db.add(item_pendiente_de_migrar())
        db.commit()
    finally:
        db.close()

    consultas_del_backfill.clear()
    backfill_v2_columns()
    primera = len(consultas_del_backfill)
    assert primera >= 2, "el backfill no llegó a consultar nada"

    consultas_del_backfill.clear()
    backfill_v2_columns()
    assert consultas_del_backfill == [], (
        "el segundo arranque vuelve a escanear la tabla: la marca no se está leyendo"
    )


def test_la_marca_se_escribe_al_terminar(base_limpia):
    assert leer_meta(CLAVE_BACKFILL_V2) is None
    backfill_v2_columns()
    assert leer_meta(CLAVE_BACKFILL_V2) is not None


def test_una_base_ya_marcada_no_toca_los_datos(base_limpia):
    """Si alguien recrea el caso de la v1 después de la marca, el backfill ya
    no lo arregla -- y debe ser así: es una migración puntual, no una regla."""
    escribir_meta(CLAVE_BACKFILL_V2, "2026-01-01")
    db = SessionLocal()
    try:
        db.add(item_pendiente_de_migrar())
        db.commit()
    finally:
        db.close()

    backfill_v2_columns()

    db = SessionLocal()
    try:
        assert db.query(MediaItem).one().genres is None
    finally:
        db.close()


class TestTablaDeMetadatos:
    def test_leer_una_clave_que_no_existe_devuelve_none(self, base_limpia):
        assert leer_meta("no-existe") is None

    def test_escribir_y_leer(self, base_limpia):
        escribir_meta("prueba", "valor")
        assert leer_meta("prueba") == "valor"

    def test_escribir_dos_veces_sustituye(self, base_limpia):
        escribir_meta("prueba", "uno")
        escribir_meta("prueba", "dos")
        assert leer_meta("prueba") == "dos"
        with engine.connect() as conn:
            n = list(conn.execute(text("SELECT COUNT(*) FROM app_meta WHERE clave='prueba'")))[0][0]
        assert n == 1
