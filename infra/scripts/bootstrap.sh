#!/bin/bash
# user-data de la instancia (se ejecuta como root en el primer arranque).
# SOLO prepara el host: Docker + Docker Compose + montaje del EBS de datos.
# NO trae secretos ni el código de la app (eso es la fase 4 del despliegue).
# Idempotente: re-ejecutarlo no rompe nada.
set -euxo pipefail

LOG=/var/log/bootstrap.log
exec > >(tee -a "$LOG") 2>&1
echo "=== bootstrap $(date -u) ==="

# --- Docker + plugin de Compose (Amazon Linux 2023) ---
dnf install -y docker
systemctl enable --now docker

DOCKER_CLI_PLUGINS=/usr/libexec/docker/cli-plugins
mkdir -p "$DOCKER_CLI_PLUGINS"
if [ ! -x "$DOCKER_CLI_PLUGINS/docker-compose" ]; then
  COMPOSE_VERSION="v2.32.4"
  curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
    -o "$DOCKER_CLI_PLUGINS/docker-compose"
  chmod +x "$DOCKER_CLI_PLUGINS/docker-compose"
fi

# ec2-user puede usar docker sin sudo.
usermod -aG docker ec2-user || true

# --- Montaje del EBS de datos en /data ---
# En Nitro el volumen adjunto aparece como /dev/nvmeXn1 (no como /dev/sdf).
# Detectamos el disco SIN sistema de archivos y SIN montar (el de datos).
MOUNT=/data
mkdir -p "$MOUNT"

ROOT_DISK="$(lsblk -no PKNAME "$(findmnt -no SOURCE /)" | head -n1 || true)"

DATA_DEV=""
for dev in /dev/nvme*n1 /dev/xvd[b-z]; do
  [ -b "$dev" ] || continue
  base="$(basename "$dev")"
  # Saltar el disco raíz.
  if [ -n "$ROOT_DISK" ] && [ "$base" = "$ROOT_DISK" ]; then
    continue
  fi
  # Saltar si ya tiene particiones montadas o es el raíz por montaje.
  if findmnt -S "$dev" >/dev/null 2>&1; then
    continue
  fi
  DATA_DEV="$dev"
  break
done

if [ -z "$DATA_DEV" ]; then
  echo "WARN: no se detectó disco de datos; ¿se adjuntó el EBS? Reintentar tras attach."
else
  # Formatear solo si está vacío (preserva datos en reinicios/recreación).
  if ! blkid "$DATA_DEV" >/dev/null 2>&1; then
    mkfs.xfs -f "$DATA_DEV"
  fi
  UUID="$(blkid -s UUID -o value "$DATA_DEV")"
  if ! grep -q "$UUID" /etc/fstab; then
    echo "UUID=$UUID $MOUNT xfs defaults,nofail 0 2" >> /etc/fstab
  fi
  mount -a
fi

# Carpetas de datos (los volúmenes del compose se mapean bajo /data).
mkdir -p "$MOUNT"/{op_pgdata,op_assets,gitea_data,gitea_pgdata,kc_pgdata,temporal_pgdata,minio_data,caddy_data,caddy_config}

echo "=== bootstrap OK $(date -u) ==="
