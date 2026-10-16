"""Proyección del lifecycle hacia la DB de proyecto (read-model CQRS-lite).

La VERDAD del lifecycle es Temporal; esto solo PROYECTA a Postgres para que el
Panel tenga consultas ágiles (estimado/fechas/tiempo/avance) sin recorrer repos.
Por eso las escrituras son BEST-EFFORT: si la DB falla, el workflow NO se rompe
(la proyección se puede reconstruir desde Temporal + Git).

Reemplaza el rol de OpenProject (registro de requisitos + estado). Ver
db/schema.sql, db/funciones.sql y docs/plan-simplificacion-modelo.md.

DSN por env `PROYECTO_DB_DSN` (p.ej. postgresql://fabrica:***@db:5432/fabrica).
Si no está configurado, todas las funciones son no-op (permite desplegar por
fases sin obligar a tener la DB lista).
"""
from __future__ import annotations

import json
import logging
import os

try:
    import asyncpg  # type: ignore
except ImportError:  # el worker puede no tener el driver aún
    asyncpg = None  # type: ignore

log = logging.getLogger("orquestador.proyeccion")

_DSN = os.environ.get("PROYECTO_DB_DSN", "")
_pool: "asyncpg.Pool | None" = None


def habilitada() -> bool:
    return bool(_DSN and asyncpg is not None)


async def _get_pool() -> "asyncpg.Pool | None":
    global _pool
    if not habilitada():
        return None
    if _pool is None:
        _pool = await asyncpg.create_pool(_DSN, min_size=1, max_size=4)
    return _pool


async def upsert_requisito(req: dict) -> int | None:
    """Crea/actualiza el registro del requisito (y su jerarquía cliente/proyecto/
    capacidad). Idempotente por `wp_id` (clave externa estable = work package OP),
    así un replay/reinicio del workflow no duplica filas. Devuelve el id en la DB,
    o None si la proyección está deshabilitada o falla.

    req: {wp_id, cliente, proyecto, capacidad?, titulo, stack?, talla?, repo_org?,
          repo_name?, repo_branch?, inicio_plan?, fin_plan?}
    """
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as c, c.transaction():
            cli = await c.fetchval(
                """INSERT INTO cliente(nombre, slug) VALUES($1,$2)
                   ON CONFLICT (slug) DO UPDATE SET nombre=EXCLUDED.nombre
                   RETURNING id""",
                req["cliente"], req.get("slug") or _slug(req["cliente"]))
            proy = await c.fetchval(
                """INSERT INTO proyecto(cliente_id, nombre) VALUES($1,$2)
                   ON CONFLICT (cliente_id, nombre) DO UPDATE SET nombre=EXCLUDED.nombre
                   RETURNING id""",
                cli, req["proyecto"])
            cap = None
            if req.get("capacidad"):
                cap = await c.fetchval(
                    """INSERT INTO capacidad(proyecto_id, nombre) VALUES($1,$2)
                       ON CONFLICT (proyecto_id, nombre) DO UPDATE SET nombre=EXCLUDED.nombre
                       RETURNING id""",
                    proy, req["capacidad"])
            # Keado por wp_id; en conflicto refresca lo informativo sin pisar lo
            # ya conocido (COALESCE) ni la etapa_actual (la maneja la transición).
            # workflow_iniciado=true: upsert_requisito lo llama el workflow al
            # ARRANCAR (run), así que marca el requisito como iniciado (deja de
            # mostrarse "sin iniciar"). Ver [[workflow-dirigido-por-blog]].
            rid = await c.fetchval(
                """INSERT INTO requisito(wp_id, proyecto_id, capacidad_id, titulo,
                        stack, talla, inicio_plan, fin_plan,
                        repo_org, repo_name, repo_branch, workflow_iniciado)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,COALESCE($11,'main'),true)
                   ON CONFLICT (wp_id) DO UPDATE SET
                        proyecto_id  = EXCLUDED.proyecto_id,
                        capacidad_id = COALESCE(EXCLUDED.capacidad_id, requisito.capacidad_id),
                        titulo       = EXCLUDED.titulo,
                        stack        = COALESCE(EXCLUDED.stack, requisito.stack),
                        talla        = COALESCE(EXCLUDED.talla, requisito.talla),
                        inicio_plan  = COALESCE(EXCLUDED.inicio_plan, requisito.inicio_plan),
                        fin_plan     = COALESCE(EXCLUDED.fin_plan, requisito.fin_plan),
                        repo_org     = COALESCE(EXCLUDED.repo_org, requisito.repo_org),
                        repo_name    = COALESCE(EXCLUDED.repo_name, requisito.repo_name),
                        repo_branch  = COALESCE(EXCLUDED.repo_branch, requisito.repo_branch),
                        workflow_iniciado = true,
                        updated_at   = now()
                   RETURNING id""",
                req["wp_id"], proy, cap, req["titulo"], req.get("stack"),
                req.get("talla"), req.get("inicio_plan"), req.get("fin_plan"),
                req.get("repo_org"), req.get("repo_name"), req.get("repo_branch"))
            return rid
    except Exception as exc:  # noqa: BLE001  (best-effort)
        log.warning("proyeccion.upsert_requisito falló (no fatal): %s", exc)
        return None


async def registrar_transicion(wp_id: int, etapa: str, ts, # noqa: ANN001
                               es_espera: bool = False) -> None:
    """Proyecta un cambio de etapa: resuelve el requisito por `wp_id`, cierra el
    segmento abierto (fija sus horas laborales), abre el nuevo y actualiza la
    etapa actual. `ts` ISO-8601 (str) o datetime. No-op si el requisito aún no
    está registrado (la transición previa al upsert sería un orden imposible)."""
    pool = await _get_pool()
    if pool is None:
        return
    # asyncpg exige un datetime para el parámetro timestamptz (un str rompe).
    if isinstance(ts, str):
        import datetime
        try:
            ts = datetime.datetime.fromisoformat(ts)
        except ValueError:
            log.warning("registrar_transicion: ts no parseable: %r", ts)
            return
    try:
        async with pool.acquire() as c:
            rid = await c.fetchval(
                "SELECT id FROM requisito WHERE wp_id=$1", wp_id)
            if rid is None:
                log.warning(
                    "registrar_transicion: requisito wp_id=%s no existe (skip)",
                    wp_id)
                return
            await c.execute(
                "SELECT registrar_transicion($1,$2,$3,$4)",
                rid, etapa, ts, es_espera)
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.registrar_transicion falló (no fatal): %s", exc)


async def _requisito_id_por_wp(c, wp_id: int) -> int | None:
    return await c.fetchval("SELECT id FROM requisito WHERE wp_id=$1", wp_id)


def _wd_back(fin, n: int):
    """inicio = (n-1) días laborables antes de fin (fin = último día laborable)."""
    import datetime
    d = fin if isinstance(fin, datetime.date) else datetime.date.fromisoformat(str(fin)[:10])
    steps = max(0, n - 1)
    while steps > 0:
        d -= datetime.timedelta(days=1)
        if d.weekday() < 5:
            steps -= 1
    return d


async def editar_campo(wp_id: int, field: str, value: str) -> bool:
    """Edita un campo del requerimiento desde el Gantt del inbox. field ∈ {fin,
    funcional, desarrollador, usuario_clave}. 'fin' recalcula inicio = fin − horas/8
    (días laborables); los demás → NULL si value vacío."""
    import datetime
    pool = await _get_pool()
    if pool is None:
        return False
    val = (value or "").strip() or None
    try:
        async with pool.acquire() as c:
            if field == "fin":
                if not val:
                    return False
                fin = datetime.date.fromisoformat(val[:10])
                h = await c.fetchval("SELECT horas_plan FROM requisito WHERE wp_id=$1", wp_id)
                n = max(1, round(float(h) / 8)) if h else 1
                ini = _wd_back(fin, n)
                r = await c.execute(
                    "UPDATE requisito SET inicio_plan=$2, fin_plan=$3, updated_at=now() "
                    "WHERE wp_id=$1", wp_id, ini, fin)
                return r.endswith(" 1")
            if field in ("horas_tecnicas", "horas_funcionales", "horas"):
                # Horas partidas en Técnicas + Funcionales (mig-014). horas_plan =
                # suma de ambas (NULL si ambas NULL); al cambiar cualquiera se
                # recalcula inicio = fin − ceil(suma/8) días laborables. 'horas'
                # (legado / entrada única) se trata como Técnicas.
                col = "horas_tecnicas" if field == "horas" else field
                try:
                    cv = float(val) if val else None
                except (TypeError, ValueError):
                    cv = None
                await c.execute(
                    f"UPDATE requisito SET {col}=$2, updated_at=now() WHERE wp_id=$1",
                    wp_id, cv)
                row = await c.fetchrow(
                    "SELECT horas_tecnicas, horas_funcionales, fin_plan "
                    "FROM requisito WHERE wp_id=$1", wp_id)
                tec, fun, fin = row["horas_tecnicas"], row["horas_funcionales"], row["fin_plan"]
                total = (None if tec is None and fun is None
                         else float(tec or 0) + float(fun or 0))
                if total and fin:
                    ini = _wd_back(fin, max(1, round(total / 8)))
                    r = await c.execute(
                        "UPDATE requisito SET horas_plan=$2, inicio_plan=$3, updated_at=now() "
                        "WHERE wp_id=$1", wp_id, total, ini)
                else:
                    r = await c.execute(
                        "UPDATE requisito SET horas_plan=$2, updated_at=now() WHERE wp_id=$1",
                        wp_id, total)
                return r.endswith(" 1")
            if field in ("funcional", "desarrollador", "usuario_clave"):
                r = await c.execute(
                    f"UPDATE requisito SET {field}=$2, updated_at=now() WHERE wp_id=$1",
                    wp_id, val)
                return r.endswith(" 1")
            if field == "avance_pct":
                # % manual del PM (mig-012). Vacío => NULL => vuelve al cálculo por etapa.
                pv = None
                if val:
                    try:
                        pv = max(0, min(100, int(round(float(val)))))
                    except (TypeError, ValueError):
                        return False
                r = await c.execute(
                    "UPDATE requisito SET avance_manual=$2, updated_at=now() WHERE wp_id=$1",
                    wp_id, pv)
                return r.endswith(" 1")
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.editar_campo falló: %s", exc)
    return False


# ----------------------------------------------------------------------
# Sidecars derivados movidos de Gitea al board (migración 010). Antes vivían
# como JSON en el repo del requerimiento; ahora el board es la fuente de
# verdad y el repo solo guarda artefactos reales. Ver memoria de limpieza.
# ----------------------------------------------------------------------
async def set_dev_repo(wp_id: int, meta: dict) -> bool:
    """Guarda las coordenadas del repo del requerimiento (antes
    desarrollo/repo.json). Best-effort."""
    pool = await _get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as c:
            r = await c.execute(
                "UPDATE requisito SET dev_repo=$2::jsonb, updated_at=now() "
                "WHERE wp_id=$1", wp_id, json.dumps(meta or {}))
            return r.endswith(" 1")
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.set_dev_repo falló: %s", exc)
        return False


async def get_dev_repo(wp_id: int) -> dict:
    """Coordenadas del repo del requerimiento desde el board. {} si no hay."""
    pool = await _get_pool()
    if pool is None:
        return {}
    try:
        async with pool.acquire() as c:
            v = await c.fetchval(
                "SELECT dev_repo FROM requisito WHERE wp_id=$1", wp_id)
            if not v:
                return {}
            return json.loads(v) if isinstance(v, str) else dict(v)
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.get_dev_repo falló: %s", exc)
        return {}


async def set_clarificacion(wp_id: int, payload: dict) -> bool:
    """Guarda las preguntas de aclaración del agente (antes
    clarificacion/preguntas.json). Best-effort."""
    pool = await _get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as c:
            r = await c.execute(
                "UPDATE requisito SET clarificacion=$2::jsonb, updated_at=now() "
                "WHERE wp_id=$1", wp_id, json.dumps(payload or {}))
            return r.endswith(" 1")
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.set_clarificacion falló: %s", exc)
        return False


async def get_clarificacion(wp_id: int) -> dict:
    """Preguntas de aclaración del agente desde el board. {} si no hay."""
    pool = await _get_pool()
    if pool is None:
        return {}
    try:
        async with pool.acquire() as c:
            v = await c.fetchval(
                "SELECT clarificacion FROM requisito WHERE wp_id=$1", wp_id)
            if not v:
                return {}
            return json.loads(v) if isinstance(v, str) else dict(v)
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.get_clarificacion falló: %s", exc)
        return {}


async def usuarios_de_proyecto(project_id: int) -> list[str]:
    """Usuarios asignados al proyecto (para los selectores del Gantt)."""
    pool = await _get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT DISTINCT usuario FROM usuario_proyecto WHERE proyecto_id=$1 "
                "ORDER BY usuario", project_id)
        return [r["usuario"] for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.usuarios_de_proyecto falló: %s", exc)
        return []


async def usuarios_de_proyecto_por_wp(wp_id: int) -> list[str]:
    """Usuarios del proyecto del requerimiento (para selectores del Gantt inbox)."""
    pool = await _get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT DISTINCT up.usuario FROM usuario_proyecto up "
                "JOIN requisito r ON r.proyecto_id = up.proyecto_id "
                "WHERE r.wp_id = $1 ORDER BY up.usuario", wp_id)
        return [r["usuario"] for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.usuarios_de_proyecto_por_wp falló: %s", exc)
        return []


async def sap_system_id_de_cliente(cliente_slug: str) -> str | None:
    """system_id del SAP del cliente (tabla sap_system, registrada en el Panel),
    para hornearlo como `x-sap-system-id` en el .mcp.json del repo. Prefiere el
    ambiente DEV (system_id que contenga 'dev'); si no, el primero. None si el
    cliente no tiene sistema SAP registrado. cliente_slug = slug del cliente."""
    pool = await _get_pool()
    if pool is None or not cliente_slug:
        return None
    try:
        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT s.system_id FROM sap_system s "
                "JOIN cliente cli ON cli.id = s.cliente_id "
                "WHERE cli.slug = $1 "
                "ORDER BY (s.system_id ILIKE '%dev%') DESC, s.system_id "
                "LIMIT 1", cliente_slug)
        return row["system_id"] if row else None
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.sap_system_id_de_cliente falló: %s", exc)
        return None


async def set_prioridad(wp_id: int, prioridad: str) -> bool:
    """Fija la prioridad del requisito (catálogo `prioridad`). True si actualizó."""
    pool = await _get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as c:
            n = await c.execute(
                "UPDATE requisito SET prioridad=$2, updated_at=now() WHERE wp_id=$1",
                wp_id, prioridad)
            return n.endswith(" 1")
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.set_prioridad falló: %s", exc)
        return False


async def get_prioridad(wp_id: int) -> str | None:
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as c:
            return await c.fetchval(
                "SELECT prioridad FROM requisito WHERE wp_id=$1", wp_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.get_prioridad falló: %s", exc)
        return None


async def listar_prioridades() -> list[dict]:
    """Catálogo de prioridad (codigo/label/orden) para poblar selectores."""
    pool = await _get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT codigo, label, orden FROM prioridad ORDER BY orden")
            return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.listar_prioridades falló: %s", exc)
        return []


async def listar_comentarios(wp_id: int) -> list[dict]:
    """Hilo del blog (orden cronológico) con sus adjuntos. [] si no hay DB."""
    pool = await _get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as c:
            rid = await _requisito_id_por_wp(c, wp_id)
            if rid is None:
                return []
            rows = await c.fetch(
                """SELECT id, autor, autor_nombre, cuerpo, created_at
                     FROM comentario WHERE requisito_id=$1 ORDER BY created_at""",
                rid)
            out = [dict(r) for r in rows]
            if out:
                adj = await c.fetch(
                    """SELECT comentario_id, filename, repo_path, minio_key,
                              size, content_type
                         FROM comentario_adjunto
                        WHERE comentario_id = ANY($1::bigint[])""",
                    [r["id"] for r in out])
                by_c: dict[int, list] = {}
                for a in adj:
                    by_c.setdefault(a["comentario_id"], []).append(dict(a))
                for r in out:
                    r["adjuntos"] = by_c.get(r["id"], [])
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.listar_comentarios falló: %s", exc)
        return []


async def guardar_plan(wp_id: int, inicio_plan, fin_plan,
                       horas_plan=None) -> bool:
    """Guarda el PLAN del requisito en el board (read-model): fechas planeadas +
    esfuerzo (horas). inicio/fin ISO 'YYYY-MM-DD' (o date); None deja la columna
    intacta. Reemplaza el viejo sync a OpenProject."""
    pool = await _get_pool()
    if pool is None:
        return False
    import datetime

    def _d(v):
        if v is None:
            return None
        if isinstance(v, datetime.date):
            return v
        try:
            return datetime.date.fromisoformat(str(v)[:10])
        except (ValueError, TypeError):
            return None

    def _n(v):
        try:
            return float(v) if v is not None and str(v) != "" else None
        except (ValueError, TypeError):
            return None

    try:
        async with pool.acquire() as c:
            # El total del estimado (horas_plan param) SIEMBRA Técnicas SOLO si aún
            # no hay captura (COALESCE(horas_tecnicas, $4)): el split manual del PM
            # gana sobre re-sincronizaciones del estimado. Funcionales es siempre
            # manual. horas_plan = Técnicas + Funcionales (NULL si ambas NULL).
            n = await c.execute(
                """UPDATE requisito
                      SET inicio_plan    = COALESCE($2, inicio_plan),
                          fin_plan       = COALESCE($3, fin_plan),
                          horas_tecnicas = COALESCE(horas_tecnicas, $4),
                          horas_plan     = CASE
                              WHEN COALESCE(horas_tecnicas, $4) IS NULL
                                   AND horas_funcionales IS NULL THEN NULL
                              ELSE COALESCE(horas_tecnicas, $4, 0)
                                   + COALESCE(horas_funcionales, 0) END,
                          updated_at     = now()
                    WHERE wp_id = $1""",
                wp_id, _d(inicio_plan), _d(fin_plan), _n(horas_plan))
            return n.endswith(" 1")
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.guardar_plan falló: %s", exc)
        return False


async def get_plan(wp_id: int) -> dict | None:
    """Plan actual en el board (para recomputar fin al cambiar el estimado)."""
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as c:
            r = await c.fetchrow(
                """SELECT inicio_plan, fin_plan, horas_plan,
                          horas_tecnicas, horas_funcionales
                     FROM requisito WHERE wp_id=$1""", wp_id)
            return dict(r) if r else None
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.get_plan falló: %s", exc)
        return None


_INBOX_SEC = (
    "($1::bool OR EXISTS (SELECT 1 FROM requisito r2 "
    "JOIN usuario_proyecto up ON up.proyecto_id = r2.proyecto_id "
    "WHERE r2.id = vp.id AND up.usuario = $2 AND (r2.capacidad_id IS NULL "
    "OR EXISTS (SELECT 1 FROM usuario_capacidad uc "
    "WHERE uc.capacidad_id = r2.capacidad_id AND uc.usuario = $2))))")

# Variante para LÍDER de proyecto (fábrica o cliente): ve TODAS las capacidades
# de los proyectos a los que está asignado, sin el filtro por usuario_capacidad.
# Sigue acotado a SUS proyectos (usuario_proyecto) — no ve otros proyectos.
_INBOX_SEC_LEADER = (
    "($1::bool OR EXISTS (SELECT 1 FROM requisito r2 "
    "JOIN usuario_proyecto up ON up.proyecto_id = r2.proyecto_id "
    "WHERE r2.id = vp.id AND up.usuario = $2))")


async def listar_capacidades(proyecto_id: int) -> list[dict]:
    """TODAS las capacidades del proyecto (para el alta de requerimiento)."""
    pool = await _get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT id, nombre FROM capacidad WHERE proyecto_id=$1 "
                "ORDER BY nombre", proyecto_id)
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.listar_capacidades falló: %s", exc)
        return []


async def crear_capacidad(proyecto_id: int, nombre: str) -> int | None:
    """Crea (o reusa) una capacidad del proyecto. Devuelve su id."""
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as c:
            return await c.fetchval(
                """INSERT INTO capacidad(proyecto_id, nombre) VALUES($1,$2)
                   ON CONFLICT (proyecto_id, nombre)
                   DO UPDATE SET nombre=EXCLUDED.nombre RETURNING id""",
                proyecto_id, nombre)
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.crear_capacidad falló: %s", exc)
        return None


async def crear_requisito(proyecto_id: int, capacidad_id: int | None,
                          titulo: str, descripcion: str = "",
                          repo_org: str | None = None,
                          repo_name: str | None = None) -> dict | None:
    """Crea un requerimiento NUEVO en el board con su propio wp_id (sin OP).
    repo_branch = req-{wp_id}. Devuelve {id, wp_id}."""
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as c, c.transaction():
            wp_id = await c.fetchval("SELECT nextval('requisito_wp_seq')")
            rid = await c.fetchval(
                """INSERT INTO requisito(wp_id, proyecto_id, capacidad_id, titulo,
                        descripcion, etapa_actual, pelota_rol, pelota_desde,
                        repo_org, repo_name, repo_branch, workflow_iniciado)
                   VALUES($1,$2,$3,$4,$5,'recepcion','swf-funcional',now(),
                          $6,$7,$8,false) RETURNING id""",
                wp_id, proyecto_id, capacidad_id, titulo, descripcion or None,
                repo_org, repo_name, f"req-{wp_id}")
            return {"id": rid, "wp_id": wp_id}
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.crear_requisito falló: %s", exc)
        return None


async def crear_relacion(wp_origen: int, wp_destino: int, tipo: str = "relacionado",
                         hereda_repo: bool = False) -> bool:
    """Liga dos requerimientos (CR/relacionado) en requisito_relacion."""
    pool = await _get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as c:
            o = await _requisito_id_por_wp(c, wp_origen)
            d = await _requisito_id_por_wp(c, wp_destino)
            if not o or not d or o == d:
                return False
            await c.execute(
                """INSERT INTO requisito_relacion(origen_id, destino_id, tipo,
                        hereda_repo) VALUES($1,$2,$3,$4)
                   ON CONFLICT (origen_id, destino_id, tipo) DO NOTHING""",
                o, d, tipo, hereda_repo)
            return True
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.crear_relacion falló: %s", exc)
        return False


async def set_capacidad(wp_id: int, capacidad_id: int) -> dict | None:
    """Reasigna la capacidad de un requisito (debe ser del MISMO proyecto).
    Devuelve {capacidad_id, capacidad_nombre} o None si inválida."""
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT proyecto_id FROM requisito WHERE wp_id=$1", wp_id)
            if not row:
                return None
            cap = await c.fetchrow(
                "SELECT id, nombre FROM capacidad WHERE id=$1 AND proyecto_id=$2",
                capacidad_id, row["proyecto_id"])
            if not cap:
                return None  # no existe o es de otro proyecto
            await c.execute(
                "UPDATE requisito SET capacidad_id=$2, updated_at=now() "
                "WHERE wp_id=$1", wp_id, capacidad_id)
            return {"capacidad_id": cap["id"], "capacidad_nombre": cap["nombre"]}
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.set_capacidad falló: %s", exc)
        return None


async def eliminar_relacion(relacion_id: int) -> bool:
    """Borra una relación entre requisitos por su id (requisito_relacion.id)."""
    pool = await _get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as c:
            n = await c.execute(
                "DELETE FROM requisito_relacion WHERE id=$1", relacion_id)
            return n.endswith(" 1")
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.eliminar_relacion falló: %s", exc)
        return False


async def mis_proyectos(usuario: str, es_admin: bool) -> list[dict]:
    """Proyectos para el selector del inbox (proyecto-db). Admin = todos; resto =
    los que tiene asignados en usuario_proyecto."""
    pool = await _get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as c:
            if es_admin:
                rows = await c.fetch(
                    "SELECT p.id, p.nombre, cli.nombre AS cliente FROM proyecto p "
                    "JOIN cliente cli ON cli.id = p.cliente_id "
                    "ORDER BY cli.nombre, p.nombre")
            else:
                rows = await c.fetch(
                    "SELECT DISTINCT p.id, p.nombre, cli.nombre AS cliente "
                    "FROM proyecto p JOIN cliente cli ON cli.id = p.cliente_id "
                    "JOIN usuario_proyecto up ON up.proyecto_id = p.id "
                    "WHERE up.usuario = $1 ORDER BY cli.nombre, p.nombre", usuario)
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.mis_proyectos falló: %s", exc)
        return []


async def mis_capacidades(usuario: str, proyecto_id: int,
                          es_admin: bool) -> list[dict]:
    """Capacidades del proyecto para el selector del inbox (proyecto-db)."""
    pool = await _get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as c:
            if es_admin:
                rows = await c.fetch(
                    "SELECT id, nombre FROM capacidad WHERE proyecto_id=$1 "
                    "ORDER BY nombre", proyecto_id)
            else:
                rows = await c.fetch(
                    "SELECT cap.id, cap.nombre FROM capacidad cap "
                    "JOIN usuario_capacidad uc ON uc.capacidad_id = cap.id "
                    "WHERE cap.proyecto_id=$1 AND uc.usuario=$2 ORDER BY cap.nombre",
                    proyecto_id, usuario)
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.mis_capacidades falló: %s", exc)
        return []


async def listar_inbox(usuario: str, es_admin: bool, q: str = "",
                       incluir_cerrados: bool = False,
                       proyecto_id: int | None = None,
                       capacidad_id: int | None = None,
                       ver_todas_capacidades: bool = False) -> list[dict]:
    """Requerimientos visibles para el inbox desde proyecto-db (reemplaza la
    consulta a OpenProject). Seguridad = roles×proyectos×capacidades. q = # exacto
    o título contiene. `ver_todas_capacidades` (líder de proyecto) = ve todas las
    capacidades de SUS proyectos, sin el filtro por usuario_capacidad."""
    pool = await _get_pool()
    if pool is None:
        return []
    _sec = _INBOX_SEC_LEADER if ver_todas_capacidades else _INBOX_SEC
    base = (
        "SELECT vp.wp_id, vp.titulo, vp.cliente, vp.proyecto, "
        "cap.nombre AS capacidad, vp.etapa_actual, vp.workflow_iniciado, "
        "vp.pelota_rol, vp.pelota_usuario, vp.prioridad, vp.prioridad_orden, "
        "vp.avance_pct, vp.comentarios, r.desarrollador, r.funcional, "
        "r.usuario_clave, r.avance_manual "
        "FROM v_requisito_panel vp JOIN requisito r ON r.id = vp.id "
        "LEFT JOIN capacidad cap ON cap.id = r.capacidad_id "
        f"WHERE {_sec}")
    args: list = [es_admin, usuario]
    conds: list[str] = []
    qn = (q or "").strip()
    if qn:
        if qn.isdigit():
            args.append(int(qn))
            conds.append(f"vp.wp_id = ${len(args)}")
        else:
            args.append(f"%{qn}%")
            conds.append(f"vp.titulo ILIKE ${len(args)}")
    if proyecto_id:
        args.append(int(proyecto_id))
        conds.append(f"r.proyecto_id = ${len(args)}")
    if capacidad_id:
        args.append(int(capacidad_id))
        conds.append(f"r.capacidad_id = ${len(args)}")
    if not incluir_cerrados:
        # Terminales (cerrado + desestimado) salen del inbox activo; se ven con
        # "mostrar todos".
        conds.append("vp.etapa_actual NOT IN ('cerrado', 'desestimado')")
    where = (" AND " + " AND ".join(conds)) if conds else ""
    sql = base + where + " ORDER BY vp.prioridad_orden, vp.wp_id LIMIT 300"
    try:
        async with pool.acquire() as c:
            rows = await c.fetch(sql, *args)
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.listar_inbox falló: %s", exc)
        return []


# --- Fotos de avance (facturación por delta) -------------------------------
async def guardar_snapshot_avance(proyecto_id: int, tomado_por: str,
                                  etiqueta: str | None = None) -> int | None:
    """Toma una FOTO del avance actual del proyecto (avance del líder + horas
    técnicas por GAP, excluyendo desestimados) y la persiste. Devuelve el id."""
    import json as _json
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as c:
            rows = await c.fetch(
                """SELECT r.wp_id, r.titulo AS subject, cap.nombre AS capability,
                          COALESCE(r.avance_manual, 0) AS avance,
                          r.horas_tecnicas
                     FROM requisito r
                     LEFT JOIN capacidad cap ON cap.id = r.capacidad_id
                    WHERE r.proyecto_id = $1
                      AND COALESCE(r.etapa_actual, '') <> 'desestimado'""",
                proyecto_id)
            datos = [{"wp_id": x["wp_id"], "subject": x["subject"],
                      "capability": x["capability"] or "(sin capacidad)",
                      "avance": int(x["avance"] or 0),
                      "horas_tecnicas": (float(x["horas_tecnicas"])
                                         if x["horas_tecnicas"] is not None else None)}
                     for x in rows]
            return await c.fetchval(
                """INSERT INTO snapshot_avance(proyecto_id, tomado_por, etiqueta, datos)
                   VALUES($1, $2, $3, $4::jsonb) RETURNING id""",
                proyecto_id, tomado_por, etiqueta, _json.dumps(datos))
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.guardar_snapshot_avance falló: %s", exc)
        return None


async def listar_snapshots(proyecto_id: int) -> list[dict]:
    """Fotos del proyecto (metadatos), más recientes primero."""
    pool = await _get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as c:
            rows = await c.fetch(
                """SELECT id, tomado_en, tomado_por, etiqueta,
                          jsonb_array_length(datos) AS n_gaps
                     FROM snapshot_avance WHERE proyecto_id = $1
                    ORDER BY tomado_en DESC""", proyecto_id)
        return [{"id": r["id"], "tomado_en": r["tomado_en"].isoformat(),
                 "tomado_por": r["tomado_por"], "etiqueta": r["etiqueta"],
                 "n_gaps": r["n_gaps"]} for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.listar_snapshots falló: %s", exc)
        return []


async def ultima_snapshot(proyecto_id: int) -> dict | None:
    """Última foto del proyecto (con sus datos por GAP) para calcular el delta."""
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as c:
            r = await c.fetchrow(
                """SELECT id, tomado_en, tomado_por, etiqueta, datos
                     FROM snapshot_avance WHERE proyecto_id = $1
                    ORDER BY tomado_en DESC LIMIT 1""", proyecto_id)
        if not r:
            return None
        import json as _json
        return {"id": r["id"], "tomado_en": r["tomado_en"].isoformat(),
                "tomado_por": r["tomado_por"], "etiqueta": r["etiqueta"],
                "datos": _json.loads(r["datos"]) if isinstance(r["datos"], str)
                         else r["datos"]}
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.ultima_snapshot falló: %s", exc)
        return None


async def get_requisito_detalle(wp_id: int) -> dict | None:
    """Detalle del requisito para el inbox (reemplaza get_work_package de OP):
    titulo, descripcion, etapa, proyecto, capacidad, cliente, pelota, iniciado."""
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as c:
            r = await c.fetchrow(
                """SELECT r.titulo, r.descripcion, r.etapa_actual,
                          r.proyecto_id, p.nombre AS proyecto,
                          r.capacidad_id, cap.nombre AS capacidad,
                          cli.nombre AS cliente,
                          r.pelota_rol, r.pelota_usuario, r.workflow_iniciado,
                          r.funcional, r.usuario_clave, r.desarrollador,
                          r.prioridad, r.inicio_plan, r.fin_plan, r.horas_plan
                     FROM requisito r
                     JOIN proyecto p   ON p.id = r.proyecto_id
                     JOIN cliente cli  ON cli.id = p.cliente_id
                     LEFT JOIN capacidad cap ON cap.id = r.capacidad_id
                    WHERE r.wp_id = $1""", wp_id)
            return dict(r) if r else None
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.get_requisito_detalle falló: %s", exc)
        return None


async def listar_relaciones(wp_id: int) -> list[dict]:
    """Requisitos relacionados (requisito_relacion) — el otro extremo + tipo."""
    pool = await _get_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as c:
            rid = await _requisito_id_por_wp(c, wp_id)
            if rid is None:
                return []
            rows = await c.fetch(
                """SELECT rr.id, rr.tipo, rr.hereda_repo,
                          (CASE WHEN rr.origen_id=$1 THEN rr.destino_id
                                ELSE rr.origen_id END) AS otro_id
                     FROM requisito_relacion rr
                    WHERE rr.origen_id=$1 OR rr.destino_id=$1""", rid)
            out = []
            for r in rows:
                o = await c.fetchrow(
                    "SELECT wp_id, titulo FROM requisito WHERE id=$1", r["otro_id"])
                if o:
                    out.append({"relation_id": r["id"], "wp_id": o["wp_id"],
                                "subject": o["titulo"], "type": r["tipo"],
                                "hereda_repo": r["hereda_repo"]})
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.listar_relaciones falló: %s", exc)
        return []


async def marcar_iniciado(wp_id: int) -> bool:
    """Marca el requisito como iniciado SIN tocar cliente/proyecto (lo llama el
    workflow al arrancar; no debe re-resolver/mover el cliente)."""
    pool = await _get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as c:
            n = await c.execute(
                "UPDATE requisito SET workflow_iniciado=true, updated_at=now() "
                "WHERE wp_id=$1", wp_id)
            return n.endswith(" 1")
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.marcar_iniciado falló: %s", exc)
        return False


async def reset_requisito(wp_id: int) -> bool:
    """Reinicia el requisito a 'recepcion' sin borrarlo: etapa inicial, pelota al
    rol swf-funcional, workflow_iniciado=false. Lo usa el botón Reiniciar."""
    pool = await _get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as c:
            n = await c.execute(
                "UPDATE requisito SET etapa_actual='recepcion', "
                "pelota_rol='swf-funcional', pelota_usuario=NULL, "
                "pelota_desde=now(), workflow_iniciado=false, updated_at=now() "
                "WHERE wp_id=$1", wp_id)
            return n.endswith(" 1")
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.reset_requisito falló: %s", exc)
        return False


async def cerrar_requisito(wp_id: int) -> bool:
    """Cierra el requisito (etapa 'cerrado') — p. ej. al descartarlo como
    duplicado. Libera la pelota."""
    pool = await _get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as c:
            n = await c.execute(
                "UPDATE requisito SET etapa_actual='cerrado', "
                "pelota_usuario=NULL, pelota_desde=now(), updated_at=now() "
                "WHERE wp_id=$1", wp_id)
            return n.endswith(" 1")
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.cerrar_requisito falló: %s", exc)
        return False


async def desestimar_requisito(wp_id: int) -> bool:
    """Desestima el requisito (etapa terminal 'desestimado'): sale del flujo y el
    inbox lo bloquea. Libera la pelota. Reversible con reset_requisito (admin)."""
    pool = await _get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as c:
            n = await c.execute(
                "UPDATE requisito SET etapa_actual='desestimado', "
                "pelota_usuario=NULL, pelota_rol=NULL, pelota_desde=now(), "
                "updated_at=now() WHERE wp_id=$1", wp_id)
            return n.endswith(" 1")
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.desestimar_requisito falló: %s", exc)
        return False


async def set_requisito_texto(wp_id: int, titulo: str | None = None,
                              descripcion: str | None = None) -> bool:
    """Actualiza título/descripción del requisito en el board (al validar/editar
    en el inbox). None deja la columna intacta."""
    pool = await _get_pool()
    if pool is None:
        return False
    try:
        async with pool.acquire() as c:
            n = await c.execute(
                """UPDATE requisito
                      SET titulo = COALESCE($2, titulo),
                          descripcion = COALESCE($3, descripcion),
                          updated_at = now()
                    WHERE wp_id = $1""", wp_id, titulo, descripcion)
            return n.endswith(" 1")
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.set_requisito_texto falló: %s", exc)
        return False


async def get_requisito_texto(wp_id: int) -> dict | None:
    """Título + descripción del requisito desde el board (para fetch_inputs sin
    OpenProject)."""
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as c:
            r = await c.fetchrow(
                "SELECT titulo, descripcion FROM requisito WHERE wp_id=$1", wp_id)
            return dict(r) if r else None
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.get_requisito_texto falló: %s", exc)
        return None


async def get_creador(wp_id: int) -> str | None:
    """Autor del comentario más antiguo del requisito (= quien lo creó). Sustituye
    al marker `<!-- fabro:creator -->` de OpenProject para autorizar al creador."""
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as c:
            rid = await _requisito_id_por_wp(c, wp_id)
            if rid is None:
                return None
            return await c.fetchval(
                "SELECT autor FROM comentario WHERE requisito_id=$1 "
                "ORDER BY created_at ASC LIMIT 1", rid)
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.get_creador falló: %s", exc)
        return None


async def get_estado(wp_id: int) -> dict | None:
    """Etapa actual + pelota del requisito (responsable actual y desde cuándo)."""
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as c:
            r = await c.fetchrow(
                """SELECT etapa_actual, workflow_iniciado, pelota_rol,
                          pelota_usuario, pelota_desde
                     FROM requisito WHERE wp_id=$1""", wp_id)
            return dict(r) if r else None
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.get_estado falló: %s", exc)
        return None


async def get_fase(etapa: str) -> dict | None:
    """Config de la fase (hub-and-spoke): {codigo, hub_rol, avanza_a, drivers,
    radios}. None si la etapa no es una fase dirigida por blog."""
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as c:
            f = await c.fetchrow(
                """SELECT codigo, hub_rol, avanza_a, devuelve_a
                     FROM fase WHERE codigo=$1""", etapa)
            if f is None:
                return None
            acts = await c.fetch(
                "SELECT rol, tipo FROM fase_actor WHERE fase=$1", etapa)
            return {"codigo": f["codigo"], "hub_rol": f["hub_rol"],
                    "avanza_a": f["avanza_a"], "devuelve_a": f["devuelve_a"],
                    "drivers": [a["rol"] for a in acts if a["tipo"] == "driver"],
                    "radios": [a["rol"] for a in acts if a["tipo"] == "radio"]}
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.get_fase falló: %s", exc)
        return None


async def agregar_comentario(wp_id: int, autor: str, autor_nombre: str,
                             cuerpo: str, adjuntos: list[dict] | None = None,
                             accion: str = "responder",
                             destino_rol: str | None = None,
                             destino_usuario: str | None = None,
                             etapa: str | None = None,
                             autor_es_radio: bool = False) -> int | None:
    """Inserta un comentario del blog (con su acción y adjuntos) y MUEVE LA PELOTA
    según el modelo hub-and-spoke. Devuelve el id, o None si no hay DB / el
    requisito no existe.

    Pelota:
      - accion='delegar' (driver revisa) → pelota = destino_rol/usuario.
      - accion='responder' por un RADIO  → pelota vuelve al hub de la fase.
      - 'aprobar'/'rechazar' NO tocan la pelota aquí: el workflow Temporal hace
        la transición (registrar_transicion pone la pelota en el hub nuevo).
    `adjuntos`: [{filename, repo_path, minio_key, size, content_type}]."""
    pool = await _get_pool()
    if pool is None:
        return None
    try:
        async with pool.acquire() as c, c.transaction():
            rid = await _requisito_id_por_wp(c, wp_id)
            if rid is None:
                log.warning("agregar_comentario: requisito wp_id=%s no existe", wp_id)
                return None
            cid = await c.fetchval(
                """INSERT INTO comentario(requisito_id, autor, autor_nombre, cuerpo,
                        accion, destino_rol, destino_usuario, etapa)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id""",
                rid, autor, autor_nombre, cuerpo, accion, destino_rol,
                destino_usuario, etapa)
            for a in (adjuntos or []):
                await c.execute(
                    """INSERT INTO comentario_adjunto(comentario_id, filename,
                            repo_path, minio_key, size, content_type)
                       VALUES($1,$2,$3,$4,$5,$6)""",
                    cid, a.get("filename"), a.get("repo_path"), a.get("minio_key"),
                    a.get("size"), a.get("content_type"))
            # Mover la pelota.
            if accion == "tomar":
                # Alguien del rol-pool TOMA la pelota (se asigna como persona).
                # pelota_rol se mantiene; solo se fija la persona responsable.
                await c.execute(
                    """UPDATE requisito SET pelota_usuario=$2, pelota_desde=now()
                        WHERE id=$1""", rid, autor)
            elif accion == "delegar" and (destino_rol or destino_usuario):
                await c.execute(
                    """UPDATE requisito SET pelota_rol=$2, pelota_usuario=$3,
                            pelota_desde=now() WHERE id=$1""",
                    rid, destino_rol, destino_usuario)
            elif accion == "responder" and autor_es_radio and etapa:
                hub = await c.fetchval(
                    "SELECT hub_rol FROM fase WHERE codigo=$1", etapa)
                if hub:
                    await c.execute(
                        """UPDATE requisito SET pelota_rol=$2, pelota_usuario=NULL,
                                pelota_desde=now() WHERE id=$1""",
                        rid, hub)
            return cid
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.agregar_comentario falló: %s", exc)
        return None


async def validar_mcp_token(token: str) -> dict | None:
    """Valida un token personal de MCP (sha256 contra `mcp_token`, no revocado).
    Devuelve {usuario, roles} y refresca ultimo_uso, o None si inválido. Lo usa
    el webhook para resolver el ACTOR automático de las acciones del MCP."""
    if not token:
        return None
    pool = await _get_pool()
    if pool is None:
        return None
    import hashlib
    th = hashlib.sha256(token.encode("utf-8")).hexdigest()
    try:
        async with pool.acquire() as c:
            r = await c.fetchrow(
                """SELECT usuario, roles FROM mcp_token
                    WHERE token_hash=$1 AND NOT revocado""", th)
            if r is None:
                return None
            await c.execute(
                "UPDATE mcp_token SET ultimo_uso=now() WHERE token_hash=$1", th)
            return {"usuario": r["usuario"], "roles": list(r["roles"] or [])}
    except Exception as exc:  # noqa: BLE001
        log.warning("proyeccion.validar_mcp_token falló: %s", exc)
        return None


def _slug(nombre: str) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "-", (nombre or "").lower()).strip("-")
    return s or "sin-cliente"
