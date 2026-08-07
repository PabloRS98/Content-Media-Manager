#!/usr/bin/env bash
# Pide todas las páginas y exige 200. Uso: comprobar-rutas.sh <puerto>
#
# Un healthcheck en verde no basta como prueba de despliegue: hay que pedir las
# páginas de verdad. Una columna que falte en el esquema deja el proceso vivo y
# respondiendo, pero devuelve 500 en cada vista, y eso solo se ve pidiéndolas.
#
# REGLA: cuando se añada una ruta GET nueva a la app, se añade aquí.
set -euo pipefail

puerto="$1"
rutas=(
    /
    /catalogo
    /catalogo?tipo=libro
    /catalogo?tipo=pelicula
    /catalogo?tipo=serie
    /catalogo?tipo=videojuego
    /catalogo?tipo=podcast
    /listas
    /estadisticas
    /calendario
    /importar
    /importar/estado-portadas
    /estado
    /sugerencia
    /tengo-tiempo
    /salud
)

fallos=0
for ruta in "${rutas[@]}"; do
    codigo=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$puerto$ruta")
    if [ "$codigo" = "200" ]; then
        printf '  %-30s %s\n' "$ruta" "$codigo"
    else
        printf '  %-30s %s  <-- FALLO\n' "$ruta" "$codigo"
        fallos=$((fallos + 1))
    fi
done

if [ "$fallos" -gt 0 ]; then
    echo "$fallos rutas no responden 200"
    exit 1
fi
echo "las ${#rutas[@]} rutas responden 200"
