# Despliegue AWS — Plataforma de fábrica de software

Runbook para desplegar, operar, respaldar, restaurar y escalar la plataforma
completa (OpenProject + Gitea + Keycloak + Temporal + Orquestador "Fabro" +
Panel) en **un EC2 apagable** detrás de Caddy (TLS Let's Encrypt).

## Decisiones de este despliegue

| # | Decisión | Valor |
|---|----------|-------|
| 1 | Región | `us-east-1` |
| 2 | Dominio / subdominios | `peru.epiuse.darkswf.7businesssolutions.com` → `op` `git` `kc` `app` `inbox` `temporal` |
| 3 | Instancia | `t3.xlarge` (apagable; subir a `t3.2xlarge` si va lento) |
| 4 | Acceso web | Abierto a internet (`0.0.0.0/0`) en 80/443; TLS + login Keycloak |
| 5 | Base de datos | Postgres en contenedor (una por sistema) |
| 6 | Zona DNS | **Fuera de Route53** → los A records se crean a mano |
| 7 | EBS de datos | `gp3` 50 GB en `/data` |
| 8 | Tags | `Project/CostCenter/Env/ManagedBy` (en `terraform.tfvars`) |
| — | Adjuntos | **S3 nativo** (presigned), no MinIO |

Costo aprox (us-east-1): 24/7 ~$140/mes · horario laboral (apagable) ~$55/mes ·
apagado ~$15/mes (EBS+S3+EIP).

## Estructura

```
infra/
  terraform/   main.tf variables.tf outputs.tf versions.tf terraform.tfvars(.example)
  app/         docker-compose.yml  Caddyfile  .env.example
  scripts/     bootstrap.sh (user-data)  backup.sh (instancia)  start.ps1 stop.ps1 (laptop)
```

Los **secretos no se versionan**: van en `.env` real en la instancia (chmod 600)
o en SSM Parameter Store. `terraform.tfvars` y `app/.env` están en `.gitignore`.

---

## Fase 1 — Preparación

```powershell
aws sts get-caller-identity      # confirma cuenta 203677518499 / user rodolfo
cd infra/terraform
Copy-Item terraform.tfvars.example terraform.tfvars
# Edita terraform.tfvars: aws_region, base_domain, allowed_web_cidrs, tags (CostCenter).
```

## Fase 2 — Infraestructura

```powershell
terraform init
terraform plan       # REVÍSALO. No se aplica nada sin tu OK.
terraform apply      # crea VPC/SG/EC2/EBS/EIP/IAM/S3/AWS Backup
terraform output     # anota public_ip, instance_id, attachments_bucket, backup_bucket
terraform output -raw app_secret_access_key   # secreto S3 (sensible)
```

## Fase 3 — DNS (antes de levantar Caddy)

En el proveedor DNS de `7businesssolutions.com`, crea un **A record por
subdominio** apuntando a `public_ip`:

```
op.peru.epiuse.darkswf.7businesssolutions.com        A   <public_ip>
git.peru.epiuse.darkswf.7businesssolutions.com       A   <public_ip>
kc.peru.epiuse.darkswf.7businesssolutions.com        A   <public_ip>
app.peru.epiuse.darkswf.7businesssolutions.com       A   <public_ip>
inbox.peru.epiuse.darkswf.7businesssolutions.com     A   <public_ip>
temporal.peru.epiuse.darkswf.7businesssolutions.com  A   <public_ip>
```

Alternativa: un **wildcard** `*.peru.epiuse.darkswf.7businesssolutions.com  A
<public_ip>` cubre los seis de una vez (Caddy igual emite un cert por host vía
http-01). `terraform output dns_records_to_create` te imprime los explícitos.
Espera a que resuelvan (`nslookup op.peru.epiuse.darkswf.7businesssolutions.com`)
**antes** de la fase 4: Caddy necesita el DNS para el reto ACME de Let's Encrypt.

## Fase 4 — Capa de aplicación

**1) Subir el código a la instancia** (sin SSH; vía S3 + SSM). Desde tu laptop:

```powershell
# empaqueta lo que la compose necesita
tar -czf fabrica.tgz infra orquestador panel local/patches idp/realm-fabrica.json
aws s3 cp fabrica.tgz s3://<backup_bucket>/deploy/fabrica.tgz
```

Abre una shell en el host por SSM (sin puerto 22):

```powershell
aws ssm start-session --target <instance_id> --region us-east-1
```

Dentro de la instancia:

```bash
sudo mkdir -p /opt/fabrica && cd /opt/fabrica
sudo aws s3 cp s3://<backup_bucket>/deploy/fabrica.tgz . && sudo tar xzf fabrica.tgz
cd /opt/fabrica/infra/app
sudo cp .env.example .env && sudo chmod 600 .env
sudo nano .env     # rellena secretos + outputs de Terraform (ver abajo)
```

**2) Rellenar `.env`** con secretos fuertes (`openssl rand -hex 32`) y los
outputs de Terraform:
- `ATTACHMENTS_BUCKET` = output `attachments_bucket`
- `S3_ACCESS_KEY` = output `app_access_key_id`
- `S3_SECRET_KEY` = output `app_secret_access_key`
- `BASE_DOMAIN=peru.epiuse.darkswf.7businesssolutions.com`, `ACME_EMAIL=...`, `AWS_REGION=us-east-1`
- Passwords de Postgres, `SECRET_KEY_BASE`, `KC_ADMIN_*`, tokens y los
  `*_OIDC_CLIENT_SECRET` (deben coincidir con los clients del realm de Keycloak).

> Alternativa hardening: guarda cada secreto en SSM Parameter Store bajo
> `/fabrica-sw/...` (el rol de instancia ya puede leerlos) y genera el `.env`
> con un script en el arranque.

**3) Levantar el stack:**

```bash
sudo docker compose up -d --build
sudo docker compose ps         # todos Up; web/keycloak tardan (healthcheck)
sudo docker compose logs -f caddy   # verifica que emite los certificados LE
```

## Fase 5 — Configuración inicial

1. **OpenProject** `https://op.peru.epiuse.darkswf.7businesssolutions.com`: primer login admin, **cambia la
   contraseña** por defecto. El proveedor OIDC **Keycloak** se crea con el script
   reproducible `local/seed-oidc-provider.rb` (NO a mano en el Admin UI — eso no
   se versiona y no se replica entre entornos). El patch habilita la feature SSO
   en CE. Ejecutar dentro del contenedor `web` (descarga el discovery y puebla los
   endpoints; sin eso `metadata_configured?` queda false y el login NO muestra el
   botón):
   ```bash
   docker cp local/seed-oidc-provider.rb <web>:/tmp/seed-oidc-provider.rb
   docker exec \
     -e OIDC_METADATA_URL="https://kc.peru.epiuse.darkswf.7businesssolutions.com/realms/fabrica/.well-known/openid-configuration" \
     -e OIDC_CLIENT_ID="openproject" \
     -e OIDC_CLIENT_SECRET="$OP_OIDC_CLIENT_SECRET" \
     <web> bundle exec rails runner /tmp/seed-oidc-provider.rb
   # luego: limpiar caché (Rails.cache.clear) o reiniciar `web` para que el
   # botón aparezca en /login.
   ```
2. **Usuario de servicio "Fabro"** en OpenProject: rol restringido **sin** las
   transiciones de aprobación (frontera 8.4 del brief). Genera su token de API →
   `OPENPROJECT_API_TOKEN` en `.env`.
3. **Gitea** `https://git.peru.epiuse.darkswf.7businesssolutions.com`: registro ya deshabilitado. Crea la org
   `mcp-agent` y los repos `fabrica` y `fabrica-arquitectura`; sube
   `pipeline.yaml`, `spec_template.md`, `skill.md`, `arquitectura.md`. Genera un
   token acotado → `GITEA_API_TOKEN`.
4. **Keycloak** `https://kc.peru.epiuse.darkswf.7businesssolutions.com`: verifica el realm `fabrica` y que los
   secretos de los clients `inbox`, `panel`, `temporal-web` coincidan con `.env`.
5. **Webhook** en OpenProject → `https://inbox.peru.epiuse.darkswf.7businesssolutions.com/webhooks/openproject`
   (o la ruta que exponga el webhook), con `OPENPROJECT_WEBHOOK_SECRET`.
6. Reinicia los servicios que tomaron tokens nuevos: `docker compose up -d`.

## Fase 6 — Respaldos

- **AWS Backup** ya hace snapshots diarios del EBS de datos (creado por Terraform).
- **Dump lógico a S3** (`backup.sh`): instálalo en cron en la instancia
  (instrucciones dentro del propio script). Verifica que llega a
  `s3://<backup_bucket>/backups/`.

---

## Operación

**Apagar / encender (ahorro):** desde tu laptop, en `infra/scripts/`:

```powershell
.\stop.ps1     # detiene el EC2 (siguen EBS/S3/EIP)
.\start.ps1    # lo enciende; los contenedores levantan solos
```

**Administración del host:** solo por SSM (no hay puerto 22):

```powershell
aws ssm start-session --target <instance_id> --region us-east-1
```

**Escalar:** `stop.ps1` → en `terraform.tfvars` sube `instance_type` (o
`data_ebs_size_gb`) → `terraform apply` → `start.ps1`. (Tras agrandar el EBS,
en la instancia: `sudo xfs_growfs /data`.)

## Restaurar un respaldo

```bash
cd /opt/fabrica/infra/app
# baja el respaldo deseado
aws s3 cp s3://<backup_bucket>/backups/<STAMP>/ ./restore/ --recursive
docker compose stop web worker cron seeder gitea keycloak
# OpenProject
gunzip -c restore/openproject.sql.gz | docker compose exec -T db psql -U openproject -d openproject
# Gitea (base + repos)
gunzip -c restore/gitea.sql.gz | docker compose exec -T gitea-db psql -U gitea -d gitea
tar xzf restore/gitea_repos.tar.gz -C /data
# Keycloak
gunzip -c restore/keycloak.sql.gz | docker compose exec -T keycloak-db psql -U keycloak -d keycloak
docker compose up -d
```

(Los adjuntos viven en el bucket S3 de adjuntos, versionado; no requieren
restauración salvo borrado accidental → recuperar versión previa.)

## MCP ABAP — sistemas SAP por cliente (FASE 2)

El servicio `abap-mcp` (compose) envuelve el sample de AWS y expone herramientas
ABAP por ADT en `mcp.${BASE_DOMAIN}/abap/mcp`. Es **multi-cliente**: Claude Code
elige el sistema con el header `x-sap-system-id`.

**De dónde salen las credenciales.** El sample anuncia un provider `aws_secrets`
pero NO lo implementa (y en Docker su keychain es solo en memoria). Por eso
`infra/app/abap-mcp/entrypoint.py` siembra ese keychain al arrancar con una
entrada por sistema. Fuente de verdad: **SSM Parameter Store**, un SecureString
por sistema en `/fabrica-sw/abap/<system_id>` con el bundle:

```json
{ "host": "sap.cliente.com:44300", "client": "100",
  "username": "DEVELOPER", "password": "***",
  "description": "S/4HANA Dev cliente X", "secure": true }
```

El entrypoint **descubre los sistemas** listando ese path (`GetParametersByPath`),
no necesita el `sap-systems.yaml` (que queda como override estático opcional para
local/dev). El rol de la instancia ya lee `/fabrica-sw/*`; para que el **panel**
los cree/borre se le suma `ssm:PutParameter`/`DeleteParameter` sobre ese prefijo
(ver `instance_inline` en Terraform).

**Quién los crea.** El Panel (`app.${BASE_DOMAIN}`, sección Sistemas SAP):
escribe el SecureString en SSM y espeja lo no-secreto en `proyecto-db` para
listarlos. Tras alta/baja, **reiniciar `abap-mcp`** para re-sembrar (el seeding
es al arrancar): `docker compose up -d abap-mcp` o `restart abap-mcp`.

**Reconstrucción.** Los parámetros SSM **no** los respalda `backup.sh` ni AWS
Backup (igual que el `.env`): en un rebuild total se recrean desde el Panel.

> Pendiente: VPN/ruta de red a los SAP de cada cliente.

## Seguridad (resumen)

- **Datos protegidos contra `destroy`:** el EBS de datos y los buckets S3
  (`backups`, `attachments`) llevan `lifecycle { prevent_destroy = true }` en
  Terraform → un `terraform destroy` accidental falla en vez de borrarlos. Para
  retirarlos de verdad: quitar el bloque en un cambio aparte y luego destruir.
- Sin puerto 22; administración por SSM Session Manager. IMDSv2 obligatorio.
- SG solo 80/443 (y 2222 si se habilitan CIDRs de git-ssh). Sin puertos de BD.
- TLS en todo el tráfico de usuarios (Caddy/Let's Encrypt).
- Secretos en `.env` (chmod 600) o SSM; nunca en el repo ni en user-data.
- Buckets S3 privados, cifrados; el de respaldos versionado con expiración.
- Frontera de aprobaciones de Fabro forzada por permisos (rol restringido en OP).
