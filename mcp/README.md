# Componente MCP — OpenProject + Gitea

Conecta **Claude Code** con la plataforma (OpenProject + Gitea) vía dos
servidores [MCP](https://modelcontextprotocol.io), de modo que se pueda
gestionar requisitos, work packages y repositorios de forma conversacional.

Es **tooling del operador**, no parte del despliegue de la plataforma: los
servidores MCP corren en la máquina donde se ejecuta Claude Code y hablan con
OpenProject/Gitea por su API. No confundir con *Fabro* (fase posterior del
brief), que tiene su propio usuario de servicio y su propio contrato de
integración.

## Arquitectura

```
  Claude Code
      │  (stdio, protocolo MCP)
      ├──────────────► docker run  openproject-mcp   ──┐  API v3
      │                (servidor MCP custom, este repo)│  Basic apikey:token
      │                                                ▼
      └──────────────► docker run  gitea-mcp-server  ──┐  http://localhost:8080  (OpenProject)
                       (servidor MCP oficial de Gitea) │  http://localhost:3000  (Gitea)
                                                        ▼
                                              despliegue local de ../local
```

Claude Code descubre los servidores en [`.mcp.json`](../.mcp.json) (raíz del
repo) y los lanza como procesos stdio mediante `docker run -i`. Cada servidor
recibe su configuración por `--env-file mcp/.env`.

## Componentes

| Servidor | Imagen | Origen | Función |
|----------|--------|--------|---------|
| `openproject` | `reqplatform-openproject-mcp:0.1.0` | **Custom** (este repo, `openproject/`) | API v3 de OpenProject: proyectos, work packages, tiempo |
| `gitea` | `gitea/gitea-mcp-server:1.3.0` | **Oficial** de Gitea | Repos, issues, PRs, archivos, ramas, releases |

### Por qué un servidor custom para OpenProject

El MCP server oficial de OpenProject (introducido en OpenProject 17.2) es un
add-on **Enterprise** — no está en la Community Edition, que es la que
despliega el brief. El servidor de `openproject/server.py` usa la **API v3
pública** (autenticación Basic con usuario `apikey` y el token como
contraseña), así que funciona en CE. Gitea, en cambio, publica un MCP server
oficial y gratuito: ahí no tiene sentido reconstruir nada.

## Estructura

```
.mcp.json                   # raiz del repo: config de los servidores MCP
mcp/
  README.md                 # este archivo
  .env.example              # plantilla de variables
  .env                      # tokens reales (NO se versiona; lo crea setup-agents)
  .gitignore
  openproject/
    server.py               # servidor MCP custom (FastMCP + httpx)
    requirements.txt
    Dockerfile
    .dockerignore
  scripts/
    create-op-agent.rb      # rails runner: usuario + rol + token en OpenProject
    setup-agents.ps1        # crea usuarios/tokens de servicio y escribe mcp/.env
    smoke-test.ps1          # verifica que ambos servidores MCP funcionan
```

## Requisitos

- Docker Desktop en ejecución.
- El stack local de [`../local`](../local) **arriba y sano** (`docker compose
  ps` muestra `web` y `gitea` como `healthy`/`running`).
- Claude Code, abierto desde la raíz del repositorio.

## Puesta en marcha

Tres pasos, en orden:

### 1. Construir la imagen del MCP de OpenProject

```powershell
docker build -t reqplatform-openproject-mcp:0.1.0 mcp\openproject
```

La imagen de Gitea (`gitea/gitea-mcp-server:1.3.0`) se descarga sola la
primera vez que Claude Code la use.

### 2. Crear los usuarios y tokens de servicio

```powershell
mcp\scripts\setup-agents.ps1
```

Crea, **con mínimo privilegio** (ver [Seguridad](#seguridad)):

- OpenProject — usuario `mcp-agent` (no admin), rol de proyecto *Agente MCP*,
  membresía en los proyectos existentes y un token de API.
- Gitea — usuario `mcp-agent` (no admin) y un token con scopes acotados.

Y deja escrito `mcp/.env` con ambos tokens. Guarda las contraseñas que el
script imprime: solo se necesitan para entrar por la UI (p. ej. a regenerar
un token).

### 3. Reconectar Claude Code

Reinicia Claude Code (o vuelve a cargar los servidores MCP) para que tome
`.mcp.json` y el `mcp/.env` recién escrito. Verifica con:

```powershell
mcp\scripts\smoke-test.ps1
```

## Apuntar a AWS

Cuando OpenProject y Gitea estén desplegados en AWS, solo cambia `mcp/.env`:

```ini
OPENPROJECT_BASE_URL=https://op.epiuse.lat
OPENPROJECT_HOST_HEADER=
GITEA_HOST=https://git.epiuse.lat
```

`OPENPROJECT_HOST_HEADER` se **vacía**: en AWS la URL pública ya coincide con
el `host_name` de OpenProject, así que no hay que forzar la cabecera `Host`
(en local sí, porque se alcanza por `host.docker.internal`).

Regenera además los tokens contra esa instancia (los usuarios de servicio son
por instancia). `.mcp.json` y las imágenes no cambian. Con HTTPS público no
hace falta `host.docker.internal`, pero dejarlo en `--add-host` es inocuo.

## Herramientas del MCP de OpenProject

El servidor custom expone 14 herramientas sobre la API v3:

| Herramienta | Qué hace |
|-------------|----------|
| `whoami` | Usuario autenticado (verifica token y conectividad) |
| `list_projects` | Lista proyectos |
| `get_project` | Detalle de un proyecto |
| `create_project` | Crea un proyecto |
| `list_types` | Tipos de work package (Task, Requisito, ...) |
| `list_statuses` | Estados de work package |
| `list_users` | Usuarios/grupos (ids para asignación) |
| `list_work_packages` | Lista work packages con filtros |
| `get_work_package` | Detalle de un work package |
| `create_work_package` | Crea un work package |
| `update_work_package` | Actualiza un work package (gestiona `lockVersion`) |
| `add_comment` | Comenta un work package |
| `list_time_entries` | Lista registros de tiempo |
| `log_time` | Registra tiempo sobre un work package |

`type` y `status` aceptan **nombre o id** indistintamente. `update_work_package`
obtiene el `lockVersion` actual automáticamente.

Las herramientas de Gitea (45+: repos, issues, PRs, archivos, ramas, releases,
acciones, wiki, organizaciones) las provee el servidor oficial; ver
[gitea.com/gitea/gitea-mcp](https://gitea.com/gitea/gitea-mcp).

## Seguridad

- **Mínimo privilegio.** Ninguno de los dos usuarios de servicio es
  administrador.
  - OpenProject: el rol *Agente MCP* tiene permisos de ciclo de vida de work
    packages y de tiempo, pero **no** borra work packages ni gestiona
    miembros, versiones o la configuración del proyecto.
  - Gitea: el token se limita a los scopes `read:user`, `write:repository` y
    `write:issue`. Sin scopes de administración ni de organización.
- **Secretos fuera del repositorio.** Los tokens viven solo en `mcp/.env`,
  que está en `.gitignore`. `.env.example` lleva únicamente placeholders.
- Los servidores MCP no exponen puertos: son procesos stdio efímeros
  (`docker run --rm`), uno por invocación.
- Para acotar aún más las transiciones de estado del agente en OpenProject,
  edita los workflows del rol *Agente MCP* en *Administración → Work packages
  → Estados*.

## Operación

**Regenerar un token de OpenProject.** El valor en claro solo se ve al
crearlo. Para rotarlo, dentro del contenedor `web`:

```powershell
docker compose -f local\docker-compose.yml --project-directory local exec -T web `
  bundle exec rails runner "Token::API.joins(:user).where(users: {login: 'mcp-agent'}).destroy_all"
```

y vuelve a ejecutar `setup-agents.ps1` (es idempotente: no duplica el usuario
ni el rol, solo crea el token nuevo).

**Regenerar un token de Gitea.** Genera otro y actualiza `mcp/.env`:

```powershell
docker compose -f local\docker-compose.yml --project-directory local exec -u git gitea `
  gitea admin user generate-access-token --username mcp-agent `
  --token-name claude-mcp-nuevo --scopes "read:user,write:repository,write:issue" --raw
```

## Solución de problemas

- **Claude Code no carga los servidores MCP** — confirma que `mcp/.env`
  existe y que Claude Code se abrió desde la raíz del repo (`--env-file
  mcp/.env` es una ruta relativa a ese directorio).
- **`whoami` falla con HTTP 401** — el `OPENPROJECT_API_TOKEN` de `mcp/.env`
  es inválido o se revocó; regenéralo.
- **`whoami` falla con HTTP 400 "Invalid host_name configuration"** —
  OpenProject rechaza la cabecera `Host`. En local, `OPENPROJECT_HOST_HEADER`
  debe valer `localhost:8080`; en AWS debe estar vacío.
- **`whoami` falla con error de conexión** — el stack local no está arriba, o
  `OPENPROJECT_BASE_URL` no usa `host.docker.internal` (desde el contenedor
  del MCP, `localhost` es el propio contenedor, no el host).
- **`docker build` o `docker run` fallan** — Docker Desktop no está en
  ejecución.
- **El servidor MCP de OpenProject responde "Faltan variables de entorno"** —
  `mcp/.env` no se está pasando; revisa la ruta de `--env-file` en
  `.mcp.json`.
