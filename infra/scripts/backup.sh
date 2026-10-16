#!/bin/bash
# Respaldo lógico → S3. Corre EN LA INSTANCIA (cron diario). Usa el rol de
# instancia (acceso al bucket de backups por IAM, sin llaves).
#
# Qué respalda: dumps de las 3 bases críticas (OpenProject, Gitea, Keycloak) +
# tar de los repos de Gitea. Los ADJUNTOS ya viven en S3 nativo (durable), no
# se incluyen aquí. El estado de Temporal es efímero y se omite.
#
# Variables (export antes de correr, o vía el cron de abajo):
#   BACKUP_BUCKET  bucket de respaldos (output 'backup_bucket' de Terraform)
#   APP_DIR        dir de la compose en la instancia (def: /opt/fabrica/infra/app)
#
# Instalar en cron (en la instancia, vía SSM):
#   echo 'BACKUP_BUCKET=fabrica-sw-backups-203677518499' | sudo tee /etc/fabrica-backup.env
#   ( crontab -l 2>/dev/null; echo '30 6 * * * . /etc/fabrica-backup.env; /opt/fabrica/infra/scripts/backup.sh >> /var/log/fabrica-backup.log 2>&1' ) | crontab -
set -euo pipefail

: "${BACKUP_BUCKET:?define BACKUP_BUCKET}"
APP_DIR="${APP_DIR:-/opt/fabrica/infra/app}"
DATA_DIR="${DATA_DIR:-/data}"

STAMP="$(date -u +%Y-%m-%d_%H%M)"
DEST="s3://${BACKUP_BUCKET}/backups/${STAMP}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

dc() { docker compose --project-directory "$APP_DIR" -f "$APP_DIR/docker-compose.yml" "$@"; }

echo "[backup ${STAMP}] dumps de bases…"
dc exec -T db        pg_dump -U openproject openproject | gzip > "$TMP/openproject.sql.gz"
dc exec -T gitea-db  pg_dump -U gitea       gitea       | gzip > "$TMP/gitea.sql.gz"
dc exec -T keycloak-db pg_dump -U keycloak  keycloak    | gzip > "$TMP/keycloak.sql.gz"

echo "[backup ${STAMP}] tar de repos de Gitea…"
tar czf "$TMP/gitea_repos.tar.gz" -C "$DATA_DIR" gitea_data

echo "[backup ${STAMP}] subiendo a ${DEST}…"
aws s3 cp "$TMP/" "$DEST/" --recursive --only-show-errors

echo "[backup ${STAMP}] OK"
