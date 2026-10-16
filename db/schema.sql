-- Esquema de la base de datos de PROYECTO de la fábrica (Postgres).
--
-- Reemplaza el rol relacional de OpenProject. Es el read-model + datos de
-- proyecto (CQRS-lite): la VERDAD del lifecycle es Temporal y la de los
-- artefactos es Git; esta DB es proyección reconstruible para consultas ágiles
-- y escrituras transaccionales. Ver docs/plan-simplificacion-modelo.md.
--
-- Jerarquía: Cliente -> Proyecto -> Capacidad -> Requisito  (+ links req<->req)
-- SIN portafolio/programa (decisión 2026-06-14).

BEGIN;

-- ===========================================================================
-- Jerarquía de dominio
-- ===========================================================================

CREATE TABLE cliente (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nombre      text NOT NULL,
    slug        text NOT NULL UNIQUE,          -- = org en Gitea
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE proyecto (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cliente_id  bigint NOT NULL REFERENCES cliente(id) ON DELETE CASCADE,
    nombre      text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (cliente_id, nombre)
);

CREATE TABLE capacidad (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    proyecto_id bigint NOT NULL REFERENCES proyecto(id) ON DELETE CASCADE,
    nombre      text NOT NULL,                 -- segmentación fina de usuarios
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (proyecto_id, nombre)
);

-- Sistemas SAP por cliente (MCP ABAP). Espejo NO-secreto: la credencial vive en
-- AWS SSM (/fabrica-sw/abap/<system_id>, SecureString); aquí solo lo no-secreto
-- para listar/editar. La password NUNCA se guarda aquí. Ver migración 011.
CREATE TABLE sap_system (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cliente_id  bigint NOT NULL REFERENCES cliente(id) ON DELETE CASCADE,
    system_id   text NOT NULL UNIQUE,          -- = header x-sap-system-id = sufijo del param SSM
    host        text NOT NULL,                 -- host:puerto (44300 + nº de instancia)
    sap_client  text NOT NULL DEFAULT '100',   -- mandante SAP
    username    text NOT NULL,
    description text,
    secure      boolean NOT NULL DEFAULT true, -- HTTPS
    ssm_param   text,                          -- override del path SSM (default /fabrica-sw/abap/<system_id>)
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_sap_system_cliente ON sap_system (cliente_id);

-- ===========================================================================
-- Catálogos / configuración (editables por admin en el Panel)
-- ===========================================================================

-- Etapas del flujo (modelo dirigido por blog), con orden para calcular avance.
-- Recepción → Diseño → Desarrollo → Pruebas Funcionales → Pruebas Usuario →
-- Cerrado. En Diseño/Desarrollo/Pruebas el BLOG dirige el flujo (ver tabla fase).
CREATE TABLE etapa (
    codigo  text PRIMARY KEY,                  -- recepcion, diseno, ...
    label   text NOT NULL,
    orden   int  NOT NULL UNIQUE
);

-- Config de fase dirigida por blog (hub-and-spoke). hub_rol = a quién REGRESA la
-- pelota cuando un radio solo "responde"; avanza_a = etapa siguiente al aprobar
-- (NULL = terminal/cerrado). Ver [[workflow-dirigido-por-blog]].
CREATE TABLE fase (
    codigo    text PRIMARY KEY REFERENCES etapa(codigo) ON DELETE CASCADE,
    hub_rol   text NOT NULL,                   -- rol que conduce la fase
    avanza_a  text REFERENCES etapa(codigo),   -- etapa al aprobar (NULL=cerrado)
    devuelve_a text REFERENCES etapa(codigo)   -- fase anterior al "devolver" (NULL=ninguna)
);

-- Actores de cada fase: drivers (aprobar/rechazar/revisar=delegar) y radios
-- (solo responder → la pelota vuelve al hub). Data-driven: cambiar el set es un
-- INSERT/DELETE, no un cambio de esquema.
CREATE TABLE fase_actor (
    fase  text NOT NULL REFERENCES fase(codigo) ON DELETE CASCADE,
    rol   text NOT NULL,                       -- rol Keycloak
    tipo  text NOT NULL CHECK (tipo IN ('driver','radio')),
    PRIMARY KEY (fase, rol)
);

-- Tallas de camisa (t-shirt sizing). Data-driven: agregar 'medium' u otra
-- talla es un INSERT, no un cambio de esquema.
CREATE TABLE talla (
    codigo  text PRIMARY KEY,                  -- tiny, small, large, verylarge
    orden   int  NOT NULL UNIQUE
);

-- Prioridad del requisito (data-driven). orden 1 = más urgente (ordena el board).
CREATE TABLE prioridad (
    codigo  text PRIMARY KEY,                  -- urgente, alta, media, baja
    label   text NOT NULL,
    orden   int  NOT NULL UNIQUE
);

-- Conversión talla -> horas, DEPENDIENTE DEL STACK (front React != back ABAP).
CREATE TABLE talla_horas (
    stack   text NOT NULL,
    talla   text NOT NULL REFERENCES talla(codigo),
    horas   numeric(7,2) NOT NULL CHECK (horas >= 0),
    PRIMARY KEY (stack, talla)
);

-- Calendario laboral: para que "tiempo invertido" cuente SOLO horas laborales
-- (descuenta noches/fines de semana/feriados). Singleton (id=1) por ahora.
CREATE TABLE calendario (
    id              int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    tz              text NOT NULL DEFAULT 'America/Lima',
    dias_laborales  int[] NOT NULL DEFAULT '{1,2,3,4,5}',  -- 1=lunes .. 7=domingo
    hora_inicio     time NOT NULL DEFAULT '09:00',
    hora_fin        time NOT NULL DEFAULT '18:00'
);

CREATE TABLE feriado (
    fecha       date PRIMARY KEY,
    descripcion text
);

-- ===========================================================================
-- Requisitos, subtareas (front/back) y relaciones (CR)
-- ===========================================================================

CREATE TABLE requisito (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    wp_id         bigint UNIQUE,                           -- id del requisito (work package OP / clave externa estable)
    proyecto_id   bigint NOT NULL REFERENCES proyecto(id),
    capacidad_id  bigint REFERENCES capacidad(id),        -- opcional
    titulo        text NOT NULL,
    descripcion   text,
    stack         text,                                    -- stack principal
    talla         text REFERENCES talla(codigo),           -- estimado = camisa
    prioridad     text REFERENCES prioridad(codigo) DEFAULT 'media',
    inicio_plan   date,
    fin_plan      date,
    -- Esfuerzo del Plan partido en dos columnas capturables (mig-014). horas_plan
    -- es la SUMA de ambas, la mantiene la app en cada edición (NULL si ambas NULL,
    -- para que la vista caiga al estimado por camisas). La barra del Gantt y el
    -- total "Esfuerzo" usan horas_plan (= la suma).
    horas_tecnicas    numeric(9,2),                       -- horas técnicas (capturable)
    horas_funcionales numeric(9,2),                       -- horas funcionales (capturable)
    horas_plan    numeric(9,2),                           -- = horas_tecnicas + horas_funcionales (esfuerzo del Plan)
    etapa_actual  text REFERENCES etapa(codigo),
    -- True una vez que ARRANCÓ su workflow Temporal (lo pone proyectar_requisito
    -- al inicio del run). False = registrado pero "sin iniciar" (se arranca a
    -- mano con el botón ▶ Iniciar cuando llegan sus inputs).
    workflow_iniciado boolean NOT NULL DEFAULT false,
    -- "La pelota": quién es el responsable ACTUAL dentro de la fase y desde
    -- cuándo (lo dirige el blog). pelota_usuario = persona concreta; si es NULL,
    -- la pelota está en el POOL del rol (nadie la ha TOMADO todavía — p.ej. tras
    -- aprobar diseño, espera que un desarrollador la tome). Ver
    -- [[workflow-dirigido-por-blog]].
    pelota_rol      text,
    pelota_usuario  text,
    pelota_desde    timestamptz,
    -- Asignaciones FIJAS (editables desde el Gantt; distintas de pelota_usuario).
    desarrollador   text,
    funcional       text,
    usuario_clave   text,
    -- Coordenadas del repo del requerimiento (1 repo por requerimiento).
    repo_org      text,
    repo_name     text,
    repo_branch   text DEFAULT 'main',
    -- Sidecars derivados movidos de Gitea al board (migración 010):
    -- dev_repo = coords completas del repo del requerimiento (antes
    -- desarrollo/repo.json); clarificacion = preguntas del agente en modo
    -- blocked (antes clarificacion/preguntas.json).
    dev_repo      jsonb,
    clarificacion jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Secuencia del wp_id de requerimientos NUEVOS (sin OpenProject). Arranca alto
-- para no colisionar con los wp_id heredados de OP.
CREATE SEQUENCE IF NOT EXISTS requisito_wp_seq START 100000;

-- Sub-tracks front/back de un requisito. Cada uno con su stack y talla, así
-- pueden tener la misma talla y distintas horas.
CREATE TABLE subtarea (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    requisito_id  bigint NOT NULL REFERENCES requisito(id) ON DELETE CASCADE,
    tipo          text NOT NULL CHECK (tipo IN ('front','back')),
    stack         text,
    talla         text REFERENCES talla(codigo),
    inicio_plan   date,
    fin_plan      date,
    responsable   text,                                    -- usuario Keycloak
    estado        text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (requisito_id, tipo)
);

-- Links requisito<->requisito. Un CR ligado a su requisito origen. hereda_repo
-- = si el repo del nuevo se clonó del origen como baseline.
CREATE TABLE requisito_relacion (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    origen_id     bigint NOT NULL REFERENCES requisito(id) ON DELETE CASCADE,
    destino_id    bigint NOT NULL REFERENCES requisito(id) ON DELETE CASCADE,
    tipo          text NOT NULL CHECK (tipo IN ('cr','relacionado')),
    hereda_repo   boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (origen_id <> destino_id),
    UNIQUE (origen_id, destino_id, tipo)
);

-- ===========================================================================
-- Blog colaborativo de comentarios (sustituye los comentarios de OpenProject).
-- Los ARCHIVOS adjuntos son INPUTS: sus bytes se commitean al repo en Gitea
-- (/inputs/) para que un `git pull` los traiga; estas filas son el registro.
-- ===========================================================================
CREATE TABLE comentario (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    requisito_id bigint NOT NULL REFERENCES requisito(id) ON DELETE CASCADE,
    autor        text NOT NULL,                  -- username Keycloak
    autor_nombre text,                           -- display name
    cuerpo       text NOT NULL,
    -- Acción que dirige el flujo (hub-and-spoke). 'responder' = comentario normal
    -- (si lo hace un radio, la pelota vuelve al hub); 'delegar'/'revisar' = pasa
    -- la pelota al destino; 'aprobar' = avanza de fase; 'rechazar' = termina.
    accion         text NOT NULL DEFAULT 'responder'
                   CHECK (accion IN ('responder','delegar','aprobar','rechazar','devolver','tomar')),
    destino_rol      text,                        -- a quién se delega (rol)
    destino_usuario  text,                        -- a quién se delega (persona)
    etapa          text,                          -- fase al momento del comentario (snapshot)
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_comentario_req ON comentario (requisito_id, created_at);

CREATE TABLE comentario_adjunto (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    comentario_id bigint NOT NULL REFERENCES comentario(id) ON DELETE CASCADE,
    filename      text NOT NULL,
    repo_path     text,                          -- ruta en el repo (inputs/<archivo>)
    minio_key     text,
    size          bigint,
    content_type  text
);

-- ===========================================================================
-- Tiempo por etapa (proyección desde el timeline de Temporal)
-- horas_laborales se calcula contra `calendario` + `feriado`.
-- ===========================================================================
CREATE TABLE tiempo_etapa (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    requisito_id    bigint NOT NULL REFERENCES requisito(id) ON DELETE CASCADE,
    subtarea_id     bigint REFERENCES subtarea(id) ON DELETE CASCADE,  -- null = a nivel requisito
    etapa           text NOT NULL REFERENCES etapa(codigo),
    entrada         timestamptz NOT NULL,
    salida          timestamptz,                            -- null = etapa en curso
    horas_laborales numeric(9,2),                           -- recalculable
    es_espera       boolean NOT NULL DEFAULT false          -- true = espera de cliente (toggle trabajo vs espera)
);
CREATE INDEX ix_tiempo_etapa_req ON tiempo_etapa (requisito_id);

-- ===========================================================================
-- Seguridad: Roles (Keycloak) x Proyectos x Capacidades (asignaciones aquí)
-- usuario = identificador Keycloak (username o sub). Los ROLES viven en KC;
-- estas tablas son la segmentación fina por proyecto/capacidad.
-- ===========================================================================
CREATE TABLE usuario_proyecto (
    usuario     text NOT NULL,
    proyecto_id bigint NOT NULL REFERENCES proyecto(id) ON DELETE CASCADE,
    PRIMARY KEY (usuario, proyecto_id)
);

CREATE TABLE usuario_capacidad (
    usuario      text NOT NULL,
    capacidad_id bigint NOT NULL REFERENCES capacidad(id) ON DELETE CASCADE,
    PRIMARY KEY (usuario, capacidad_id)
);

-- Tokens personales de MCP (uno por usuario, para Claude Code). Se guarda solo
-- el hash (sha256). El Panel los genera (muestra el texto una vez); el MCP/
-- webhook los valida y de ahí sale el `actor` automático + sus roles. Revocable.
CREATE TABLE mcp_token (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    usuario     text NOT NULL,                  -- username Keycloak
    token_hash  text NOT NULL UNIQUE,           -- sha256 hex del token
    roles       text[] NOT NULL DEFAULT '{}',   -- snapshot de roles al emitir
    descripcion text,
    creado      timestamptz NOT NULL DEFAULT now(),
    ultimo_uso  timestamptz,
    revocado    boolean NOT NULL DEFAULT false
);
CREATE INDEX ix_mcp_token_usuario ON mcp_token (usuario);

-- ===========================================================================
-- Vistas de conveniencia
-- ===========================================================================

-- Estimado en horas del requisito = suma de horas de sus subtareas (por stack+talla);
-- si no tiene subtareas, usa la talla/stack del propio requisito.
CREATE VIEW v_requisito_estimado AS
SELECT r.id AS requisito_id,
       COALESCE(
         (SELECT sum(th.horas)
            FROM subtarea s
            JOIN talla_horas th ON th.stack = s.stack AND th.talla = s.talla
           WHERE s.requisito_id = r.id),
         (SELECT th2.horas
            FROM talla_horas th2
           WHERE th2.stack = r.stack AND th2.talla = r.talla)
       ) AS horas_estimadas
  FROM requisito r;

-- Avance % = orden de la etapa actual / orden máximo. Recepción (orden 1) NO
-- acredita avance (un requisito que solo "llegó" cuenta 0%, no 17%); a partir
-- de Diseño sí progresa hasta 100% en Cerrado.
CREATE VIEW v_requisito_avance AS
SELECT r.id AS requisito_id,
       e.orden,
       CASE WHEN e.orden <= 1 THEN 0
            ELSE round(100.0 * e.orden / (SELECT max(orden) FROM etapa), 0)
       END AS avance_pct
  FROM requisito r
  LEFT JOIN etapa e ON e.codigo = r.etapa_actual;

-- ===========================================================================
-- Semillas mínimas
-- ===========================================================================
INSERT INTO etapa (codigo, label, orden) VALUES
  ('desestimado','Desestimado',0),   -- terminal fuera de la progresión (etapas.py _EXTRA)
  ('recepcion','Recepción',1),
  ('diseno','Diseño',2),
  ('desarrollo','Desarrollo',3),
  ('pruebas_funcionales','Pruebas Funcionales',4),
  ('pruebas_usuario','Pruebas de Usuario',5),
  ('cerrado','Cerrado',6);

-- Config de fases dirigidas por blog: hub + a qué etapa avanza (aprobar) y a
-- cuál devuelve (fase anterior; p.ej. desde Desarrollo se devuelve a Diseño si
-- cambia el contrato — "documentos vivos").
INSERT INTO fase (codigo, hub_rol, avanza_a, devuelve_a) VALUES
  ('recepcion','swf-funcional','diseno',NULL),
  ('diseno','swf-funcional','desarrollo','recepcion'),
  ('desarrollo','swf-desarrollador','pruebas_funcionales','diseno'),
  ('pruebas_funcionales','swf-desarrollador','pruebas_usuario','desarrollo'),
  ('pruebas_usuario','swf-desarrollador','cerrado','pruebas_funcionales');

-- Actores por fase: drivers (aprobar/rechazar/revisar) y radios (solo responder).
INSERT INTO fase_actor (fase, rol, tipo) VALUES
  -- Recepción: solo fábrica/admin.
  ('recepcion','swf-funcional','driver'),
  ('recepcion','admin','driver'),
  -- Diseño: fábrica/admin conducen; funcional, usuario y líder de proyecto responden.
  ('diseno','swf-funcional','driver'),
  ('diseno','admin','driver'),
  ('diseno','funcional','radio'),
  ('diseno','usuario-clave','radio'),
  ('diseno','project-manager','radio'),
  ('diseno','swf-project-manager','radio'),
  -- Desarrollo: desarrollador conduce (+ fábrica/admin); funcional y usuario responden.
  ('desarrollo','swf-desarrollador','driver'),
  ('desarrollo','swf-funcional','driver'),
  ('desarrollo','admin','driver'),
  ('desarrollo','funcional','radio'),
  ('desarrollo','usuario-clave','radio'),
  -- Pruebas Funcionales: dev + fábrica + admin + tester conducen; funcional responde.
  ('pruebas_funcionales','swf-desarrollador','driver'),
  ('pruebas_funcionales','swf-funcional','driver'),
  ('pruebas_funcionales','admin','driver'),
  ('pruebas_funcionales','swf-tester','driver'),
  ('pruebas_funcionales','funcional','radio'),
  -- Pruebas de Usuario: igual + el usuario también responde.
  ('pruebas_usuario','swf-desarrollador','driver'),
  ('pruebas_usuario','swf-funcional','driver'),
  ('pruebas_usuario','admin','driver'),
  ('pruebas_usuario','swf-tester','driver'),
  ('pruebas_usuario','funcional','radio'),
  ('pruebas_usuario','usuario-clave','radio');

INSERT INTO talla (codigo, orden) VALUES
  ('tiny',1),('small',2),('medium',3),('large',4),('verylarge',5);

INSERT INTO prioridad (codigo, label, orden) VALUES
  ('urgente','Urgente',1),('alta','Alta',2),('media','Media',3),('baja','Baja',4);

INSERT INTO calendario (id) VALUES (1) ON CONFLICT DO NOTHING;

COMMIT;
