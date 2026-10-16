"""Lectura de la DB de proyecto para el Panel (read-model CQRS-lite).

Consulta `v_requisito_panel` (estimado por camisas, tiempo invertido en horas
LABORALES, avance por etapa) aplicando la seguridad = Roles × Proyectos ×
Capacidades. Los roles vienen de Keycloak (sesión); las asignaciones
proyecto/capacidad viven en la DB. Ver db/schema.sql y db/funciones.sql.

DSN por env `PROYECTO_DB_DSN`. Si no está configurado, devuelve vacío (permite
correr el Panel sin la DB durante la migración).
"""
from __future__ import annotations

import logging
import os

try:
    import asyncpg  # type: ignore
except ImportError:
    asyncpg = None  # type: ignore

log = logging.getLogger("panel.proyecto_db")

_DSN = os.environ.get("PROYECTO_DB_DSN", "")
_pool: "asyncpg.Pool | None" = None

# Query base: una fila lista para el board. El filtro de seguridad se inyecta
# como predicado ($1 = es_admin, $2 = usuario).
_Q = """
SELECT vp.* FROM v_requisito_panel vp
WHERE $1::bool
   OR EXISTS (
     SELECT 1 FROM requisito r
       JOIN usuario_proyecto up ON up.proyecto_id = r.proyecto_id
      WHERE r.id = vp.id AND up.usuario = $2
        AND (r.capacidad_id IS NULL
             OR EXISTS (SELECT 1 FROM usuario_capacidad uc
                         WHERE uc.capacidad_id = r.capacidad_id AND uc.usuario = $2)))
ORDER BY vp.cliente, vp.proyecto, vp.prioridad_orden, vp.id
"""

_Q_SUB = """
SELECT s.requisito_id, s.tipo, s.stack, s.talla,
       th.horas AS horas_estimadas, s.responsable, s.estado
  FROM subtarea s
  LEFT JOIN talla_horas th ON th.stack = s.stack AND th.talla = s.talla
 WHERE s.requisito_id = ANY($1::bigint[])
 ORDER BY s.requisito_id, s.tipo
"""


def habilitada() -> bool:
    return bool(_DSN and asyncpg is not None)


async def _get_pool() -> "asyncpg.Pool | None":
    global _pool
    if not habilitada():
        return None
    if _pool is None:
        _pool = await asyncpg.create_pool(_DSN, min_size=1, max_size=4)
    return _pool


async def crear_mcp_token(usuario: str, roles: list[str],
                          descripcion: str = "") -> str | None:
    """Genera un token personal de MCP para `usuario`, guarda solo su sha256 y
    devuelve el texto plano UNA vez (None si no hay DB). El usuario lo pone en su
    env como FABRICA_MCP_TOKEN. Ver [[workflow-dirigido-por-blog]]."""
    import secrets
    import hashlib
    pool = await _get_pool()
    if pool is None:
        return None
    token = "fmcp_" + secrets.token_urlsafe(32)
    th = hashlib.sha256(token.encode("utf-8")).hexdigest()
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO mcp_token(usuario, token_hash, roles, descripcion)
               VALUES($1,$2,$3,$4)""",
            usuario, th, list(roles or []), descripcion or None)
    return token


async def listar_mcp_tokens(usuario: str) -> list[dict]:
    """Tokens de un usuario (metadatos, NUNCA el token)."""
    pool = await _get_pool()
    if pool is None:
        return []
    async with pool.acquire() as c:
        rows = await c.fetch(
            """SELECT id, descripcion, creado, ultimo_uso, revocado
                 FROM mcp_token WHERE usuario=$1 ORDER BY creado DESC""", usuario)
    return [dict(r) for r in rows]


async def revocar_mcp_token(token_id: int) -> bool:
    pool = await _get_pool()
    if pool is None:
        return False
    async with pool.acquire() as c:
        n = await c.execute(
            "UPDATE mcp_token SET revocado=true WHERE id=$1", token_id)
    return n.endswith(" 1")


_SEC = ("($1::bool OR EXISTS (SELECT 1 FROM requisito r "
        "JOIN usuario_proyecto up ON up.proyecto_id = r.proyecto_id "
        "WHERE r.id = vp.id AND up.usuario = $2 AND (r.capacidad_id IS NULL "
        "OR EXISTS (SELECT 1 FROM usuario_capacidad uc "
        "WHERE uc.capacidad_id = r.capacidad_id AND uc.usuario = $2))))")


async def dashboard(usuario: str, es_admin: bool) -> dict | None:
    """KPIs del estado de los requerimientos desde proyecto-db (reemplaza el
    dashboard que leía OpenProject). Filtrado por seguridad (roles×proyectos×
    capacidades). atrasado = fin_plan < hoy AND etapa <> cerrado."""
    pool = await _get_pool()
    if pool is None:
        return None
    base = f"SELECT * FROM v_requisito_panel vp WHERE {_SEC}"
    late = ("fin_plan IS NOT NULL AND fin_plan < CURRENT_DATE "
            "AND etapa_actual <> 'cerrado'")
    async with pool.acquire() as c:
        total = await c.fetchval(f"SELECT count(*) FROM ({base}) q", es_admin, usuario)
        atrasados = await c.fetchval(
            f"SELECT count(*) FROM ({base}) q WHERE {late}", es_admin, usuario)
        en_pool = await c.fetchval(
            f"SELECT count(*) FROM ({base}) q WHERE workflow_iniciado "
            "AND pelota_usuario IS NULL", es_admin, usuario)
        sin_iniciar = await c.fetchval(
            f"SELECT count(*) FROM ({base}) q WHERE NOT workflow_iniciado",
            es_admin, usuario)
        por_etapa = await c.fetch(
            f"SELECT etapa_actual, count(*) n FROM ({base}) q "
            "GROUP BY etapa_actual", es_admin, usuario)
        por_proyecto = await c.fetch(
            f"SELECT cliente, proyecto, count(*) n, "
            f"count(*) FILTER (WHERE {late}) atrasados FROM ({base}) q "
            "GROUP BY cliente, proyecto ORDER BY atrasados DESC, n DESC LIMIT 50",
            es_admin, usuario)
    return {
        "total": total, "atrasados": atrasados, "en_pool": en_pool,
        "sin_iniciar": sin_iniciar,
        "por_etapa": {r["etapa_actual"]: r["n"] for r in por_etapa},
        "por_proyecto": [dict(r) for r in por_proyecto],
    }


async def clientes_de_usuario(usuario: str, es_admin: bool) -> list[str]:
    """Nombres de los CLIENTES a los que el usuario tiene acceso (vía sus
    proyectos asignados en proyecto-db). Admin = todos. El nombre se convierte a
    org de Gitea con slug_org() para apuntar a la MISMA org que provisionar_repo."""
    pool = await _get_pool()
    if pool is None:
        return []
    async with pool.acquire() as c:
        if es_admin:
            rows = await c.fetch("SELECT nombre FROM cliente ORDER BY nombre")
        else:
            rows = await c.fetch(
                """SELECT DISTINCT cli.nombre
                     FROM usuario_proyecto up
                     JOIN proyecto p ON p.id = up.proyecto_id
                     JOIN cliente  cli ON cli.id = p.cliente_id
                    WHERE up.usuario = $1
                    ORDER BY cli.nombre""", usuario)
    return [r["nombre"] for r in rows]


async def actualizar_roles_tokens(usuario: str, roles: list[str]) -> int:
    """Propaga un cambio de roles a TODOS los tokens vigentes del usuario (así el
    snapshot de roles del token no queda obsoleto cuando un admin cambia roles).
    Devuelve cuántos tokens se actualizaron."""
    pool = await _get_pool()
    if pool is None:
        return 0
    async with pool.acquire() as c:
        n = await c.execute(
            """UPDATE mcp_token SET roles=$2
                WHERE usuario=$1 AND NOT revocado""",
            usuario, list(roles or []))
    try:
        return int(n.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


async def listar_proyectos(usuario: str, es_admin: bool) -> list[dict]:
    """Proyectos para la segmentación de Usuarios (reemplaza OP.list_projects).
    Admin = todos; resto = los asignados en usuario_proyecto. name = 'cliente ·
    proyecto'. parent_id se conserva (None) por compat con el frontend."""
    pool = await _get_pool()
    if pool is None:
        return []
    async with pool.acquire() as c:
        if es_admin:
            rows = await c.fetch(
                "SELECT p.id, p.nombre AS proyecto, cli.nombre AS cliente "
                "FROM proyecto p JOIN cliente cli ON cli.id = p.cliente_id "
                "ORDER BY cli.nombre, p.nombre")
        else:
            rows = await c.fetch(
                "SELECT DISTINCT p.id, p.nombre AS proyecto, cli.nombre AS cliente "
                "FROM proyecto p JOIN cliente cli ON cli.id = p.cliente_id "
                "JOIN usuario_proyecto up ON up.proyecto_id = p.id "
                "WHERE up.usuario = $1 ORDER BY cli.nombre, p.nombre", usuario)
    return [{"id": r["id"], "name": f"{r['cliente']} · {r['proyecto']}",
             "parent_id": None} for r in rows]


async def listar_clientes() -> list[dict]:
    """Todos los clientes (para el alta de proyectos). [{id, nombre, slug}]."""
    pool = await _get_pool()
    if pool is None:
        return []
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT id, nombre, slug FROM cliente ORDER BY nombre")
    return [dict(r) for r in rows]


async def capacidades_de_cliente(cliente_nombre: str) -> list[str]:
    """Nombres DISTINTOS de capacidades usados en los proyectos de un cliente
    (resuelto por slug = org Gitea), para poder clonarlos al crear un proyecto
    nuevo del mismo cliente. [] si el cliente no existe o no tiene capacidades."""
    import clients
    pool = await _get_pool()
    if pool is None:
        return []
    slug = clients.slug_org(cliente_nombre or "")
    async with pool.acquire() as c:
        rows = await c.fetch(
            """SELECT DISTINCT cap.nombre
                 FROM capacidad cap
                 JOIN proyecto p   ON p.id = cap.proyecto_id
                 JOIN cliente  cli ON cli.id = p.cliente_id
                WHERE cli.slug = $1
                ORDER BY cap.nombre""", slug)
    return [r["nombre"] for r in rows]


async def crear_proyecto(cliente_nombre: str, proyecto_nombre: str,
                         capacidades: list[str] | None = None,
                         usuarios: list[str] | None = None) -> dict:
    """Crea (o reutiliza) cliente + proyecto en proyecto-db, con capacidades y
    usuarios asignados. El slug del cliente = la org de Gitea (slug_org, igual que
    provisionar_repo). Idempotente por slug de cliente y por (cliente_id, nombre
    de proyecto); las capacidades/usuarios se agregan sin duplicar. Devuelve el
    proyecto creado/actualizado. Mismo patrón de upsert que proyeccion.py."""
    import clients
    pool = await _get_pool()
    if pool is None:
        raise RuntimeError("DB de proyecto no configurada")
    cli = (cliente_nombre or "").strip()
    proy = (proyecto_nombre or "").strip()
    if not cli or not proy:
        raise ValueError("cliente y proyecto son obligatorios")
    slug = clients.slug_org(cli)
    caps = [x.strip() for x in (capacidades or []) if x and x.strip()]
    users = [u.strip() for u in (usuarios or []) if u and u.strip()]
    async with pool.acquire() as c, c.transaction():
        cliente_id = await c.fetchval(
            """INSERT INTO cliente(nombre, slug) VALUES($1,$2)
               ON CONFLICT (slug) DO UPDATE SET nombre=EXCLUDED.nombre
               RETURNING id""", cli, slug)
        proyecto_id = await c.fetchval(
            """INSERT INTO proyecto(cliente_id, nombre) VALUES($1,$2)
               ON CONFLICT (cliente_id, nombre) DO UPDATE SET nombre=EXCLUDED.nombre
               RETURNING id""", cliente_id, proy)
        for cap in caps:
            await c.execute(
                """INSERT INTO capacidad(proyecto_id, nombre) VALUES($1,$2)
                   ON CONFLICT (proyecto_id, nombre) DO NOTHING""",
                proyecto_id, cap)
        for u in users:
            await c.execute(
                """INSERT INTO usuario_proyecto(usuario, proyecto_id) VALUES($1,$2)
                   ON CONFLICT DO NOTHING""", u, proyecto_id)
    return {"id": proyecto_id, "cliente": cli, "cliente_id": cliente_id,
            "slug": slug, "proyecto": proy, "capacidades": caps,
            "usuarios": users}


async def set_usuario_proyectos(usuario: str, project_ids: list[str]) -> int:
    """Fija la segmentación de PROYECTOS del usuario en proyecto-db (fuente de
    verdad de la seguridad del inbox/dashboard, reemplaza las membresías OP).
    Reemplaza el set completo. Devuelve cuántos quedaron."""
    pool = await _get_pool()
    if pool is None:
        return 0
    ids = [int(p) for p in project_ids if str(p).isdigit()]
    async with pool.acquire() as c, c.transaction():
        await c.execute("DELETE FROM usuario_proyecto WHERE usuario=$1", usuario)
        for pid in ids:
            await c.execute(
                "INSERT INTO usuario_proyecto(usuario, proyecto_id) VALUES($1,$2) "
                "ON CONFLICT DO NOTHING", usuario, pid)
    return len(ids)


async def set_usuario_capacidades(usuario: str, capacidad_ids: list[str]) -> int:
    """Fija la segmentación de CAPACIDADES del usuario en proyecto-db. Reemplaza
    el set completo. Devuelve cuántas quedaron."""
    pool = await _get_pool()
    if pool is None:
        return 0
    ids = [int(c2) for c2 in capacidad_ids if str(c2).isdigit()]
    async with pool.acquire() as c, c.transaction():
        await c.execute("DELETE FROM usuario_capacidad WHERE usuario=$1", usuario)
        for cid in ids:
            await c.execute(
                "INSERT INTO usuario_capacidad(usuario, capacidad_id) VALUES($1,$2) "
                "ON CONFLICT DO NOTHING", usuario, cid)
    return len(ids)


async def capacidades_por_proyecto(project_ids: list[str]) -> list[dict]:
    """Capacidades de cada proyecto (reemplaza OP.list_capabilities) para el
    selector de segmentación. Devuelve grupos [{project_id, project_name,
    capabilities:[{id, subject}]}]."""
    pool = await _get_pool()
    if pool is None:
        return []
    ids = [int(p) for p in project_ids if str(p).isdigit()]
    if not ids:
        return []
    async with pool.acquire() as c:
        prows = await c.fetch(
            "SELECT p.id, p.nombre AS proyecto, cli.nombre AS cliente "
            "FROM proyecto p JOIN cliente cli ON cli.id = p.cliente_id "
            "WHERE p.id = ANY($1::bigint[])", ids)
        pname = {r["id"]: f"{r['cliente']} · {r['proyecto']}" for r in prows}
        crows = await c.fetch(
            "SELECT id, proyecto_id, nombre FROM capacidad "
            "WHERE proyecto_id = ANY($1::bigint[]) ORDER BY nombre", ids)
    by_proj: dict[int, list[dict]] = {}
    for r in crows:
        by_proj.setdefault(r["proyecto_id"], []).append(
            {"id": r["id"], "subject": r["nombre"]})
    return [{"project_id": str(pid), "project_name": pname.get(pid, str(pid)),
             "capabilities": by_proj.get(pid, [])} for pid in ids]


async def requisitos_de_proyecto(usuario: str, es_admin: bool,
                                 project_id: int) -> list[dict]:
    """Requisitos de UN proyecto para el reporte de control (reemplaza
    OP.list_requisitos). Filtrado por seguridad. Devuelve id=wp_id, subject,
    capability, etapa, avance — para mezclar con timeline.json de Gitea."""
    pool = await _get_pool()
    if pool is None:
        return []
    sql = (
        "SELECT r.wp_id, r.titulo, r.etapa_actual, "
        "COALESCE(cap.nombre, '(sin capacidad)') AS capacidad, "
        "av.avance_pct, r.inicio_plan, r.fin_plan, r.horas_plan, "
        "r.horas_tecnicas, r.horas_funcionales, "
        "r.pelota_usuario, r.pelota_rol, r.desarrollador, r.funcional, "
        "r.usuario_clave, COALESCE(r.prioridad,'media') AS prioridad "
        "FROM requisito r "
        "JOIN proyecto p ON p.id = r.proyecto_id "
        "LEFT JOIN capacidad cap ON cap.id = r.capacidad_id "
        "LEFT JOIN v_requisito_avance av ON av.requisito_id = r.id "
        "WHERE r.proyecto_id = $3 AND ($1::bool OR EXISTS ("
        "  SELECT 1 FROM usuario_proyecto up WHERE up.proyecto_id = r.proyecto_id "
        "  AND up.usuario = $2 AND (r.capacidad_id IS NULL OR EXISTS ("
        "    SELECT 1 FROM usuario_capacidad uc WHERE uc.capacidad_id = r.capacidad_id "
        "    AND uc.usuario = $2)))) "
        "ORDER BY r.inicio_plan NULLS LAST, r.wp_id")
    async with pool.acquire() as c:
        rows = await c.fetch(sql, es_admin, usuario, project_id)
    return [{"id": r["wp_id"], "subject": r["titulo"],
             "capability": r["capacidad"], "etapa": r["etapa_actual"],
             "avance_pct": (float(r["avance_pct"]) if r["avance_pct"] is not None else None),
             "inicio": r["inicio_plan"].isoformat() if r["inicio_plan"] else None,
             "fin": r["fin_plan"].isoformat() if r["fin_plan"] else None,
             "horas": (float(r["horas_plan"]) if r["horas_plan"] is not None else None),
             "horas_tecnicas": (float(r["horas_tecnicas"]) if r["horas_tecnicas"] is not None else None),
             "horas_funcionales": (float(r["horas_funcionales"]) if r["horas_funcionales"] is not None else None),
             "asignado": r["pelota_usuario"], "rol": r["pelota_rol"],
             "desarrollador": r["desarrollador"], "funcional": r["funcional"],
             "usuario_clave": r["usuario_clave"],
             "prioridad": r["prioridad"]}
            for r in rows]


async def usuarios_de_proyecto(project_id: int) -> list[str]:
    """Usuarios asignados al proyecto (para los selectores del Gantt)."""
    pool = await _get_pool()
    if pool is None:
        return []
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT DISTINCT usuario FROM usuario_proyecto WHERE proyecto_id=$1 "
            "ORDER BY usuario", project_id)
    return [r["usuario"] for r in rows]


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
    """Edita un campo del requerimiento desde el Gantt. field ∈ {fin, funcional,
    desarrollador, usuario_clave}. Para 'fin' recalcula inicio = fin − horas/8
    (días laborables); para los demás, NULL si value vacío."""
    import datetime
    pool = await _get_pool()
    if pool is None:
        return False
    val = (value or "").strip() or None
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
            # Horas partidas Técnicas + Funcionales (mig-014). horas_plan = suma
            # (NULL si ambas NULL); al cambiar cualquiera recalcula inicio =
            # fin − ceil(suma/8). 'horas' (legado) se trata como Técnicas.
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
    return False


async def listar_prioridades() -> list[dict]:
    """Catálogo de prioridad (codigo, label, orden) para el selector del Gantt."""
    pool = await _get_pool()
    if pool is None:
        return []
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT codigo, label, orden FROM prioridad ORDER BY orden")
    return [dict(r) for r in rows]


async def set_prioridad(wp_id: int, codigo: str) -> bool:
    """Fija la prioridad de un requerimiento (desde el Gantt). True si actualizó."""
    pool = await _get_pool()
    if pool is None:
        return False
    async with pool.acquire() as c:
        n = await c.execute(
            "UPDATE requisito SET prioridad=$2, updated_at=now() WHERE wp_id=$1",
            wp_id, codigo)
    return n.endswith(" 1")


# ===========================================================================
# Sistemas SAP del MCP ABAP (FASE 2). El espejo NO-secreto vive en proyecto-db
# (tabla sap_system); la CREDENCIAL vive en AWS SSM Parameter Store como
# SecureString por sistema. El entrypoint del abap-mcp descubre los sistemas
# listando ese path. Ver infra/app/abap-mcp/entrypoint.py y infra/README.md.
# ===========================================================================

_SSM_ABAP_PREFIX = os.environ.get("SSM_ABAP_PREFIX", "/fabrica-sw/abap")
_SYSTEM_ID_RE = None  # compilado perezoso


def _valid_system_id(system_id: str) -> bool:
    global _SYSTEM_ID_RE
    if _SYSTEM_ID_RE is None:
        import re
        _SYSTEM_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
    return bool(_SYSTEM_ID_RE.match(system_id or ""))


def _ssm_param_name(system_id: str, override: str | None = None) -> str:
    return (override or "").strip() or f"{_SSM_ABAP_PREFIX}/{system_id}"


def _ssm_client():
    import boto3  # import perezoso: el panel corre sin boto3 en dev
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    return boto3.client("ssm", region_name=region)


async def _ssm_get(param_name: str) -> dict | None:
    """Lee un SecureString y lo devuelve como dict (o None si no existe)."""
    import asyncio
    import json

    def _do():
        try:
            c = _ssm_client()
            raw = (c.get_parameter(Name=param_name, WithDecryption=True)
                   .get("Parameter") or {}).get("Value") or ""
            raw = raw.strip()
            if not raw:
                return None
            return json.loads(raw) if raw.startswith("{") else {"password": raw}
        except Exception:  # noqa: BLE001 (incluye ParameterNotFound)
            return None
    return await asyncio.to_thread(_do)


async def _ssm_put(param_name: str, bundle: dict) -> None:
    import asyncio
    import json
    val = json.dumps(bundle, ensure_ascii=False)

    def _do():
        _ssm_client().put_parameter(
            Name=param_name, Value=val, Type="SecureString", Overwrite=True)
    await asyncio.to_thread(_do)


async def _ssm_delete(param_name: str) -> None:
    import asyncio

    def _do():
        try:
            _ssm_client().delete_parameter(Name=param_name)
        except Exception:  # noqa: BLE001 (ParameterNotFound = ya no está)
            pass
    await asyncio.to_thread(_do)


async def listar_sap_systems() -> list[dict]:
    """Sistemas SAP registrados (espejo no-secreto), con su cliente. SIN password."""
    pool = await _get_pool()
    if pool is None:
        return []
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT s.id, s.system_id, s.host, s.sap_client, s.username, "
            "s.description, s.secure, s.ssm_param, cli.nombre AS cliente "
            "FROM sap_system s JOIN cliente cli ON cli.id = s.cliente_id "
            "ORDER BY cli.nombre, s.system_id")
    return [dict(r) for r in rows]


async def crear_sap_system(cliente_nombre: str, system_id: str, host: str,
                           sap_client: str = "100", username: str = "",
                           password: str = "", description: str = "",
                           secure: bool = True,
                           ssm_param: str | None = None) -> dict:
    """Alta/edición de un sistema SAP: escribe la credencial en SSM (SecureString)
    y espeja lo no-secreto en proyecto-db. Si `password` viene vacío en una
    edición, conserva la que ya está en SSM. Idempotente por system_id."""
    import clients
    pool = await _get_pool()
    if pool is None:
        raise RuntimeError("DB de proyecto no configurada")
    cli = (cliente_nombre or "").strip()
    sid = (system_id or "").strip()
    hst = (host or "").strip()
    usr = (username or "").strip()
    if not cli or not sid or not hst or not usr:
        raise ValueError("cliente, system_id, host y username son obligatorios")
    if not _valid_system_id(sid):
        raise ValueError("system_id inválido: usa solo letras, dígitos, '.', '-' o '_'")
    slug = clients.slug_org(cli)
    param_name = _ssm_param_name(sid, ssm_param)

    bundle = {"host": hst, "client": str(sap_client or "100").strip(),
              "username": usr, "description": (description or "").strip(),
              "secure": bool(secure)}
    pwd = (password or "").strip()
    if pwd:
        bundle["password"] = pwd
    else:
        existing = await _ssm_get(param_name)
        if existing and existing.get("password"):
            bundle["password"] = existing["password"]
        else:
            raise ValueError("password requerido para un sistema nuevo")

    # SSM primero (fuente de verdad del MCP); luego el espejo en BD.
    await _ssm_put(param_name, bundle)
    async with pool.acquire() as c, c.transaction():
        cliente_id = await c.fetchval(
            """INSERT INTO cliente(nombre, slug) VALUES($1,$2)
               ON CONFLICT (slug) DO UPDATE SET nombre=EXCLUDED.nombre
               RETURNING id""", cli, slug)
        await c.execute(
            """INSERT INTO sap_system
                 (cliente_id, system_id, host, sap_client, username,
                  description, secure, ssm_param)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8)
               ON CONFLICT (system_id) DO UPDATE SET
                 cliente_id=EXCLUDED.cliente_id, host=EXCLUDED.host,
                 sap_client=EXCLUDED.sap_client, username=EXCLUDED.username,
                 description=EXCLUDED.description, secure=EXCLUDED.secure,
                 ssm_param=EXCLUDED.ssm_param, updated_at=now()""",
            cliente_id, sid, hst, bundle["client"], usr,
            bundle["description"], bundle["secure"],
            (ssm_param or "").strip() or None)
    return {"system_id": sid, "cliente": cli, "host": hst,
            "sap_client": bundle["client"], "username": usr,
            "description": bundle["description"], "secure": bundle["secure"],
            "ssm_param": param_name}


async def eliminar_sap_system(system_id: str) -> bool:
    """Baja de un sistema SAP: borra el espejo en BD y el SecureString en SSM."""
    pool = await _get_pool()
    if pool is None:
        return False
    sid = (system_id or "").strip()
    if not sid:
        return False
    async with pool.acquire() as c:
        override = await c.fetchval(
            "SELECT ssm_param FROM sap_system WHERE system_id=$1", sid)
        n = await c.execute("DELETE FROM sap_system WHERE system_id=$1", sid)
    await _ssm_delete(_ssm_param_name(sid, override))
    return n.endswith(" 1")


async def listar_requisitos(usuario: str, es_admin: bool) -> list[dict]:
    """Board de proyecto filtrado por seguridad. Cada fila trae estimado,
    tiempo invertido (trabajo/espera/total), avance, etapa y repo; más sus
    subtareas front/back."""
    pool = await _get_pool()
    if pool is None:
        return []
    async with pool.acquire() as c:
        rows = await c.fetch(_Q, es_admin, usuario)
        reqs = [dict(r) for r in rows]
        if reqs:
            subs = await c.fetch(_Q_SUB, [r["id"] for r in reqs])
            by_req: dict[int, list[dict]] = {}
            for s in subs:
                by_req.setdefault(s["requisito_id"], []).append(dict(s))
            for r in reqs:
                r["subtareas"] = by_req.get(r["id"], [])
        return reqs
