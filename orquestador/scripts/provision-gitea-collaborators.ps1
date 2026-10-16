# Provisiona acceso a Gitea (repo de la fábrica) para los roles que trabajan
# el código/artefactos. Gitea auto-crea la cuenta en el 1er login SSO (OAuth2
# Keycloak ya configurado); este script da el PERMISO de colaborador DESPUÉS
# (mismo patrón que OP). Idempotente.
#
# Acceso (configurable en $rolePerm): swf-desarrollador/swf-tester = write,
# swf-project-manager/project-manager = read. Repo: mcp-agent/fabrica.
#
# Requiere: token Gitea con admin del repo (owner mcp-agent o admin) en
# $env:GITEA_API_TOKEN o el default de abajo; KC admin para listar usuarios.

param(
  [string]$Repo = "mcp-agent/fabrica",
  [string]$GiteaBase = "http://localhost:3000",
  [string]$GiteaToken = "f1987bbd462ece11e8c33151bac55e854e7dbf7d",
  [string]$KcBase = "http://localhost:8090",
  [string]$KcUser = "admin",
  [string]$KcPass = "Admin-Local-2026!"
)
$ErrorActionPreference = "Stop"

# rol Keycloak -> permiso Gitea
$rolePerm = @{
  "swf-desarrollador"   = "write"
  "swf-tester"          = "write"
  "swf-project-manager" = "read"
  "project-manager"     = "read"
}

$kcTok = (Invoke-RestMethod -Uri "$KcBase/realms/master/protocol/openid-connect/token" -Method POST -Body @{ client_id="admin-cli"; username=$KcUser; password=$KcPass; grant_type="password" } -ContentType "application/x-www-form-urlencoded").access_token
$kcH = @{ Authorization = "Bearer $kcTok" }

# username -> permiso (write gana sobre read si tiene varios roles)
$targets = @{}
foreach ($role in $rolePerm.Keys) {
  $perm = $rolePerm[$role]
  try { $users = Invoke-RestMethod -Uri "$KcBase/admin/realms/fabrica/roles/$role/users?max=200" -Headers $kcH } catch { $users = @() }
  foreach ($u in $users) {
    $un = $u.username
    if (-not $targets.ContainsKey($un)) { $targets[$un] = $perm }
    elseif ($targets[$un] -eq "read" -and $perm -eq "write") { $targets[$un] = "write" }
  }
}
"Objetivo: $($targets.Count) usuarios con rol de acceso a Gitea"

$gH = @{ Authorization = "token $GiteaToken"; "Content-Type" = "application/json" }
$prov = 0; $pend = 0
foreach ($un in ($targets.Keys | Sort-Object)) {
  $perm = $targets[$un]
  # existe en Gitea? (se crea al primer login SSO)
  try { Invoke-RestMethod -Uri "$GiteaBase/api/v1/users/$un" -Headers @{Authorization="token $GiteaToken"} | Out-Null }
  catch { "PENDIENTE $un ($perm): sin cuenta Gitea aún (login SSO la crea; re-correr)"; $pend++; continue }
  try {
    Invoke-WebRequest -Uri "$GiteaBase/api/v1/repos/$Repo/collaborators/$un" -Method PUT -Headers $gH -Body (@{ permission = $perm } | ConvertTo-Json) -UseBasicParsing | Out-Null
    "OK $un -> $perm en $Repo"; $prov++
  } catch {
    "ERR $un : HTTP $($_.Exception.Response.StatusCode.value__)"
  }
}
"PROVISION_GITEA provisionados=$prov pendientes=$pend de $($targets.Count)"
