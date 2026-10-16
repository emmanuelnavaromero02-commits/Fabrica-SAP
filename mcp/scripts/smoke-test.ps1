#requires -version 5
<#
.SYNOPSIS
  Verifica que los dos servidores MCP arrancan, hablan el protocolo MCP y
  alcanzan sus APIs. Es la comprobacion de la fase de verificacion.

.DESCRIPTION
  OpenProject MCP : levanta la imagen como Claude Code lo haria, le envia el
                    handshake MCP por stdio (initialize -> tools/list ->
                    tools/call whoami) y comprueba las respuestas.
  Gitea MCP       : mismo handshake (initialize -> tools/list) para confirmar
                    que el binario arranca y expone herramientas.
  Token de Gitea  : llamada directa a /api/v1/user para confirmar que el
                    token alcanza la API de Gitea.

  Requiere: el stack local arriba, la imagen reqplatform-openproject-mcp
  construida y mcp/.env con tokens validos (lo deja setup-agents.ps1).
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
# UTF-8 sin BOM al enviar datos por stdin a comandos nativos: un BOM
# corromperia el primer mensaje JSON-RPC que recibe el servidor MCP.
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$repo    = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$envPath = Join-Path $repo 'mcp\.env'
$opImage = 'reqplatform-openproject-mcp:0.1.0'
$gtImage = 'gitea/gitea-mcp-server:1.3.0'
$ok = $true

if (-not (Test-Path $envPath)) {
  throw "No existe mcp/.env. Ejecuta primero mcp\scripts\setup-agents.ps1."
}

# Parsea mcp/.env a una tabla hash.
$envVars = @{}
foreach ($line in Get-Content $envPath) {
  $t = $line.Trim()
  if (-not $t -or $t.StartsWith('#')) { continue }
  $i = $t.IndexOf('=')
  if ($i -lt 1) { continue }
  $envVars[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim()
}

# Habla el protocolo MCP con un servidor en contenedor: envia los mensajes
# JSON-RPC por stdin y devuelve las respuestas parseadas.
#
# Mantiene stdin ABIERTO hasta haber recibido ExpectResponses respuestas: si
# se cerrara stdin justo despues de escribir, el EOF apagaria el servidor y
# cancelaria las llamadas a herramientas que aun esten en vuelo (las que
# hacen I/O de red). Escribe los bytes ASCII directo al BaseStream para
# evitar el preambulo BOM del StreamWriter por defecto.
function Invoke-McpStdio {
  param(
    [string[]]$DockerArgs,
    [string[]]$Messages,
    [int]$ExpectResponses,
    [int]$TimeoutSec = 45
  )
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = 'docker'
  $psi.Arguments = (@('run', '-i', '--rm',
                      '--add-host', 'host.docker.internal:host-gateway',
                      '--env-file', $envPath) + $DockerArgs) -join ' '
  $psi.RedirectStandardInput  = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError  = $true
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow  = $true
  $proc = [System.Diagnostics.Process]::Start($psi)
  $errTask = $proc.StandardError.ReadToEndAsync()

  # Se antepone una linea vacia: el StreamWriter de stdin del proceso hijo
  # emite un BOM UTF-8 al inicio que corromperia el primer mensaje. La linea
  # vacia absorbe el BOM (el servidor MCP descarta la linea invalida y sigue);
  # asi 'initialize' y el resto llegan limpios.
  $payload = "`n" + ($Messages -join "`n") + "`n"
  $bytes = [System.Text.Encoding]::ASCII.GetBytes($payload)
  $proc.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
  $proc.StandardInput.BaseStream.Flush()

  $parsed = @()
  $stdinClosed = $false
  $pending = $proc.StandardOutput.ReadLineAsync()
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    if (-not $pending.Wait(500)) { continue }
    $line = $pending.Result
    if ($null -eq $line) { break }   # EOF de stdout
    $s = "$line".Trim()
    if ($s.StartsWith('{')) {
      try {
        $obj = $s | ConvertFrom-Json
        $parsed += $obj
        $respCount = ($parsed | Where-Object { $null -ne $_.id }).Count
        if ($respCount -ge $ExpectResponses -and -not $stdinClosed) {
          $proc.StandardInput.Close()   # ya tenemos todo: deja que el server cierre
          $stdinClosed = $true
        }
      } catch { }
    }
    $pending = $proc.StandardOutput.ReadLineAsync()
  }
  if (-not $stdinClosed) { try { $proc.StandardInput.Close() } catch { } }
  if (-not $proc.WaitForExit(5000)) { try { $proc.Kill() } catch { } }
  if ($parsed.Count -eq 0) {
    Write-Host '  (sin respuestas; stderr del contenedor:)' -ForegroundColor DarkGray
    ($errTask.Result -split "`n" | Select-Object -Last 8) |
      ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
  }
  return ,$parsed
}

$initialize = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke-test","version":"1.0"}}}'
$initialized = '{"jsonrpc":"2.0","method":"notifications/initialized"}'
$toolsList  = '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# === OpenProject MCP =======================================================
Write-Host '== OpenProject MCP ==' -ForegroundColor Cyan
$haveOpImage = (docker images --format '{{.Repository}}:{{.Tag}}') -contains $opImage
if (-not $haveOpImage) {
  Write-Host "  FALLO: la imagen $opImage no existe. Construyela con:" -ForegroundColor Red
  Write-Host "    docker build -t $opImage mcp\openproject"
  $ok = $false
}
else {
  $callWhoami = '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"whoami","arguments":{}}}'
  $resp = Invoke-McpStdio -DockerArgs @($opImage) -ExpectResponses 3 `
    -Messages @($initialize, $initialized, $toolsList, $callWhoami)

  $r1 = $resp | Where-Object { $_.id -eq 1 } | Select-Object -First 1
  $r2 = $resp | Where-Object { $_.id -eq 2 } | Select-Object -First 1
  $r3 = $resp | Where-Object { $_.id -eq 3 } | Select-Object -First 1

  if ($r1 -and $r1.result) {
    Write-Host "  OK   handshake: servidor '$($r1.result.serverInfo.name)'" -ForegroundColor Green
  } else { Write-Host '  FALLO: sin respuesta a initialize' -ForegroundColor Red; $ok = $false }

  if ($r2 -and $r2.result) {
    Write-Host "  OK   tools/list: $($r2.result.tools.Count) herramientas expuestas" -ForegroundColor Green
  } else { Write-Host '  FALLO: sin respuesta a tools/list' -ForegroundColor Red; $ok = $false }

  if ($r3 -and $r3.result -and -not $r3.result.isError) {
    $txt = ($r3.result.content | Where-Object { $_.type -eq 'text' } | Select-Object -First 1).text
    Write-Host '  OK   whoami: el MCP alcanza la API de OpenProject' -ForegroundColor Green
    Write-Host "       $txt"
  }
  elseif ($r3 -and $r3.result.isError) {
    $txt = ($r3.result.content | Select-Object -First 1).text
    Write-Host "  FALLO: whoami devolvio error: $txt" -ForegroundColor Red; $ok = $false
  }
  else { Write-Host '  FALLO: sin respuesta a tools/call whoami' -ForegroundColor Red; $ok = $false }
}

# === Gitea MCP =============================================================
Write-Host ''
Write-Host '== Gitea MCP ==' -ForegroundColor Cyan
$resp = Invoke-McpStdio -DockerArgs @($gtImage) -ExpectResponses 2 `
  -Messages @($initialize, $initialized, $toolsList)
$g1 = $resp | Where-Object { $_.id -eq 1 } | Select-Object -First 1
$g2 = $resp | Where-Object { $_.id -eq 2 } | Select-Object -First 1
if ($g1 -and $g1.result) {
  Write-Host "  OK   handshake: servidor '$($g1.result.serverInfo.name)'" -ForegroundColor Green
} else { Write-Host '  FALLO: el Gitea MCP no respondio a initialize' -ForegroundColor Red; $ok = $false }
if ($g2 -and $g2.result) {
  Write-Host "  OK   tools/list: $($g2.result.tools.Count) herramientas expuestas" -ForegroundColor Green
} else { Write-Host '  FALLO: sin respuesta a tools/list' -ForegroundColor Red; $ok = $false }

# === Token de Gitea contra la API ==========================================
Write-Host ''
Write-Host '== Token de Gitea -> API ==' -ForegroundColor Cyan
$gtToken = $envVars['GITEA_ACCESS_TOKEN']
if (-not $gtToken -or $gtToken -eq 'CHANGE_ME') {
  Write-Host '  FALLO: GITEA_ACCESS_TOKEN no esta definido en mcp/.env' -ForegroundColor Red
  $ok = $false
}
else {
  try {
    $u = Invoke-RestMethod -Uri 'http://localhost:3000/api/v1/user' `
      -Headers @{ Authorization = "token $gtToken" }
    Write-Host "  OK   /api/v1/user -> '$($u.login)' (el token alcanza la API)" -ForegroundColor Green
  }
  catch {
    Write-Host "  FALLO: el token de Gitea no validó: $($_.Exception.Message)" -ForegroundColor Red
    $ok = $false
  }
}

Write-Host ''
if ($ok) {
  Write-Host 'VERIFICACION OK: ambos servidores MCP estan operativos.' -ForegroundColor Green
  exit 0
}
else {
  Write-Host 'VERIFICACION CON FALLOS: revisa los mensajes anteriores.' -ForegroundColor Red
  exit 1
}
