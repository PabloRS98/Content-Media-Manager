"""Los `downgrade` de las migraciones. [R3-B22]

Nada los ejercitaba: uno roto solo se descubre el día que hace falta volver
atrás, que es el peor momento. El test recorre las revisiones que haya en
`migrations/versions` (no una lista fija), así que una migración nueva queda
cubierta sin tocar este fichero.
"""
import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.database import _config_alembic


@pytest.fixture
def engine_temporal(tmp_path):
    # Fichero y no memoria: las migraciones usan ALTER TABLE y modo batch.
    engine = create_engine("sqlite:///%s" % (tmp_path / "prueba.db"))
    yield engine
    engine.dispose()


def _revisiones(engine) -> list[str]:
    """De la más antigua a la más nueva."""
    script = ScriptDirectory.from_config(_config_alembic(engine))
    return [r.revision for r in reversed(list(script.walk_revisions()))]


def _estructura(engine) -> dict:
    """Tablas, columnas, índices y claves foráneas, para comparar esquemas."""
    insp = inspect(engine)
    tablas = {}
    for t in sorted(insp.get_table_names()):
        tablas[t] = {
            "columnas": sorted((c["name"], str(c["type"]), c["nullable"]) for c in insp.get_columns(t)),
            "indices": sorted((i["name"], tuple(i["column_names"]), bool(i["unique"])) for i in insp.get_indexes(t)),
            "fks": sorted(
                (tuple(f["constrained_columns"]), f["referred_table"], tuple(f["referred_columns"]))
                for f in insp.get_foreign_keys(t)
            ),
        }
    return tablas


def test_hay_revisiones_que_probar(engine_temporal):
    assert len(_revisiones(engine_temporal)) >= 7


def test_subir_a_head_bajar_a_base_y_volver_a_subir(engine_temporal):
    cfg = _config_alembic(engine_temporal)
    command.upgrade(cfg, "head")
    esquema_head = _estructura(engine_temporal)

    command.downgrade(cfg, "base")
    # Solo debe quedar la tabla de versión de Alembic.
    assert set(inspect(engine_temporal).get_table_names()) <= {"alembic_version"}

    command.upgrade(cfg, "head")
    assert _estructura(engine_temporal) == esquema_head


def test_cada_downgrade_deshace_exactamente_su_upgrade(engine_temporal):
    """Más fino que el anterior: si el fallo está en el downgrade de la
    migración N, el test dice cuál, en vez de un error genérico en `base`."""
    cfg = _config_alembic(engine_temporal)
    revisiones = _revisiones(engine_temporal)
    command.upgrade(cfg, "head")
    for rev in reversed(revisiones):
        esquema_antes = _estructura(engine_temporal)
        command.downgrade(cfg, "-1")
        command.upgrade(cfg, rev)
        assert _estructura(engine_temporal) == esquema_antes, "downgrade/upgrade de %s no es simétrico" % rev
        command.downgrade(cfg, "-1")


def test_el_downgrade_de_generos_no_pierde_los_generos(engine_temporal):
    cfg = _config_alembic(engine_temporal)
    command.upgrade(cfg, "d4e5f6a70004")
    with engine_temporal.begin() as conn:
        usuario = conn.execute(text("SELECT id FROM usuarios LIMIT 1")).scalar()
        conn.execute(text(
            "INSERT INTO media_items (usuario_id, media_type, title, status, overview, notes, "
            "release_notified, priority, genres, created_at, updated_at) "
            "VALUES (:u, 'pelicula', 'Con generos', 'pendiente', '', '', 0, 0, 'Drama, comedia', "
            "'2026-01-01', '2026-01-01')"
        ), {"u": usuario})

    command.upgrade(cfg, "e5f6a7b80005")
    with engine_temporal.connect() as conn:
        nombres = {r[0] for r in conn.execute(text(
            "SELECT g.nombre FROM generos g JOIN media_item_generos m ON m.genero_id = g.id"))}
    assert nombres == {"Drama", "Comedia"}
    assert "genres" not in {c["name"] for c in inspect(engine_temporal).get_columns("media_items")}

    command.downgrade(cfg, "d4e5f6a70004")
    with engine_temporal.connect() as conn:
        genres = conn.execute(text("SELECT genres FROM media_items WHERE title = 'Con generos'")).scalar()
    assert set(genres.split(", ")) == {"Comedia", "Drama"}
