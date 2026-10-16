# Infraestructura de la plataforma (plan acordado): UN EC2 público con Docker
# Compose + Caddy/Let's Encrypt, APAGABLE. Postgres en contenedor (decisión #5).
# Red mínima (VPC + subred pública), SG 80/443/2222 (sin 22 — admin por SSM),
# EBS gp3 de datos separado del raíz, EIP, IAM (SSM + S3 backup), S3 cifrado +
# versionado, y AWS Backup del volumen de datos.
#
# Los SECRETOS no viven aquí: .env en la instancia / SSM Parameter Store.
# Nada destructivo se aplica sin que el usuario confirme el `terraform plan`.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# AMI más reciente de Amazon Linux 2023 x86_64 (no se quema un id: se resuelve).
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
  filter {
    name   = "root-device-type"
    values = ["ebs"]
  }
}

# ---------------------------------------------------------------------------
# Red: VPC + 1 subred pública + IGW + tabla de rutas.
# ---------------------------------------------------------------------------
resource "aws_vpc" "main" {
  cidr_block           = "10.30.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.project_name}-vpc" }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project_name}-igw" }
}

# Subred pública en la primera AZ de la región. La instancia recibe IP pública
# (luego se le asocia la EIP estable).
data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.30.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.project_name}-public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
  tags = { Name = "${var.project_name}-public-rt" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# ---------------------------------------------------------------------------
# Security Group: 80/443 (web) y 2222 (git SSH) desde los CIDR autorizados.
# SIN puerto 22 (admin por SSM Session Manager). Egress abierto: Let's Encrypt,
# pulls de imágenes y salida 443 a las APIs de IA (Anthropic).
# ---------------------------------------------------------------------------
resource "aws_security_group" "web" {
  name        = "${var.project_name}-web"
  description = "80/443 web + 2222 git-ssh desde CIDRs autorizados; sin 22 (SSM)"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP (Caddy / redireccion a HTTPS y ACME)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.allowed_web_cidrs
  }

  ingress {
    description = "HTTPS (Caddy / Lets Encrypt)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_web_cidrs
  }

  # 2222 solo si se definieron CIDRs para SSH de Git (si la lista está vacía,
  # no se abre el puerto: clonado por HTTPS solamente).
  dynamic "ingress" {
    for_each = length(var.allowed_git_ssh_cidrs) > 0 ? [1] : []
    content {
      description = "SSH de Git (Gitea, puerto host 2222)"
      from_port   = 2222
      to_port     = 2222
      protocol    = "tcp"
      cidr_blocks = var.allowed_git_ssh_cidrs
    }
  }

  egress {
    description = "Salida a internet (ACME, pulls, APIs de IA por 443)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-web" }
}

# ---------------------------------------------------------------------------
# IAM: rol de instancia con SSM (admin sin SSH) + acceso mínimo al bucket de
# respaldos + lectura de secretos en SSM Parameter Store bajo /${project_name}/.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${var.project_name}-instance"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# Administración por SSM Session Manager (reemplaza al SSH).
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "instance_inline" {
  # Respaldos: la instancia escribe/lee SOLO su bucket de backups.
  statement {
    sid       = "BackupBucketObjects"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:ListMultipartUploadParts", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.backups.arn}/*"]
  }
  statement {
    sid       = "BackupBucketList"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.backups.arn]
  }
  # Lectura de secretos (tokens, passwords, ANTHROPIC_API_KEY) en SSM Parameter
  # Store. Es el almacén recomendado; el bootstrap/operador los inyecta al .env.
  statement {
    sid       = "ReadOwnSsmParameters"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/${var.project_name}/*"]
  }
  # El Panel administra las credenciales de los sistemas SAP del MCP ABAP como
  # SecureString en /<project>/abap/*. Escritura/borrado acotado a ESE prefijo
  # (la lectura ya la cubre el statement anterior sobre /<project>/*).
  statement {
    sid       = "WriteAbapSsmParameters"
    actions   = ["ssm:PutParameter", "ssm:DeleteParameter"]
    resources = ["arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/${var.project_name}/abap/*"]
  }
  # Tools de VPN del MCP `fabrica`: el contenedor corre vpnctl EN EL HOST por
  # SSM. SendCommand acotado a ESTA instancia + el documento AWS-RunShellScript.
  statement {
    sid     = "SsmSendCommandToSelf"
    actions = ["ssm:SendCommand"]
    resources = [
      "arn:aws:ssm:${data.aws_region.current.name}::document/AWS-RunShellScript",
      "arn:aws:ec2:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:instance/${aws_instance.host.id}",
    ]
  }
  # GetCommandInvocation no admite permisos a nivel de recurso.
  statement {
    sid       = "SsmReadCommandResults"
    actions   = ["ssm:GetCommandInvocation"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "instance_inline" {
  name   = "${var.project_name}-instance-inline"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance_inline.json
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.project_name}-instance"
  role = aws_iam_role.instance.name
}

# ---------------------------------------------------------------------------
# EBS de datos (gp3) separado del raíz: Postgres (OP/Gitea/Keycloak/Temporal),
# repos de Gitea, datos de MinIO y adjuntos. Se monta en /data (ver bootstrap).
# ---------------------------------------------------------------------------
resource "aws_ebs_volume" "data" {
  availability_zone = aws_subnet.public.availability_zone
  size              = var.data_ebs_size_gb
  type              = "gp3"
  encrypted         = true
  tags              = { Name = "${var.project_name}-data" }

  # Aquí viven TODOS los datos (BDs, repos Gitea, adjuntos). Un `terraform
  # destroy` accidental no debe borrarlo. Para retirarlo de verdad: quitar este
  # bloque en un cambio aparte y luego destruir.
  lifecycle {
    prevent_destroy = true
  }
}

# ---------------------------------------------------------------------------
# EC2: el host de la plataforma. IMDSv2 obligatorio, raíz gp3 cifrado,
# user-data = bootstrap.sh (Docker + Compose + montaje del EBS de datos).
# ---------------------------------------------------------------------------
resource "aws_instance" "host" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.web.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name

  user_data                   = file("${path.module}/../scripts/bootstrap.sh")
  user_data_replace_on_change = false

  metadata_options {
    http_tokens   = "required" # IMDSv2 obligatorio
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_ebs_size_gb
    encrypted             = true
    delete_on_termination = true
  }

  tags = { Name = "${var.project_name}-host" }

  # NO recrear la instancia cuando el data source del AMI AL2023 publique uno
  # nuevo (data.aws_ami.al2023 es most_recent). Un cambio de `ami` fuerza
  # REEMPLAZO de la instancia de prod (se perdió el root + .env el 2026-06-18).
  # Para actualizar el AMI a propósito: quitar este ignore en un cambio aparte y
  # planear el reemplazo con respaldo del .env. Ver memoria terraform-ami-drift.
  lifecycle {
    ignore_changes = [ami]
  }
}

# El volumen de datos NO se destruye al recrear la instancia (persiste el dato).
resource "aws_volume_attachment" "data" {
  device_name = "/dev/sdf" # en Nitro aparece como /dev/nvme1n1 (lo detecta el bootstrap)
  volume_id   = aws_ebs_volume.data.id
  instance_id = aws_instance.host.id
}

# IP elástica estable: a ella apuntan los A records del DNS (creados a mano,
# la zona base no vive en Route53 de esta cuenta).
resource "aws_eip" "host" {
  domain   = "vpc"
  instance = aws_instance.host.id
  tags     = { Name = "${var.project_name}-eip" }

  depends_on = [aws_internet_gateway.igw]
}

# ---------------------------------------------------------------------------
# S3: bucket privado de respaldos — versionado + cifrado + sin acceso público +
# expiración de respaldos antiguos.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "backups" {
  bucket = "${var.project_name}-backups-${data.aws_caller_identity.current.account_id}"
  tags   = { Name = "${var.project_name}-backups" }

  # Red de seguridad de los dumps; no se borra por un destroy accidental.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id

  rule {
    id     = "expire-old-backups"
    status = "Enabled"
    filter {}
    expiration {
      days = var.backup_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# ---------------------------------------------------------------------------
# AWS Backup: snapshots periódicos del volumen EBS de datos.
# ---------------------------------------------------------------------------
resource "aws_backup_vault" "main" {
  name = "${var.project_name}-vault"
}

data "aws_iam_policy_document" "backup_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["backup.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backup" {
  name               = "${var.project_name}-backup"
  assume_role_policy = data.aws_iam_policy_document.backup_assume.json
}

resource "aws_iam_role_policy_attachment" "backup" {
  role       = aws_iam_role.backup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}

resource "aws_backup_plan" "main" {
  name = "${var.project_name}-plan"

  rule {
    rule_name         = "daily"
    target_vault_name = aws_backup_vault.main.name
    schedule          = var.backup_schedule_cron
    start_window      = 60
    completion_window = 180
    lifecycle {
      delete_after = var.backup_retention_days
    }
  }
}

resource "aws_backup_selection" "data_volume" {
  iam_role_arn = aws_iam_role.backup.arn
  name         = "${var.project_name}-data-volume"
  plan_id      = aws_backup_plan.main.id
  resources    = [aws_ebs_volume.data.arn]
}

# ---------------------------------------------------------------------------
# S3 de ADJUNTOS (nativo): el browser sube/descarga con presigned URLs. Privado,
# cifrado, sin acceso público; CORS solo desde las apps que suben (app/inbox).
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "attachments" {
  bucket = "${var.project_name}-attachments-${data.aws_caller_identity.current.account_id}"
  tags   = { Name = "${var.project_name}-attachments" }

  # Adjuntos en vivo (presigned S3). No se borra por un destroy accidental.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "attachments" {
  bucket = aws_s3_bucket.attachments.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "attachments" {
  bucket                  = aws_s3_bucket.attachments.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# CORS: el browser hace PUT/GET presignado desde el Panel y el Inbox.
resource "aws_s3_bucket_cors_configuration" "attachments" {
  bucket = aws_s3_bucket.attachments.id
  cors_rule {
    allowed_methods = ["PUT", "GET", "HEAD"]
    allowed_origins = [
      "https://app.${var.base_domain}",
      "https://inbox.${var.base_domain}",
    ]
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3600
  }
}

# Usuario IAM dedicado SOLO para firmar presigned URLs del bucket de adjuntos
# (mínimo privilegio). Sus llaves van al .env/SSM de la instancia, no al repo.
resource "aws_iam_user" "app" {
  name = "${var.project_name}-app-s3"
}

data "aws_iam_policy_document" "app_s3" {
  statement {
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.attachments.arn}/*"]
  }
  statement {
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.attachments.arn]
  }
}

resource "aws_iam_user_policy" "app_s3" {
  name   = "${var.project_name}-app-s3"
  user   = aws_iam_user.app.name
  policy = data.aws_iam_policy_document.app_s3.json
}

resource "aws_iam_access_key" "app" {
  user = aws_iam_user.app.name
}

# ---------------------------------------------------------------------------
# Route53 (decisión #6): SOLO si la zona vive en esta cuenta (route53_zone_id
# no vacío). Hoy NO vive aquí, así que esto crea 0 recursos y los A records se
# hacen a mano apuntando a aws_eip.host.public_ip.
# ---------------------------------------------------------------------------
resource "aws_route53_record" "subdomains" {
  for_each = var.route53_zone_id == "" ? toset([]) : toset(var.subdomains)

  zone_id = var.route53_zone_id
  name    = "${each.value}.${var.base_domain}"
  type    = "A"
  ttl     = 300
  records = [aws_eip.host.public_ip]
}
