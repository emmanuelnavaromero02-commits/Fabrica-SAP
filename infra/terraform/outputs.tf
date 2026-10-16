# Lo que necesitas tras `apply`: la EIP (para los A records del DNS), el id de
# la instancia (para abrir sesión SSM y para start/stop), y el bucket de backup.

output "public_ip" {
  description = "IP elástica. Apunta AQUÍ los A records de cada subdominio ANTES de levantar Caddy (Let's Encrypt lo necesita)."
  value       = aws_eip.host.public_ip
}

output "dns_records_to_create" {
  description = "A records a crear a mano en el DNS de la zona base (no está en Route53 de esta cuenta)."
  value       = [for s in var.subdomains : "${s}.${var.base_domain}  A  ${aws_eip.host.public_ip}"]
}

output "instance_id" {
  description = "Id de la instancia. Úsalo para SSM y para start/stop."
  value       = aws_instance.host.id
}

output "ssm_session_command" {
  description = "Cómo abrir una shell en el host sin SSH (admin por SSM Session Manager)."
  value       = "aws ssm start-session --target ${aws_instance.host.id} --region ${data.aws_region.current.name}"
}

output "backup_bucket" {
  description = "Bucket S3 de respaldos (privado, cifrado, versionado)."
  value       = aws_s3_bucket.backups.bucket
}

output "data_volume_id" {
  description = "Volumen EBS de datos (persiste aunque se recree la instancia)."
  value       = aws_ebs_volume.data.id
}

output "region" {
  value = data.aws_region.current.name
}

# --- Adjuntos S3 (van al .env de la instancia: ATTACHMENTS_BUCKET + llaves) ---
output "attachments_bucket" {
  description = "Bucket de adjuntos → ATTACHMENTS_BUCKET en el .env de la app."
  value       = aws_s3_bucket.attachments.bucket
}

output "app_access_key_id" {
  description = "S3_ACCESS_KEY para presigned URLs (usuario IAM acotado al bucket)."
  value       = aws_iam_access_key.app.id
}

output "app_secret_access_key" {
  description = "S3_SECRET_KEY (SENSIBLE). Cópialo al .env/SSM; no lo versiones."
  value       = aws_iam_access_key.app.secret
  sensitive   = true
}
