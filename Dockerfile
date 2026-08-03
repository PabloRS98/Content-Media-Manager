FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Usuario sin privilegios: sin esto el proceso corre como root y los ficheros
# de /data (la base de datos y los backups) quedan a nombre de root en el
# volumen del host. gosu es lo que usa docker-entrypoint.sh para bajar
# privilegios después de arreglar el dueño de /data en cada arranque (ver ese
# fichero: hace falta también para actualizaciones, no solo instalaciones nuevas).
RUN mkdir -p /data \
    && useradd --system --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app \
    && apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

VOLUME ["/data"]

EXPOSE 8000

# El scheduler (avisos + backup diario, ver app/config.py: enable_scheduler)
# arranca una vez por proceso: si algún día se añade --workers N al CMD de
# abajo, hay que poner ENABLE_SCHEDULER=false en N-1 de los workers, o habrá
# N jobs de backup pisándose el mismo fichero y avisos de Telegram duplicados.
HEALTHCHECK --interval=60s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/salud', timeout=3)" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
