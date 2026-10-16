-- Migración 004: tokens personales de MCP (uno por usuario, para Claude Code).
--
-- El Panel los genera (muestra el texto UNA vez, guarda solo el sha256). El
-- webhook/MCP los valida → de ahí sale el `actor` automático + roles. Revocable.
-- Cierra el hueco de identidad multi-usuario (antes: un token compartido).
-- Idempotente.

BEGIN;

CREATE TABLE IF NOT EXISTS mcp_token (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    usuario     text NOT NULL,
    token_hash  text NOT NULL UNIQUE,
    roles       text[] NOT NULL DEFAULT '{}',
    descripcion text,
    creado      timestamptz NOT NULL DEFAULT now(),
    ultimo_uso  timestamptz,
    revocado    boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS ix_mcp_token_usuario ON mcp_token (usuario);

COMMIT;
