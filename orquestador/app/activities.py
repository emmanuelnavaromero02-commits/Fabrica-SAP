"""Activities de Temporal: toda la I/O de la fábrica (agentes, Gitea, MinIO/S3 y
el read-model proyecto-db). Las activities pueden fallar y Temporal las reintenta;
el código del workflow nunca hace I/O directamente.
"""
from __future__ import annotations

import base64
import io
import json
import zipfile
from datetime import datetime, timezone

import yaml
from temporalio import activity

import config
from agent import run_agent
from clients import Gitea, slug_org, repo_slug


@activity.defn
async def agent_stage(stage: str, ctx: dict,
                      inputs: dict | None = None,
                      architecture: dict | None = None,
                      model: str | None = None,
                      iterations: list[dict] | None = None,
                      max_tokens: int | None = None) -> dict:
    """Ejecuta el agente de una etapa. Acepta `model` (override per call)
    y `iterations` (historia previa con feedback humano) para el loop de
    revision humana de la spec."""
    activity.logger.info(
        f"Agente etapa '{stage}' WP #{ctx.get('work_package_id')} "
        f"model={model or 'default'} iters={len(iterations or [])}"
    )
    # Etapas de DISEÑO: inyectar el skill de diseño del perfil del requisito
    # (fidelidad de UI + convenciones del stack). Default profile si no se eligió.
    if stage in ("diseno", "prototipo", "test_plan", "especificacion"):
        ds = await load_design_skill(ctx.get("work_package_id"))
        if ds:
            architecture = {**(architecture or {}), "design_skill": ds}
    # Re-hidratar IMÁGENES desde MinIO (van como binario al modelo). Los PDFs NO
    # se re-hidratan: su contenido ya viaja como TEXTO en `documents` (sin límite
    # de páginas/tokens); el binario del PDF solo se usa para el snapshot Gitea.
    if inputs and inputs.get("images"):
        inputs = {**inputs, "images": await _hydrate_binaries(inputs["images"])}
    return await run_agent(stage, ctx, inputs, architecture,
                            model=model, iterations=iterations,
                            max_tokens=max_tokens)


# --------------------------------------------------------------------------
# Etapa cargar_documentos: trae todo lo que el agente necesita
# --------------------------------------------------------------------------
# Extensiones que tratamos como texto plano (UTF-8) cuando OpenProject no
# detecta bien el content_type (suele devolver application/octet-stream para
# .md, .yaml, etc.).
_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json", ".yaml",
                    ".yml", ".log", ".rst"}


def _is_text_attachment(content_type: str, name: str) -> bool:
    ct = (content_type or "").lower()
    if ct.startswith("text/"):
        return True
    if ct in ("application/json", "application/yaml", "application/x-yaml"):
        return True
    n = (name or "").lower()
    return any(n.endswith(ext) for ext in _TEXT_EXTENSIONS)


def _docx_to_text(data: bytes) -> str:
    """Convierte .docx a texto plano (párrafos + tablas).
    Preserva orden de lectura y básicos de formato."""
    import io
    from docx import Document  # type: ignore
    doc = Document(io.BytesIO(data))
    parts: list[str] = []
    # Iterar el body en orden — párrafos y tablas mezclados.
    for el in doc.element.body.iter():
        tag = el.tag.rsplit('}', 1)[-1]
        if tag == "p":
            text = "".join(t.text or "" for t in el.iter() if t.tag.endswith("}t"))
            if text.strip():
                parts.append(text)
        elif tag == "tbl":
            # tabla → markdown
            rows = []
            for row in el.iter():
                if row.tag.endswith("}tr"):
                    cells = []
                    for cell in row.iter():
                        if cell.tag.endswith("}tc"):
                            txt = " ".join(
                                (t.text or "") for t in cell.iter()
                                if t.tag.endswith("}t")).strip()
                            cells.append(txt or " ")
                    if cells:
                        rows.append("| " + " | ".join(cells) + " |")
            if rows:
                n_cols = rows[0].count("|") - 1
                sep = "|" + "---|" * n_cols
                if len(rows) > 1:
                    parts.append(rows[0])
                    parts.append(sep)
                    parts.extend(rows[1:])
                else:
                    parts.append(rows[0])
                parts.append("")
    return "\n\n".join(parts)


def _xlsx_to_text(data: bytes, max_rows_per_sheet: int = 200) -> str:
    """Convierte .xlsx a texto: una sección por hoja, tabla markdown con
    cap de filas para no explotar el contexto del agente."""
    import io
    from openpyxl import load_workbook  # type: ignore
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out: list[str] = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        out.append(f"## Hoja: {sheet_name}")
        rows_md: list[str] = []
        n_rows = 0
        truncated = False
        for row in ws.iter_rows(values_only=True):
            if not any(c not in (None, "") for c in row):
                continue
            cells = [str(c) if c is not None else "" for c in row]
            rows_md.append("| " + " | ".join(cells) + " |")
            n_rows += 1
            if n_rows > max_rows_per_sheet:
                truncated = True
                break
        if rows_md:
            n_cols = rows_md[0].count("|") - 1
            sep = "|" + "---|" * n_cols
            out.append(rows_md[0])
            out.append(sep)
            out.extend(rows_md[1:])
            if truncated:
                out.append(f"\n_(truncado a {max_rows_per_sheet} filas — el archivo tiene más)_")
        else:
            out.append("_(hoja vacía)_")
        out.append("")
    wb.close()
    return "\n".join(out)


def _office_to_text(content_type: str, name: str, data: bytes) -> str | None:
    """Si el adjunto es .docx/.xlsx, devuelve su texto convertido. Si no
    es uno de esos formatos o falla la conversión, devuelve None."""
    n = (name or "").lower()
    ct = (content_type or "").lower()
    try:
        if n.endswith(".docx") or "officedocument.wordprocessing" in ct:
            return _docx_to_text(data)
        if n.endswith(".xlsx") or "officedocument.spreadsheetml" in ct:
            return _xlsx_to_text(data)
    except Exception as exc:  # noqa: BLE001
        activity.logger.warning("Conversión office '%s' falló: %s", name, exc)
    return None


async def _read_minio_manifest(wp_id: int) -> list[dict]:
    """Lee inputs/manifest.json de Gitea (lo escribió el inbox al crear el WP).
    Devuelve [{filename, key, size, content_type}, ...] o [] si no existe."""
    try:
        async with Gitea() as gt:
            owner = config.FACTORY_GITEA_OWNER
            repo = config.FACTORY_GITEA_REPO
            f = await gt.get_file(owner, repo, "inputs/manifest.json",
                                  ref=f"req-{wp_id}")
            data = json.loads(f["text"])
            return data.get("files") or []
    except Exception:  # noqa: BLE001
        return []


_MAX_INLINE_BYTES = 1_200_000   # binario legacy (OP) que se inline-a directo
# Texto de UN documento que va al prompt (~4 chars/token). 500K ≈ 125K tokens:
# entra holgado en 200K de contexto dejando sitio para system+spec+salida. Si un
# PDF rinde más texto (spec gigante), se trunca; el degradado cubre el resto.
_MAX_DOC_CHARS = 500_000


def _dedup_by_name(items: list[dict]) -> list[dict]:
    """Quita adjuntos repetidos por nombre (p.ej. el mismo PDF subido 2 veces),
    conservando la versión de MAYOR tamaño. Mantiene el orden de aparición."""
    best: dict[str, dict] = {}
    order: list[str] = []
    for it in (items or []):
        nm = (it.get("name") or "").strip().lower()
        if nm not in best:
            best[nm] = it
            order.append(nm)
        elif (it.get("bytes") or 0) > (best[nm].get("bytes") or 0):
            best[nm] = it
    return [best[nm] for nm in order]


def _pdf_to_text(data: bytes) -> str:
    """Extrae el texto de un PDF (pypdf). Se manda al agente como TEXTO en vez
    del PDF visual: evita el límite de 100 páginas y la explosión de tokens del
    render (un PDF de varios MB puede ser >200K tokens visuales pero pocos como
    texto). Devuelve "" si no hay texto extraíble (p.ej. PDF escaneado)."""
    import io
    try:
        from pypdf import PdfReader
    except Exception:  # noqa: BLE001
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:  # noqa: BLE001
        return ""
    parts: list[str] = []
    for pg in reader.pages:
        try:
            t = pg.extract_text() or ""
        except Exception:  # noqa: BLE001
            t = ""
        if t.strip():
            parts.append(t)
    return "\n\n".join(parts)


def _cap_text(text: str) -> str:
    """Acota el texto de un documento para no inflar el payload (Temporal/prompt)."""
    if text and len(text) > _MAX_DOC_CHARS:
        return text[:_MAX_DOC_CHARS] + "\n\n[…documento truncado por tamaño…]"
    return text


async def _hydrate_binaries(items: list[dict]) -> list[dict]:
    """Descarga de MinIO el binario (data_b64) de cada item con `key` que aún no
    lo traiga. fetch_inputs devuelve SOLO la referencia (key) para NO pasar
    binarios pesados por Temporal (límite ~2MB/payload); aquí se re-hidrata
    DENTRO de la actividad (worker) que sí necesita el contenido (snapshot /
    agente). Items con data_b64 (legacy) o sin key se devuelven tal cual."""
    from clients import S3Storage
    out: list[dict] = []
    for it in (items or []):
        if it.get("data_b64") or not it.get("key"):
            out.append(it)
            continue
        try:
            data = await S3Storage.download(it["key"])
            out.append({**it, "data_b64": base64.b64encode(data).decode("ascii"),
                        "bytes": len(data)})
        except Exception as exc:  # noqa: BLE001
            activity.logger.warning("hydrate MinIO falló %s: %s",
                                    it.get("key"), exc)
            out.append(it)   # sin data_b64 → el consumidor lo omite
    return out


@activity.defn
async def fetch_inputs(work_package_id: int) -> dict:
    """Trae título + descripción (de proyecto-db) y adjuntos: lee
    inputs/manifest.json de Gitea y descarga los archivos de MinIO/S3. Si falta
    descripción, cae a inputs/transcripcion.md en la rama del requerimiento.
    Procesa imágenes, PDFs, text, .docx, .xlsx (convertidos a texto)."""
    from clients import S3Storage
    import proyeccion
    images: list[dict] = []
    pdfs: list[dict] = []
    documents: list[dict] = []

    # ---- Texto del requisito desde el board (proyecto-db, SIN OpenProject) ----
    txt0 = await proyeccion.get_requisito_texto(work_package_id) or {}
    subject = (txt0.get("titulo") or "").strip()
    description = (txt0.get("descripcion") or "").strip()

    # ---- Manifest MinIO (fuente primaria de adjuntos; en Gitea + MinIO) ----
    minio_files = await _read_minio_manifest(work_package_id)
    for mf in minio_files:
        key = mf.get("key")
        name = mf.get("filename") or "archivo"
        content_type = (mf.get("content_type") or "").lower()
        size = mf.get("size") or 0
        if content_type.startswith("image/"):
            images.append({"name": name, "content_type": content_type,
                           "key": key, "bytes": size})
            continue
        if content_type == "application/pdf" or name.lower().endswith(".pdf"):
            pdfs.append({"name": name, "content_type": "application/pdf",
                         "key": key, "bytes": size})
            try:
                data = await S3Storage.download(key)
                txt = _pdf_to_text(data)
                if txt.strip():
                    documents.append({
                        "name": name + ".txt", "content_type": "text/plain",
                        "text": _cap_text(txt), "bytes": size,
                        "chars": len(txt), "source_filename": name})
                else:
                    activity.logger.warning(
                        "PDF sin texto extraíble (¿escaneado?): %s", name)
            except Exception as exc:  # noqa: BLE001
                activity.logger.warning("PDF→texto falló %s: %s", name, exc)
            continue
        try:
            data = await S3Storage.download(key)
        except Exception as exc:  # noqa: BLE001
            activity.logger.warning("MinIO descarga falló %s: %s", key, exc)
            continue
        if _is_text_attachment(content_type, name):
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("latin-1", errors="replace")
            documents.append({"name": name,
                              "content_type": content_type or "text/plain",
                              "text": _cap_text(text), "bytes": len(data),
                              "chars": len(text)})
        else:
            converted = _office_to_text(content_type, name, data)
            if converted is not None:
                documents.append({
                    "name": name + ".converted.txt",
                    "content_type": "text/plain",
                    "text": _cap_text(f"# Convertido desde {name}\n\n"
                                      f"(Original en MinIO: {key})\n\n"
                                      + converted),
                    "bytes": len(data), "chars": len(converted),
                    "source_filename": name,
                    "source_content_type": content_type,
                })

    # ---- Fallback de descripción: transcripción snapshot en Gitea ----
    if not description:
        try:
            async with Gitea() as gt:
                f = await gt.get_file(config.FACTORY_GITEA_OWNER,
                                      config.FACTORY_GITEA_REPO,
                                      "inputs/transcripcion.md",
                                      ref=f"req-{work_package_id}")
                description = (f.get("text") or "").strip()
        except Exception:  # noqa: BLE001
            pass

    return {
        "subject": subject,
        "description": description,
        "images": _dedup_by_name(images),
        "pdfs": _dedup_by_name(pdfs),
        "documents": documents,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


@activity.defn
async def load_architecture() -> dict:
    """Lee el archivo de arquitectura del repo configurado.

    Devuelve {markdown, sha} (sha del commit que contiene el archivo, para
    auditabilidad y para invalidar caches cuando cambia).
    """
    if not config.FACTORY_ARCH_REPO or not config.FACTORY_ARCH_PATH:
        return {"markdown": "", "sha": None,
                "spec_template": "", "spec_template_sha": None,
                "skill": "", "skill_sha": None}
    owner, _, repo = config.FACTORY_ARCH_REPO.partition("/")
    spec_tpl = ""
    spec_tpl_sha = None
    skill_md = ""
    skill_sha = None
    async with Gitea() as gt:
        f = await gt.get_file(owner, repo, config.FACTORY_ARCH_PATH,
                              ref=config.FACTORY_ARCH_BRANCH)
        # Plantilla canónica opcional. Si no existe en el repo, el agente
        # diseno improvisa la estructura de la spec.
        if config.FACTORY_SPEC_TEMPLATE_PATH:
            try:
                t = await gt.get_file(
                    owner, repo, config.FACTORY_SPEC_TEMPLATE_PATH,
                    ref=config.FACTORY_ARCH_BRANCH)
                spec_tpl = t["text"]
                spec_tpl_sha = t["sha"]
            except Exception:  # noqa: BLE001
                pass
        # Skill del proyecto (bloque 'desarrollo'). Opcional: si no existe, el
        # agente dev trabaja solo con arquitectura + spec + assertions.
        if config.FACTORY_SKILL_PATH:
            try:
                sk = await gt.get_file(
                    owner, repo, config.FACTORY_SKILL_PATH,
                    ref=config.FACTORY_ARCH_BRANCH)
                skill_md = sk["text"]
                skill_sha = sk["sha"]
            except Exception:  # noqa: BLE001
                pass
    return {"markdown": f["text"], "sha": f["sha"],
            "spec_template": spec_tpl, "spec_template_sha": spec_tpl_sha,
            "skill": skill_md, "skill_sha": skill_sha}


# --------------------------------------------------------------------------
# Catalogo de skills seleccionables (clave -> bundle .skill). El humano elige
# el skill al liberar desarrollo; el agente de desarrollo recibe UNO solo.
# --------------------------------------------------------------------------
async def load_skill_catalog() -> dict:
    """Lee skills/catalog.yaml del repo de arquitectura.
    Devuelve {'default': <key|None>, 'skills': [{key,title,when_to_use,path}]}.
    Si no hay catalogo, devuelve vacio (se cae al skill unico de compat)."""
    if not config.FACTORY_ARCH_REPO or not config.FACTORY_SKILLS_CATALOG_PATH:
        return {"default": None, "skills": []}
    owner, _, repo = config.FACTORY_ARCH_REPO.partition("/")
    async with Gitea() as gt:
        try:
            f = await gt.get_file(owner, repo,
                                  config.FACTORY_SKILLS_CATALOG_PATH,
                                  ref=config.FACTORY_ARCH_BRANCH)
        except Exception:  # noqa: BLE001
            return {"default": None, "skills": []}
    try:
        cat = yaml.safe_load(f["text"]) or {}
    except Exception:  # noqa: BLE001
        return {"default": None, "skills": []}
    return {"default": cat.get("default"),
            "default_profile": cat.get("default_profile"),
            "profiles": [p for p in (cat.get("profiles") or []) if p.get("key")],
            "skills": [s for s in (cat.get("skills") or []) if s.get("key")]}


async def _load_profile_json(wp_id: int) -> dict:
    """Lee profile.json de la rama del requisito (repo de artefactos): el perfil
    de stack elegido al validar. Devuelve {} si no existe."""
    owner = config.FACTORY_GITEA_OWNER
    repo = config.FACTORY_GITEA_REPO
    if not owner or not repo:
        return {}
    async with Gitea() as gt:
        try:
            f = await gt.get_file(owner, repo, "profile.json", ref=f"req-{wp_id}")
            return json.loads(f["text"])
        except Exception:  # noqa: BLE001
            return {}


async def _resolve_design_skill_path(wp_id: int) -> str | None:
    """Path (en el repo de arquitectura) del skill de diseño del requisito:
    el del perfil de profile.json, o el del default_profile del catálogo."""
    prof = await _load_profile_json(wp_id)
    path = prof.get("design_skill")
    if path:
        return path
    cat = await load_skill_catalog()
    profiles = {p["key"]: p for p in cat.get("profiles", [])}
    defp = profiles.get(cat.get("default_profile"))
    return defp.get("design_skill") if defp else None


async def load_design_skill(wp_id: int) -> str:
    """Carga el TEXTO del skill de diseño del requisito (descomprime si es
    bundle .skill; lee tal cual si es .md). Vacío si no hay perfil/skill."""
    path = await _resolve_design_skill_path(wp_id)
    if not path or not config.FACTORY_ARCH_REPO:
        return ""
    owner, _, repo = config.FACTORY_ARCH_REPO.partition("/")
    async with Gitea() as gt:
        try:
            if path.lower().endswith(".skill"):
                b = await gt.get_file_bytes(owner, repo, path,
                                            ref=config.FACTORY_ARCH_BRANCH)
                return _assemble_skill_bundle(b["bytes"])
            return (await gt.get_file(owner, repo, path,
                                      ref=config.FACTORY_ARCH_BRANCH))["text"]
        except Exception:  # noqa: BLE001
            return ""


def _assemble_skill_bundle(data: bytes) -> str:
    """Descomprime un bundle .skill (ZIP, formato Claude Code Skill) y arma el
    TEXTO a inyectar al agente: SKILL.md primero, luego el resto de archivos de
    texto (patterns/*.md, *.template.md, etc.). Maxima precision = todo el
    contenido textual del bundle. Ignora binarios."""
    _TEXT_EXT = (".md", ".markdown", ".txt", ".yaml", ".yml", ".json")
    skill_md: str | None = None
    others: list[tuple[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in sorted(zf.namelist()):
            if name.endswith("/"):
                continue
            if not name.lower().endswith(_TEXT_EXT):
                continue  # salta binarios (imagenes, etc.)
            try:
                content = zf.read(name).decode("utf-8")
            except Exception:  # noqa: BLE001
                continue
            if name.rsplit("/", 1)[-1].lower() == "skill.md":
                skill_md = content
            else:
                others.append((name, content))
    parts: list[str] = []
    if skill_md:
        parts.append(skill_md)
    for name, content in others:
        parts.append(f"\n\n--- {name} ---\n\n{content}")
    return "".join(parts).strip()


@activity.defn
async def load_skill(skill_key: str | None) -> dict:
    """Resuelve y carga un skill por clave del catalogo. Si el path es un
    bundle .skill (ZIP) lo descomprime y ensambla; si es .md lo lee tal cual.
    Devuelve {skill, skill_key, skill_title, skill_sha}. Si no hay catalogo o
    la clave no existe, cae al FACTORY_SKILL_PATH unico (compat)."""
    cat = await load_skill_catalog()
    by_key = {s["key"]: s for s in cat["skills"]}
    key = skill_key or cat.get("default")
    entry = by_key.get(key)
    owner, _, repo = (config.FACTORY_ARCH_REPO or "").partition("/")
    # Fallback: sin catalogo/entrada -> skill unico legacy (FACTORY_SKILL_PATH)
    if not entry:
        if not owner or not config.FACTORY_SKILL_PATH:
            return {"skill": "", "skill_key": None,
                    "skill_title": None, "skill_sha": None}
        async with Gitea() as gt:
            try:
                f = await gt.get_file(owner, repo, config.FACTORY_SKILL_PATH,
                                      ref=config.FACTORY_ARCH_BRANCH)
                return {"skill": f["text"], "skill_key": None,
                        "skill_title": None, "skill_sha": f["sha"]}
            except Exception:  # noqa: BLE001
                return {"skill": "", "skill_key": None,
                        "skill_title": None, "skill_sha": None}
    path = entry["path"]
    async with Gitea() as gt:
        if path.lower().endswith(".skill"):
            b = await gt.get_file_bytes(owner, repo, path,
                                        ref=config.FACTORY_ARCH_BRANCH)
            text = _assemble_skill_bundle(b["bytes"])
            sha = b["sha"]
        else:
            f = await gt.get_file(owner, repo, path,
                                  ref=config.FACTORY_ARCH_BRANCH)
            text, sha = f["text"], f["sha"]
    return {"skill": text, "skill_key": key,
            "skill_title": entry.get("title"), "skill_sha": sha}


@activity.defn
async def load_pipeline() -> dict:
    """Carga el pipeline.yaml del repo configurado. Si no existe (o el repo
    no esta configurado), devuelve un pipeline minimo embebido equivalente
    al v1 — para que el orquestador funcione sin obligar a tener YAML.
    """
    import yaml

    embedded_default = {
        "version": 1,
        "pipeline": [
            {"block": "cargar_documentos", "type": "load_inputs"},
            {"block": "especificacion", "type": "agent",
             "prompt": "especificacion", "blocked_mode": True,
             "artifact_path": "especificacion/spec.md",
             "meta_path": "especificacion/meta.json",
             "comment_template":
                 "Agente de especificacion (borrador en rama {branch}). {summary}"},
            {"block": "prototipo", "type": "agent",
             "prompt": "prototipo",
             "artifact_path": "prototipo/prototipo.md",
             "creates_child_wp": {
                 "type_name": "Prototype",
                 "subject_template": "Prototipo: {parent_subject}",
                 "description_template":
                     "Generado por la fabrica. Padre: #{parent_id}\n\n{summary}",
             }},
            {"block": "status_in_review", "type": "set_status",
             "status": "En revision"},
            {"block": "gate_aprobacion", "type": "human_gate"},
            {"block": "scripts_prueba", "type": "agent",
             "prompt": "test_plan",
             "artifact_path": "pruebas/scripts.md"},
            {"block": "cierre", "type": "set_status",
             "status": "En desarrollo"},
        ],
    }
    if not config.FACTORY_PIPELINE_REPO or not config.FACTORY_PIPELINE_PATH:
        return {"pipeline": embedded_default, "sha": None,
                "source": "embedded_default"}
    owner, _, repo = config.FACTORY_PIPELINE_REPO.partition("/")
    try:
        async with Gitea() as gt:
            f = await gt.get_file(owner, repo, config.FACTORY_PIPELINE_PATH,
                                  ref=config.FACTORY_PIPELINE_BRANCH)
        data = yaml.safe_load(f["text"])
        return {"pipeline": data, "sha": f["sha"],
                "source": f"{owner}/{repo}@{f['sha'][:8]}"}
    except Exception as exc:  # noqa: BLE001
        activity.logger.warning(
            "No se pudo cargar pipeline.yaml (%s); usando default embebido.", exc
        )
        return {"pipeline": embedded_default, "sha": None,
                "source": "embedded_default (fallback)"}


def _build_manifest(args: dict) -> dict:
    return {
        "work_package_id": args["work_package_id"],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "subject": args.get("subject"),
        "description_chars": len(args.get("description") or ""),
        "images": [
            {"name": i["name"], "content_type": i["content_type"],
             "bytes": i.get("bytes", 0)}
            for i in args.get("images", [])
        ],
        "pdfs": [
            {"name": p["name"], "bytes": p.get("bytes", 0)}
            for p in args.get("pdfs", [])
        ],
        "documents": [
            {"name": d["name"], "content_type": d["content_type"],
             "bytes": d.get("bytes", 0), "chars": d.get("chars", 0)}
            for d in args.get("documents", [])
        ],
        "architecture": {
            "repo": config.FACTORY_ARCH_REPO,
            "path": config.FACTORY_ARCH_PATH,
            "sha": args.get("architecture_sha"),
        },
    }


@activity.defn
async def snapshot_inputs(args: dict) -> dict:
    """Guarda en Gitea (rama req-N) los inputs cargados: manifest +
    transcripcion + imagenes + snapshot de arquitectura. Esto es la traza
    de 'que vio el agente' para esta corrida."""
    wp_id = args["work_package_id"]
    owner, repo, branch = args["owner"], args["repo"], args["branch"]
    refs = f"refs #{wp_id}"

    # Las imágenes/PDFs llegan como REFERENCIA (key MinIO) — se re-hidrata el
    # binario aquí para escribir el snapshot a Gitea (no viajan por Temporal).
    images = await _hydrate_binaries(args.get("images", []))
    pdfs = await _hydrate_binaries(args.get("pdfs", []))

    async with Gitea() as gt:
        await gt.ensure_branch(owner, repo, branch)

        manifest = _build_manifest(args)
        # PRESERVAR el array `files` (uploads a MinIO con sus keys) que escribió
        # el inbox: tanto _read_minio_manifest (re-corridas/clarificación) como
        # el botón de borrar adjuntos dependen de esas keys. Sin esto, el
        # snapshot las clobbearía y los adjuntos se perderían tras la 1ª corrida.
        try:
            prev = await gt.get_file(owner, repo, "inputs/manifest.json",
                                     ref=branch)
            prev_files = (json.loads(prev["text"]) or {}).get("files")
            if prev_files:
                manifest["files"] = prev_files
        except Exception:  # noqa: BLE001
            pass
        await gt.put_file(owner, repo, branch, "inputs/manifest.json",
                          json.dumps(manifest, indent=2, ensure_ascii=False),
                          f"Manifest de inputs {refs}")

        if args.get("description"):
            await gt.put_file(owner, repo, branch, "inputs/transcripcion.md",
                              args["description"],
                              f"Transcripcion del requisito {refs}")

        for img in images:
            if not img.get("data_b64"):
                continue
            safe = img["name"].replace("/", "_").replace("..", "_")
            await gt.put_file_binary(owner, repo, branch,
                                     f"inputs/imagenes/{safe}",
                                     img["data_b64"],
                                     f"Imagen {safe} {refs}")

        for pdf in pdfs:
            if not pdf.get("data_b64"):
                continue
            safe = pdf["name"].replace("/", "_").replace("..", "_")
            await gt.put_file_binary(owner, repo, branch,
                                     f"inputs/pdfs/{safe}",
                                     pdf["data_b64"],
                                     f"PDF {safe} {refs}")

        for doc in args.get("documents", []):
            safe = doc["name"].replace("/", "_").replace("..", "_")
            await gt.put_file(owner, repo, branch,
                              f"inputs/documentos/{safe}",
                              doc["text"],
                              f"Documento {safe} {refs}")

        if args.get("architecture_markdown"):
            sha = args.get("architecture_sha") or "unknown"
            await gt.put_file(owner, repo, branch, "inputs/arquitectura.md",
                              args["architecture_markdown"],
                              f"Snapshot de arquitectura ({sha[:8]}) {refs}")

    return {"manifest": manifest}


# --------------------------------------------------------------------------
# Carpeta /code para el ZIP de abapGit. El código NO se trabaja en línea (sin
# round-trip): el entregable es el ZIP exportado de abapGit, en UN solo archivo
# code/<repo>.zip; las versiones quedan en el historial de git. Se sube por
# `git push` desde el clon local del dev; el agente lo descomprime a una carpeta
# temporal para trabajarlo. Ver [[rediseno-fase-desarrollo-repo]].
# --------------------------------------------------------------------------
_CODE_README = (
    "# Código\n\n"
    "El código de este requerimiento se guarda aquí como el **ZIP exportado de "
    "abapGit** (`code/<objeto>.zip`), no como árbol de trabajo online.\n\n"
    "- **No** se hace round-trip en línea (sin vincular el repo en abapGit).\n"
    "- Es **un solo archivo** ZIP; las versiones quedan en el **historial de "
    "git** (cada publicación = un commit).\n\n"
    "## Cómo trabajarlo\n"
    "1. Descomprime `code/<objeto>.zip` en una **carpeta temporal**.\n"
    "2. Impórtalo a abapGit/SAP, desarrolla y prueba.\n"
    "3. Exporta de abapGit a ZIP, sobrescribe `code/<objeto>.zip` en el clon y "
    "publícalo por **git**: `git add code/ && git commit -m \"código\" && "
    "git push`. Git lleva el historial (cada push = un commit); **no** se sube "
    "por ningún otro mecanismo.\n"
)


# --------------------------------------------------------------------------
# Fase DESARROLLO (rediseño "repo por requerimiento"): en vez de generar código
# con el agente, se crea UN repo por requerimiento bajo la org del cliente y se
# siembra con el paquete de diseño + adjuntos + esqueleto abapGit + CLAUDE.md.
# El humano lo clona, programa local (Claude Code/Codex), hace round-trip
# abapGit con SAP y devuelve entregables como versión (vía el MCP fabrica).
# --------------------------------------------------------------------------

# Paquete de diseño vigente (repo fabrica, rama req-N) -> repo del requerimiento.
_DESIGN_COPY_MAP = [
    ("especificacion/spec.md", "diseno/spec.md"),
    ("especificacion/assertions.json", "diseno/assertions.json"),
    ("prototipo/prototipo.html", "diseno/prototipo.html"),
    ("test-plan/test-plan.md", "diseno/test-plan.md"),
    # profile.json NO se copia: clasificación interna derivada; la maquinaria la
    # lee del repo interno, no debe confundir al usuario en su repo.
]


async def _file_exists(gt: Gitea, owner: str, repo: str, path: str,
                       ref: str) -> bool:
    """True si `path` existe en `owner/repo@ref`."""
    try:
        await gt.get_file_bytes(owner, repo, path, ref=ref)
        return True
    except Exception:  # noqa: BLE001
        return False


async def _copy_file_between_repos(gt: Gitea, s_owner: str, s_repo: str,
                                   s_ref: str, d_owner: str, d_repo: str,
                                   d_branch: str, s_path: str, d_path: str,
                                   refs: str) -> bool:
    """Copia un archivo (texto o binario) de un repo a otro vía base64. False si
    el origen no existe."""
    try:
        b = await gt.get_file_bytes(s_owner, s_repo, s_path, ref=s_ref)
    except Exception:  # noqa: BLE001
        return False
    b64 = base64.b64encode(b["bytes"]).decode("ascii")
    await gt.put_file_binary(d_owner, d_repo, d_branch, d_path, b64,
                             f"sembrar {d_path} {refs}")
    return True


async def _copy_tree_between_repos(gt: Gitea, s_owner: str, s_repo: str,
                                   s_ref: str, d_owner: str, d_repo: str,
                                   d_branch: str, s_dir: str, d_dir: str,
                                   refs: str, exclude: set[str] | None = None) -> int:
    """Copia recursiva de un directorio entre repos. Devuelve nº de archivos.
    `exclude` = basenames a NO copiar (p.ej. manifest.json, índice derivado que
    no debe ensuciar el repo del cliente)."""
    exclude = exclude or set()
    n = 0
    for e in await gt.list_contents(s_owner, s_repo, s_dir, ref=s_ref):
        if e.get("name") in exclude:
            continue
        d_path = f"{d_dir}/{e['name']}"
        if e.get("type") == "dir":
            n += await _copy_tree_between_repos(
                gt, s_owner, s_repo, s_ref, d_owner, d_repo, d_branch,
                e["path"], d_path, refs, exclude)
        elif e.get("type") == "file":
            if await _copy_file_between_repos(
                    gt, s_owner, s_repo, s_ref, d_owner, d_repo, d_branch,
                    e["path"], d_path, refs):
                n += 1
    return n


async def _profile_is_abap(wp_id: int) -> bool:
    """¿El stack del requisito es ABAP/SAP? (define si se siembra esqueleto
    abapGit o git plano). Lee profile.json; si no hay, usa el default del
    catálogo. Heurística: 'abap' o 'sap' en la clave del perfil / dev_skill."""
    prof = await _load_profile_json(wp_id)
    hay = " ".join(str(prof.get(k, "")) for k in
                   ("key", "dev_skill_key", "design_skill")).lower()
    if not hay.strip():
        cat = await load_skill_catalog()
        profiles = {p["key"]: p for p in cat.get("profiles", [])}
        defp = profiles.get(cat.get("default_profile")) or {}
        hay = " ".join(str(defp.get(k, "")) for k in
                       ("key", "dev_skill_key", "design_skill")).lower()
    return ("abap" in hay) or ("sap" in hay)


def _build_mcp_json(wp_id: int, org: str, repo: str, branch: str,
                    stack: str = "", is_abap: bool = False,
                    sap_system_id: str = "") -> str:
    """`.mcp.json` para Claude Code: conecta al MCP `fabrica` HOSTED. El token
    NO se commitea — Claude Code expande ${FABRICA_MCP_TOKEN} del entorno del
    desarrollador. El contexto del requerimiento viaja por headers (X-Fabrica-*)
    para que las tools no necesiten argumentos. `stack` (clave del perfil elegido
    al validar) viaja como `X-Fabrica-Stack` → el MCP resuelve el design/dev skill
    del stack sin necesidad de un profile.json en el repo del cliente.

    En repos ABAP con un sistema SAP del cliente registrado, agrega además el
    server `abap` (SAP ADT): crear objetos directo en SAP por el túnel VPN. El
    sistema concreto viaja en `x-sap-system-id`. AUTH UNIFICADA: `abap` usa el
    MISMO token personal (Bearer ${FABRICA_MCP_TOKEN}) que `fabrica` — Caddy lo
    valida con forward_auth contra `mcp_token`, así revocar el token corta todos
    los MCP. Ver [[unificacion-auth-mcp-por-token-personal]]."""
    headers = {
        "Authorization": "Bearer ${FABRICA_MCP_TOKEN}",
        "X-Fabrica-Wp": str(wp_id),
        "X-Fabrica-Org": org,
        "X-Fabrica-Repo": repo,
        "X-Fabrica-Branch": branch,
    }
    if stack:
        headers["X-Fabrica-Stack"] = stack
    servers = {
        "fabrica": {
            "type": "http",
            "url": config.FABRICA_MCP_URL,
            "headers": headers,
        }
    }
    if is_abap and config.ABAP_MCP_URL and sap_system_id:
        servers["abap"] = {
            "type": "http",
            "url": config.ABAP_MCP_URL,
            "headers": {
                "Authorization": "Bearer ${FABRICA_MCP_TOKEN}",
                "x-sap-system-id": sap_system_id,
            },
        }
    return json.dumps({"mcpServers": servers}, indent=2, ensure_ascii=False) + "\n"


def _build_claude_md(subject: str, wp_id: int, org: str, repo: str,
                     is_abap: bool, clone_url: str, mcp_enabled: bool,
                     phase: str = "dev", sap_system_id: str = "") -> str:
    """Prompt sembrado en el repo. phase='diseno' → genera el PAQUETE TÉCNICO
    (spec/prototipo/casos) con Claude Code y lo publica vía MCP. phase='dev' →
    el prompt de desarrollo (cubrir las AS + entregables + liberar)."""
    persona = (
        "## Cómo te comportas (regla permanente)\n"
        "- Parco: ni una palabra de más. Telegráfico y analítico.\n"
        "- Cero suposiciones: solo afirma lo que verificas en vivo; si no puedes verificar, dilo.\n"
        "- Paso a paso; en cada paso espera confirmación antes de seguir.\n"
        "- Antes de CUALQUIER escritura en sistema vivo (SAP/transportes/repos): muestra el plan exacto y pide OK.\n"
        "- Transportes: siempre la OT/orden (no la tarea).\n\n"
    )
    if is_abap and sap_system_id:
        persona += (
            "## Conexión SAP de este requerimiento (regla dura)\n"
            f"- Sistema: **{sap_system_id}** — ya fijado en `.mcp.json` (`x-sap-system-id`).\n"
            "- Usa SIEMPRE ese sistema. **NUNCA** pases `sap_system_id` a las tools del MCP "
            "`abap` ni apuntes a otro: hay varios clientes SAP conectados y cruzarlos lee/"
            "escribe en el sistema equivocado.\n\n"
        )
    if phase == "diseno":
        return (
            f"# Requerimiento #{wp_id} — {subject}  (FASE DISEÑO)\n\n"
            + persona +
            "Genera el **paquete técnico** de este requerimiento con Claude Code "
            "—especificación, prototipo y casos de prueba— **dentro de la carpeta "
            "`diseno/`** de este repo y publícalo por **git push**.\n\n"
            "## Qué hay en este repo\n"
            "- `/diseno/` — **aquí va TODO el paquete que generes** (ver abajo).\n"
            "- `/inputs/` — insumos de partida: transcripción, adjuntos "
            "(PDFs/imágenes/documentos) y `arquitectura.md`.\n"
            "- `/referencias/` — `spec_template.md` (estructura de la spec) y "
            "`estimacion_metodologia.md` (cómo dimensionar).\n"
            "- `.claude/skills/` — **estándares de codificación** (nomenclatura, "
            "reglas, plantillas); Claude Code los usa automáticamente.\n\n"
            "## Qué escribir en `diseno/` (paths estables, sin `vN`)\n"
            "- `diseno/spec.md` — especificación.\n"
            "- `diseno/assertions.json` — aseveraciones **AS** verificables.\n"
            "- `diseno/prototipo.html` — prototipo navegable.\n"
            "- `diseno/test-plan.md` — casos de prueba.\n"
            "- `diseno/soporte/<doc>` — **todo material de apoyo** que generes "
            "(cuestionarios al funcional, notas, hojas de cálculo, etc.). Crea "
            "los archivos **bajo `diseno/soporte/`**, NUNCA en la raíz del repo.\n\n"
            "## Cómo trabajar\n"
            "1. `contexto()` — confirma sobre qué requerimiento trabajas.\n"
            "2. Lee `/inputs/` (transcripción + adjuntos) y, del MCP: "
            "`arquitectura()`, `guia_diseno()` (convenciones del stack) y "
            "`plantilla_spec()` (estructura de la spec).\n"
            "3. Genera, **iterando con el humano**, los archivos de `diseno/` "
            "(spec + AS + prototipo + casos + soporte). Sin alucinar: el origen "
            "del dato (tabla-campo SAP/no-SAP) va en la spec; lo no determinable, "
            "márcalo *(a confirmar)*.\n"
            "4. Publica por **git**: `git add diseno/ && git commit -m "
            f"\"diseño #{wp_id}\" && git push`. Reescribe el **mismo** archivo "
            "para corregir y vuelve a hacer push (git lleva el historial; no "
            "crees `vN`). El inbox lee el paquete de `diseno/`.\n"
            "5. El **blog dirige el flujo**: pasa la pelota para revisión con "
            "`enviar_a_revision(rol='funcional', actor='tu_nombre')` (o "
            "`'usuario-clave'`, `'project-manager'`); cuando esté aprobado, "
            "`aprobar(actor='tu_nombre')` AVANZA a Desarrollo. Si cambia el "
            "contrato, `devolver(...)`; para comentar sin mover el flujo, "
            "`comentar(texto, actor)`.\n\n"
            f"{_claude_mcp_section(mcp_enabled, phase='diseno')}"
            "_El paquete de diseño vive en `diseno/` de este repo; se publica "
            "por git push (no por el MCP) y el inbox lo lee de ahí._\n"
        )
    stack = "ABAP / SAP (ZIP abapGit, offline)" if is_abap else "no-SAP (git)"
    abap_note = (
        "- El código vive en `/code/<objeto>.zip` (export de abapGit), **no** "
        "como árbol online. Descomprímelo en una **carpeta temporal**, "
        "impórtalo a abapGit/SAP, desarrolla y prueba. Al terminar, exporta de "
        "abapGit a ZIP, sobrescribe `code/<objeto>.zip` en el clon y publícalo "
        "por **git**: `git add code/ && git commit && git push`. **No** vincules "
        "el repo en abapGit ni hagas pull/push de SAP contra él: el round-trip "
        "con SAP es por export/import de ZIP; el repo se llena por git.\n"
        if is_abap else
        "- Desarrolla bajo `/src/` (o donde el stack lo requiera) y haz commit/push normal.\n"
    )
    return (
        f"# Requerimiento #{wp_id} — {subject}\n\n"
        f"Repo de trabajo del requerimiento. Stack: **{stack}**.\n"
        f"Clónalo y trabájalo localmente con Claude Code/Codex.\n\n"
        + persona +
        "## Qué hay en este repo\n"
        "- `/diseno/` — **contrato a implementar**: `spec.md`, `assertions.json` "
        "(aseveraciones AS verificables), `prototipo.html`, `test-plan.md`.\n"
        "- `/inputs/` — insumos originales: transcripción, adjuntos (imágenes/PDFs/"
        "documentos) y `arquitectura.md` (arquitectura objetivo).\n"
        + ("- `/code/` — el **código** como ZIP de abapGit (`<objeto>.zip`); "
           "descomprímelo a una carpeta temporal para trabajarlo.\n" if is_abap
           else "- `/src/` — el **código** que vas a escribir.\n") +
        "- `/entregables/` — aquí van los documentos técnicos, el manual de "
        "usuario y las evidencias de prueba que generes, en paths estables por "
        "categoría (`entregables/<categoria>/<nombre>`). Git lleva el historial.\n"
        + ("- `.claude/skills/` — **estándares de codificación** (nomenclatura, "
           "reglas, plantillas); Claude Code los aplica automáticamente al "
           "escribir el código.\n- `/referencias/` — plantillas de diseño/"
           "estimación.\n" if is_abap else
           "- `/referencias/` — plantillas de diseño/estimación.\n") +
        "\n## Cómo trabajar\n"
        "1. Lee `/diseno/spec.md` + `/diseno/assertions.json`: cada **AS-xx** es un "
        "criterio verificable. Tu código y tus pruebas deben cubrir todas.\n"
        "2. Respeta `/inputs/arquitectura.md` y los **estándares de codificación** "
        "(`.claude/skills/`).\n"
        f"{abap_note}"
        "3. Cuando termines, escribe evidencias de prueba, doc técnico y manual "
        "de usuario en `entregables/<categoria>/<nombre>` y publícalos por "
        "**git**: `git add entregables/ && git commit && git push`. Para "
        "corregir un entregable, reescribe el **mismo** archivo y vuelve a "
        "hacer push (git lleva el historial; no crees `vN`). Luego libera a "
        "pruebas (ver MCP `fabrica` abajo).\n\n"
        f"{_claude_mcp_section(mcp_enabled)}"
        "_El paquete de diseño (contrato a implementar) está en `/diseno/` de "
        "este mismo repo._\n"
    )


def _claude_mcp_section(mcp_enabled: bool, phase: str = "dev") -> str:
    """Sección del CLAUDE.md que documenta el MCP `fabrica` (tools concretas)."""
    if not mcp_enabled:
        return (
            "> El MCP `fabrica` no está cableado en este repo (sin `FABRICA_MCP_URL`).\n"
            "> Trabaja con `/diseno/` e `/inputs/`; publica código y entregables "
            "(`entregables/<categoria>/<nombre>`) por **git push** desde el clon "
            "local y libera a pruebas desde el Inbox.\n\n")
    if phase == "diseno":
        return (
            "## MCP `fabrica` (insumos + compuerta) + entrega por git\n"
            "Este repo trae `.mcp.json`: al abrirlo con Claude Code se conecta al "
            "MCP `fabrica`. **Requisito**: exporta `FABRICA_MCP_TOKEN` en tu "
            "entorno (no va en el repo). El contexto del requerimiento ya viaja "
            "por la conexión.\n\n"
            "**La entrega del paquete es por git, no por el MCP.** Escribe los "
            "archivos en `diseno/` de tu **clon local** y publícalos con "
            "`git add diseno/ && git commit && git push`. El MCP NO escribe en el "
            "repo: solo lo consultas (lectura) y señalas el flujo del blog. Así no "
            "se desincronizan los commits.\n\n"
            "Herramientas (lectura + flujo):\n"
            "- `contexto()` — wp, cliente, repo, stack.\n"
            "- `arquitectura()` — arquitectura objetivo (Clean-Core, capas).\n"
            "- `guia_diseno()` — convenciones de spec + fidelidad de UI del "
            "prototipo (Fiori Elements / Dynpro) + formato de casos.\n"
            "- `plantilla_spec()` — estructura canónica de la especificación.\n"
            "- `diseno()` — si ya publicaste un borrador (en `diseno/`), lo lee.\n"
            "- `enviar_a_revision(actor)` — cuando el paquete esté pusheado, "
            "pásalo a **Revisión Fábrica**.\n\n"
            "Flujo: `contexto()` → `arquitectura()` + `guia_diseno()` + "
            "`plantilla_spec()` → (lee `/inputs/`) → escribir `diseno/` "
            "(spec/AS/prototipo/casos/soporte) → `git add diseno/ && commit && "
            "push` → `enviar_a_revision(tu_nombre)`.\n\n")
    return (
        "## MCP `fabrica` (consulta y compuerta) + entrega por git\n"
        "Este repo trae `.mcp.json`: al abrirlo con Claude Code/Codex se conecta al "
        "MCP `fabrica` de la plataforma. **Requisito**: exporta `FABRICA_MCP_TOKEN` "
        "en tu entorno (el token bearer que te dieron) — no va en el repo. El "
        "contexto del requerimiento (wp/org/repo) ya viaja por la conexión, así que "
        "las tools no necesitan argumentos.\n\n"
        "**La entrega es por git, no por el MCP.** Trabaja en tu **clon local** "
        "(única fuente de verdad) y publica código y entregables con "
        "`git add/commit/push`. El MCP NO escribe en el repo: solo lo consultas "
        "(lectura) y firmas la compuerta. Así no se desincronizan los commits.\n\n"
        "Herramientas (solo lectura + compuerta):\n"
        "- `contexto()` — wp, cliente, repo, stack (ABAP/SAP o no).\n"
        "- `arquitectura()` — arquitectura objetivo (principios, capas, Clean-Core).\n"
        "- `estandares()` — estándares de codificación + convenciones del stack.\n"
        "- `diseno()` — spec + assertions (AS) + test-plan (el contrato a cumplir).\n"
        "- `liberar_a_pruebas(actor)` — cuando el código y los entregables ya estén "
        "**pusheados**, manda el requerimiento a **'Lista para pruebas'**.\n\n"
        "Flujo sugerido: `contexto()` → `arquitectura()` + `estandares()` → "
        "desarrollar cubriendo las AS de `diseno()` → `git add/commit/push` del "
        "código (`code/`) y entregables (`entregables/<categoria>/<nombre>`) → "
        "`liberar_a_pruebas(tu_nombre)`.\n\n")


# Skills (bundles .skill en el repo de arquitectura) que se siembran en cada
# repo ABAP para que Claude Code los active solo, junto con las plantillas de
# diseño. Ver [[rediseno-fase-diseno-mcp]]. Cada bundle se descomprime a
# `.claude/skills/<skill>/...`.
_ABAP_SKILL_PATHS = (
    "skills/estandares-abap.skill",      # estándares de codificación ZOS
    "skills/empaquetado-abapgit.skill",  # cómo armar/publicar el ZIP abapGit
    "skills/sap-abap-mcp.skill",         # crear objetos en SAP por el MCP abap (ADT)
)
_ESTIMACION_PATH = "estimacion_metodologia.md"


async def _seed_referencias(gt: "Gitea", org: str, repo: str, branch: str,
                            is_abap: bool, refs: str) -> dict:
    """Siembra en el repo del requerimiento, para que Claude Code los use:
      - `.claude/skills/<skill>/...`  los skills ABAP (estándares de codificación
        + empaquetado abapGit), descomprimidos de los bundles del arch repo;
        solo ABAP. Claude Code los activa automáticamente.
      - `referencias/spec_template.md` + `referencias/estimacion_metodologia.md`
        (plantillas de diseño/estimación; stack-agnósticas).
    Best-effort: si el arch repo no tiene un archivo, lo salta."""
    out = {"plantillas": 0, "skill_files": 0}
    a_owner, _, a_repo = (config.FACTORY_ARCH_REPO or "").partition("/")
    a_branch = config.FACTORY_ARCH_BRANCH
    if not a_owner:
        return out
    for src_path, dst_path in (
        (config.FACTORY_SPEC_TEMPLATE_PATH, "referencias/spec_template.md"),
        (_ESTIMACION_PATH, "referencias/estimacion_metodologia.md"),
    ):
        try:
            f = await gt.get_file(a_owner, a_repo, src_path, ref=a_branch)
            await gt.put_file(org, repo, branch, dst_path, f["text"],
                              f"referencia {dst_path} {refs}")
            out["plantillas"] += 1
        except Exception:  # noqa: BLE001
            pass
    if is_abap:
        for skill_path in _ABAP_SKILL_PATHS:
            try:
                b = await gt.get_file_bytes(a_owner, a_repo, skill_path,
                                            ref=a_branch)
                with zipfile.ZipFile(io.BytesIO(b["bytes"])) as zf:
                    for name in zf.namelist():
                        if name.endswith("/"):
                            continue
                        try:
                            text = zf.read(name).decode("utf-8")
                        except UnicodeDecodeError:
                            continue  # salta binarios (el skill es .md)
                        await gt.put_file(org, repo, branch,
                                          f".claude/skills/{name}", text,
                                          f"skill {name} {refs}")
                        out["skill_files"] += 1
            except Exception:  # noqa: BLE001
                pass
    return out


@activity.defn
async def provisionar_repo(args: dict) -> dict:
    """Crea (idempotente) el repo del requerimiento bajo la org del cliente y lo
    siembra con el paquete de diseño + adjuntos + carpeta code/ (ZIP abapGit) +
    CLAUDE.md.
    Devuelve {org, repo, clone_url, is_abap, copied_design, copied_inputs}."""
    wp_id = args["work_package_id"]
    project_id = args.get("project_id")
    subject = args.get("subject") or f"Requerimiento {wp_id}"
    # phase='diseno' = provisionar TEMPRANO (al validar) para que Claude Code
    # genere el paquete; sin copiar el paquete (no existe aún) ni abapGit.
    # phase='dev' (default) = al entrar a desarrollo: copia el paquete aprobado
    # a /diseno/ + abapGit + CLAUDE.md de desarrollo. Mismo repo (idempotente).
    phase = args.get("phase", "dev")
    src_owner = config.FACTORY_GITEA_OWNER
    src_repo = config.FACTORY_GITEA_REPO
    src_ref = f"req-{wp_id}"
    refs = f"refs #{wp_id}"

    # Cliente desde proyecto-db (read-model).
    import proyeccion
    det = await proyeccion.get_requisito_detalle(wp_id)
    cliente = (det or {}).get("cliente") or ""
    org = slug_org(cliente) if cliente else "sin-cliente"
    repo = repo_slug(wp_id, subject)
    is_abap = await _profile_is_abap(wp_id)
    # Clave del perfil de stack elegido al validar (req-N:profile.json) → viaja
    # al MCP por el header X-Fabrica-Stack del .mcp.json (así el MCP resuelve el
    # design/dev skill del stack sin profile.json en el repo del cliente).
    _prof = await _load_profile_json(wp_id)
    stack_key = (_prof.get("profile") or _prof.get("key") or "").strip()

    async with Gitea() as gt:
        await gt.ensure_org(org, full_name=cliente or None)
        await gt.ensure_repo(org, repo,
                             description=f"{subject} (#{wp_id})",
                             private=True, auto_init=True)
        branch = "main"

        # 1) paquete de diseño: YA NO se copia entre repos. El diseño se produce
        # en la fase diseño y se publica por `git push` directo a `diseno/` de
        # ESTE repo (mismo repo, idempotente entre fases). Aquí solo contamos lo
        # que ya esté presente, para el mensaje de provisión (informativo).
        copied_design = 0
        if phase != "diseno":
            for _s_path, d_path in _DESIGN_COPY_MAP:
                if await _file_exists(gt, org, repo, d_path, branch):
                    copied_design += 1

        # 2) adjuntos / insumos (/inputs recursivo). Se excluye manifest.json:
        # es un índice derivado que la maquinaria lee del repo interno, no debe
        # confundir al usuario en su repo.
        copied_inputs = await _copy_tree_between_repos(
            gt, src_owner, src_repo, src_ref, org, repo, branch,
            "inputs", "inputs", refs, exclude={"manifest.json"})

        # 3) carpeta /code para el ZIP de abapGit (solo ABAP/SAP, y solo en dev).
        # NO se siembra el árbol online src/ + .abapgit.xml: el código no se
        # trabaja en línea (sin round-trip). El entregable es el ZIP exportado
        # de abapGit, en un solo archivo code/<repo>.zip; las versiones quedan en
        # el historial de git. Se sube por `git push` desde el clon local.
        if is_abap and phase != "diseno":
            await gt.put_file(org, repo, branch, "code/README.md",
                              _CODE_README, f"carpeta code (ZIP abapGit) {refs}")

        # 4) marcador de /entregables (git no versiona carpetas vacías)
        await gt.put_file(
            org, repo, branch, "entregables/README.md",
            "# Entregables\n\nDocumentos técnicos, manual de usuario y "
            "evidencias de prueba, en paths estables por categoría: "
            "`entregables/<categoria>/<nombre>` (categoria: `doc-tecnica` | "
            "`manual-usuario` | `evidencias`). Se publican por **git push** "
            "desde el clon local; el historial de versiones lo lleva git "
            "(cada push = un commit). No se crean carpetas `vN`.\n", refs)

        # 4b) estándares de codificación (.claude/skills) + plantillas de diseño
        # (referencias/) — para que Claude Code los use localmente.
        await _seed_referencias(gt, org, repo, branch, is_abap, refs)

        # 5) wiring del MCP `fabrica`: .mcp.json apuntando al MCP del ambiente
        # (la URL la fija el .env del orquestador → cuadra local vs AWS). El
        # token lo pone el dev en su entorno (no se commitea).
        mcp_enabled = bool(config.FABRICA_MCP_URL)
        # En repos ABAP, el sistema SAP del cliente (registrado en el Panel) se
        # hornea en .mcp.json (x-sap-system-id) y se nombra en CLAUDE.md como
        # regla dura (no cruzar clientes). Si no hay, solo va `fabrica`.
        sap_system_id = (await proyeccion.sap_system_id_de_cliente(org)
                         if is_abap else None)
        if mcp_enabled:
            await gt.put_file(org, repo, branch, ".mcp.json",
                              _build_mcp_json(wp_id, org, repo, branch, stack_key,
                                              is_abap=is_abap,
                                              sap_system_id=sap_system_id or ""),
                              f".mcp.json {refs}")

        # 6) CLAUDE.md (prompt para el agente local)
        clone_url = f"{config.GITEA_PUBLIC_URL}/{org}/{repo}.git"
        web_url = f"{config.GITEA_PUBLIC_URL}/{org}/{repo}"
        await gt.put_file(org, repo, branch, "CLAUDE.md",
                          _build_claude_md(subject, wp_id, org, repo,
                                           is_abap, clone_url, mcp_enabled,
                                           phase=phase,
                                           sap_system_id=sap_system_id or ""),
                          f"CLAUDE.md {refs}")

        # 7) marcador para que el Inbox sepa dónde está el repo del
        # requerimiento (lo lee la pestaña Desarrollo). Antes era
        # desarrollo/repo.json en la rama de la fábrica; ahora vive en el board
        # (migración 010) para no ensuciar el repo con objetos derivados.
        repo_meta = {"org": org, "repo": repo, "clone_url": clone_url,
                     "web_url": web_url, "is_abap": is_abap,
                     "cliente": cliente, "mcp_seeded": mcp_enabled,
                     "phase": phase}
        await proyeccion.set_dev_repo(wp_id, repo_meta)

    return {
        "org": org, "repo": repo, "clone_url": clone_url, "web_url": web_url,
        "is_abap": is_abap, "copied_design": copied_design,
        "copied_inputs": copied_inputs, "cliente": cliente,
        "mcp_seeded": mcp_enabled,
    }


# --------------------------------------------------------------------------
# Proyección al read-model (DB de proyecto) — sustituye el rol de OpenProject.
# Best-effort: nunca rompe el workflow (proyeccion ya envuelve en try/except).
# --------------------------------------------------------------------------
@activity.defn
async def proyectar_requisito(args: dict) -> None:
    """Registra el requisito en la DB de proyecto al arrancar el workflow.

    Si el requisito YA existe en el board (lo normal: lo creó el inbox o el
    backfill), SOLO lo marca iniciado — NO re-resuelve cliente/proyecto (eso lo
    MOVERÍA de cliente, porque el project_id que llega es el de proyecto-db, no el
    de OP). Para un wp_id desconocido (legacy) usa cliente/proyecto de args."""
    import proyeccion
    if not proyeccion.habilitada():
        return
    if await proyeccion.get_requisito_detalle(args["wp_id"]):
        await proyeccion.marcar_iniciado(args["wp_id"])
        return
    cliente = (args.get("cliente") or "").strip()
    proyecto = (args.get("proyecto") or "").strip()
    await proyeccion.upsert_requisito({
        "wp_id": args["wp_id"],
        "cliente": cliente or "sin-cliente",
        "proyecto": proyecto or cliente or "sin-proyecto",
        "titulo": args.get("subject") or f"WP {args['wp_id']}",
        "repo_org": args.get("repo_org"),
        "repo_name": args.get("repo_name"),
        "repo_branch": args.get("repo_branch"),
    })


@activity.defn
async def blog_comment(args: dict) -> None:
    """Postea un comentario al blog del requerimiento (proyecto-db). Reemplaza a
    op_comment en el flujo nuevo. Best-effort. args: {wp_id, texto, autor?}."""
    import proyeccion
    if not proyeccion.habilitada():
        return
    autor = args.get("autor") or "fabrica"
    await proyeccion.agregar_comentario(
        args["wp_id"], autor, args.get("autor_nombre") or "Fábrica",
        args.get("texto") or "", None)


@activity.defn
async def proyectar_transicion(args: dict) -> None:
    """Proyecta un cambio de etapa de negocio a la DB de proyecto (cierra el
    segmento abierto y abre el nuevo, con horas laborales). Best-effort."""
    import proyeccion
    if not proyeccion.habilitada():
        return
    await proyeccion.registrar_transicion(
        args["wp_id"], args["etapa"], args["ts"], bool(args.get("es_espera")))


# --------------------------------------------------------------------------
# Persistencia de estado/artefactos (Gitea + read-model)
# --------------------------------------------------------------------------
@activity.defn
async def save_timeline(args: dict) -> dict:
    """Persiste timeline.json (historial de etapas con timestamp) en la rama
    del WP, para que el Panel calcule cuánto tiempo lleva cada etapa — en WPs
    en curso y cerrados. El workflow pasa el historial completo (sobrescribe).
    args = {work_package_id, owner, repo, branch, timeline: [{stage,etapa,ts}]}"""
    owner, repo, branch = args["owner"], args["repo"], args["branch"]
    wp_id = args["work_package_id"]
    async with Gitea() as gt:
        await gt.ensure_branch(owner, repo, branch)
        await gt.put_file(
            owner, repo, branch, "timeline.json",
            json.dumps({"wp_id": wp_id, "timeline": args["timeline"]},
                       indent=2, ensure_ascii=False),
            f"timeline etapas refs #{wp_id}")
    return {"entries": len(args.get("timeline") or [])}


@activity.defn
async def save_draft(args: dict) -> dict:
    """Persiste un borrador del loop de revisión (modelo "versión = scope
    estable"). NO crea iteraciones/v{n}/ — sobreescribe el vigente y acumula
    historial. Ver [[versionado-artefactos]].

    Por cada `draft` en args["drafts"]:
      - Sobreescribe {root}/{filename} con el contenido nuevo (vigente).
      - Append a {root}/historial-aprendizaje.md con la entrada del iter.
      - Actualiza {root}/meta.json con meta_common (preserva campos de
        aprobaciones previas — no reescribe versiones_aprobadas si ya existe).

    args = {
      owner, repo, branch, refs,
      drafts: [
        {root: "especificacion", filename: "spec.md", content: "...",
         learning_entry: "## v01 · iter 002 · ..."},
        ...
      ],
      meta_common: {
        version_actual: "v01",         # versión hacia la que estamos iterando
        status: "draft" | "in_change",
        cr_actual: null | "CR-1",
        iter_actual: 2,
        ultimo_modelo: "...",
        ultimo_summary: "...",
        ultimo_actor: "rodolfo"|"",    # quien dio el feedback (vacío en iter 1)
      },
    }
    """
    owner = args["owner"]
    repo = args["repo"]
    branch = args["branch"]
    refs = args.get("refs", "")
    drafts = args.get("drafts") or []
    meta_common = args.get("meta_common") or {}

    async with Gitea() as gt:
        await gt.ensure_branch(owner, repo, branch)
        for d in drafts:
            root = d["root"].rstrip("/")
            filename = d["filename"]
            content = d.get("content", "")
            learning = d.get("learning_entry", "")

            # 1. Vigente — sobreescribe en cada iteración
            if content:
                await gt.put_file(
                    owner, repo, branch, f"{root}/{filename}",
                    content,
                    f"draft {root}/{filename} iter {meta_common.get('iter_actual')} {refs}")

            # 2. historial-aprendizaje.md — append (lee actual, concatena)
            if learning:
                hist_path = f"{root}/historial-aprendizaje.md"
                try:
                    existing = await gt.get_file(owner, repo, hist_path, ref=branch)
                    existing_text = existing["text"]
                except Exception:  # noqa: BLE001
                    existing_text = (
                        f"# Historial de aprendizaje · {root}\n\n"
                        "Cada entrada documenta una iteración del agente: "
                        "feedback recibido, modelo usado, y el cambio resultante. "
                        "Sirve para fine-tune del prompt + auditoría.\n\n---\n\n"
                    )
                new_hist = existing_text + learning + "\n"
                await gt.put_file(
                    owner, repo, branch, hist_path, new_hist,
                    f"historial {root} iter {meta_common.get('iter_actual')} {refs}")

            # 3. meta.json — preserva versiones_aprobadas, aprobado_* previos
            meta_path = f"{root}/meta.json"
            try:
                existing_meta_raw = await gt.get_file(owner, repo, meta_path, ref=branch)
                prev_meta = json.loads(existing_meta_raw["text"])
            except Exception:  # noqa: BLE001
                prev_meta = {}
            new_meta = {
                **prev_meta,
                **meta_common,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            await gt.put_file(
                owner, repo, branch, meta_path,
                json.dumps(new_meta, indent=2, ensure_ascii=False),
                f"meta {root} iter {meta_common.get('iter_actual')} {refs}")
    return {"drafts": [d["root"] for d in drafts],
            "iter": meta_common.get("iter_actual")}


@activity.defn
async def snapshot_version(args: dict) -> dict:
    """Al aprobar: snapshot inmutable del vigente a {root}/versiones/{vXX}/.
    Marca meta del vigente como approved.

    args = {
      owner, repo, branch, refs,
      snapshots: [
        {root: "especificacion", filename: "spec.md"},
        {root: "prototipo", filename: "prototipo.html"},
      ],
      version_label: "v01",
      trigger: "inicial" | "CR-1",
      aprobado_por: "rodolfo",
    }
    """
    owner = args["owner"]
    repo = args["repo"]
    branch = args["branch"]
    refs = args.get("refs", "")
    snapshots = args.get("snapshots") or []
    version = args["version_label"]
    trigger = args.get("trigger", "inicial")
    aprobado_por = args.get("aprobado_por", "")
    aprobado_en = datetime.now(timezone.utc).isoformat()

    async with Gitea() as gt:
        await gt.ensure_branch(owner, repo, branch)
        for s in snapshots:
            root = s["root"].rstrip("/")
            filename = s["filename"]

            # Lee el vigente (lo que está aprobándose en esta versión)
            try:
                vigente = await gt.get_file(owner, repo,
                                             f"{root}/{filename}", ref=branch)
                vigente_content = vigente["text"]
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"snapshot_version: no se pudo leer vigente {root}/{filename}: {exc}") from exc

            # Snapshot inmutable
            snap_path = f"{root}/versiones/{version}/{filename}"
            await gt.put_file(
                owner, repo, branch, snap_path, vigente_content,
                f"snapshot {version} {root}/{filename} ({trigger}) {refs}")

            snap_meta = {
                "version": version,
                "trigger": trigger,
                "aprobado_por": aprobado_por,
                "aprobado_en": aprobado_en,
                "filename": filename,
            }
            await gt.put_file(
                owner, repo, branch,
                f"{root}/versiones/{version}/meta.json",
                json.dumps(snap_meta, indent=2, ensure_ascii=False),
                f"snapshot {version} meta {root} {refs}")

            # Actualiza meta vigente: status=approved + acumula en versiones_aprobadas
            meta_path = f"{root}/meta.json"
            try:
                existing_meta_raw = await gt.get_file(owner, repo, meta_path, ref=branch)
                prev_meta = json.loads(existing_meta_raw["text"])
            except Exception:  # noqa: BLE001
                prev_meta = {}
            versiones_aprobadas = list(prev_meta.get("versiones_aprobadas") or [])
            versiones_aprobadas.append({
                "version": version, "trigger": trigger,
                "aprobado_por": aprobado_por, "aprobado_en": aprobado_en,
            })
            new_meta = {
                **prev_meta,
                "version_actual": version,
                "status": "approved",
                "cr_actual": None,
                "cr_motivo": None,
                "cr_solicitado_por": None,
                "cr_solicitado_en": None,
                "aprobado_por": aprobado_por,
                "aprobado_en": aprobado_en,
                "versiones_aprobadas": versiones_aprobadas,
                "updated_at": aprobado_en,
            }
            await gt.put_file(
                owner, repo, branch, meta_path,
                json.dumps(new_meta, indent=2, ensure_ascii=False),
                f"meta {root} approved {version} {refs}")
    return {"version": version, "snapshots": [s["root"] for s in snapshots],
            "aprobado_en": aprobado_en}


@activity.defn
async def save_iteration(args: dict) -> dict:
    """[DEPRECATED — usar save_draft + snapshot_version]
    Versiona en Gitea una iteracion del loop de revision humana:

      <iterations_folder>/v{n:02d}/<artifact_filename>   (artefacto primario)
      <iterations_folder>/v{n:02d}/<secondary_filename>  (opcional; p.ej. .md
                                                          junto al .html del
                                                          prototipo)
      <iterations_folder>/v{n:02d}/meta.json             (mode, summary, ...)
      <iterations_folder>/v{n:02d}/feedback_recibido.json (feedback que
                                                          disparó esta
                                                          iteracion; vacío en
                                                          v01)

    `artifact_filename` por defecto es "spec.<ext>" (compat con el loop de la
    spec). El loop del prototipo lo pasa explícito como "prototipo.html".
    """
    owner = args["owner"]
    repo = args["repo"]
    branch = args["branch"]
    folder = args["iterations_folder"].rstrip("/")
    n = int(args["n"])
    ext = args.get("extension", "md")
    refs = args.get("refs", "")
    primary_name = args.get("artifact_filename") or f"spec.{ext}"
    secondary_name = args.get("secondary_filename")
    secondary_content = args.get("secondary_artifact")
    # Lista genérica de artefactos extras (bloque 'diseno' usa esto para
    # guardar spec.md + prototipo.html + prototipo.md en una iteracion).
    # Cada elemento: {"filename": "spec.md", "content": "..."}
    extra_artifacts = args.get("extra_artifacts") or []
    vfolder = f"{folder}/v{n:02d}"

    async with Gitea() as gt:
        await gt.ensure_branch(owner, repo, branch)
        # Primary solo si trae contenido (los bloques multi-artefacto pueden
        # mandar primary vacio si todo se llena por extra_artifacts).
        primary_content = args.get("artifact", "")
        if primary_content:
            await gt.put_file(owner, repo, branch,
                              f"{vfolder}/{primary_name}",
                              primary_content,
                              f"Iteracion v{n:02d} artefacto {refs}")
        if secondary_name and secondary_content is not None:
            await gt.put_file(owner, repo, branch,
                              f"{vfolder}/{secondary_name}",
                              secondary_content,
                              f"Iteracion v{n:02d} secundario {refs}")
        for extra in extra_artifacts:
            fname = extra.get("filename")
            fcontent = extra.get("content")
            if not fname or fcontent is None:
                continue
            await gt.put_file(owner, repo, branch,
                              f"{vfolder}/{fname}",
                              fcontent,
                              f"Iteracion v{n:02d} extra {fname} {refs}")
        await gt.put_file(owner, repo, branch,
                          f"{vfolder}/meta.json",
                          json.dumps(args.get("meta", {}), indent=2,
                                      ensure_ascii=False),
                          f"Iteracion v{n:02d} meta {refs}")
        await gt.put_file(owner, repo, branch,
                          f"{vfolder}/feedback_recibido.json",
                          json.dumps(args.get("feedback_recibido", {}),
                                      indent=2, ensure_ascii=False),
                          f"Iteracion v{n:02d} feedback {refs}")
    return {"folder": vfolder, "n": n}


@activity.defn
async def save_open_questions(args: dict) -> dict:
    """Persiste en el branch las preguntas de aclaración del agente (modo
    blocked) para que el inbox las muestre en el panel de clarificación.
    Sobreescribe en cada bloqueo (la última tanda es la vigente)."""
    import proyeccion
    payload = {
        "wp_id": args.get("wp_id"),
        "model": args.get("model"),
        "questions": list(args.get("questions") or []),
    }
    # Antes: clarificacion/preguntas.json en el repo. Ahora el board es la
    # fuente de verdad (migración 010) — el repo no guarda objetos derivados.
    await proyeccion.set_clarificacion(args.get("wp_id"), payload)
    return payload


# --------------------------------------------------------------------------
# Gitea: escribir artefactos (texto y binario)
# --------------------------------------------------------------------------
@activity.defn
async def gitea_commit(args: dict) -> dict:
    """Escribe un artefacto de texto en una rama del repo de la fabrica."""
    async with Gitea() as gt:
        await gt.ensure_branch(args["owner"], args["repo"], args["branch"])
        return await gt.put_file(
            owner=args["owner"], repo=args["repo"], branch=args["branch"],
            path=args["path"], content=args["content"], message=args["message"],
        )
