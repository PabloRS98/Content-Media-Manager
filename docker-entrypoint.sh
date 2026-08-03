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
set -e

chown -R appuser:appuser /data

exec gosu appuser "$@"
