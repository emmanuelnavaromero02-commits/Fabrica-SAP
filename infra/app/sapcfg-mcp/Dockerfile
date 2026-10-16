# MCP `sapcfg` — configuración de SAP por código (BC-Sets), servidor MCP propio.
# Llama al handler ABAP ZCL_7BS_SAPCFG_API (SICF /sap/bc/z7bs/sapcfg) por HTTP.
# Mismo patrón liviano que el resto de la fábrica: FastMCP + Streamable HTTP en :8000,
# detrás de Caddy (/sapcfg/* -> :8000). Auth a SAP = basic, creds desde SSM.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=true \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY server.py /app/server.py

EXPOSE 8000
CMD ["python", "/app/server.py"]
