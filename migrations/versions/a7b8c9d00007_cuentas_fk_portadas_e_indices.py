"""claves foráneas de cuenta, intento de portada e índices de las tablas puente

Tres cosas que tocan las mismas tablas, juntas para recrear `media_items` (que
en SQLite es copiarla entera) una sola vez:

- `usuario_id` de `media_items` y `listas` pasa a ser clave foránea con ON
  DELETE CASCADE. La migración de cuentas añadió la columna sin la FK, así que
  la base migrada y la creada desde los modelos no coincidían, y
  `alembic check` lo señalaba. [R3-B9]
- `media_items.cover_checked_at`, para que el lote de portadas no se atasque
  en los mismos ítems. [R3-A4]
- Índices sobre la segunda columna de cada tabla puente. [R3-B10]

Revision ID: a7b8c9d00007
Revises: f6a7b8c90006
Create Date: 2026-10-28

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d00007"
down_revision: str | None = "f6a7b8c90006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDICES = (
    ("ix_media_item_tags_tag_id", "media_item_tags", ["tag_id"]),
    ("ix_media_item_generos_genero_id", "media_item_generos", ["genero_id"]),
    ("ix_list_items_media_item_id", "list_items", ["media_item_id"]),
)


def _tiene_fk_de_cuenta(inspector, tabla: str) -> bool:
    return any(fk["constrained_columns"] == ["usuario_id"] for fk in inspector.get_foreign_keys(tabla))


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columnas = {c["name"] for c in inspector.get_columns("media_items")}

    with op.batch_alter_table("media_items") as lote:
        if "cover_checked_at" not in columnas:
            lote.add_column(sa.Column("cover_checked_at", sa.DateTime(), nullable=True))
        if not _tiene_fk_de_cuenta(inspector, "media_items"):
            lote.create_foreign_key("fk_media_items_usuario", "usuarios", ["usuario_id"], ["id"],
                                    ondelete="CASCADE")
    if not _tiene_fk_de_cuenta(inspector, "listas"):
        with op.batch_alter_table("listas") as lote:
            lote.create_foreign_key("fk_listas_usuario", "usuarios", ["usuario_id"], ["id"],
                                    ondelete="CASCADE")

    for nombre, tabla, cols in INDICES:
        op.create_index(nombre, tabla, cols, if_not_exists=True)


def downgrade() -> None:
    for nombre, tabla, _ in INDICES:
        op.drop_index(nombre, table_name=tabla, if_exists=True)
    with op.batch_alter_table("listas") as lote:
        lote.drop_constraint("fk_listas_usuario", type_="foreignkey")
    with op.batch_alter_table("media_items") as lote:
        lote.drop_constraint("fk_media_items_usuario", type_="foreignkey")
        lote.drop_column("cover_checked_at")
