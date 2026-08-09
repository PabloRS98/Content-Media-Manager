"""generos a tabla

Lo delicado no es crear la tabla: es que **no se pierda ni un género** de los
que ya están escritos dentro de `media_items.genres`. La columna se borra al
final, y solo después de haber pasado su contenido a filas.

El orden:

1. Se crean `generos` y `media_item_generos`.
2. Se lee `media_items.genres` de todas las filas, se parte por comas y se
   normaliza igual que lo hace `services/generos.normalizar` -- si aquí se
   normalizara distinto, la app crearía duplicados del mismo género en cuanto
   alguien guardara un ítem.
3. Se insertan los géneros y sus relaciones.
4. Solo entonces se borra la columna.

`downgrade` rehace la cadena a partir de las filas, para que dar marcha atrás
tampoco pierda nada.

Revision ID: e5f6a7b80005
Revises: d4e5f6a70004
Create Date: 2026-08-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b80005"
down_revision: str | None = "d4e5f6a70004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalizar(nombre: str) -> str:
    """Copia deliberada de `services/generos.normalizar`.

    Las migraciones no importan código de la app a propósito: la app cambia y
    la migración tiene que seguir describiendo lo que pasó aquel día. Si la
    normalización cambia algún día, esta se queda como estaba y la de la app
    evoluciona -- que es justo lo que se quiere.
    """
    limpio = " ".join(nombre.split())
    return limpio[:1].upper() + limpio[1:] if limpio else ""


def upgrade() -> None:
    conexion = op.get_bind()
    inspector = sa.inspect(conexion)

    # Guardas, como en el resto de la serie: `init_db` reconcilia una base
    # anterior a Alembic con `create_all(checkfirst=True)`, que ya crea estas
    # dos tablas antes de que la migración llegue aquí.
    if not inspector.has_table("generos"):
        op.create_table(
            "generos",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("nombre", sa.String(length=80), nullable=False, unique=True),
        )
    if not inspector.has_table("media_item_generos"):
        op.create_table(
            "media_item_generos",
            sa.Column("media_item_id", sa.Integer(), sa.ForeignKey("media_items.id"),
                      primary_key=True),
            sa.Column("genero_id", sa.Integer(), sa.ForeignKey("generos.id"),
                      primary_key=True),
        )

    columnas = {c["name"] for c in inspector.get_columns("media_items")}
    if "genres" not in columnas:
        return  # ya migrada: nada que pasar ni que borrar

    filas = conexion.execute(
        sa.text("SELECT id, genres FROM media_items WHERE genres IS NOT NULL AND genres != ''")
    ).all()

    # Los que ya estén (por el camino de reconciliación) no se duplican.
    ids_por_nombre = {
        nombre: id_genero
        for id_genero, nombre in conexion.execute(sa.text("SELECT id, nombre FROM generos")).all()
    }
    relaciones: set[tuple[int, str]] = set()
    for id_item, cadena in filas:
        for trozo in cadena.split(","):
            nombre = _normalizar(trozo)
            if nombre:
                relaciones.add((id_item, nombre))

    for _, nombre in sorted(relaciones, key=lambda r: r[1]):
        if nombre not in ids_por_nombre:
            resultado = conexion.execute(
                sa.text("INSERT INTO generos (nombre) VALUES (:n)"), {"n": nombre}
            )
            ids_por_nombre[nombre] = resultado.lastrowid

    for id_item, nombre in relaciones:
        conexion.execute(
            sa.text(
                "INSERT OR IGNORE INTO media_item_generos (media_item_id, genero_id) "
                "VALUES (:item, :genero)"
            ),
            {"item": id_item, "genero": ids_por_nombre[nombre]},
        )

    with op.batch_alter_table("media_items") as lote:
        lote.drop_column("genres")


def downgrade() -> None:
    conexion = op.get_bind()
    inspector = sa.inspect(conexion)

    columnas = {c["name"] for c in inspector.get_columns("media_items")}
    if "genres" not in columnas:
        op.add_column("media_items", sa.Column("genres", sa.String(length=255), nullable=True))

    filas = conexion.execute(sa.text(
        "SELECT mig.media_item_id, g.nombre FROM media_item_generos mig "
        "JOIN generos g ON g.id = mig.genero_id ORDER BY g.nombre"
    )).all()
    por_item: dict[int, list[str]] = {}
    for id_item, nombre in filas:
        por_item.setdefault(id_item, []).append(nombre)
    for id_item, nombres in por_item.items():
        conexion.execute(
            sa.text("UPDATE media_items SET genres = :g WHERE id = :id"),
            {"g": ", ".join(nombres), "id": id_item},
        )

    op.drop_table("media_item_generos")
    op.drop_table("generos")
