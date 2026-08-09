"""plataforma donde tienes cada ítem

Una columna nullable y nada más: los ítems que ya están se quedan sin
plataforma, que es la verdad --nadie la ha rellenado todavía-- y no un valor
inventado que luego haya que distinguir de los de verdad.

Revision ID: c3d4e5f60003
Revises: b2c3d4e50002
Create Date: 2026-08-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f60003"
down_revision: str | None = "b2c3d4e50002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Guardado igual que el resto de esta serie: `init_db` reconcilia las bases
    # anteriores a Alembic con `create_all(checkfirst=True)`, que crea las
    # tablas de TODOS los modelos --ya con esta columna-- antes de migrar.
    inspector = sa.inspect(op.get_bind())
    columnas = {c["name"] for c in inspector.get_columns("media_items")}
    if "plataforma" not in columnas:
        op.add_column("media_items", sa.Column("plataforma", sa.String(length=60), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("media_items") as lote:
        lote.drop_column("plataforma")
