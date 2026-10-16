-- Migración 005: horas_plan (esfuerzo del Plan) en el board + sync.
--
-- El estimado del tab Plan (horas) aterriza en proyecto-db para que el board lo
-- muestre y se mantenga sincronizado al cambiar el plan. La vista prefiere
-- horas_plan; si no hay, cae al estimado por camisas. Idempotente.

BEGIN;

ALTER TABLE requisito ADD COLUMN IF NOT EXISTS horas_plan numeric(9,2);

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

COMMIT;
