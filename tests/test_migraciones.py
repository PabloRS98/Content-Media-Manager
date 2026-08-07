"""Puesta al día del esquema desde una base de datos anterior a Alembic.

Es el caso delicado de este cambio. Las bases anteriores se crearon con
`create_all()`, que no altera tablas ya existentes: a cada una le falta lo que
se añadiera al modelo después de su creación. Y no tienen tabla
`alembic_version`, así que un `upgrade` las trata como vacías e intenta crear
tablas que ya existen.

`init_db()` tiene que detectarlas, completarlas y marcarlas sin intervención
manual: un `alembic stamp` a mano deja la app rota hasta que alguien lo
recuerda, y marcar sin completar el esquema esconde el fallo en vez de
arreglarlo.
"""
import pytest
from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401  registra los modelos en Base
from app.database import INDICES, REVISION_INICIAL, Base, _config_alembic, init_db
from app.models import MediaItem, MediaStatus, MediaType

TABLAS_DEL_MODELO = sorted(Base.metadata.tables)


@pytest.fixture
def engine_temporal(tmp_path):
    """Motor sobre fichero: las migraciones usan ALTER TABLE y modo batch, que
    necesitan una base real, no la de memoria."""
    engine = create_engine("sqlite:///%s" % (tmp_path / "prueba.db"))
    yield engine
    # En Windows el fichero queda bloqueado si el pool no se cierra.
    engine.dispose()


def _revision_actual(engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _revision_head(engine) -> str:
    return ScriptDirectory.from_config(_config_alembic(engine)).get_current_head()


def _columnas(engine, tabla: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(tabla)}


def _indices(engine, tabla: str) -> set[str]:
    return {i["name"] for i in inspect(engine).get_indexes(tabla)}


def _base_anterior_a_alembic(engine) -> None:
    """Reproduce una base como las de antes de Alembic: el esquema inicial, sin
    marca de versión y sin dos de las columnas que se fueron añadiendo."""
    command.upgrade(_config_alembic(engine), REVISION_INICIAL)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text("ALTER TABLE media_items DROP COLUMN saga"))
        conn.execute(text("ALTER TABLE listas DROP COLUMN filtro_estado"))
        # Una base que venga de la 1.0.0 tampoco tiene la tabla de metadatos.
        conn.execute(text("DROP TABLE app_meta"))


def test_una_base_anterior_a_alembic_se_pone_al_dia(engine_temporal):
    _base_anterior_a_alembic(engine_temporal)
    assert "saga" not in _columnas(engine_temporal, "media_items")
    assert _revision_actual(engine_temporal) is None

    init_db(bind=engine_temporal)

    assert _revision_actual(engine_temporal) == _revision_head(engine_temporal)
    assert "saga" in _columnas(engine_temporal, "media_items")
    assert "filtro_estado" in _columnas(engine_temporal, "listas")


def test_una_base_de_la_1_0_0_recupera_la_tabla_de_metadatos(engine_temporal):
    """`create_all(checkfirst=True)` en la reconciliación: `ensure_columns` solo
    sabe de columnas, y `app_meta` es una tabla entera que falta."""
    _base_anterior_a_alembic(engine_temporal)
    assert "app_meta" not in inspect(engine_temporal).get_table_names()

    init_db(bind=engine_temporal)

    assert "app_meta" in inspect(engine_temporal).get_table_names()


@pytest.mark.parametrize("tabla", TABLAS_DEL_MODELO)
def test_tras_migrar_no_falta_ninguna_columna_del_modelo(engine_temporal, tabla):
    """El síntoma de este tipo de fallo es un 500 en todas las páginas por una
    columna ausente: tras la puesta al día no puede faltar ninguna."""
    _base_anterior_a_alembic(engine_temporal)
    init_db(bind=engine_temporal)

    del_modelo = {c.name for c in Base.metadata.tables[tabla].columns}
    assert del_modelo - _columnas(engine_temporal, tabla) == set()


def test_se_puede_consultar_tras_migrar(engine_temporal):
    """Sin la reconciliación esto lanzaba
    OperationalError("no such column: media_items.saga")."""
    _base_anterior_a_alembic(engine_temporal)
    init_db(bind=engine_temporal)

    sesion = sessionmaker(bind=engine_temporal)()
    try:
        sesion.add(MediaItem(title="Duna", media_type=MediaType.LIBRO,
                             status=MediaStatus.PENDIENTE))
        sesion.commit()
        assert [i.saga for i in sesion.query(MediaItem).all()] == [None]
    finally:
        sesion.close()


def test_una_base_nueva_se_crea_al_dia(engine_temporal):
    init_db(bind=engine_temporal)

    assert _revision_actual(engine_temporal) == _revision_head(engine_temporal)
    tablas = set(inspect(engine_temporal).get_table_names())
    assert set(TABLAS_DEL_MODELO) - tablas == set()


def test_init_db_es_idempotente(engine_temporal):
    """Corre en cada arranque del contenedor: repetirlo no puede fallar."""
    init_db(bind=engine_temporal)
    antes = _revision_actual(engine_temporal)

    init_db(bind=engine_temporal)

    assert _revision_actual(engine_temporal) == antes


class TestPrimeraMigracionReal:
    """Los índices de [MC-M1]: el motivo de que Alembic entrara, porque
    `ensure_columns` no sabía crearlos."""

    def test_la_migracion_crea_los_indices(self, engine_temporal):
        init_db(bind=engine_temporal)
        for nombre, tabla, _ in INDICES:
            assert nombre in _indices(engine_temporal, tabla), nombre

    def test_una_base_que_ya_tenia_los_indices_no_falla(self, engine_temporal):
        """Las bases ya desplegadas los tienen: los creó `init_db` antes de este
        cambio. Sin `if_not_exists`, la migración las dejaría sin arrancar con
        "index ix_media_items_status already exists"."""
        command.upgrade(_config_alembic(engine_temporal), REVISION_INICIAL)
        with engine_temporal.begin() as conn:
            for nombre, tabla, columnas in INDICES:
                conn.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS %s ON %s (%s)"
                    % (nombre, tabla, ", ".join(columnas))
                )
            conn.execute(text("DROP TABLE alembic_version"))

        init_db(bind=engine_temporal)

        assert _revision_actual(engine_temporal) == _revision_head(engine_temporal)


def test_el_esquema_de_las_migraciones_coincide_con_los_modelos(engine_temporal, tmp_path):
    """`alembic upgrade head` sobre una base vacía tiene que dar el mismo
    esquema que `create_all()`. Si divergen, los tests (que usan `create_all`)
    dejarían de probar lo que corre en producción."""
    init_db(bind=engine_temporal)
    migrado = inspect(engine_temporal)

    desde_modelos = create_engine("sqlite:///%s" % (tmp_path / "modelos.db"))
    try:
        Base.metadata.create_all(bind=desde_modelos)
        reflejado = inspect(desde_modelos)

        assert set(migrado.get_table_names()) - {"alembic_version"} == set(
            reflejado.get_table_names()
        )
        for tabla in reflejado.get_table_names():
            assert _columnas(engine_temporal, tabla) == _columnas(desde_modelos, tabla), tabla
    finally:
        desde_modelos.dispose()
