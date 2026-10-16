# Enciende la instancia (plataforma APAGABLE). Corre en tu Windows.
# Lee el id/region de los outputs de Terraform. La EIP persiste al apagar.
$ErrorActionPreference = "Stop"
$tf = Join-Path $PSScriptRoot "..\terraform"

$id     = terraform -chdir="$tf" output -raw instance_id
$region = terraform -chdir="$tf" output -raw region
$ip     = terraform -chdir="$tf" output -raw public_ip

Write-Host "Encendiendo $id en $region ..."
aws ec2 start-instances --instance-ids $id --region $region | Out-Null
aws ec2 wait instance-running --instance-ids $id --region $region
Write-Host "Instancia ARRIBA. IP: $ip"
Write-Host "Los contenedores arrancan solos (restart: unless-stopped)."
Write-Host "Si no, abre sesion SSM y: cd /opt/fabrica/infra/app; docker compose up -d"
