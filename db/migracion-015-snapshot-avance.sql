-- migracion-015: fotos de avance para facturación por DELTA.
-- Un "congelar avance" guarda una foto del avance por GAP en un momento; la
-- facturación del período = (avance ahora − avance de la última foto) × horas
-- técnicas. Se conserva el historial de fotos (una por período/corte).
-- `datos` = jsonb array: [{wp_id, subject, capability, avance, horas_tecnicas}]

CREATE TABLE IF NOT EXISTS snapshot_avance (
    id          serial PRIMARY KEY,
    proyecto_id integer NOT NULL REFERENCES proyecto(id) ON DELETE CASCADE,
    tomado_en   timestamptz NOT NULL DEFAULT now(),
    tomado_por  text,
    etiqueta    text,
    datos       jsonb NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_snapshot_avance_proj
    ON snapshot_avance (proyecto_id, tomado_en DESC);
