# Imagen del servidor MCP custom de OpenProject.
# Es un servidor MCP por stdio: lo lanza Claude Code con `docker run -i`
# (ver ../../.mcp.json). No expone puertos ni se queda escuchando en red.
FROM python:3.13-slim

# PYTHONUNBUFFERED es importante: el transporte MCP es stdio y el buffering
# de stdout romperia el intercambio de mensajes.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Instala dependencias en una capa propia para aprovechar la cache.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py ./

# Ejecuta como usuario sin privilegios.
RUN useradd --create-home --uid 10001 mcp
USER mcp

ENTRYPOINT ["python", "server.py"]
