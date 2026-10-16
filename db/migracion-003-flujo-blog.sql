-- Migración 003: modelo de flujo DIRIGIDO POR BLOG (hub-and-spoke por fase).
--
-- CUTOVER: reemplaza el catálogo de etapas viejo (recibido/generando/
-- revision_fabrica/validacion_cliente/desarrollo/pruebas/cerrado) por el nuevo
-- (recepcion/diseno/desarrollo/pruebas_funcionales/pruebas_usuario/cerrado),
-- resetea TODOS los requerimientos a Recepción conservando sus documentos, y
-- siembra la config de fases (hub/drivers/radios). El tiempo por etapa se
-- REINICIA (la fábrica arranca el modelo nuevo). Ver [[workflow-dirigido-por-blog]].
--
-- DESTRUCTIVO para tiempo_etapa y para etapa_actual (reset). Correr SOLO en el
-- cutover, coordinado con la reescritura del workflow Temporal. Idempotente.

BEGIN;

-- 1) Columnas nuevas (aditivas).
ALTER TABLE requisito ADD COLUMN IF NOT EXISTS pelota_rol        text;
ALTER TABLE requisito ADD COLUMN IF NOT EXISTS pelota_usuario    text;
ALTER TABLE requisito ADD COLUMN IF NOT EXISTS pelota_desde      timestamptz;
ALTER TABLE requisito ADD COLUMN IF NOT EXISTS workflow_iniciado boolean NOT NULL DEFAULT false;

ALTER TABLE comentario ADD COLUMN IF NOT EXISTS accion text NOT NULL DEFAULT 'responder';
DO $$ BEGIN
  ALTER TABLE comentario ADD CONSTRAINT comentario_accion_chk
    CHECK (accion IN ('responder','delegar','aprobar','rechazar','devolver','tomar'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
ALTER TABLE comentario ADD COLUMN IF NOT EXISTS destino_rol     text;
ALTER TABLE comentario ADD COLUMN IF NOT EXISTS destino_usuario text;
ALTER TABLE comentario ADD COLUMN IF NOT EXISTS etapa           text;

-- 2) Tablas de config de fase.
CREATE TABLE IF NOT EXISTS fase (
    codigo    text PRIMARY KEY REFERENCES etapa(codigo) ON DELETE CASCADE,
    hub_rol   text NOT NULL,
    avanza_a  text REFERENCES etapa(codigo),
    devuelve_a text REFERENCES etapa(codigo)
);
ALTER TABLE fase ADD COLUMN IF NOT EXISTS devuelve_a text REFERENCES etapa(codigo);
CREATE TABLE IF NOT EXISTS fase_actor (
    fase  text NOT NULL REFERENCES fase(codigo) ON DELETE CASCADE,
    rol   text NOT NULL,
    tipo  text NOT NULL CHECK (tipo IN ('driver','radio')),
    PRIMARY KEY (fase, rol)
);

-- 3) Swap del catálogo de etapas + reset a Recepción.
UPDATE requisito SET etapa_actual = NULL;   -- soltar refs a etapas viejas
TRUNCATE tiempo_etapa;                        -- el tiempo se reinicia
DELETE FROM fase_actor;
DELETE FROM fase;
DELETE FROM etapa;

INSERT INTO etapa (codigo, label, orden) VALUES
  ('recepcion','Recepción',1),
  ('diseno','Diseño',2),
  ('desarrollo','Desarrollo',3),
  ('pruebas_funcionales','Pruebas Funcionales',4),
  ('pruebas_usuario','Pruebas de Usuario',5),
  ('cerrado','Cerrado',6);

INSERT INTO fase (codigo, hub_rol, avanza_a, devuelve_a) VALUES
  ('recepcion','swf-funcional','diseno',NULL),
  ('diseno','swf-funcional','desarrollo','recepcion'),
  ('desarrollo','swf-desarrollador','pruebas_funcionales','diseno'),
  ('pruebas_funcionales','swf-desarrollador','pruebas_usuario','desarrollo'),
  ('pruebas_usuario','swf-desarrollador','cerrado','pruebas_funcionales');

INSERT INTO fase_actor (fase, rol, tipo) VALUES
  ('recepcion','swf-funcional','driver'),
  ('recepcion','admin','driver'),
  ('diseno','swf-funcional','driver'),
  ('diseno','admin','driver'),
  ('diseno','funcional','radio'),
  ('diseno','usuario-clave','radio'),
  ('diseno','project-manager','radio'),
  ('diseno','swf-project-manager','radio'),
  ('desarrollo','swf-desarrollador','driver'),
  ('desarrollo','swf-funcional','driver'),
  ('desarrollo','admin','driver'),
  ('desarrollo','funcional','radio'),
  ('desarrollo','usuario-clave','radio'),
  ('pruebas_funcionales','swf-desarrollador','driver'),
  ('pruebas_funcionales','swf-funcional','driver'),
  ('pruebas_funcionales','admin','driver'),
  ('pruebas_funcionales','swf-tester','driver'),
  ('pruebas_funcionales','funcional','radio'),
  ('pruebas_usuario','swf-desarrollador','driver'),
  ('pruebas_usuario','swf-funcional','driver'),
  ('pruebas_usuario','admin','driver'),
  ('pruebas_usuario','swf-tester','driver'),
  ('pruebas_usuario','funcional','radio'),
  ('pruebas_usuario','usuario-clave','radio');

-- Reset: todos los requerimientos a Recepción, "sin iniciar" (workflow apagado)
-- y con la pelota en el POOL de funcional-fábrica. Se arrancan a mano con ▶
-- Iniciar cuando lleguen sus inputs.
UPDATE requisito
   SET etapa_actual      = 'recepcion',
       workflow_iniciado = false,
       pelota_rol        = 'swf-funcional',
       pelota_usuario    = NULL,
       pelota_desde      = now();

-- 4) Vista del panel con la pelota (DROP+CREATE por el cambio de columnas).
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
       est.horas_estimadas,
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

-- 5) registrar_transicion: al entrar a una fase, la pelota arranca en su hub.
CREATE OR REPLACE FUNCTION registrar_transicion(p_req bigint, p_etapa text,
                                                p_ts timestamptz,
                                                p_es_espera boolean DEFAULT false)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  UPDATE tiempo_etapa
     SET salida = p_ts,
         horas_laborales = horas_laborales(entrada, p_ts)
   WHERE requisito_id = p_req AND salida IS NULL;
  INSERT INTO tiempo_etapa(requisito_id, etapa, entrada, es_espera)
  VALUES (p_req, p_etapa, p_ts, p_es_espera);
  UPDATE requisito
     SET etapa_actual   = p_etapa,
         pelota_rol     = (SELECT hub_rol FROM fase WHERE codigo = p_etapa),
         pelota_usuario = NULL,
         pelota_desde   = p_ts,
         updated_at     = now()
   WHERE id = p_req;
END; $$;

COMMIT;
