-- Migración 001: clave externa estable `wp_id` en `requisito`.
--
-- La proyección desde el workflow Temporal (orquestador/app/proyeccion.py) hace
-- un upsert idempotente del requisito keado por `wp_id` (el id del work package
-- de OpenProject = identidad estable del requerimiento, puente Temporal↔DB).
-- Sin esta columna el upsert duplicaría filas en cada replay/reinicio.
--
-- Idempotente: se puede correr varias veces. Backfillea wp_id en las filas ya
-- migradas (2a) derivándolo de repo_branch = 'req-<wp_id>'.

BEGIN;

ALTER TABLE requisito ADD COLUMN IF NOT EXISTS wp_id bigint;

-- Backfill de las filas migradas en 2a: el branch codifica el wp_id.
UPDATE requisito
   SET wp_id = (substring(repo_branch FROM '^req-(\d+)$'))::bigint
 WHERE wp_id IS NULL
   AND repo_branch ~ '^req-\d+$';

-- Unicidad para el ON CONFLICT (wp_id) del upsert. Postgres permite múltiples
-- NULL bajo un índice UNIQUE, así que las filas sin wp_id no estorban.
CREATE UNIQUE INDEX IF NOT EXISTS ux_requisito_wp_id ON requisito (wp_id);

COMMIT;
