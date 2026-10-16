-- Migración 008: consultor funcional y usuario clave por requerimiento.
--
-- Asignación editable desde el Gantt (junto con desarrollador, migración 007).
-- Antes el funcional/usuario_clave vivían en asignaciones.json (Gitea); ahora
-- el board es la fuente de verdad (los lectores caen a Gitea si la columna es
-- NULL, por compat). Idempotente.

ALTER TABLE requisito ADD COLUMN IF NOT EXISTS funcional text;
ALTER TABLE requisito ADD COLUMN IF NOT EXISTS usuario_clave text;
