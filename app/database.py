"""Motor y sesión de SQLAlchemy sobre SQLite, con migración ligera de columnas."""
import os

from sqlalchemy import (
    Column,
    String,
    Table,
    create_engine,
    delete,
    event,
    insert,
    inspect,
    select,
)
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


# Tabla de metadatos del propio esquema: qué migraciones puntuales ya se
# aplicaron. Vive aquí y no en models.py a propósito -- no es parte del dominio
# (no hay libros ni episodios aquí), es infraestructura de la base, y las
# funciones que la usan están en este mismo módulo.
#
# Es además el primer paso hacia saber en qué versión de esquema está una base,
# que es lo que hoy no se puede responder (ver MC-M4, Alembic).
app_meta = Table(
    "app_meta",
    Base.metadata,
    Column("clave", String(50), primary_key=True),
    Column("valor", String(255)),
)

# Marca del backfill de columnas de la v2 (ver main.backfill_v2_columns).
CLAVE_BACKFILL_V2 = "backfill_v2_completado"


def leer_meta(clave: str, bind=None) -> str | None:
    with (bind or engine).connect() as conn:
        fila = conn.execute(
            select(app_meta.c.valor).where(app_meta.c.clave == clave)
        ).first()
    return fila[0] if fila else None


def escribir_meta(clave: str, valor: str, bind=None) -> None:
    with (bind or engine).begin() as conn:
        conn.execute(delete(app_meta).where(app_meta.c.clave == clave))
        conn.execute(insert(app_meta).values(clave=clave, valor=valor))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_columns(table: str, columns: dict[str, str], bind=None) -> list[str]:
    """Añade a `table` las columnas de `columns` ({nombre: DDL}) que aún no
    existan, con ALTER TABLE ADD COLUMN. Solo para columnas nullable/con
    default: no rompe bases de datos existentes. Devuelve las que añadió.

    Ya NO es el mecanismo general de migración: eso es Alembic desde MC-M4.
    Se conserva solo para el camino de reconciliación de `init_db`, que tiene
    que completar una base anterior a Alembic antes de marcarla.

    `bind` permite apuntar a otro motor (los tests migran bases temporales)."""
    added: list[str] = []
    with (bind or engine).begin() as conn:
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
# Los índices se crean ahora en una migración de Alembic
# (`migrations/versions/*_indices_de_filtrado.py`). La lista se conserva aquí
# como fuente única para los tests, que comprueban que el esquema los tiene.
INDICES = (
    ("ix_media_items_status", "media_items", ["status"]),
    ("ix_media_items_tipo_estado", "media_items", ["media_type", "status"]),
    ("ix_media_items_external_id", "media_items", ["external_id"]),
    ("ix_media_items_completed_at", "media_items", ["completed_at"]),
    ("ix_media_items_updated_at", "media_items", ["updated_at"]),
    ("ix_episodes_air_date", "episodes", ["air_date"]),
)


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


# Primera revisión de Alembic: describe el esquema tal y como quedó en la 1.0.0
# más lo que la segunda auditoría añadió antes de este cambio.
REVISION_INICIAL = "c3b3688bf8aa"

# Columnas que se fueron añadiendo al modelo antes de que existiera Alembic.
#
# Solo se usan para reconciliar una base anterior a Alembic: esas bases se
# crearon con `create_all()`, que no altera tablas ya existentes, así que a cada
# una le falta todo lo que se añadiera después de su creación. Antes de marcarla
# en REVISION_INICIAL hay que completarlas, porque esa revisión afirma que la
# tabla ya tiene estas columnas. Si no, la app arranca y revienta en la primera
# consulta con "no such column: ...". Lo vigila tests/test_migraciones.py.
COLUMNAS_PRE_ALEMBIC: dict[str, dict[str, str]] = {
    "media_items": {
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
    },
    "episodes": {
        "notified": "BOOLEAN DEFAULT 0",
    },
    "listas": {
        "filtro_estado": "VARCHAR(20)",
    },
}


def _config_alembic(target):
    from alembic.config import Config

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = Config(os.path.join(raiz, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(raiz, "migrations"))
    config.set_main_option("sqlalchemy.url", target.url.render_as_string(hide_password=False))
    return config


def revision_pendiente(bind=None) -> tuple[str | None, str | None]:
    """(revisión de la BD, revisión objetivo). Iguales = esquema al día.

    Solo lee `alembic_version`, sin abrir transacción de escritura ni tocar el
    DDL, así que es seguro llamarlo desde el arranque del servidor: es la
    diferencia con `init_db()`, que sí puede quedarse esperando un lock."""
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    target = bind or engine
    with target.connect() as conn:
        actual = MigrationContext.configure(conn).get_current_revision()
    head = ScriptDirectory.from_config(_config_alembic(target)).get_current_head()
    return actual, head


def init_db(bind=None):
    """Deja el esquema al día aplicando las migraciones pendientes de Alembic.

    Alembic sustituye a `ensure_columns` como mecanismo general: aquel solo
    sabía hacer ADD COLUMN, no podía crear índices ni cambiar tipos ni llevar
    registro de en qué versión está una base.

    Una base anterior a Alembic no tiene tabla `alembic_version`, así que
    `upgrade` la trataría como vacía e intentaría crear tablas que ya existen.
    Se detecta y se marca en la revisión inicial, completándole antes lo que le
    falte para que la marca no mienta:

    - `create_all(checkfirst=True)` crea las TABLAS que falten sin tocar las que
      ya están. Hace falta porque no todas las bases desplegadas llegan aquí
      desde el mismo punto: una que venga de la 1.0.0 no tiene `app_meta`.
    - `ensure_columns` completa las COLUMNAS que falten, que es lo que
      `create_all` no hace.

    Es automático a propósito: pedir un `alembic stamp` a mano deja la app rota
    hasta que alguien lo recuerde.
    """
    from alembic import command

    from . import models  # noqa: F401  asegura que los modelos queden registrados

    target = bind or engine
    config = _config_alembic(target)

    tablas = set(inspect(target).get_table_names())
    if "media_items" in tablas and "alembic_version" not in tablas:
        Base.metadata.create_all(bind=target, checkfirst=True)
        for tabla, columnas in COLUMNAS_PRE_ALEMBIC.items():
            if tabla in tablas:
                ensure_columns(tabla, columnas, target)
        command.stamp(config, REVISION_INICIAL)

    command.upgrade(config, "head")
    limpiar_filas_huerfanas(target)
