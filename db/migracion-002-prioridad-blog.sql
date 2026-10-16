-- Migración 002: prioridad del requisito + blog de comentarios colaborativo.
--
-- Colaboración/PM que SUSTITUYE a OpenProject (OP se apaga): prioridad y los
-- comentarios viven en proyecto-db (no en OP, no en el repo de artefactos). Los
-- ARCHIVOS que se suben en el blog son INPUTS: sus bytes se commitean al repo
-- en Gitea (/inputs/) para que un `git pull` los traiga; aquí solo se guarda el
-- registro para mostrarlos en el hilo.
--
-- Idempotente: se puede correr varias veces.

BEGIN;

-- Catálogo de prioridad (data-driven, como talla/etapa). orden 1 = más urgente.
CREATE TABLE IF NOT EXISTS prioridad (
    codigo text PRIMARY KEY,
    label  text NOT NULL,
    orden  int  NOT NULL UNIQUE
);
INSERT INTO prioridad(codigo, label, orden) VALUES
  ('urgente','Urgente',1),
  ('alta','Alta',2),
  ('media','Media',3),
  ('baja','Baja',4)
ON CONFLICT (codigo) DO NOTHING;

ALTER TABLE requisito
  ADD COLUMN IF NOT EXISTS prioridad text REFERENCES prioridad(codigo) DEFAULT 'media';

-- Blog colaborativo. autor = username Keycloak; autor_nombre = display name.
CREATE TABLE IF NOT EXISTS comentario (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    requisito_id bigint NOT NULL REFERENCES requisito(id) ON DELETE CASCADE,
    autor        text NOT NULL,
    autor_nombre text,
    cuerpo       text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_comentario_req ON comentario (requisito_id, created_at);

-- Archivos adjuntos en un comentario que se vuelven INPUTS. Los bytes viven en
-- Gitea (repo_path) + MinIO (minio_key); esta fila es el registro para el hilo.
CREATE TABLE IF NOT EXISTS comentario_adjunto (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    comentario_id bigint NOT NULL REFERENCES comentario(id) ON DELETE CASCADE,
    filename      text NOT NULL,
    repo_path     text,                 -- ruta en el repo (p.ej. inputs/<archivo>)
    minio_key     text,
    size          bigint,
    content_type  text
);

-- Vista del panel con prioridad (+ orden para el board) y conteo de comentarios.
-- DROP+CREATE porque CREATE OR REPLACE no permite insertar/reordenar columnas en
-- una vista existente (se agrega wp_id en medio). Nada depende de esta vista a
-- nivel DB (el Panel la consulta directo), así que el DROP es seguro.
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
       av.avance_pct,
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
