"""indices de filtrado (MC-M1)

Primera migración real del proyecto, y el motivo de que Alembic entrara:
`ensure_columns` solo sabía hacer ADD COLUMN, así que estos índices se habían
tenido que crear con `CREATE INDEX IF NOT EXISTS` sueltos en `init_db`.

Se crean con `if_not_exists` porque las bases ya desplegadas los tienen: los
puso ese `init_db` antes de este cambio, así que aquí serían un error de
"index already exists" que dejaría la app sin arrancar.

Revision ID: a1d2e3f40001
Revises: c3b3688bf8aa
Create Date: 2026-08-07

"""
from collections.abc import Sequence

from alembic import op

revision: str = "a1d2e3f40001"
down_revision: str | None = "c3b3688bf8aa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (nombre, tabla, columnas). El compuesto (media_type, status) cubre el caso
# más común del catálogo --filtrar por pestaña y estado a la vez-- y sirve
# también para las consultas que solo filtran por media_type.
INDICES = (
    ("ix_media_items_status", "media_items", ["status"]),
    ("ix_media_items_tipo_estado", "media_items", ["media_type", "status"]),
    ("ix_media_items_external_id", "media_items", ["external_id"]),
    ("ix_media_items_completed_at", "media_items", ["completed_at"]),
    ("ix_media_items_updated_at", "media_items", ["updated_at"]),
    ("ix_episodes_air_date", "episodes", ["air_date"]),
)


def upgrade() -> None:
    for nombre, tabla, columnas in INDICES:
        op.create_index(nombre, tabla, columnas, if_not_exists=True)


def downgrade() -> None:
    for nombre, tabla, _ in INDICES:
        op.drop_index(nombre, table_name=tabla, if_exists=True)
