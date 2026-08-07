"""Motor y sesión de SQLAlchemy sobre SQLite, con migración ligera de columnas."""
import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_columns(table: str, columns: dict[str, str]) -> list[str]:
    """Migración mínima sin Alembic: añade a `table` las columnas de `columns`
    ({nombre: DDL}) que aún no existan, con ALTER TABLE ADD COLUMN.
    Solo para columnas nullable/con default: no rompe bases de datos existentes.
    Devuelve la lista de columnas añadidas."""
    added: list[str] = []
    with engine.begin() as conn:
        existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                added.append(name)
    return added


# Índices de las columnas por las que la aplicación filtra u ordena. El esquema
# solo tenía uno declarado (la FK de episodes.item_id), y estas ocho consultas
# hacían un escaneo completo cada vez.
#
# Medido con EXPLAIN QUERY PLAN sobre una copia de la base real: seis de ocho
# consultas representativas pasan de SCAN a SEARCH ... USING INDEX, y el ORDER
# BY por defecto del catálogo deja de necesitar un TEMP B-TREE.
#
# El compuesto (media_type, status) cubre el caso más común --filtrar por
# pestaña y estado a la vez-- y sirve también para las consultas que solo
# filtran por media_type, porque es la primera columna del índice.
#
# NO se indexa cover_url pese a que también se filtra por ella ("sin portada"):
# es una columna de 500 caracteres y la consulta es un contador que se pide una
# vez por carga; el coste en escrituras no compensa. Queda medido y descartado
# a propósito, no olvidado.
#
# Van con CREATE INDEX IF NOT EXISTS porque `ensure_columns` solo sabe hacer
# ADD COLUMN. Cuando entre Alembic (MC-M4), esto se convierte en la primera
# migración real y esta función desaparece.
INDICES = (
    "CREATE INDEX IF NOT EXISTS ix_media_items_status ON media_items (status)",
    "CREATE INDEX IF NOT EXISTS ix_media_items_tipo_estado ON media_items (media_type, status)",
    "CREATE INDEX IF NOT EXISTS ix_media_items_external_id ON media_items (external_id)",
    "CREATE INDEX IF NOT EXISTS ix_media_items_completed_at ON media_items (completed_at)",
    "CREATE INDEX IF NOT EXISTS ix_media_items_updated_at ON media_items (updated_at)",
    "CREATE INDEX IF NOT EXISTS ix_episodes_air_date ON episodes (air_date)",
)


def crear_indices(bind=None) -> None:
    """Crea los índices que falten. Idempotente: corre en cada arranque."""
    with (bind or engine).begin() as conn:
        for sentencia in INDICES:
            conn.exec_driver_sql(sentencia)


def limpiar_filas_huerfanas(bind=None) -> dict[str, int]:
    """Borra de las tablas puente las filas que apuntan a ítems inexistentes.

    Declarar la relación inversa evita que se creen nuevas, pero no limpia las
    que ya arrastra una base desplegada: cada ítem borrado antes de ese arreglo
    dejó su fila en `list_items`. Y el id se reutiliza, así que una fila muerta
    no es solo basura -- puede resucitar como pertenencia de un ítem distinto.

    Idempotente y barato (dos DELETE con subconsulta), así que corre en cada
    arranque sin necesidad de marca de versión.
    """
    borradas: dict[str, int] = {}
    with (bind or engine).begin() as conn:
        for tabla in ("list_items", "media_item_tags"):
            r = conn.exec_driver_sql(
                "DELETE FROM %s WHERE media_item_id NOT IN (SELECT id FROM media_items)" % tabla
            )
            if r.rowcount:
                borradas[tabla] = r.rowcount
    return borradas


def init_db():
    from . import models  # noqa: F401  asegura que los modelos queden registrados

    Base.metadata.create_all(bind=engine)
    # Columnas añadidas después de la v1 (bases de datos ya desplegadas)
    ensure_columns("media_items", {
        "completed_at": "DATE",
        "genres": "VARCHAR(255)",
        # v3
        "cast": "TEXT",
        "priority": "VARCHAR(10) DEFAULT 'media'",
        "runtime_minutes": "INTEGER",
        "page_count": "INTEGER",
        "hltb_hours": "FLOAT",
        "tmdb_collection_id": "INTEGER",
        "saga": "VARCHAR(120)",
        "release_date": "DATE",
        "release_notified": "BOOLEAN DEFAULT 0",
    })
    ensure_columns("episodes", {
        "notified": "BOOLEAN DEFAULT 0",
    })
    ensure_columns("listas", {
        "filtro_estado": "VARCHAR(20)",
    })
    crear_indices()
    limpiar_filas_huerfanas()
