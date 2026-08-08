"""cuentas de usuario

Cada persona de la casa tiene su propio catálogo. Lo delicado de esta migración
no es crear la tabla: es que **todo lo que ya existe tiene que acabar en una
cuenta**, o el catálogo desaparecería de la vista al filtrar por usuario.

El orden importa:

1. Se crea `usuarios`.
2. Se crea la cuenta inicial ("Yo") y se guarda su id.
3. Se añaden las columnas `usuario_id` como NULLABLE, para que las filas que ya
   están no violen la restricción al añadirlas.
4. Se asigna esa cuenta a todas las filas existentes.
5. Solo entonces se pasan a NOT NULL.

Hacerlo al revés --NOT NULL de entrada-- falla en cualquier base con datos, que
son todas las desplegadas.

Revision ID: b2c3d4e50002
Revises: a1d2e3f40001
Create Date: 2026-08-08

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e50002"
down_revision: str | None = "a1d2e3f40001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tiene_columna(inspector, tabla: str, columna: str) -> bool:
    return columna in {c["name"] for c in inspector.get_columns(tabla)}


def upgrade() -> None:
    conexion = op.get_bind()
    inspector = sa.inspect(conexion)

    # Cada paso comprueba si ya está hecho. No es paranoia: `init_db` reconcilia
    # una base anterior a Alembic con `create_all(checkfirst=True)`, que crea
    # las tablas de TODOS los modelos --incluida `usuarios`-- antes de marcarla
    # en la revisión inicial y migrar. Sin estas comprobaciones, esta migración
    # fallaría justo ahí con "table usuarios already exists", que es el camino
    # que recorre cualquier instalación existente.
    if not inspector.has_table("usuarios"):
        op.create_table(
            "usuarios",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nombre", sa.String(length=60), nullable=False, unique=True),
            sa.Column("password_hash", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    id_inicial = conexion.execute(
        sa.text("SELECT id FROM usuarios ORDER BY id LIMIT 1")
    ).scalar()
    if id_inicial is None:
        conexion.execute(
            sa.text(
                "INSERT INTO usuarios (nombre, password_hash, created_at) "
                "VALUES ('Yo', NULL, CURRENT_TIMESTAMP)"
            )
        )
        id_inicial = conexion.execute(
            sa.text("SELECT id FROM usuarios ORDER BY id LIMIT 1")
        ).scalar()

    for tabla in ("media_items", "listas"):
        if not _tiene_columna(inspector, tabla, "usuario_id"):
            # NULLABLE de entrada: las filas que ya están violarían un NOT NULL
            # en el momento de añadir la columna. Se rellena y luego se aprieta.
            op.add_column(tabla, sa.Column("usuario_id", sa.Integer(), nullable=True))
        conexion.execute(
            sa.text("UPDATE %s SET usuario_id = :uid WHERE usuario_id IS NULL" % tabla),
            {"uid": id_inicial},
        )

    # `batch_alter_table`: SQLite no sabe hacer ALTER COLUMN, así que Alembic
    # recrea la tabla y copia los datos.
    with op.batch_alter_table("media_items") as lote:
        lote.alter_column("usuario_id", existing_type=sa.Integer(), nullable=False)

    # `listas.name` era único GLOBALMENTE, y eso con cuentas no vale: que tu
    # pareja tenga una lista "Pendientes" no puede impedirte tener la tuya. Peor
    # aún, las cuatro listas automáticas se siembran con nombres fijos, así que
    # la segunda cuenta ni siquiera se podría crear.
    #
    # Cambiarla hay que pedirlo: el modo batch RECREA la tabla a partir de lo
    # que refleja de la actual, así que una restricción que no se toque
    # sobrevive intacta a la recreación. Y en SQLite la vieja se declaró en
    # línea (`UNIQUE (name)`), sin nombre, así que se refleja con `name=None` y
    # no hay nada que pasarle a `drop_constraint`. Para eso está
    # `naming_convention`: le da un nombre determinista a la reflejada.
    unicos = {
        tuple(u["column_names"]): u["name"]
        for u in inspector.get_unique_constraints("listas")
    }
    convencion = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
    with op.batch_alter_table("listas", naming_convention=convencion) as lote:
        lote.alter_column("usuario_id", existing_type=sa.Integer(), nullable=False)
        if ("name",) in unicos:
            lote.drop_constraint(unicos[("name",)] or "uq_listas_name", type_="unique")
        if ("usuario_id", "name") not in unicos:
            lote.create_unique_constraint("uq_lista_usuario_nombre", ["usuario_id", "name"])

    # Todo el catálogo se filtra siempre por cuenta, así que la cuenta va
    # delante: un índice que empiece por otra columna no sirve para una
    # consulta que siempre acota por usuario.
    for nombre, tabla, columnas in (
        ("ix_media_items_usuario_id", "media_items", ["usuario_id"]),
        ("ix_listas_usuario_id", "listas", ["usuario_id"]),
        ("ix_media_items_usuario_estado", "media_items", ["usuario_id", "status"]),
        ("ix_media_items_usuario_tipo_estado", "media_items",
         ["usuario_id", "media_type", "status"]),
    ):
        op.create_index(nombre, tabla, columnas, if_not_exists=True)


def downgrade() -> None:
    for nombre, tabla in (
        ("ix_media_items_usuario_tipo_estado", "media_items"),
        ("ix_media_items_usuario_estado", "media_items"),
        ("ix_listas_usuario_id", "listas"),
        ("ix_media_items_usuario_id", "media_items"),
    ):
        op.drop_index(nombre, table_name=tabla, if_exists=True)

    with op.batch_alter_table("listas") as lote:
        lote.drop_column("usuario_id")
    with op.batch_alter_table("media_items") as lote:
        lote.drop_column("usuario_id")
    op.drop_table("usuarios")
