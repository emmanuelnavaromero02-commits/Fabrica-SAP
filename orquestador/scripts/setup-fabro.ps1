#requires -version 5
<#
.SYNOPSIS
  Provisiona el usuario y rol "Fabro" en OpenProject con la frontera
  estructural del brief (8.4), actualiza orquestador/.env con el token nuevo
  y los estados del brief, y reconstruye el worker.

.DESCRIPTION
  Ejecuta scripts/create-fabro-agent.rb dentro del contenedor 'web' (rails
  runner). Captura el token de API recien creado y reescribe los campos
  pertinentes de orquestador/.env. Por ultimo, hace 'compose up -d --build'
  del worker y del webhook para que tomen el nuevo token y los nuevos
  estados.

  Idempotente: re-ejecutarlo no duplica usuarios, roles ni estados.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$localCompose = @('compose',
  '-f', (Join-Path $repo 'local\docker-compose.yml'),
  '--project-directory', (Join-Path $repo 'local'))
$orqCompose = @('compose',
  '-f', (Join-Path $repo 'orquestador\docker-compose.yml'),
  '--project-directory', (Join-Path $repo 'orquestador'))
$envPath = Join-Path $repo 'orquestador\.env'

# --- Generador de contrasenas fuertes (politica de OpenProject) -----------
$script:rng = New-Object System.Security.Cryptography.RNGCryptoServiceProvider
function Get-RandomChar([string]$Set) {
  $b = New-Object byte[] 1
  $script:rng.GetBytes($b)
  $Set[$b[0] % $Set.Length]
}
function New-StrongPassword([int]$Length = 24) {
  $lower='abcdefghijkmnpqrstuvwxyz'; $upper='ABCDEFGHJKLMNPQRSTUVWXYZ'
  $digit='23456789'; $special='!@#%^*-_=+'; $all=$lower+$upper+$digit+$special
  $chars = @((Get-RandomChar $lower),(Get-RandomChar $upper),
             (Get-RandomChar $digit),(Get-RandomChar $special))
  while ($chars.Count -lt $Length) { $chars += (Get-RandomChar $all) }
  ($chars | Sort-Object { $b=New-Object byte[] 1; $script:rng.GetBytes($b); $b[0] }) -join ''
}

# --- 1. Provisionar Fabro via Rails runner ---------------------------------
Write-Host '== Provisionando usuario/rol "Fabro" en OpenProject ==' -ForegroundColor Cyan
$null = docker @localCompose ps --status running --services
if ($LASTEXITCODE -ne 0) { throw 'El stack local no responde. Levantalo primero.' }

$password = New-StrongPassword
$railsScript = Get-Content (Join-Path $PSScriptRoot 'create-fabro-agent.rb') -Raw
$railsOut = $railsScript |
  docker @localCompose exec -T -e "FABRO_AGENT_PASSWORD=$password" `
    web bundle exec rails runner -
if ($LASTEXITCODE -ne 0) {
  $railsOut | Write-Host
  throw 'create-fabro-agent.rb fallo dentro del contenedor web.'
}
$railsOut | ForEach-Object { Write-Host "  $_" }

$tokenMatch = $railsOut | Select-String -Pattern '^TOKEN_API_VALUE=(.+)$'
$fabroToken = ''
if ($tokenMatch) { $fabroToken = $tokenMatch.Matches[0].Groups[1].Value.Trim() }
if (-not $fabroToken) {
  Write-Host '  AVISO: fabro-agent ya tenia token; no es recuperable.' -ForegroundColor Yellow
  Write-Host '         Para regenerarlo: borralo en Rails y re-ejecuta este script.' -ForegroundColor Yellow
}

# --- 2. Actualizar orquestador/.env ---------------------------------------
if (-not (Test-Path $envPath)) { throw "No existe $envPath" }
$envText = [System.IO.File]::ReadAllText($envPath)

function Set-EnvKey([string]$Text, [string]$Key, [string]$Value) {
  $pattern = "^${Key}=.*$"
  if ($Text -match "(?m)$pattern") {
    return [System.Text.RegularExpressions.Regex]::Replace(
      $Text, $pattern, "$Key=$Value", [System.Text.RegularExpressions.RegexOptions]::Multiline)
  }
  return $Text.TrimEnd("`r","`n") + "`n$Key=$Value`n"
}

if ($fabroToken) {
  $envText = Set-EnvKey $envText 'OPENPROJECT_API_TOKEN' $fabroToken
}
$envText = Set-EnvKey $envText 'OP_STATUS_ASSIGNED'              'Asignado a la fabrica'
$envText = Set-EnvKey $envText 'OP_STATUS_IN_REVIEW'             'En revision'
$envText = Set-EnvKey $envText 'OP_STATUS_IN_DEVELOPMENT'        'En desarrollo'
$envText = Set-EnvKey $envText 'OP_STATUS_CLIENT_OK'             'VoBo del cliente'
$envText = Set-EnvKey $envText 'OP_STATUS_AWAITING_CLARIFICATION' 'Esperando clarificacion'

[System.IO.File]::WriteAllText($envPath, $envText)
Write-Host '== orquestador/.env actualizado (token + estados del brief) ==' -ForegroundColor Cyan

# --- 3. Rebuild + restart de worker y webhook -----------------------------
Write-Host '== Reconstruyendo worker y webhook ==' -ForegroundColor Cyan
docker @orqCompose up -d --build worker webhook | Out-Host

Write-Host ''
Write-Host '== Listo ==' -ForegroundColor Green
Write-Host "Contrasena de fabro-agent (UI): $password"
Write-Host '  (Guardala en tu gestor de secretos.)'
Write-Host ''
Write-Host 'Verifica con:'
Write-Host '  - Consola Temporal:    http://localhost:8233'
Write-Host '  - Etapa de un run:     curl http://localhost:8000/factory/<id>'
