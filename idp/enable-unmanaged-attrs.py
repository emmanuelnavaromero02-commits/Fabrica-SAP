#!/usr/bin/env python3
"""Habilita (idempotente) atributos de usuario NO gestionados en el realm
`fabrica`, política `ADMIN_EDIT`.

Keycloak reciente (Quarkus) trae el User Profile DECLARATIVO: solo persiste los
atributos declarados (username/email/firstName/lastName). Cualquier otro —p.ej.
`proyectos` (segmentación por proyecto OpenProject)— se DESCARTA en silencio: el
PUT del usuario responde 204 pero el atributo no se guarda. `ADMIN_EDIT` permite
que los administradores (Admin API) lean/escriban atributos no declarados.

Cambio VIVO: NO queda en realm-fabrica.json (igual que el op-admin mapper y el
temporal-web audience). Correr tras un re-import del realm. Pensado para correr
DENTRO de fabro-webhook (alcanza KC por la red interna):

    docker cp idp/enable-unmanaged-attrs.py <webhook>:/tmp/x.py
    docker exec -e KCU=$KC_ADMIN_USER -e KCP=$KC_ADMIN_PASSWORD \
      -e KC_BASE=https://kc.<dominio> <webhook> python3 /tmp/x.py

Solo stdlib.
"""
import json
import os
import ssl
import urllib.parse
import urllib.request

KCU = os.environ["KCU"]
KCP = os.environ["KCP"]
BASE = os.environ.get("KC_BASE",
                      "http://kc.fabrica.local:8090").rstrip("/")
REALM = os.environ.get("KC_REALM", "fabrica")
POLICY = os.environ.get("UNMANAGED_ATTR_POLICY", "ADMIN_EDIT")
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

prof_url = f"{BASE}/admin/realms/{REALM}/users/profile"
prof = _req("GET", prof_url, tok)[1] or {}
before = prof.get("unmanagedAttributePolicy")
if before == POLICY:
    print(f"YA configurado: unmanagedAttributePolicy={POLICY} (no-op)")
else:
    prof["unmanagedAttributePolicy"] = POLICY
    st, _ = _req("PUT", prof_url, tok, data=prof)
    print(f"ACTUALIZADO unmanagedAttributePolicy: {before} -> {POLICY} ({st})")

now = _req("GET", prof_url, tok)[1] or {}
print("policy actual =", now.get("unmanagedAttributePolicy"))
