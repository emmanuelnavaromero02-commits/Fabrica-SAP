"""Etapas de negocio del modelo DIRIGIDO POR BLOG (hub-and-spoke).

Las 6 etapas son las que el usuario ve en el timeline. En Diseño/Desarrollo/
Pruebas el flujo lo conduce el blog (la "pelota" = responsable actual). El
detalle del inbox toma la pelota/fase de proyecto-db (blog_fase); aquí solo
está el catálogo para pintar el timeline y filtrar visibilidad por rol.

Importado por:
  - inbox.py (timeline + filtro de visibilidad)
  - workflow.py (compat; el modelo nuevo usa las etapas directamente)

Ver [[workflow-dirigido-por-blog]].
"""
from __future__ import annotations

# `progress` = % de avance del requisito por etapa (para barras/indicadores).
# `responsable_roles` = quién conduce la etapa (hub/drivers), para el filtro de
# visibilidad del inbox. El detalle de drivers/radios vive en la tabla `fase`.
ETAPAS = [
    {"key": "recepcion", "index": 1, "icon": "📥", "label": "Recepción",
     "responsable_roles": ["swf-funcional", "admin"], "progress": 0},
    {"key": "diseno", "index": 2, "icon": "✏️", "label": "Diseño",
     "responsable_roles": ["swf-funcional", "admin"], "progress": 20},
    {"key": "desarrollo", "index": 3, "icon": "🛠", "label": "Desarrollo",
     "responsable_roles": ["swf-desarrollador", "swf-funcional"], "progress": 45},
    {"key": "pruebas_funcionales", "index": 4, "icon": "🧪",
     "label": "Pruebas Funcionales",
     "responsable_roles": ["swf-desarrollador", "swf-tester"], "progress": 70},
    {"key": "pruebas_usuario", "index": 5, "icon": "👥",
     "label": "Pruebas de Usuario",
     "responsable_roles": ["swf-desarrollador", "swf-tester"], "progress": 90},
    {"key": "cerrado", "index": 6, "icon": "✅", "label": "Cerrado",
     "responsable_roles": [], "progress": 100},
]

# Estados terminales FUERA de la progresión lineal (no van en el timeline de 6
# pasos): el requisito salió del flujo. 'desestimado' = descartado por admin/
# líder; bloquea el inbox; un admin puede reactivarlo (vuelve a 'recepcion').
_EXTRA = [
    {"key": "desestimado", "index": 0, "icon": "🚫", "label": "Desestimado",
     "responsable_roles": [], "progress": 0},
]

# Etapas que TERMINAN el flujo: el inbox no permite acciones sobre ellas.
TERMINALES = {"cerrado", "desestimado"}

_BY_KEY = {e["key"]: e for e in (ETAPAS + _EXTRA)}


def etapa_from_stage(stage: str | None, op_status: str | None = None) -> dict:
    """Resuelve la etapa de negocio a partir del stage del workflow (modelo
    nuevo: el stage ES el código de etapa) y, como respaldo, del status. Devuelve
    el dict de ETAPAS + flag `is_blocked` (no se usa en el modelo nuevo)."""
    s = (stage or "").strip()
    status_lower = (op_status or "").lower()
    key = s if s in _BY_KEY else None
    if key is None:
        if s in ("", "iniciando"):
            key = "recepcion"
        elif (s in ("completado", "cerrado")
              or "cerrad" in status_lower or "closed" in status_lower
              or "reject" in status_lower or "rechaz" in status_lower):
            key = "cerrado"
        elif "prueba" in status_lower:
            key = "pruebas_funcionales"
        elif "desarrollo" in status_lower:
            key = "desarrollo"
        elif "dise" in status_lower:
            key = "diseno"
        else:
            key = "recepcion"
    e = dict(_BY_KEY[key])
    e["is_blocked"] = False
    return e


# Etapas dirigidas por el blog (compuertas de decisión humana). El timeline las
# marca como interactivas; la decisión concreta (aprobar/devolver/rechazar) la
# toman los drivers de la fase. Recepción y Cerrado no se marcan como gate.
_GATE_KEYS = {"diseno", "desarrollo", "pruebas_funcionales", "pruebas_usuario"}


def all_stages_for_ui() -> list[dict]:
    """Lista compacta de las 6 etapas para que el frontend pinte el timeline sin
    hardcodearlas. Incluye el/los rol(es) que conducen cada etapa y, si es
    dirigida por el blog, `gate='blog'`."""
    return [{"key": e["key"], "icon": e["icon"], "label": e["label"],
             "responsable_roles": e["responsable_roles"],
             "progress": e["progress"],
             "gate": "blog" if e["key"] in _GATE_KEYS else None}
            for e in ETAPAS]
