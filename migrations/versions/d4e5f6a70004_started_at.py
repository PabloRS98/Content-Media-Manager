"""fecha de comienzo, para el diario

Se deja NULL en todo lo que ya está. Se podría rellenar con `updated_at`, y
sería mentira: esa columna cambia al corregir una errata. Un diario que dice
que empezaste un libro el día que le arreglaste el título vale menos que uno
que no dice nada de antes de existir.

Revision ID: d4e5f6a70004
Revises: c3d4e5f60003
Create Date: 2026-08-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a70004"
down_revision: str | None = "c3d4e5f60003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Guardado, como el resto: `init_db` reconcilia las bases anteriores a
    # Alembic con `create_all(checkfirst=True)`, que ya crea la columna.
    conexion = op.get_bind()
    inspector = sa.inspect(conexion)
    columnas = {c["name"] for c in inspector.get_columns("media_items")}
    if "started_at" not in columnas:
        op.add_column("media_items", sa.Column("started_at", sa.Date(), nullable=True))
    op.create_index("ix_media_items_started_at", "media_items", ["started_at"],
                    if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_media_items_started_at", table_name="media_items", if_exists=True)
    with op.batch_alter_table("media_items") as lote:
        lote.drop_column("started_at")
