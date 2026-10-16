-- Migración 009: líder de proyecto (project-manager) y líder de proyecto fábrica
-- (swf-project-manager) pasan a ser DRIVERS de todas las fases, para que puedan
-- VER/ACTUALIZAR el estado (aprobar / devolver / rechazar) del requerimiento.
-- Antes solo eran radios en 'diseno'. Idempotente.

INSERT INTO fase_actor(fase, rol, tipo) VALUES
  ('recepcion','project-manager','driver'),
  ('recepcion','swf-project-manager','driver'),
  ('diseno','project-manager','driver'),
  ('diseno','swf-project-manager','driver'),
  ('desarrollo','project-manager','driver'),
  ('desarrollo','swf-project-manager','driver'),
  ('pruebas_funcionales','project-manager','driver'),
  ('pruebas_funcionales','swf-project-manager','driver'),
  ('pruebas_usuario','project-manager','driver'),
  ('pruebas_usuario','swf-project-manager','driver')
ON CONFLICT (fase, rol) DO UPDATE SET tipo = 'driver';
