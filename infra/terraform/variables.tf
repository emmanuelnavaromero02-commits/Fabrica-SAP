# Parametrización de las "Decisiones pendientes" (sección 5 del brief) + plan
# acordado: t3.xlarge público + Caddy, apagable, Postgres en contenedor.
# Nada de valores reales aquí — van en terraform.tfvars (no se versiona).

variable "aws_region" {
  type        = string
  description = "Decisión #1. us-east-1 (más barato) o sa-east-1 (São Paulo, menos latencia, ~+35%)."
}

variable "project_name" {
  type        = string
  default     = "fabrica-sw"
  description = "Prefijo de nombres de recursos."
}

# Decisión #3 — tamaño de instancia. La plataforma creció a ~18 contenedores.
# t3.xlarge (4 vCPU/16GB) ~$55/mes apagable como arranque; subir a t3.2xlarge
# (8/32) si los agentes van lentos (solo cambiar esta variable + apply).
variable "instance_type" {
  type    = string
  default = "t3.xlarge"
}

variable "root_ebs_size_gb" {
  type    = number
  default = 30
}

# Decisión #7 — EBS de datos (gp3) separado del raíz: Postgres (OpenProject,
# Gitea, Keycloak, Temporal), repos de Gitea, datos de MinIO y adjuntos.
variable "data_ebs_size_gb" {
  type    = number
  default = 50
}

# Decisión #2 — dominio + subdominios. Terraform crea un A record por cada
# subdominio apuntando a la EIP (si route53_zone_id no está vacío). Caddy hace
# el reverse proxy host -> servicio interno (eso vive en el Caddyfile).
variable "base_domain" {
  type        = string
  description = "Dominio base, p.ej. peru.epiuse.darkswf.7businesssolutions.com"
}

variable "subdomains" {
  type        = list(string)
  description = "Hosts a publicar (A record -> EIP). Caddy enruta cada uno al servicio interno."
  default     = ["op", "git", "kc", "app", "inbox", "temporal"]
}

# Decisión #6 — zona DNS. Si la zona vive en Route53 de esta cuenta, pon su
# zone_id y Terraform crea los A records; si está vacío, se crean a mano.
variable "route53_zone_id" {
  type    = string
  default = ""
}

# Decisión #4 — acceso de red. CIDRs permitidos en 80/443. ["0.0.0.0/0"] =
# abierto a internet (protegido por auth fuerte de Keycloak + TLS de Caddy);
# o restringir a IPs corporativas/VPN.
variable "allowed_web_cidrs" {
  type        = list(string)
  description = "CIDRs permitidos en 80/443."
}

# SSH de Git (puerto host 2222, NUNCA el 22). Vacío = no se expone (clonado por
# HTTPS solamente). El 22 del sistema no se abre: administración por SSM.
variable "allowed_git_ssh_cidrs" {
  type    = list(string)
  default = []
}

# Decisión #8 — etiquetado/centro de costo. Obligatorio para análisis de costos.
variable "tags" {
  type        = map(string)
  description = "Tags aplicados a todos los recursos (CostCenter, Env, etc.)."
}

# Respaldos: retención (S3 lifecycle + AWS Backup) y horario del snapshot diario.
variable "backup_retention_days" {
  type    = number
  default = 30
}

variable "backup_schedule_cron" {
  type        = string
  default     = "cron(0 7 * * ? *)" # 07:00 UTC diario (snapshot del EBS de datos)
  description = "Cron de AWS Backup (UTC) para el snapshot del volumen de datos."
}
