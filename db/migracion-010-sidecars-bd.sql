-- Migración 010: mover sidecars derivados de Gitea al board (Fase 1).
--
-- El repo del requerimiento debe contener SOLO artefactos reales
-- (inputs / diseño / código / evidencia de pruebas). Los objetos derivados
-- de control se mueven a proyecto-db. Esta fase migra:
--   desarrollo/repo.json        -> requisito.dev_repo      (jsonb)
--   clarificacion/preguntas.json -> requisito.clarificacion (jsonb)
-- (asignaciones.json ya vive en columnas funcional/usuario_clave/desarrollador,
--  migraciones 007-008.)
--
-- Los lectores caen al sidecar de Gitea si la columna es NULL (compat con
-- requerimientos viejos); los nuevos solo escriben a BD. Idempotente.

ALTER TABLE requisito ADD COLUMN IF NOT EXISTS dev_repo jsonb;
ALTER TABLE requisito ADD COLUMN IF NOT EXISTS clarificacion jsonb;
