-- Migración 011: sistemas SAP por cliente (MCP ABAP, FASE 2).
--
-- Espejo NO-secreto de los sistemas SAP que administra el Panel. La fuente de
-- verdad de las credenciales es AWS SSM Parameter Store (un SecureString por
-- sistema en /fabrica-sw/abap/<system_id> con {host,client,username,password,...});
-- esta tabla guarda solo lo no-secreto para listar/editar sin descifrar SSM.
-- La PASSWORD nunca vive aquí. Ver infra/app/abap-mcp/entrypoint.py y
-- infra/README.md (sección MCP ABAP). Idempotente.

CREATE TABLE IF NOT EXISTS sap_system (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cliente_id  bigint NOT NULL REFERENCES cliente(id) ON DELETE CASCADE,
    system_id   text NOT NULL UNIQUE,          -- = header x-sap-system-id = sufijo del param SSM
    host        text NOT NULL,                 -- host:puerto (44300 + nº de instancia)
    sap_client  text NOT NULL DEFAULT '100',   -- mandante SAP
    username    text NOT NULL,
    description text,
    secure      boolean NOT NULL DEFAULT true, -- HTTPS
    ssm_param   text,                          -- override del path SSM (default /fabrica-sw/abap/<system_id>)
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_sap_system_cliente ON sap_system (cliente_id);
