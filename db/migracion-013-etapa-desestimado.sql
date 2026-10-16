-- migracion-013: falta la etapa terminal 'desestimado' en el catalogo `etapa`.
--
-- El codigo (etapas.py _EXTRA, proyeccion.desestimar_requisito, filtro del inbox)
-- ya usa 'desestimado', pero la tabla `etapa` solo tenia recepcion..cerrado. Como
-- requisito.etapa_actual tiene FK -> etapa(codigo), el UPDATE a 'desestimado'
-- fallaba por violacion de FK; desestimar_requisito se tragaba la excepcion y el
-- requisito seguia en su etapa previa (y en el inbox). Ver el boton Desestimar.
--
-- orden=0: fuera de la progresion lineal (recepcion=1..cerrado=6), como en
-- etapas.py (index 0). Idempotente.
INSERT INTO etapa (codigo, label, orden) VALUES
  ('desestimado', 'Desestimado', 0)
ON CONFLICT (codigo) DO NOTHING;
