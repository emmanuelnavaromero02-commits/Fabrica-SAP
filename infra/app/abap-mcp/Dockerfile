# MCP ABAP (SAP ADT) — build desde el SAMPLE VENDORIZADO en el repo.
# Antes clonábamos el sample de AWS en build; ahora el fuente vive versionado en
# ./aws-7bs-abap-mcp/ (commit base ec6e685 + nuestros fixes DDIC: builders v2,
# corrNr, activación, tools create_domain/data_element/table). Así los cambios
# quedan bajo Git y se editan/revisan como código propio.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=true \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements + pip ANTES del src: así editar el src no reinstala dependencias
# (capa pip cacheada) → rebuilds rápidos al iterar el código.
COPY aws-7bs-abap-mcp/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt \
    && pip install boto3 pyyaml

# Fuente vendorizado (cambia seguido al iterar).
COPY aws-7bs-abap-mcp/src /app/src
RUN touch /app/.env.example

# Entrypoint propio: siembra el keychain en memoria (1 entrada por system_id,
# password desde SSM Parameter Store / docker secret / env) y luego cede al
# main() del sample. Ver entrypoint.py.
COPY entrypoint.py /app/entrypoint.py

EXPOSE 8000
CMD ["python", "/app/entrypoint.py"]
