#!/usr/bin/env python3
"""Sube arquitectura.md + skills/* del seed al repo Gitea de arquitectura
(mcp-agent/fabrica-arquitectura). Idempotente: si el archivo ya existe, hace
update con su sha. Los .skill van en binario (base64).

Pensado para correr DENTRO del contenedor fabro-webhook (alcanza Gitea por la
red interna via el alias de Caddy, sin hairpin del EIP):

    docker compose cp /opt/fabrica/seed fabro-webhook:/tmp/seed
    docker compose exec -T fabro-webhook python3 /tmp/seed/upload-fabrica-arquitectura.py

Toma GITEA_BASE_URL y GITEA_API_TOKEN del entorno (ya estan en el contenedor).
Fallback: lee un .env (arg 1) y, si falta GITEA_BASE_URL, lo arma de BASE_DOMAIN.
Solo stdlib — no requiere dependencias.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = os.path.join(HERE, "fabrica-arquitectura")
OWNER, REPO = "mcp-agent", "fabrica-arquitectura"
FILES = [
    "arquitectura.md",
    "spec_template.md",
    "estimacion_metodologia.md",
    "pipeline.yaml",
    "skills/catalog.yaml",
    "skills/sap-abap-cloud.skill",
    "skills/sap-abap-classic.skill",
    "skills/estandares-abap.skill",
    "skills/empaquetado-abapgit.skill",
    "skills/design-sap-fiori.md",
    "skills/design-sap-classic.md",
]


def _env_file(path: str) -> dict:
    vals: dict = {}
    if path and os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


env = _env_file(sys.argv[1] if len(sys.argv) > 1 else "")
token = os.environ.get("GITEA_API_TOKEN") or env.get("GITEA_API_TOKEN")
base = (os.environ.get("GITEA_BASE_URL") or env.get("GITEA_BASE_URL") or "").rstrip("/")
if not base:
    dom = os.environ.get("BASE_DOMAIN") or env.get("BASE_DOMAIN")
    if dom:
        base = f"https://git.{dom}"
if not token or not base:
    sys.exit("falta GITEA_API_TOKEN o GITEA_BASE_URL/BASE_DOMAIN en entorno/.env")


def call(method: str, url: str, data: bytes | None = None):
    r = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "token " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


print(f"Gitea: {base}  repo: {OWNER}/{REPO}")
fail = 0
for rel in FILES:
    local = os.path.join(SEED, rel)
    if not os.path.exists(local):
        print(f"  SKIP {rel} (no existe en {SEED})")
        fail += 1
        continue
    content = base64.b64encode(open(local, "rb").read()).decode()
    url = f"{base}/api/v1/repos/{OWNER}/{REPO}/contents/{rel}"
    st, body = call("GET", url + "?ref=main")
    sha = None
    if st == 200:
        try:
            sha = json.loads(body)["sha"]
        except Exception:  # noqa: BLE001
            pass
    payload = {"content": content, "branch": "main",
               "message": f"seed: actualizar {rel}"}
    if sha:
        payload["sha"] = sha
    st2, body2 = call("PUT", url, json.dumps(payload).encode())
    ok = st2 in (200, 201)
    if not ok:
        fail += 1
    print(f"  {rel} -> {st2} {'OK' if ok else body2[:300]}")
print("LISTO" if not fail else f"TERMINADO con {fail} fallo(s)")
sys.exit(1 if fail else 0)
