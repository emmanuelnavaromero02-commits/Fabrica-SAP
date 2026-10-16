#requires -version 5
<#
.SYNOPSIS
  Crea los usuarios y tokens de servicio del componente MCP, con minimo
  privilegio, y deja escrito mcp/.env listo para usar.

.DESCRIPTION
  OpenProject : usuario 'mcp-agent' (no admin) + rol "Agente MCP" + token de
                API. Lo hace ejecutando mcp/scripts/create-op-agent.rb dentro
                del contenedor 'web' con rails runner.
  Gitea       : usuario 'mcp-agent' (no admin) + token con scopes acotados
                (read:user, write:repository, write:issue), via la CLI gitea.

  Requiere que el stack local de ../local este arriba y sano. Es idempotente:
  re-ejecutarlo no duplica usuarios ni roles (pero ver la nota sobre tokens).

.NOTES
  Los tokens en claro solo se ven al crearse. Este script los escribe en
  mcp/.env. Guarda tambien las contrasenas que imprime: las necesitas para
  entrar por la UI (p. ej. a regenerar un token).
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$repo       = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$composeArgs = @(
  'compose',
  '-f', (Join-Path $repo 'local\docker-compose.yml'),
  '--project-directory', (Join-Path $repo 'local')
)

$script:rng = New-Object System.Security.Cryptography.RNGCryptoServiceProvider

function Get-RandomChar {
  param([string]$Set)
  $b = New-Object byte[] 1
  $script:rng.GetBytes($b)
  $Set[$b[0] % $Set.Length]
}

# Genera una contrasena con minuscula, mayuscula, digito y simbolo: la
# politica de contrasenas por defecto de OpenProject exige las cuatro clases.
function New-StrongPassword {
  param([int]$Length = 24)
  $lower   = 'abcdefghijkmnpqrstuvwxyz'
  $upper   = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
  $digit   = '23456789'
  $special = '!@#%^*-_=+'
  $all     = $lower + $upper + $digit + $special
  $chars   = @(
    (Get-RandomChar $lower), (Get-RandomChar $upper),
    (Get-RandomChar $digit), (Get-RandomChar $special)
  )
  while ($chars.Count -lt $Length) { $chars += (Get-RandomChar $all) }
  ($chars | Sort-Object { $b = New-Object byte[] 1; $script:rng.GetBytes($b); $b[0] }) -join ''
}

# --- Comprobacion previa: el stack debe estar corriendo --------------------
Write-Host 'Comprobando que el stack local esta arriba...' -ForegroundColor Cyan
$null = docker @composeArgs ps --status running --services
if ($LASTEXITCODE -ne 0) {
  throw 'No se pudo consultar el stack. Levantalo con: docker compose -f local\docker-compose.yml --project-directory local up -d'
}

# === OpenProject ===========================================================
Write-Host ''
Write-Host '== OpenProject: usuario + rol "Agente MCP" + token de API ==' -ForegroundColor Cyan
$opPassword = New-StrongPassword
$railsScript = Get-Content (Join-Path $PSScriptRoot 'create-op-agent.rb') -Raw
$railsOut = $railsScript |
  docker @composeArgs exec -T -e "MCP_AGENT_PASSWORD=$opPassword" `
    web bundle exec rails runner -
if ($LASTEXITCODE -ne 0) {
  $railsOut | Write-Host
  throw 'create-op-agent.rb fallo dentro del contenedor web.'
}
$railsOut | ForEach-Object { Write-Host "  $_" }

$opToken = ''
$match = $railsOut | Select-String -Pattern '^TOKEN_API_VALUE=(.+)$'
if ($match) { $opToken = $match.Matches[0].Groups[1].Value.Trim() }
if (-not $opToken) {
  Write-Host 'AVISO: el usuario mcp-agent ya tenia un token de API; no es recuperable.' -ForegroundColor Yellow
  Write-Host '       Para regenerarlo, sigue la instruccion impresa arriba y re-ejecuta.' -ForegroundColor Yellow
}

# === Gitea =================================================================
Write-Host ''
Write-Host '== Gitea: usuario + token con scopes minimos ==' -ForegroundColor Cyan
$gtPassword = New-StrongPassword

# Usuario (idempotente: si ya existe, la CLI devuelve error y se continua).
docker @composeArgs exec -T -u git gitea gitea admin user create `
  --username mcp-agent --email mcp-agent@epiuse.lat `
  --password $gtPassword --must-change-password=false 2>&1 |
  ForEach-Object { Write-Host "  $_" }
if ($LASTEXITCODE -ne 0) {
  Write-Host '  (el usuario de Gitea ya existia; se continua)' -ForegroundColor Yellow
}

# Token con scopes acotados. write:repository cubre crear repos y archivos;
# write:issue cubre issues y pull requests. Sin scopes de admin ni de org.
$scopes    = 'read:user,write:repository,write:issue'
$tokenName = "claude-mcp-$(Get-Date -Format yyyyMMddHHmmss)"
$gtRaw = docker @composeArgs exec -T -u git gitea gitea admin user `
  generate-access-token --username mcp-agent --token-name $tokenName `
  --scopes $scopes --raw
if ($LASTEXITCODE -ne 0) {
  $gtRaw | Write-Host
  throw 'No se pudo generar el token de Gitea.'
}
$gtToken = ($gtRaw | Where-Object { $_ -and $_.Trim() } | Select-Object -Last 1).Trim()

# === Escribir mcp/.env =====================================================
$envPath = Join-Path $repo 'mcp\.env'
$envBody = @"
# Generado por mcp/scripts/setup-agents.ps1 el $(Get-Date -Format 'yyyy-MM-dd HH:mm').
# Valores REALES del componente MCP. NO versionar (ver mcp/.gitignore).

# --- OpenProject (servidor MCP custom) ---
OPENPROJECT_BASE_URL=http://host.docker.internal:8080
OPENPROJECT_API_TOKEN=$opToken
OPENPROJECT_NOTIFY=false
OPENPROJECT_HOST_HEADER=localhost:8080

# --- Gitea (servidor MCP oficial gitea-mcp) ---
GITEA_HOST=http://host.docker.internal:3000
GITEA_ACCESS_TOKEN=$gtToken
"@
# WriteAllText escribe UTF-8 sin BOM (un BOM rompe la primera variable que lea docker).
[System.IO.File]::WriteAllText($envPath, $envBody)

# === Resumen ===============================================================
Write-Host ''
Write-Host '== Listo ==' -ForegroundColor Green
Write-Host "mcp/.env escrito: $envPath"
if (-not $opToken) {
  Write-Host 'OPENPROJECT_API_TOKEN quedo VACIO en mcp/.env: rellenalo a mano.' -ForegroundColor Yellow
}
Write-Host ''
Write-Host 'Contrasenas de los usuarios de servicio (guardalas en tu gestor de secretos;'
Write-Host 'las necesitas solo para entrar por la UI):'
Write-Host "  OpenProject  mcp-agent : $opPassword"
Write-Host "  Gitea        mcp-agent : $gtPassword"
Write-Host ''
Write-Host 'Siguiente paso: verifica los servidores MCP con'
Write-Host '  mcp\scripts\smoke-test.ps1'
