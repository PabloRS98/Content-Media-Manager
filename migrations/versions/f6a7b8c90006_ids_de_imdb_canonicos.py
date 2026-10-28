"""ids de IMDb con una sola forma

El importador de IMDb guardaba `imdb:tt…` y el de Trakt `tt…` a secas, con
`external_source = 'imdb'` en los dos. Los de Trakt no se reconocían como
duplicados de los de IMDb y no se enriquecían (`enrich.py` busca el prefijo).
Aquí se ponen todos con prefijo, que es la forma que usa ya el código. [R3-M15]

Solo datos, sin cambios de esquema. `downgrade` no hace nada: no hay forma de
saber cuáles venían sin prefijo, y con prefijo funcionan igual en la versión
anterior.

Revision ID: f6a7b8c90006
Revises: e5f6a7b80005
Create Date: 2026-10-28

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c90006"
down_revision: str | None = "e5f6a7b80005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.get_bind().execute(sa.text(
        "UPDATE media_items SET external_id = 'imdb:' || lower(trim(external_id)) "
        "WHERE lower(trim(external_id)) GLOB 'tt[0-9]*'"
    ))


def downgrade() -> None:
    pass
