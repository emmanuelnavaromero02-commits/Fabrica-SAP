# Imagen del servidor MCP `fabrica` (puente Claude Code/Codex local <-> fábrica).
# Servidor MCP por stdio: lo lanza Claude Code con `docker run -i`
# (ver .mcp.json). No expone puertos.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py ./

RUN useradd --create-home --uid 10001 mcp
USER mcp

ENTRYPOINT ["python", "server.py"]
