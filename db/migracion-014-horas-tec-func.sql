-- ===========================================================================
-- migracion-014-horas-tec-func.sql
-- Parte las horas estimadas del requisito en DOS columnas capturables:
--   horas_tecnicas    + horas_funcionales
-- `horas_plan` (el esfuerzo que mueve la barra del Gantt y el total "Esfuerzo")
-- pasa a ser la SUMA de ambas, mantenida por la app en cada edición. Se conserva
-- NULL cuando ambas componentes son NULL (para que la vista siga cayendo al
-- estimado por camisas: COALESCE(horas_plan, horas_estimadas)).
--
-- Backfill: TODO lo que ya tenía horas_plan va a Técnicas (Funcionales = 0), así
-- la suma preserva exactamente el valor actual. Idempotente.
-- ===========================================================================

ALTER TABLE requisito
  ADD COLUMN IF NOT EXISTS horas_tecnicas    numeric(9,2),
  ADD COLUMN IF NOT EXISTS horas_funcionales numeric(9,2);

-- Backfill "todo a Técnicas": solo filas con horas_plan y sin split todavía.
UPDATE requisito
   SET horas_tecnicas    = horas_plan,
       horas_funcionales = 0
 WHERE horas_plan IS NOT NULL
   AND horas_tecnicas IS NULL
   AND horas_funcionales IS NULL;

-- Vista del Panel: exponer las dos componentes (se re-crea en funciones.sql;
-- aquí se re-emite para que la migración quede autocontenida).
DROP VIEW IF EXISTS v_requisito_panel;
CREATE VIEW v_requisito_panel AS
SELECT r.id,
       r.wp_id,
       c.nombre              AS cliente,
       p.nombre              AS proyecto,
       r.titulo,
       r.stack,
       r.talla,
       r.prioridad,
       pr.label              AS prioridad_label,
       COALESCE(pr.orden, 99) AS prioridad_orden,
       COALESCE(r.horas_plan, est.horas_estimadas) AS horas_estimadas,
       r.horas_plan,
       r.horas_tecnicas,
       r.horas_funcionales,
       r.inicio_plan,
       r.fin_plan,
       t.horas_trabajo,
       t.horas_espera,
       t.horas_total         AS tiempo_invertido,
       r.etapa_actual,
       r.workflow_iniciado,
       av.avance_pct,
       r.pelota_rol,
       r.pelota_usuario,
       r.pelota_desde,
       (SELECT count(*) FROM comentario cm WHERE cm.requisito_id = r.id) AS comentarios,
       r.repo_org, r.repo_name, r.repo_branch
  FROM requisito r
  JOIN proyecto p              ON p.id = r.proyecto_id
  JOIN cliente  c              ON c.id = p.cliente_id
  LEFT JOIN prioridad pr       ON pr.codigo = r.prioridad
  LEFT JOIN v_requisito_estimado est ON est.requisito_id = r.id
  LEFT JOIN v_requisito_tiempo   t   ON t.requisito_id   = r.id
  LEFT JOIN v_requisito_avance   av  ON av.requisito_id  = r.id;
