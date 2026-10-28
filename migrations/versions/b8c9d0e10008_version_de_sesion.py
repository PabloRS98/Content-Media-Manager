"""versión de sesión por cuenta

`usuarios.sesion_version` va dentro de la cookie firmada y tiene que
coincidir. Subirla (al cambiar la contraseña, o con "cerrar en los demás
dispositivos") deja sin valor las cookies viejas, que antes valían un año
pasara lo que pasara. Empieza en 0 para todas: las sesiones abiertas antes de
esta versión no llevan el campo, cuentan como 0 y siguen abiertas. [R3-M4]

Revision ID: b8c9d0e10008
Revises: a7b8c9d00007
Create Date: 2026-10-28

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e10008"
down_revision: str | None = "a7b8c9d00007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columnas = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("usuarios")}
    if "sesion_version" not in columnas:
        op.add_column("usuarios", sa.Column("sesion_version", sa.Integer(), nullable=False,
                                            server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("usuarios") as lote:
        lote.drop_column("sesion_version")
