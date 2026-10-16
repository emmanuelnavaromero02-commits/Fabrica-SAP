-- Migración 007: desarrollador asignado por requerimiento.
--
-- Asignación FIJA de quién desarrolla el requerimiento (distinta de pelota_usuario,
-- que es el responsable ACTUAL de la fase y cambia con el flujo). La usa el Gantt
-- (columna Desarrollador) y la vista por desarrollador. Texto libre = username KC.
-- Idempotente.

ALTER TABLE requisito ADD COLUMN IF NOT EXISTS desarrollador text;
