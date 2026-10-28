#!/usr/bin/env bash
# Espera a que un contenedor quede healthy. Uso: esperar-salud.sh <nombre> <puerto>
#
# Se mira el estado del healthcheck de Docker y no solo el código HTTP: es lo
# que de verdad se quiere probar. Desde MC-M4, /salud devuelve 503 si el
# esquema no está al día, así que "healthy" significa algo -- antes Docker daba
# por sano cualquier proceso que contestara.
set -euo pipefail

contenedor="$1"
puerto="$2"
limite=90

for ((i = 0; i < limite; i++)); do
    estado=$(docker inspect --format '{{.State.Health.Status}}' "$contenedor" 2>/dev/null || echo "sin-contenedor")
    case "$estado" in
        healthy)
            echo "$contenedor healthy tras ${i}s"
            exit 0
            ;;
        unhealthy)
            echo "$contenedor está unhealthy"
            curl -s -i "http://localhost:$puerto/salud" || true
            docker logs "$contenedor" 2>&1 | tail -40
            exit 1
            ;;
    esac
    if [ "$(docker inspect --format '{{.State.Running}}' "$contenedor" 2>/dev/null)" = "false" ]; then
        echo "$contenedor se ha parado"
        docker logs "$contenedor" 2>&1 | tail -40
        exit 1
    fi
    sleep 1
done

echo "$contenedor no llegó a healthy en ${limite}s (último estado: $estado)"
curl -s -i "http://localhost:$puerto/salud" || true
docker logs "$contenedor" 2>&1 | tail -40
exit 1
