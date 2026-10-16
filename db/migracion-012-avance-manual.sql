-- Migración 012: avance manual capturado por el líder de proyecto.
--
-- El % de avance salía SOLO de la etapa (v_requisito_avance). Ahora el PM puede
-- fijar un % manual por requerimiento desde el Resumen del inbox; si lo fija,
-- ese valor manda; si lo deja vacío, se usa el cálculo por etapa de antes.
-- Misma vista que leen Resumen/Gantt/panel Avance → consistente en los tres.
-- Idempotente.

BEGIN;

ALTER TABLE requisito ADD COLUMN IF NOT EXISTS avance_manual smallint;

CREATE OR REPLACE VIEW v_requisito_avance AS
SELECT r.id AS requisito_id,
       e.orden,
       -- Sin cast de tipo: CREATE OR REPLACE VIEW exige conservar el tipo de la
       -- columna (numeric, como el round original). COALESCE(smallint, numeric)=numeric.
       COALESCE(
         r.avance_manual,
         CASE WHEN e.orden <= 1 THEN 0
              ELSE round(100.0 * e.orden / (SELECT max(orden) FROM etapa), 0)
         END
       ) AS avance_pct
  FROM requisito r
  LEFT JOIN etapa e ON e.codigo = r.etapa_actual;

COMMIT;
