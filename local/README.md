# Despliegue local — OpenProject + Gitea (Docker Desktop)

Stack local para **validar la capa de aplicación** antes del despliegue en AWS.
El `docker-compose.yml` y las variables se reutilizan casi tal cual en la EC2;
lo único que cambia allá es el reverse proxy (Caddy + TLS) y los volúmenes (EBS).

## Componentes

| Servicio   | Imagen                          | Puerto host        | Función                                  |
|------------|---------------------------------|--------------------|-------------------------------------------|
| `web`      | `openproject/openproject:17-slim` | `127.0.0.1:8080` | OpenProject CE (UI/API)                   |
| `worker`   | `openproject/openproject:17-slim` | —                | Jobs en segundo plano                     |
| `cron`     | `openproject/openproject:17-slim` | —                | Tareas programadas                        |
| `seeder`   | `openproject/openproject:17-slim` | —                | Migración + seed (corre una vez y termina)|
| `db`       | `postgres:17`                   | —                  | Base de datos de OpenProject              |
| `cache`    | `memcached:1.6-alpine`          | —                  | Caché de OpenProject                      |
| `gitea`    | `gitea/gitea:1.26`              | `127.0.0.1:3000` / `127.0.0.1:2222` (SSH) | Control de versiones |
| `gitea-db` | `postgres:17`                   | —                  | Base de datos de Gitea                    |

Los puertos se publican solo en `127.0.0.1` (loopback): accesibles desde esta
máquina, no desde la red.

### Diferencias respecto al compose oficial de OpenProject

Tomado de `opf/openproject-deploy` (rama `stable/17`), con estas omisiones para
el entorno local:

- **`proxy`** — el reverse proxy propio de OpenProject. En local se publica `web`
  directamente; en AWS lo sustituye Caddy.
- **`autoheal`** — reinicia contenedores no sanos montando el socket de Docker.
  Innecesario en local.
- **`hocuspocus`** — servidor de edición colaborativa en tiempo real. Necesita
  enrutado por el proxy (`wss://host/hocuspocus`), así que sin proxy no es
  alcanzable. La edición colaborativa se valida en AWS; el resto de OpenProject
  funciona con normalidad sin él.

PostgreSQL se fija en la **major 17** (no la 18) porque el `.env.example` oficial
de OpenProject 17 establece `POSTGRES_VERSION=17` para instalaciones nuevas.

## Requisitos

- Docker Desktop en ejecución (probado con Docker 29.x / Compose v5).
- Recomendado: asignar a Docker Desktop **≥ 6 GB de RAM** (OpenProject corre
  cuatro procesos Rails: `web`, `worker`, `cron`, `seeder`).
- Puertos libres en el host: `8080`, `3000`, `2222`.

## Arranque

```powershell
cd local
Copy-Item .env.example .env   # solo la primera vez; luego edita los secretos
docker compose up -d
```

> El `.env` de este directorio ya viene con secretos generados para uso local.
> Si partes de `.env.example`, rellena `SECRET_KEY_BASE` y las contraseñas.

El **primer arranque tarda varios minutos**: descarga de imágenes + el `seeder`
ejecuta migraciones y carga datos iniciales. `web`/`worker`/`cron` no arrancan
hasta que `seeder` termina con éxito.

Seguir el progreso:

```powershell
docker compose ps
docker compose logs -f seeder   # Ctrl+C para salir
docker compose logs -f web
```

`web` está listo cuando `docker compose ps` lo muestra como `healthy`.

## Acceso

| Aplicación  | URL                       | Credenciales iniciales                      |
|-------------|---------------------------|---------------------------------------------|
| OpenProject | http://localhost:8080     | `admin` / `admin` — **cambiar al primer login** |
| Gitea       | http://localhost:3000     | Sin usuario aún (ver siguiente paso)        |

### Crear el administrador de Gitea

El auto-registro está deshabilitado, así que el primer admin se crea por CLI
(usa los valores `GITEA_ADMIN_*` del `.env`):

```powershell
docker compose exec -u git gitea gitea admin user create `
  --admin --username gitadmin --email rodolfo.valdivia@gmail.com `
  --password 'bea9405b8f3408f0f35520d5' --must-change-password=false
```

Luego inicia sesión en http://localhost:3000 con ese usuario.

## Probar SSH de Git (puerto 2222)

Tras crear un repo en Gitea y cargar tu clave pública SSH en el perfil:

```powershell
git clone ssh://git@localhost:2222/gitadmin/<repo>.git
```

El puerto `2222` se anuncia en las URLs de clonado vía `GITEA__server__SSH_PORT`.

## Operación

```powershell
docker compose ps                 # estado de los contenedores
docker compose logs -f <servicio> # logs en vivo
docker compose stop               # detener sin borrar datos
docker compose start              # reanudar
docker compose down               # eliminar contenedores (los volúmenes persisten)
```

### Borrado total (incluye datos)

```powershell
docker compose down -v            # ELIMINA tambien los volumenes: se pierde todo
```

## Volúmenes (persistencia)

| Volumen          | Contenido                              |
|------------------|-----------------------------------------|
| `op_pgdata`      | Base de datos de OpenProject            |
| `op_assets`      | Adjuntos y archivos de OpenProject      |
| `gitea_data`     | Repositorios y configuración de Gitea   |
| `gitea_pgdata`   | Base de datos de Gitea                  |

Sobreviven a `docker compose down`; solo se borran con `down -v`.

## Solución de problemas

- **`web` no pasa a `healthy`**: el primer arranque es lento. Revisa
  `docker compose logs seeder` (debe haber terminado) y `docker compose logs web`.
- **Puerto ocupado** (`8080`/`3000`/`2222`): otro proceso lo usa. Libéralo o
  cambia el mapeo de puertos en `docker-compose.yml`.
- **OpenProject redirige a HTTPS o falla el login**: confirma que
  `OPENPROJECT_HTTPS` es `false` (así está en el compose para el entorno local).
- **Reinicio limpio**: `docker compose down -v` y `docker compose up -d`.

## Relación con el despliegue en AWS

Este stack valida OpenProject y Gitea. Para AWS (ver
`../despliegue-aws-openproject.md`) se añade:

- **Caddy** como reverse proxy con TLS automático (Let's Encrypt) — reemplaza la
  publicación directa de puertos; `OPENPROJECT_HTTPS` pasa a `true`.
- Persistencia en un volumen **EBS** en vez de volúmenes locales de Docker.
- Respaldos a **S3** y administración por **SSM**.
