#!/bin/sh
# Arranca como root solo para arreglar el dueño de /data y baja privilegios
# antes de ejecutar la app de verdad.
#
# Un simple "USER appuser" en el Dockerfile bastaría para instalaciones
# nuevas, pero rompería cualquier actualización: los volúmenes ya existentes
# (creados bajo la imagen anterior, que corría como root) tienen sus ficheros
# a nombre de root, y el proceso non-root no podría ni abrir media.db. El
# chown de aquí corrige eso en cada arranque -- barato, son unos pocos
# ficheros -- tanto en instalaciones nuevas como en actualizaciones.
#
# Aquí se aplican también las migraciones, antes de arrancar uvicorn. Hacerlo
# dentro del lifespan de FastAPI podía quedarse esperando un lock de SQLite y
# dejaba el arranque colgado sin explicar por qué; en este punto no hay
# servidor ni scheduler tocando la base, así que no hay con quién competir. Si
# una migración falla, `set -e` corta el arranque en vez de dejar la app
# sirviendo contra un esquema viejo.
set -e

chown -R appuser:appuser /data

# Se llama a init_db() y no a `alembic upgrade head` a secas: una base anterior
# a Alembic no tiene tabla alembic_version, así que upgrade la trata como vacía
# e intenta crear tablas que ya existen ("table media_items already exists").
# init_db() detecta ese caso, le completa las tablas y columnas que falten y la
# marca antes de migrar.
MIGRAR='from app.database import init_db; init_db()'

gosu appuser python -c "$MIGRAR"

exec gosu appuser "$@"
