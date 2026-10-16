<#
  Instala las skills ABAP de la fábrica en el Claude Code del desarrollador.
  Las copia a las "personal skills" (~/.claude/skills) -> disponibles en TODOS
  los repos de requerimiento que el dev clone.

  Uso (PowerShell):
      ./install-skills.ps1            # instala/actualiza ambas
      ./install-skills.ps1 -Force     # sobreescribe sin preguntar

  Requiere: Claude Code instalado. No necesita permisos de admin.
#>
[CmdletBinding()]
param([switch]$Force)

$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
$dest = Join-Path $HOME ".claude\skills"
$skills = @("sap-abap-classic", "sap-abap-cloud")

New-Item -ItemType Directory -Force -Path $dest | Out-Null
Write-Host "Instalando skills en: $dest" -ForegroundColor Cyan

foreach ($s in $skills) {
    $from = Join-Path $src $s
    $to = Join-Path $dest $s
    if (-not (Test-Path (Join-Path $from "SKILL.md"))) {
        Write-Warning "No encontre $s/SKILL.md en $src — omito."
        continue
    }
    if ((Test-Path $to) -and -not $Force) {
        $r = Read-Host "  '$s' ya existe. Sobreescribir? (s/N)"
        if ($r -notmatch '^[sSyY]') { Write-Host "  omitido: $s"; continue }
    }
    if (Test-Path $to) { Remove-Item -Recurse -Force $to }
    Copy-Item -Recurse -Force $from $to
    Write-Host "  instalado: $s" -ForegroundColor Green
}

Write-Host ""
Write-Host "Listo. Abre tu repo de requerimiento con Claude Code y verifica con /skills." -ForegroundColor Cyan
Write-Host "Claude Code elige la skill correcta segun el contexto (classic vs cloud/Fiori)." -ForegroundColor DarkGray
