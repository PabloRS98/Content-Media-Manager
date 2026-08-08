#!/usr/bin/env bash
# Pide todas las páginas y exige 200. Uso: comprobar-rutas.sh <puerto>
#
# Un healthcheck en verde no basta como prueba de despliegue: hay que pedir las
# páginas de verdad. Una columna que falte en el esquema deja el proceso vivo y
# respondiendo, pero devuelve 500 en cada vista, y eso solo se ve pidiéndolas.
#
# REGLA: cuando se añada una ruta GET nueva a la app, se añade aquí.
#
# Desde que hay cuentas, casi todas redirigen a /cuentas si no hay ninguna
# abierta, así que lo primero es abrir una: pedir las páginas sin sesión
# comprobaría solo que el selector redirige, que no es lo que interesa aquí.
set -euo pipefail

puerto="$1"
base="http://localhost:$puerto"
galletas=$(mktemp)
trap 'rm -f "$galletas"' EXIT

# El id de la primera cuenta se saca del propio selector en vez de darlo por
# hecho: la crea la migración, y no tiene por qué ser siempre el 1.
cuenta=$(curl -s "$base/cuentas" | grep -o '/cuentas/entrar/[0-9]*' | head -1)
if [ -z "$cuenta" ]; then
    echo "el selector no ofrece ninguna cuenta: la migración no creó la inicial"
    exit 1
fi
# Origin: lo exige la protección CSRF en toda petición que escribe.
codigo=$(curl -s -o /dev/null -w '%{http_code}' -c "$galletas" -X POST \
    -H "Origin: $base" "$base$cuenta")
if [ "$codigo" != "303" ]; then
    echo "no se pudo abrir la cuenta ($cuenta): $codigo"
    exit 1
fi

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
    /cuentas
    /cuentas/ajustes
    /estado
    /sugerencia
    /tengo-tiempo
    /salud
)

fallos=0
for ruta in "${rutas[@]}"; do
    codigo=$(curl -s -o /dev/null -w '%{http_code}' -b "$galletas" "$base$ruta")
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
