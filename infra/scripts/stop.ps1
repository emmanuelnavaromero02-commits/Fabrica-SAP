# Apaga la instancia para ahorrar (deja de pagar el cómputo; siguen EBS/S3/EIP).
# Corre en tu Windows. Los datos persisten en el EBS de /data.
$ErrorActionPreference = "Stop"
$tf = Join-Path $PSScriptRoot "..\terraform"

$id     = terraform -chdir="$tf" output -raw instance_id
$region = terraform -chdir="$tf" output -raw region

Write-Host "Apagando $id en $region ..."
aws ec2 stop-instances --instance-ids $id --region $region | Out-Null
aws ec2 wait instance-stopped --instance-ids $id --region $region
Write-Host "Instancia DETENIDA. Para reanudar: .\start.ps1"
