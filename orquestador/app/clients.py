"""Clientes de I/O de la fábrica: Gitea (API v1), S3/MinIO y helpers de slug.

Se usan dentro de activities de Temporal — nunca desde el codigo del
workflow, que debe ser determinista y sin I/O.
"""
from __future__ import annotations

import base64
import json
import re
import unicodedata

import httpx

import config



# --------------------------------------------------------------------------
# S3 storage (MinIO local, AWS S3 en prod)
# --------------------------------------------------------------------------
class S3Storage:
    """Cliente S3 con dos endpoints: PUBLIC (para presigned URLs que ve el
    browser) e INTERNAL (para downloads desde containers backend)."""

    @staticmethod
    def _client_internal():
        """Cliente sync para presigned URLs y operaciones puntuales."""
        import boto3
        from botocore.config import Config
        return boto3.client(
            "s3",
            endpoint_url=config.S3_ENDPOINT_PUBLIC,  # presigned debe llevar URL pública
            aws_access_key_id=config.S3_ACCESS_KEY,
            aws_secret_access_key=config.S3_SECRET_KEY,
            region_name=config.S3_REGION,
            config=Config(signature_version="s3v4"),
        )

    @staticmethod
    def presigned_put_url(key: str, content_type: str = "application/octet-stream") -> str:
        """Genera una URL presignada para que el browser haga PUT directo."""
        client = S3Storage._client_internal()
        return client.generate_presigned_url(
            "put_object",
            Params={"Bucket": config.S3_BUCKET, "Key": key,
                    "ContentType": content_type},
            ExpiresIn=config.S3_PRESIGNED_TTL,
            HttpMethod="PUT",
        )

    @staticmethod
    def presigned_get_url(key: str) -> str:
        """URL presignada para descarga (browser ve archivo)."""
        client = S3Storage._client_internal()
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": config.S3_BUCKET, "Key": key},
            ExpiresIn=config.S3_PRESIGNED_TTL,
        )

    @staticmethod
    async def download(key: str) -> bytes:
        """Descarga el objeto. Usa endpoint INTERNAL desde container."""
        import aioboto3
        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=config.S3_ENDPOINT_INTERNAL,
            aws_access_key_id=config.S3_ACCESS_KEY,
            aws_secret_access_key=config.S3_SECRET_KEY,
            region_name=config.S3_REGION,
        ) as s3:
            resp = await s3.get_object(Bucket=config.S3_BUCKET, Key=key)
            async with resp["Body"] as stream:
                return await stream.read()

    @staticmethod
    async def delete(key: str) -> None:
        """Borra el objeto del bucket (endpoint INTERNAL)."""
        import aioboto3
        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=config.S3_ENDPOINT_INTERNAL,
            aws_access_key_id=config.S3_ACCESS_KEY,
            aws_secret_access_key=config.S3_SECRET_KEY,
            region_name=config.S3_REGION,
        ) as s3:
            await s3.delete_object(Bucket=config.S3_BUCKET, Key=key)

    @staticmethod
    async def head(key: str) -> dict | None:
        """Devuelve metadata del objeto o None si no existe."""
        import aioboto3
        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=config.S3_ENDPOINT_INTERNAL,
            aws_access_key_id=config.S3_ACCESS_KEY,
            aws_secret_access_key=config.S3_SECRET_KEY,
            region_name=config.S3_REGION,
        ) as s3:
            try:
                r = await s3.head_object(Bucket=config.S3_BUCKET, Key=key)
                return {"size": r.get("ContentLength"),
                        "content_type": r.get("ContentType")}
            except Exception:  # noqa: BLE001
                return None


# Articulos/preposiciones/conjunciones (es/en) que se omiten del slug del
# repo: ruido que no aporta a identificar el requerimiento.
_SLUG_STOPWORDS = {
    # español
    "el", "la", "los", "las", "un", "una", "unos", "unas", "lo", "al", "del",
    "de", "a", "ante", "bajo", "cabe", "con", "contra", "desde", "durante",
    "en", "entre", "hacia", "hasta", "mediante", "para", "por", "segun",
    "sin", "so", "sobre", "tras", "versus", "via", "y", "e", "o", "u", "que",
    # inglés
    "the", "an", "of", "to", "for", "and", "or", "in", "on", "at", "by",
    "with", "from", "as",
}


def slug_requisito(title: str, max_len: int = 60) -> str:
    """Slug kebab-case del título de un requerimiento para nombrar el repo:
    sin acentos, sin artículos/preposiciones/conjunciones, sin signos.
    Ej: 'Reporte de Antigüación de Stock' -> 'reporte-antiguacion-stock'."""
    # quitar acentos (NFKD + descartar combinantes)
    norm = unicodedata.normalize("NFKD", title or "")
    ascii_txt = "".join(c for c in norm if not unicodedata.combining(c))
    ascii_txt = ascii_txt.lower()
    # separar en tokens alfanuméricos
    tokens = re.findall(r"[a-z0-9]+", ascii_txt)
    kept = [t for t in tokens if t not in _SLUG_STOPWORDS]
    # si todo eran stopwords, no descartar (mejor algo que nada)
    if not kept:
        kept = tokens
    slug = "-".join(kept)[:max_len].strip("-")
    return slug or "requerimiento"


def repo_slug(wp_id, title: str, max_len: int = 60) -> str:
    """Nombre del repo del requerimiento: '<n>-<slug>', con n = número de
    requerimiento (work package) al frente para poder rastrearlo/ordenarlo.
    Ej: (66, 'Reporte de Antigüación de Stock') -> '66-reporte-antiguacion-stock'."""
    return f"{wp_id}-{slug_requisito(title, max_len)}"


def slug_org(name: str, max_len: int = 40) -> str:
    """Slug para el nombre de ORG de Gitea (= cliente). Como slug_requisito
    pero NO descarta palabras (el nombre del cliente importa entero) y respeta
    el límite de Gitea (~40). Ej: 'TAILOY' -> 'tailoy'."""
    norm = unicodedata.normalize("NFKD", name or "")
    ascii_txt = "".join(c for c in norm if not unicodedata.combining(c)).lower()
    tokens = re.findall(r"[a-z0-9]+", ascii_txt)
    slug = "-".join(tokens)[:max_len].strip("-")
    return slug or "sin-cliente"


class Gitea:
    """Cliente minimo de la API v1 de Gitea."""

    def __init__(self) -> None:
        self._c = httpx.AsyncClient(
            base_url=f"{config.GITEA_BASE_URL}/api/v1",
            headers={
                "Authorization": f"token {config.GITEA_API_TOKEN}",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    async def __aenter__(self) -> "Gitea":
        return self

    async def __aexit__(self, *_exc) -> None:
        await self._c.aclose()

    async def get_file(self, owner: str, repo: str, path: str,
                       ref: str = "main") -> dict:
        """Lee un archivo del repo. Devuelve {'text': str, 'sha': str}."""
        r = await self._c.get(f"/repos/{owner}/{repo}/contents/{path}",
                              params={"ref": ref})
        if r.status_code >= 400:
            raise RuntimeError(
                f"Gitea GET {owner}/{repo}/{path} -> HTTP {r.status_code}: {r.text[:200]}"
            )
        data = r.json()
        text = base64.b64decode(data["content"]).decode("utf-8")
        return {"text": text, "sha": data["sha"]}

    async def get_file_bytes(self, owner: str, repo: str, path: str,
                             ref: str = "main") -> dict:
        """Lee un archivo BINARIO del repo (p.ej. un bundle .skill ZIP).
        Devuelve {'bytes': bytes, 'sha': str} sin decodificar a texto."""
        r = await self._c.get(f"/repos/{owner}/{repo}/contents/{path}",
                              params={"ref": ref})
        if r.status_code >= 400:
            raise RuntimeError(
                f"Gitea GET (bin) {owner}/{repo}/{path} -> HTTP {r.status_code}: {r.text[:200]}"
            )
        data = r.json()
        return {"bytes": base64.b64decode(data["content"]), "sha": data["sha"]}

    async def put_file_binary(self, owner: str, repo: str, branch: str,
                              path: str, data_b64: str, message: str) -> dict:
        """Como put_file pero acepta el contenido ya en base64 (binarios)."""
        body = {"content": data_b64, "message": message, "branch": branch}
        existing = await self._c.get(
            f"/repos/{owner}/{repo}/contents/{path}", params={"ref": branch}
        )
        if existing.status_code == 200:
            body["sha"] = existing.json()["sha"]
            r = await self._c.put(f"/repos/{owner}/{repo}/contents/{path}", json=body)
        else:
            r = await self._c.post(f"/repos/{owner}/{repo}/contents/{path}", json=body)
        if r.status_code not in (200, 201):
            raise RuntimeError(
                f"Gitea escribir binario {path} -> HTTP {r.status_code}: {r.text[:200]}"
            )
        return r.json()

    async def delete_file(self, owner: str, repo: str, branch: str,
                          path: str, message: str) -> bool:
        """Borra un archivo de una rama (commit via API). True si lo borró,
        False si no existía. Best-effort para el resto de errores."""
        ex = await self._c.get(f"/repos/{owner}/{repo}/contents/{path}",
                               params={"ref": branch})
        if ex.status_code != 200:
            return False
        sha = ex.json().get("sha")
        r = await self._c.request(
            "DELETE", f"/repos/{owner}/{repo}/contents/{path}",
            json={"message": message, "branch": branch, "sha": sha})
        return r.status_code in (200, 204)

    async def list_contents(self, owner: str, repo: str, path: str = "",
                            ref: str = "main") -> list[dict]:
        """Lista el contenido de un directorio del repo (un nivel). Cada entrada
        trae 'name', 'path', 'type' ('file'|'dir'). [] si no existe el path."""
        r = await self._c.get(f"/repos/{owner}/{repo}/contents/{path}",
                              params={"ref": ref})
        if r.status_code == 404:
            return []
        if r.status_code >= 400:
            raise RuntimeError(
                f"Gitea listar {owner}/{repo}/{path} -> HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        return data if isinstance(data, list) else [data]

    async def last_commit_date(self, owner: str, repo: str, path: str,
                               ref: str = "main") -> str | None:
        """Fecha ISO del último commit que tocó `path` — para saber qué documento
        es el más reciente en el visor. None si no se puede determinar
        (best-effort; no rompe el listado)."""
        try:
            # En el endpoint /commits de Gitea el parámetro de rama es `sha`
            # (no `ref`): con `ref` se ignoraría y consultaría la rama por defecto.
            r = await self._c.get(
                f"/repos/{owner}/{repo}/commits",
                params={"path": path, "limit": 1, "sha": ref,
                        "stat": "false", "files": "false"})
            if r.status_code != 200:
                return None
            arr = r.json()
            if not isinstance(arr, list) or not arr:
                return None
            it = arr[0]
            return (it.get("created")
                    or ((it.get("commit") or {}).get("committer") or {}).get("date"))
        except Exception:  # noqa: BLE001
            return None

    async def ensure_branch(self, owner: str, repo: str, branch: str,
                            base: str = "main") -> None:
        """Crea la rama si no existe. 409 = ya existe, se ignora."""
        r = await self._c.post(
            f"/repos/{owner}/{repo}/branches",
            json={"new_branch_name": branch, "old_branch_name": base},
        )
        if r.status_code not in (201, 409):
            raise RuntimeError(
                f"Gitea crear rama {branch} -> HTTP {r.status_code}: {r.text[:200]}"
            )

    async def put_file(self, owner: str, repo: str, branch: str, path: str,
                       content: str, message: str) -> dict:
        """Crea o actualiza un archivo en una rama (commit via API)."""
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        body = {"content": b64, "message": message, "branch": branch}
        existing = await self._c.get(
            f"/repos/{owner}/{repo}/contents/{path}", params={"ref": branch}
        )
        if existing.status_code == 200:
            body["sha"] = existing.json()["sha"]
            r = await self._c.put(f"/repos/{owner}/{repo}/contents/{path}", json=body)
        else:
            r = await self._c.post(f"/repos/{owner}/{repo}/contents/{path}", json=body)
        if r.status_code not in (200, 201):
            raise RuntimeError(
                f"Gitea escribir {path} -> HTTP {r.status_code}: {r.text[:200]}"
            )
        data = r.json()
        return {
            "path": path,
            "branch": branch,
            "commit": (data.get("commit") or {}).get("sha"),
        }

    async def ensure_org(self, name: str, full_name: str | None = None) -> dict:
        """Crea la organización (= cliente) si no existe. Idempotente:
        422/409 = ya existe, se trae con GET. Devuelve el objeto org."""
        existing = await self._c.get(f"/orgs/{name}")
        if existing.status_code == 200:
            return existing.json()
        body = {"username": name, "visibility": "private"}
        if full_name:
            body["full_name"] = full_name
        r = await self._c.post("/orgs", json=body)
        if r.status_code in (201, 200):
            return r.json()
        # carrera o nombre ya tomado por otro tipo: reintentar GET
        if r.status_code in (409, 422):
            again = await self._c.get(f"/orgs/{name}")
            if again.status_code == 200:
                return again.json()
        raise RuntimeError(
            f"Gitea crear org {name} -> HTTP {r.status_code}: {r.text[:200]}")

    async def ensure_repo(self, org: str, repo: str,
                          description: str = "", private: bool = True,
                          auto_init: bool = True,
                          default_branch: str = "main") -> dict:
        """Crea el repo dentro de la org si no existe (idempotente). auto_init
        crea el commit inicial (README) para que main exista y se pueda
        commitear/clonar de una. Devuelve el objeto repo."""
        existing = await self._c.get(f"/repos/{org}/{repo}")
        if existing.status_code == 200:
            return existing.json()
        body = {
            "name": repo,
            "description": description[:255],
            "private": private,
            "auto_init": auto_init,
            "default_branch": default_branch,
        }
        r = await self._c.post(f"/orgs/{org}/repos", json=body)
        if r.status_code in (201, 200):
            return r.json()
        if r.status_code in (409, 422):
            again = await self._c.get(f"/repos/{org}/{repo}")
            if again.status_code == 200:
                return again.json()
        raise RuntimeError(
            f"Gitea crear repo {org}/{repo} -> HTTP {r.status_code}: {r.text[:200]}")
