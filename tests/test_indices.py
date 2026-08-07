"""El esquema tenía un solo índice y se filtraba por seis columnas sin indexar.

Los tests miran el plan de ejecución, no el tiempo: con la base de un test el
reloj no distingue nada, y lo que hay que fijar es que SQLite *elija* el índice.
Medido antes con `EXPLAIN QUERY PLAN` sobre una copia de la base real, donde 6
de 8 consultas representativas pasaban de `SCAN` a `SEARCH ... USING INDEX`.

Desde [MC-M4] los crea una migración de Alembic, no `init_db` a mano. Aquí se
prueba el efecto (que SQLite los elija); que la migración los cree está en
`test_migraciones.py`.
"""
import pytest
from sqlalchemy import text

from app.database import INDICES

CONSULTAS_DEL_CATALOGO = {
    "catálogo por pestaña y estado":
        "SELECT * FROM media_items WHERE media_type='serie' AND status='pendiente'",
    "recuento de inicio por estado":
        "SELECT COUNT(*) FROM media_items WHERE status='completado'",
    "duplicados al importar":
        "SELECT id FROM media_items WHERE external_id='tt0111161'",
    "estadísticas por fecha de fin":
        "SELECT COUNT(*) FROM media_items WHERE completed_at >= '2026-01-01'",
    "próximos episodios":
        "SELECT * FROM episodes WHERE air_date >= '2026-08-01'",
}


@pytest.fixture
def base_indexada(db):
    """Los índices están declarados en los modelos, así que el `create_all` de
    conftest ya los crea. La migración de Alembic existe para las bases YA
    desplegadas, que no se recrean desde los modelos."""
    return db


def plan(db, sql: str) -> str:
    return " | ".join(fila[3] for fila in db.execute(text("EXPLAIN QUERY PLAN " + sql)))


@pytest.mark.parametrize("nombre", list(CONSULTAS_DEL_CATALOGO))
def test_las_consultas_del_catalogo_usan_indice(base_indexada, nombre):
    p = plan(base_indexada, CONSULTAS_DEL_CATALOGO[nombre])
    assert "USING INDEX" in p or "USING COVERING INDEX" in p, p
    assert "SCAN media_items" not in p.replace("SCAN media_items USING", ""), p


def test_el_orden_por_defecto_no_ordena_en_memoria(base_indexada):
    """`updated_at` es el ORDER BY por defecto del catálogo. El índice no
    elimina el recorrido, pero sí el `USE TEMP B-TREE FOR ORDER BY`: SQLite
    recorre el índice ya ordenado en vez de ordenar el resultado aparte."""
    p = plan(base_indexada, "SELECT * FROM media_items ORDER BY updated_at DESC LIMIT 24")
    assert "TEMP B-TREE" not in p, p
    assert "ix_media_items_updated_at" in p, p


def test_los_indices_declarados_existen_tras_migrar(base_indexada):
    """La lista de `app.database.INDICES` es la fuente para los tests; que
    coincida con lo que crea la migración es lo que se comprueba aquí."""
    nombres = {
        f[0] for f in base_indexada.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%'"
        ))
    }
    for nombre, _, _ in INDICES:
        assert nombre in nombres, nombre


def test_init_db_deja_los_indices_creados():
    """No basta con que la migración exista: `init_db` tiene que aplicarla.

    Va contra el motor global, que en los tests apunta a una base desechable
    (conftest fija DB_PATH antes de importar nada de `app`).
    """
    from app.database import engine, init_db

    init_db()
    with engine.connect() as conn:
        nombres = {
            f[0] for f in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%'"
            )
        }
    for nombre, _, _ in INDICES:
        assert nombre in nombres, nombre
