#!/usr/bin/env python3
"""
Entrypoint propio del MCP ABAP (envoltura del sample de AWS).

El sample anuncia un proveedor de credenciales `aws_secrets` pero NO lo
implementa, y en Docker su keychain es solo en memoria y arranca vacío. Por eso
aquí, ANTES de ceder a su `main()`, sembramos el store en memoria del singleton
`keychain_manager` con una entrada por sistema (clave = system_id), que es justo
la clave que luego busca `_get_sap_client_keychain` cuando llega el header
`x-sap-system-id`.

DESCUBRIMIENTO de sistemas (fuente de verdad = SSM, lo escribe el Panel):
  - Lista `${SSM_ABAP_PREFIX}/*` (default `/fabrica-sw/abap/*`). Cada SecureString
    es un sistema; el nombre del parámetro es el system_id y su valor un JSON
    {host,client,username,password,description,secure}. El rol de la instancia ya
    lee /fabrica-sw/* (sin IAM nuevo).
  - sap-systems.yaml es un override estático opcional (dev/local): se fusiona
    sobre lo descubierto (el yaml gana en las claves que repita).

RESOLUCIÓN del password de cada sistema (si no vino en el bundle de SSM):
  1. SSM Parameter Store  -> `ssm_param` del sistema o `/fabrica-sw/abap/<id>`.
  2. Docker secret        -> /run/secrets/sap_<id>  (password plano)
  3. Variable de entorno  -> SAP_PASSWORD_<ID>       (password plano)

Un sistema sin host/username/password resoluble se omite con warning (no tumba
el contenedor).
"""

import json
import os
import re
import sys

# El sample corre como `python src/aws_abap_accelerator/enterprise_main.py`, lo
# que pone esa carpeta en sys.path[0]. Aquí la agregamos a mano para poder
# importar sus módulos (auth.*, config.*) y luego su main().
SAMPLE_ROOT = os.environ.get("SAMPLE_ROOT", "/app/src/aws_abap_accelerator")
sys.path.insert(0, SAMPLE_ROOT)

CONFIG_PATH = os.environ.get("SAP_SYSTEMS_CONFIG_PATH", "/app/config/sap-systems.yaml")
DOCKER_SECRETS_DIR = "/run/secrets"
# Prefijo SSM donde el panel guarda los secretos de cada sistema SAP.
SSM_ABAP_PREFIX = os.environ.get("SSM_ABAP_PREFIX", "/fabrica-sw/abap")


def _env_key(system_id: str) -> str:
    return "SAP_PASSWORD_" + re.sub(r"[^A-Za-z0-9]", "_", system_id).upper()


def _from_docker_secret(system_id: str):
    path = os.path.join(DOCKER_SECRETS_DIR, f"sap_{system_id}")
    try:
        if os.path.isfile(path):
            val = open(path, encoding="utf-8").read().strip()
            return val or None
    except Exception as e:  # noqa: BLE001
        print(f"[entrypoint] WARN no pude leer docker secret {path}: {e}")
    return None


def _from_ssm_parameter(param_name: str):
    """Devuelve (username|None, password|None) desde SSM Parameter Store, o (None,None)."""
    try:
        import boto3  # import perezoso
    except ImportError:
        print("[entrypoint] WARN boto3 no instalado; no puedo leer SSM Parameter Store")
        return None, None
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    try:
        client = boto3.client("ssm", region_name=region)
        resp = client.get_parameter(Name=param_name, WithDecryption=True)
        raw = (resp.get("Parameter") or {}).get("Value")
        if not raw:
            return None, None
        raw = raw.strip()
        if raw.startswith("{"):
            data = json.loads(raw)
            return data.get("username"), data.get("password")
        return None, raw  # string plano = password
    except Exception as e:  # noqa: BLE001
        print(f"[entrypoint] WARN no pude leer SSM param '{param_name}': {e}")
        return None, None


def _discover_from_ssm() -> dict:
    """Descubre los sistemas listando `${SSM_ABAP_PREFIX}/*` (un SecureString por
    sistema, escrito por el Panel). Devuelve {system_id: bundle_dict} donde el
    bundle trae host/client/username/password/description/secure. {} si no hay
    boto3, no hay acceso, o no hay parámetros."""
    try:
        import boto3
    except ImportError:
        print("[entrypoint] WARN boto3 no instalado; no descubro sistemas por SSM")
        return {}
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    out: dict = {}
    try:
        client = boto3.client("ssm", region_name=region)
        paginator = client.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(
            Path=SSM_ABAP_PREFIX + "/", Recursive=False, WithDecryption=True
        ):
            for p in page.get("Parameters", []):
                system_id = p["Name"].rsplit("/", 1)[-1]
                raw = (p.get("Value") or "").strip()
                if not raw:
                    continue
                if raw.startswith("{"):
                    try:
                        out[system_id] = json.loads(raw)
                    except json.JSONDecodeError:
                        print(f"[entrypoint] WARN SSM '{p['Name']}' no es JSON válido; omitido")
                else:
                    out[system_id] = {"password": raw}  # string plano = password
    except Exception as e:  # noqa: BLE001
        print(f"[entrypoint] WARN no pude descubrir sistemas por SSM en '{SSM_ABAP_PREFIX}/': {e}")
        return {}
    if out:
        print(f"[entrypoint] descubiertos {len(out)} sistema(s) por SSM: {list(out)}")
    return out


def _resolve_credentials(system_id: str, cfg: dict):
    """Arma el dict que espera el keychain del sample a partir de `cfg` (bundle de
    SSM y/o entrada del yaml). El password puede venir ya en el bundle; si no, se
    busca en SSM (param individual) -> docker secret -> env."""
    username = cfg.get("username")
    password = cfg.get("password")

    if not password:
        param_name = cfg.get("ssm_param") or f"{SSM_ABAP_PREFIX}/{system_id}"
        ssm_user, ssm_pwd = _from_ssm_parameter(param_name)
        if ssm_user and not username:
            username = ssm_user
        if ssm_pwd:
            password = ssm_pwd
    if not password:
        password = _from_docker_secret(system_id)
    if not password:
        password = os.environ.get(_env_key(system_id))

    if not cfg.get("host") or not username or not password:
        return None

    return {
        "sap_host": cfg["host"],
        "sap_client": str(cfg.get("client", "100")),
        "sap_username": username,
        "sap_password": password,
        "sap_language": cfg.get("language", "ES"),
        "sap_secure": str(cfg.get("secure", True)).lower(),
        "description": cfg.get("description", ""),
        "credential_provider": "seeded",
    }


def _load_yaml_systems() -> dict:
    """Override estático opcional desde sap-systems.yaml (dev/local). {} si no hay
    archivo, no hay pyyaml, o `systems:` está vacío."""
    try:
        import yaml
    except ImportError:
        print("[entrypoint] WARN pyyaml no instalado; no leo sap-systems.yaml")
        return {}
    if not os.path.isfile(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    return doc.get("systems") or {}


def _seed_keychain():
    # Fuente de verdad = SSM (lo escribe el Panel); el yaml es override estático.
    systems = dict(_discover_from_ssm())
    for system_id, cfg in _load_yaml_systems().items():
        if isinstance(cfg, dict):
            systems[system_id] = {**systems.get(system_id, {}), **cfg}

    if not systems:
        print("[entrypoint] sin sistemas (ni SSM ni sap-systems.yaml); nada que sembrar")
        return

    # El singleton inicializa _memory_store={} al detectar DOCKER_CONTAINER.
    from auth.keychain_manager import keychain_manager

    if not hasattr(keychain_manager, "_memory_store") or keychain_manager._memory_store is None:
        keychain_manager._memory_store = {}

    seeded, skipped = [], []
    for system_id, cfg in systems.items():
        creds = _resolve_credentials(system_id, cfg) if isinstance(cfg, dict) else None
        if not creds:
            print(f"[entrypoint] WARN sin credenciales resolubles para '{system_id}'; omitido")
            skipped.append(system_id)
            continue
        keychain_manager._memory_store[system_id] = json.dumps(creds)
        seeded.append(system_id)

    print(f"[entrypoint] sembrados {len(seeded)} sistema(s): {seeded}"
          + (f" | omitidos: {skipped}" if skipped else ""))


def main():
    os.environ.setdefault("DOCKER_CONTAINER", "true")  # fuerza store en memoria
    os.environ["CREDENTIAL_PROVIDER"] = "keychain"      # ruta que usa _memory_store
    _seed_keychain()
    import enterprise_main
    enterprise_main.main()


if __name__ == "__main__":
    main()
