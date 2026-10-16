-- Migración 011: Recepción no acredita avance (0%).
--
-- El % de avance por requisito salía de la posición de la etapa: Recepción
-- (orden 1) daba 17%. Con muchos requisitos recién "recibidos", el promedio del
-- proyecto se inflaba (ej. proy 1: 25 en Recepción → 25% aunque solo 4 habían
-- avanzado de verdad). Ahora Recepción = 0%; el avance arranca en Diseño.
-- Misma vista que leen el Resumen (KPI "Avance del proyecto"), el panel Avance y
-- el Gantt → el cambio es consistente en los tres. Idempotente.

BEGIN;

CREATE OR REPLACE VIEW v_requisito_avance AS
SELECT r.id AS requisito_id,
       e.orden,
       CASE WHEN e.orden <= 1 THEN 0
            ELSE round(100.0 * e.orden / (SELECT max(orden) FROM etapa), 0)
       END AS avance_pct
  FROM requisito r
  LEFT JOIN etapa e ON e.codigo = r.etapa_actual;

COMMIT;
