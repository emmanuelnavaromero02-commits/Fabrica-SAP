-- Funciones y vistas de cálculo de la DB de proyecto.
-- Aplicar DESPUÉS de schema.sql.
--
-- Núcleo: horas_laborales() = tiempo entre dos instantes contando SOLO horas
-- laborales (descuenta noches, fines de semana y feriados) según `calendario`
-- + `feriado`. Es la base de "tiempo invertido". Ver docs/plan-simplificacion-modelo.md.

BEGIN;

-- ---------------------------------------------------------------------------
-- horas_laborales(entrada, salida) -> horas (numeric)
-- Suma, día por día, la intersección entre [entrada, salida] y la ventana
-- laboral de cada día hábil, interpretada en la zona horaria del calendario.
-- salida NULL o <= entrada -> 0.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION horas_laborales(p_entrada timestamptz,
                                           p_salida  timestamptz)
RETURNS numeric
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    cal        calendario%ROWTYPE;
    tz         text;
    total      numeric := 0;
    d          date;
    day_start  timestamptz;
    day_end    timestamptz;
    seg_start  timestamptz;
    seg_end    timestamptz;
    dow        int;
BEGIN
    IF p_salida IS NULL OR p_salida <= p_entrada THEN
        RETURN 0;
    END IF;

    SELECT * INTO cal FROM calendario WHERE id = 1;
    IF NOT FOUND THEN
        -- Sin calendario configurado: cae a tiempo calendario completo.
        RETURN round(EXTRACT(EPOCH FROM (p_salida - p_entrada)) / 3600.0, 2);
    END IF;
    tz := cal.tz;

    -- Iterar cada fecha LOCAL entre entrada y salida.
    FOR d IN
        SELECT generate_series(
                 (p_entrada AT TIME ZONE tz)::date::timestamp,
                 (p_salida  AT TIME ZONE tz)::date::timestamp,
                 interval '1 day')::date
    LOOP
        dow := EXTRACT(ISODOW FROM d);          -- 1=lunes .. 7=domingo
        CONTINUE WHEN NOT (dow = ANY (cal.dias_laborales));
        CONTINUE WHEN EXISTS (SELECT 1 FROM feriado f WHERE f.fecha = d);

        -- Ventana laboral del día, como instantes (hora local -> timestamptz).
        day_start := (d + cal.hora_inicio) AT TIME ZONE tz;
        day_end   := (d + cal.hora_fin)    AT TIME ZONE tz;

        -- Intersección con [entrada, salida].
        seg_start := GREATEST(p_entrada, day_start);
        seg_end   := LEAST(p_salida, day_end);
        IF seg_end > seg_start THEN
            total := total + EXTRACT(EPOCH FROM (seg_end - seg_start)) / 3600.0;
        END IF;
    END LOOP;

    RETURN round(total, 2);
END;
$$;

-- ---------------------------------------------------------------------------
-- Tiempo invertido por requisito (en vivo): trabajo vs espera vs total.
-- La etapa en curso (salida NULL) cuenta hasta now(). es_espera separa el
-- tiempo del equipo del tiempo esperando al cliente (toggle del Panel).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_requisito_tiempo AS
SELECT te.requisito_id,
       round(sum(horas_laborales(te.entrada, COALESCE(te.salida, now())))
             FILTER (WHERE NOT te.es_espera), 2)               AS horas_trabajo,
       round(sum(horas_laborales(te.entrada, COALESCE(te.salida, now())))
             FILTER (WHERE te.es_espera), 2)                   AS horas_espera,
       round(sum(horas_laborales(te.entrada, COALESCE(te.salida, now()))), 2)
                                                               AS horas_total
  FROM tiempo_etapa te
 GROUP BY te.requisito_id;

-- ---------------------------------------------------------------------------
-- Fila de proyecto lista para el Panel: estimado (camisas), tiempo invertido
-- (laboral) y avance, en una sola consulta.
-- ---------------------------------------------------------------------------
-- DROP+CREATE (no CREATE OR REPLACE) para poder cambiar el orden/columnas de la
-- vista aunque ya exista (CREATE OR REPLACE solo deja agregar columnas al final).
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
       -- "La pelota": responsable actual dentro de la fase (lo dirige el blog).
       -- pelota_usuario NULL = en el POOL del rol (nadie la ha tomado).
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

-- ---------------------------------------------------------------------------
-- Proyección: registrar una transición de etapa de forma ATÓMICA.
-- La activity de Temporal la llama en cada cambio de etapa (reemplaza el
-- set_status hacia OpenProject). Cierra el segmento abierto (fijando sus horas
-- laborales), abre el de la nueva etapa y actualiza requisito.etapa_actual.
-- ---------------------------------------------------------------------------
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
  -- Al entrar a una fase, la pelota arranca en su HUB (rol que la conduce);
  -- pelota_desde = ts de la transición. 'cerrado' no tiene fase → pelota NULL.
  UPDATE requisito
     SET etapa_actual   = p_etapa,
         pelota_rol     = (SELECT hub_rol FROM fase WHERE codigo = p_etapa),
         pelota_usuario = NULL,
         pelota_desde   = p_ts,
         updated_at     = now()
   WHERE id = p_req;
END; $$;

COMMIT;

-- ---------------------------------------------------------------------------
-- Verificación rápida (no se ejecuta en el apply; ejemplos de uso):
--   Lun 09:00 -> Lun 12:00            = 3.00
--   Vie 17:00 -> Lun 10:00 (salta fin de semana) = 1 + 1 = 2.00
--   Lun 09:00 -> Mar 18:00 (jornada 9-18) = 9 + 9 = 18.00
-- ---------------------------------------------------------------------------
