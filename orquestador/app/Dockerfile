# Imagen del orquestador. La misma imagen corre dos procesos segun el
# 'command' que fije docker-compose: el worker de Temporal o el receptor
# de webhooks (uvicorn).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Chromium headless para exportar artefactos (spec/test-plan/prototipo) a PDF.
# El prototipo trae JS → hace falta un motor de navegador real. Las fuentes
# evitan cajas vacías en acentos/símbolos. Solo lo usa el servicio webhook
# (inbox); el worker no lo invoca pero comparte imagen.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

RUN useradd --create-home --uid 10001 app
USER app

# Comando por defecto; docker-compose lo sobreescribe para el servicio webhook.
CMD ["python", "-u", "worker.py"]
