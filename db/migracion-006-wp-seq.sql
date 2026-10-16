-- Migración 006: secuencia para el wp_id de requerimientos NUEVOS (sin OP).
--
-- Antes el wp_id salía de OpenProject (id del work package). Al apagar OP, los
-- requerimientos nuevos se crean en proyecto-db y necesitan su propio id estable
-- (= identidad del requerimiento + workflow Temporal `requisito-{wp_id}`).
-- Arranca por encima de los wp_id existentes (de OP) para no colisionar.
-- Idempotente.

BEGIN;

CREATE SEQUENCE IF NOT EXISTS requisito_wp_seq;
-- Posiciona la secuencia por encima del máximo wp_id actual y de 100000
-- (margen sobre los ids de OP, que llegan ~120).
SELECT setval('requisito_wp_seq',
              GREATEST((SELECT COALESCE(max(wp_id), 0) FROM requisito), 100000));

COMMIT;
