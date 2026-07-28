FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Usuario sin privilegios: el proceso no necesita root, y así los ficheros del
# volumen /data no quedan a nombre de root en el host.
RUN useradd --system --uid 10001 --create-home appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data /app

VOLUME ["/data"]
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/salud || exit 1

# Un solo worker a propósito: el scheduler (avisos + backup diario) arranca en el
# lifespan, que se ejecuta una vez por worker. Con varios, pon ENABLE_SCHEDULER=false
# en todos menos uno.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
