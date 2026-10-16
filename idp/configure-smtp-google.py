#!/usr/bin/env python3
"""Configura (idempotente) el SMTP del realm `fabrica` para enviar correos vía
Google Workspace (smtp.gmail.com:587, STARTTLS).

Keycloak usa el SMTP del realm para: verificar email, "olvidé mi contraseña"
(self-service), y las required-actions enviadas por correo (p.ej. el flujo
execute-actions-email UPDATE_PASSWORD). El cambio de contraseña por el admin
desde el Panel NO necesita SMTP (es PUT reset-password directo); esto habilita
los flujos por correo.

Cambio VIVO: NO queda en realm-fabrica.json (igual que enable-unmanaged-attrs).
Correr tras un re-import del realm. Pensado para correr DENTRO de un contenedor
que alcance KC por la red interna (p.ej. fabro-webhook):

    docker cp idp/configure-smtp-google.py <webhook>:/tmp/smtp.py
    docker exec \
      -e KCU=$KC_ADMIN_USER -e KCP=$KC_ADMIN_PASSWORD \
      -e KC_BASE=https://kc.<dominio> \
      -e SMTP_USER='no-reply@tudominio.com' \
      -e SMTP_PASSWORD='<app-password-16-chars>' \
      -e SMTP_FROM='no-reply@tudominio.com' \
      -e SMTP_FROM_NAME='Fábrica de Software' \
      <webhook> python3 /tmp/smtp.py

IMPORTANTE sobre Google Workspace:
  - SMTP_PASSWORD debe ser una "App Password" de 16 caracteres (NO la contraseña
    normal). Requiere verificación en 2 pasos activa en esa cuenta:
    https://myaccount.google.com/apppasswords
  - SMTP_USER y SMTP_FROM deben ser la MISMA cuenta de Workspace (Gmail no deja
    falsear el remitente). Para un alias/buzón distinto, usar smtp-relay.gmail.com
    (requiere allowlist de IP en la consola de Admin de Google) — no cubierto aquí.

Solo stdlib.
"""
import json
import os
import ssl
import urllib.parse
import urllib.request

KCU = os.environ["KCU"]
KCP = os.environ["KCP"]
BASE = os.environ.get("KC_BASE", "http://kc.fabrica.local:8090").rstrip("/")
REALM = os.environ.get("KC_REALM", "fabrica")

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = os.environ.get("SMTP_PORT", "587")
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Fábrica de Software")
SMTP_REPLY_TO = os.environ.get("SMTP_REPLY_TO", "")

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

realm_url = f"{BASE}/admin/realms/{REALM}"
realm = _req("GET", realm_url, tok)[1] or {}

# Puerto 587 → STARTTLS; 465 → SSL. Claves booleanas como strings (formato KC).
ssl_enabled = "true" if SMTP_PORT == "465" else "false"
starttls = "false" if SMTP_PORT == "465" else "true"

smtp = {
    "host": SMTP_HOST,
    "port": SMTP_PORT,
    "from": SMTP_FROM,
    "fromDisplayName": SMTP_FROM_NAME,
    "ssl": ssl_enabled,
    "starttls": starttls,
    "auth": "true",
    "user": SMTP_USER,
    "password": SMTP_PASSWORD,
}
if SMTP_REPLY_TO:
    smtp["replyTo"] = SMTP_REPLY_TO

before = realm.get("smtpServer") or {}
# Comparación sin password (KC lo devuelve enmascarado en GETs posteriores).
same = all(before.get(k) == v for k, v in smtp.items() if k != "password")
if same and before.get("host") == SMTP_HOST and before.get("user") == SMTP_USER:
    print(f"SMTP ya apunta a {SMTP_HOST} como {SMTP_USER}; reescribo password por si cambió.")

realm["smtpServer"] = smtp
st, _ = _req("PUT", realm_url, tok, data=realm)
print(f"SMTP configurado: {SMTP_USER}@{SMTP_HOST}:{SMTP_PORT} "
      f"(starttls={starttls}, ssl={ssl_enabled}) -> HTTP {st}")
print("Probar envío: Admin Console > Realm settings > Email > 'Test connection' "
      "(requiere que tu usuario admin tenga email).")
