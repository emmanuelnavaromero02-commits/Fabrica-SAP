#!/usr/bin/env python3
"""Crea (idempotente) el Script Mapper `op-admin` en el client `openproject`
del realm `fabrica`: emite el claim booleano `op_admin=true` sólo si el usuario
tiene el rol de realm `admin`. Ver idp/op-admin-script-mapper.js.

Requiere la feature 'scripts' habilitada en Keycloak. Pensado para correr DENTRO
de fabro-webhook (alcanza KC por la red interna):

    docker cp idp/create-op-admin-mapper.py <webhook>:/tmp/x.py
    docker exec -e KCU=$KC_ADMIN_USER -e KCP=$KC_ADMIN_PASSWORD \
      -e KC_BASE=https://kc.<dominio> <webhook> python3 /tmp/x.py

Solo stdlib.
"""
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

KCU = os.environ["KCU"]
KCP = os.environ["KCP"]
BASE = os.environ.get("KC_BASE",
                      "https://kc.peru.epiuse.darkswf.7businesssolutions.com").rstrip("/")
REALM = os.environ.get("KC_REALM", "fabrica")
CLIENT = os.environ.get("KC_CLIENT", "openproject")
CLAIM = os.environ.get("OP_ADMIN_CLAIM", "op_admin")
# Provider del script mapper DESPLEGADO como JAR (script-<fileName>). El script
# vive en idp/kc-script-mapper/op-admin-mapper.js (NO inline: Quarkus deshabilita
# el inline por seguridad).
MAPPER_PROVIDER = os.environ.get("OP_ADMIN_MAPPER_PROVIDER",
                                 "script-op-admin-mapper.js")
CTX = ssl.create_default_context()


def _req(method, url, tok=None, data=None, form=False):
    headers = {}
    body = None
    if tok:
        headers["Authorization"] = "Bearer " + tok
    if data is not None and form:
        body = urllib.parse.urlencode(data).encode()
    elif data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(r, timeout=30, context=CTX) as resp:
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else None)


tok = _req("POST", f"{BASE}/realms/master/protocol/openid-connect/token",
           data={"grant_type": "password", "client_id": "admin-cli",
                 "username": KCU, "password": KCP}, form=True)[1]["access_token"]

cl = _req("GET", f"{BASE}/admin/realms/{REALM}/clients?clientId={CLIENT}", tok)[1]
if not cl:
    raise SystemExit(f"client {CLIENT} no encontrado en realm {REALM}")
cid = cl[0]["id"]
base_m = f"{BASE}/admin/realms/{REALM}/clients/{cid}/protocol-mappers/models"

mapper = {
    "name": f"{CLAIM}-script",
    "protocol": "openid-connect",
    "protocolMapper": MAPPER_PROVIDER,
    "config": {
        "claim.name": CLAIM,
        "jsonType.label": "boolean",
        "id.token.claim": "true",
        "access.token.claim": "true",
        "userinfo.token.claim": "true",
        "multivalued": "false",
    },
}

existing = _req("GET", base_m, tok)[1] or []
match = next((m for m in existing if m.get("name") == mapper["name"]), None)
if match:
    mapper["id"] = match["id"]
    st, _ = _req("PUT", f"{base_m}/{match['id']}", tok, data=mapper)
    print(f"ACTUALIZADO mapper {mapper['name']} -> {st}")
else:
    st, _ = _req("POST", base_m, tok, data=mapper)
    print(f"CREADO mapper {mapper['name']} -> {st}")

now = _req("GET", base_m, tok)[1] or []
print("--- mappers del client ---")
for m in now:
    print(f"  {m.get('name')} [{m.get('protocolMapper')}]")
