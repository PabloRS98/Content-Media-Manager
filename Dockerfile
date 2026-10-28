# Fijada por digest además de por tag [R3-B18]: `3.12-slim` es un tag móvil, y
# dos builds del mismo commit podían salir con bases distintas. El tag se deja
# para que se lea qué versión es; Dependabot (ecosistema docker) sube las dos
# cosas juntas.
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

# Sin .pyc en la imagen (el sistema de ficheros va en solo lectura en el compose,
# no se pueden escribir de todos modos) y sin buffer en stdout/stderr: sin esto,
# los logs de uvicorn llegan a `docker logs` a trompicones o se pierden si el
# proceso muere. [R3-B18]
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Usuario sin privilegios: sin esto el proceso corre como root y los ficheros
# de /data (la base de datos y los backups) quedan a nombre de root en el
# volumen del host. gosu es lo que usa docker-entrypoint.sh para bajar
# privilegios después de arreglar el dueño de /data en cada arranque (ver ese
# fichero: hace falta también para actualizaciones, no solo instalaciones nuevas).
#
# Va ANTES de copiar el código [R3-B18]: es la capa lenta (apt-get update) y no
# depende de la app, así que con el código después se reutiliza de caché en
# cada cambio en vez de repetirse.
RUN mkdir -p /data /app \
    && useradd --system --create-home --uid 1000 appuser \
    && chown appuser:appuser /app \
    && apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Con --require-hashes: pip rechaza cualquier paquete (también transitivo) cuyo
# hash no esté en el lock. requirements.txt se genera desde requirements.in. [R3-M25]
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

# --chown en lugar de un `chown -R` posterior: el resultado es el mismo y no
# duplica el código en otra capa.
COPY --chown=appuser:appuser app ./app
# Las migraciones las aplica el entrypoint antes de arrancar uvicorn, así que
# tienen que estar dentro de la imagen.
COPY --chown=appuser:appuser alembic.ini ./alembic.ini
COPY --chown=appuser:appuser migrations ./migrations

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

VOLUME ["/data"]

EXPOSE 8000

# El scheduler (avisos + backup diario, ver app/config.py: enable_scheduler)
# arranca una vez por proceso: si algún día se añade --workers N al CMD de
# abajo, hay que poner ENABLE_SCHEDULER=false en N-1 de los workers, o habrá
# N jobs de backup pisándose el mismo fichero y avisos de Telegram duplicados.
#
# Este es el ÚNICO healthcheck [R3-B20]. Estaba también en el compose, duplicado
# palabra por palabra: dos sitios que cambiar y que podían divergir. Vive aquí
# porque el CI arranca la imagen con `docker run` a secas (.github/esperar-salud.sh
# espera al estado de salud de Docker) y quien use la imagen sin el compose
# también lo tiene; el compose lo hereda de la imagen.
HEALTHCHECK --interval=60s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/salud', timeout=3)" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
