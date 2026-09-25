from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer


def actor() -> str:
    return os.environ.get("FABRICA_MCP_USER", "consultor")


def serve(server: MCPServer, default_port: int) -> None:
    transport = os.environ.get("FABRICA_MCP_TRANSPORT", "stdio")
    if transport == "http":
        import anyio

        anyio.run(
            lambda: server.run_streamable_http_async(
                host=os.environ.get("FABRICA_MCP_HOST", "127.0.0.1"),
                port=int(os.environ.get("FABRICA_MCP_PORT", default_port)),
            )
        )
    else:
        server.run("stdio")
