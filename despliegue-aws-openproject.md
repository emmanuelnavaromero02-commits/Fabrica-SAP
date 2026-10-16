# Brief de despliegue en AWS — Plataforma de gestión de requisitos

## 1. Para Claude Code: tu rol

Este documento es tu especificación de trabajo. Vas a ayudar a desplegar en una
cuenta de AWS la plataforma descrita aquí.

Reglas de trabajo:

- **Antes de generar infraestructura, confirma con el usuario las "Decisiones
  pendientes" (sección 5).** No asumas valores; pregúntalos.
- Trabaja en el orden de la sección 9: infraestructura primero, capa de
  aplicación después, validación al final.
- Produce **código real** —Terraform para AWS, Docker Compose para la capa de
  aplicación— no solo instrucciones.
- Usa el AWS CLI ya configurado en el entorno. Verifica `aws sts
  get-caller-identity` antes de empezar y muestra al usuario la cuenta/identidad
  para que confirme que es la correcta.
- No hardcodees versiones de imágenes que no puedas verificar: consulta el
  registry y fija la última versión estable mayor.
- No crees recursos destructivos ni los apliques sin que el usuario confirme el
  `terraform plan`.

## 2. Contexto

Plataforma interna para gestión de requisitos, validación de especificaciones y
prototipos, y seguimiento de pruebas y entregas. La usan consultores
funcionales, la fábrica de software y clientes externos.

Arquitectura de dos planos:

- **Plano de control:** OpenProject — requisitos, workflow, aprobaciones,
  trazabilidad, backlog, sprints.
- **Control de versiones:** Gitea — especificaciones, scripts de prueba,
  evidencia de pruebas y artefactos de desarrollo (incluido espejo `abapGit`).

Una fase posterior integrará un motor de orquestación de agentes ("Fabro") vía
la API y los webhooks de OpenProject. **Queda fuera del alcance de este
despliegue**, pero la arquitectura debe dejar margen de cómputo y red para
añadirlo más adelante (sea en la misma instancia o en cómputo aparte dentro de
la misma VPC).

## 3. Alcance v1

Incluye:

- Despliegue de OpenProject Community Edition, Gitea y Caddy en AWS.
- Infraestructura como código (Terraform) y capa de aplicación (Docker Compose).
- TLS automático, DNS, respaldos a S3 y acceso de administración por SSM.

Excluye (fases posteriores):

- Motor Fabro y agentes de IA.
- Alta disponibilidad multi-AZ y autoescalado.
- Integración CI/CD para correr los scripts de prueba (se evaluará después).

## 4. Arquitectura objetivo

Modelo recomendado para v1: **una instancia EC2 con Docker Compose**.

- Una EC2 ejecuta tres contenedores: OpenProject CE, Gitea y Caddy (reverse
  proxy con TLS automático vía Let's Encrypt).
- Persistencia en un volumen EBS `gp3` dedicado y montado aparte del volumen
  raíz, para poder respaldarlo y redimensionarlo sin tocar el sistema.
- Administración del host por **SSM Session Manager** (sin puerto SSH abierto a
  internet).
- Respaldos lógicos a un bucket S3; snapshots del EBS vía AWS Backup.
- DNS en Route 53 (o registros A manuales si la zona vive fuera de AWS).

Razón: es el modelo más eficiente en costo y operación para una herramienta
interna de un equipo acotado. Si la plataforma crece, la ruta de evolución es
mover la base de datos a Amazon RDS y los contenedores a ECS Fargate detrás de
un ALB — la capa Docker Compose se traduce a ECS sin reescribir la solución.

## 5. Decisiones pendientes — confirmar antes de generar IaC

Pregunta estas decisiones al usuario y registra sus respuestas antes de escribir
Terraform:

| # | Decisión | Opción por defecto sugerida |
|---|----------|------------------------------|
| 1 | Región de AWS | `sa-east-1` (São Paulo) o `us-east-1` según latencia/costo |
| 2 | Subdominios | `op.epiuse.lat` (OpenProject) y `git.epiuse.lat` (Gitea) |
| 3 | Tamaño de instancia | `t3.large` mínimo; `t3.xlarge` recomendado (OpenProject consume memoria) |
| 4 | Acceso de red | CIDR(s) permitidos en 80/443; ¿abierto a internet con auth fuerte o restringido a IPs corporativas/VPN? |
| 5 | Base de datos | Postgres en contenedor (v1, menor costo) vs Amazon RDS (producción) |
| 6 | Zona DNS | ¿La zona `epiuse.lat` está en Route 53 de esta cuenta? Si no, los registros A se crean manualmente |
| 7 | Tamaño inicial del EBS de datos | `gp3` 50 GB (redimensionable) |
| 8 | Etiquetado y centro de costo | Tags obligatorios para el análisis de costos |

## 6. Componentes — especificación técnica

### 6.1 OpenProject Community Edition

- Imagen: `openproject/openproject` — fija la última versión estable mayor
  (verifícala en el registry, no la asumas).
- Puerto interno: `80`; lo publica Caddy, no se expone directo.
- Volúmenes persistentes: directorio de adjuntos (`/var/openproject/assets`) y,
  si se usa Postgres en contenedor, el directorio de datos de la base.
- Variables clave: `OPENPROJECT_HOST__NAME`, `OPENPROJECT_HTTPS=true`,
  `OPENPROJECT_SECRET_KEY_BASE`, y `DATABASE_URL` si la base es externa (RDS).
- Incluye worker en segundo plano y caché; si usas el compose oficial de
  OpenProject como base, respétalo y solo añade Gitea y Caddy.

### 6.2 Gitea

- Imagen: `gitea/gitea` — última versión estable mayor.
- Puertos: HTTP `3000` (lo publica Caddy) y SSH de Git.
- **SSH de Git: usar el puerto host `2222`** para no chocar con el SSH del
  sistema; configurar `GITEA__server__SSH_PORT=2222` para que las URLs de clonado
  sean coherentes.
- Volumen persistente: `/data` (repositorios y configuración).
- Base de datos: SQLite es aceptable para un equipo pequeño en v1; Postgres si se
  prefiere consolidar con la decisión #5.
- Variables clave: `GITEA__server__DOMAIN`, `GITEA__server__ROOT_URL`,
  `GITEA__server__SSH_PORT`, `GITEA__database__*`.

### 6.3 Caddy

- Imagen: `caddy` — última versión estable mayor.
- Publica los puertos `80` y `443`; obtiene certificados TLS automáticamente vía
  Let's Encrypt (requiere DNS apuntando a la IP elástica antes de levantar).
- `Caddyfile` con dos bloques de sitio que hacen reverse proxy a
  `openproject:80` y `gitea:3000`.
- Volumen persistente para el almacén de certificados de Caddy.

## 7. Infraestructura AWS a crear (Terraform)

- **Red:** VPC con una subred pública (o uso de la VPC por defecto si el usuario
  lo prefiere), Internet Gateway y tabla de rutas.
- **Security Group:** entrante `443` y `80` desde los CIDR de la decisión #4;
  entrante `2222` (SSH de Git) desde los mismos CIDR; **sin** `22` abierto; sin
  puertos de base de datos expuestos.
- **EC2:** instancia con la AMI más reciente de Amazon Linux 2023, el tamaño de
  la decisión #3, perfil de instancia IAM adjunto, y `user-data` que instale
  Docker + Docker Compose y monte el volumen de datos.
- **EBS:** volumen `gp3` de datos (decisión #7), separado del raíz, montado en
  una ruta estable (p. ej. `/data`).
- **IP elástica** asociada a la instancia.
- **Route 53:** registros A para los dos subdominios apuntando a la IP elástica
  (solo si la zona está en esta cuenta — decisión #6).
- **IAM:** rol de instancia con la política administrada de SSM y permiso de
  escritura al bucket de respaldos S3 (mínimo privilegio).
- **S3:** bucket privado para respaldos, con versionado y cifrado activados, y
  una regla de ciclo de vida que expire respaldos antiguos.
- **AWS Backup** (o DLM): plan de snapshots del volumen EBS de datos.

## 8. Entregables que debes producir

```
infra/
  terraform/
    main.tf            # red, EC2, EBS, EIP, IAM, S3
    variables.tf       # parametrizado con las decisiones de la sección 5
    outputs.tf         # IP elástica, IDs, nombres DNS
    versions.tf
app/
  docker-compose.yml   # OpenProject + Gitea + Caddy
  Caddyfile
  .env.example         # plantilla; los secretos reales no se versionan
scripts/
  bootstrap.sh         # se ejecuta vía user-data: Docker, montaje de EBS
  backup.sh            # dump lógico + tar de repos -> S3
README.md              # runbook: desplegar, operar, restaurar, escalar
```

Reglas de los entregables:

- Los secretos (`OPENPROJECT_SECRET_KEY_BASE`, contraseñas) **no se versionan**;
  van en `.env` real en la instancia o en SSM Parameter Store. `.env.example`
  solo lleva placeholders.
- Terraform parametrizado: nada de valores quemados que correspondan a la
  sección 5.
- `README.md` debe permitir a otra persona desplegar, restaurar un respaldo y
  escalar la instancia sin contexto adicional.

## 9. Plan de despliegue por fases

1. **Preparación.** Verificar identidad AWS, región y que el dominio resuelve.
   Confirmar las decisiones de la sección 5.
2. **Infraestructura.** Escribir Terraform, mostrar `terraform plan` al usuario,
   aplicar tras su confirmación.
3. **DNS.** Asegurar que los subdominios apuntan a la IP elástica **antes** de
   levantar Caddy (el TLS automático lo necesita).
4. **Capa de aplicación.** Desplegar `docker-compose.yml` y el `Caddyfile` en la
   instancia; levantar los contenedores.
5. **Configuración inicial.** Primer login de administrador en OpenProject y en
   Gitea; cambiar credenciales por defecto; deshabilitar el registro abierto en
   Gitea.
6. **Respaldos y operación.** Programar `backup.sh` (cron) y validar que un
   respaldo llega a S3; activar el plan de AWS Backup.
7. **Validación.** Recorrer los criterios de aceptación de la sección 12.

## 10. Seguridad

- Acceso de administración al host solo por SSM Session Manager; sin puerto 22
  hacia internet.
- Security Group restrictivo: solo 80/443 y 2222 desde los CIDR autorizados.
- TLS en todo el tráfico de los usuarios (Caddy / Let's Encrypt).
- Secretos en `.env` con permisos restringidos o en SSM Parameter Store; nunca
  en el repositorio ni en `user-data`.
- Bucket S3 de respaldos privado, cifrado y versionado.
- Deshabilitar el auto-registro en Gitea; en OpenProject, configurar los roles
  (incluido el rol "Cliente" de acceso restringido) tras el primer arranque.

## 11. Respaldos y operación

- `backup.sh`: dump lógico de la base de OpenProject + `tar` del `/data` de
  Gitea + de los adjuntos de OpenProject, subidos a S3 con marca de fecha.
- AWS Backup: snapshots periódicos del volumen EBS de datos.
- El `README.md` debe documentar el procedimiento de **restauración** probado,
  no solo el de respaldo.
- Para escalar: detener contenedores, cambiar el tipo de instancia, redimensionar
  el EBS y volver a levantar — documentar estos pasos.

## 12. Criterios de aceptación

- OpenProject y Gitea responden por HTTPS en sus subdominios, con certificado
  válido.
- El login de administrador funciona en ambos; las credenciales por defecto
  fueron cambiadas.
- Se puede crear un proyecto de prueba en OpenProject y un repositorio de prueba
  en Gitea, y clonar ese repositorio por SSH en el puerto 2222.
- `backup.sh` corre y deposita un respaldo en el bucket S3.
- El acceso al host por SSM Session Manager funciona; no hay puerto 22 ni
  puertos de base de datos expuestos a internet.
- `terraform plan` no muestra cambios pendientes tras el despliegue (estado
  convergido).
- Todos los recursos llevan los tags de centro de costo de la decisión #8.


# Brief de despliegue en AWS — Fabro (motor de orquestación)

> Continuación de `despliegue-aws-openproject.md`. OpenProject y Gitea ya están
> desplegados en AWS. Este documento cubre la incorporación de Fabro.

## 1. Para Claude Code: tu rol

Este documento es tu especificación de trabajo para **contenerizar y desplegar
Fabro** e integrarlo con la plataforma OpenProject + Gitea que ya existe.

Reglas de trabajo:

- **El código de aplicación de Fabro y la definición de sus agentes los provee
  el equipo de 7vS.** Este brief NO especifica la lógica interna de Fabro.
  Especifica: cómo se contenedoriza, dónde se despliega, y el **contrato de
  integración** con OpenProject y Gitea.
- **Antes de generar nada, confirma con el usuario las "Decisiones pendientes"
  (sección 6).** En particular necesitas conocer el stack real de Fabro
  (lenguaje, runtime, gestor de dependencias, comando de arranque) para escribir
  un `Dockerfile` correcto.
- Reutiliza la infraestructura existente: misma VPC, mismo bucket de respaldos,
  mismo modelo de administración por SSM. No dupliques red ni recrees lo que ya
  está.
- Produce **código real**: `Dockerfile`, cambios al `docker-compose.yml`
  existente, `Caddyfile` actualizado, Terraform incremental si hace falta.
- No apliques cambios destructivos sobre la infraestructura existente sin mostrar
  antes el `terraform plan` / el diff del compose y obtener confirmación.

## 2. Contexto

Arquitectura de dos planos ya acordada:

- **Plano de control humano** — OpenProject (requisitos, workflow, aprobaciones,
  trazabilidad) + Gitea (control de versiones de especificaciones, prototipos,
  scripts de prueba, evidencia y artefactos). **Ya desplegado.**
- **Plano de fábrica automatizada (dark factory)** — Fabro: motor de
  orquestación que ejecuta agentes de IA para las etapas de la fábrica de
  software. **Es lo que este brief incorpora.**

Principio rector: **ejecución a oscuras, verificación encendida.** Fabro
automatiza el interior de la fábrica; las compuertas de aprobación (consultor
funcional y cliente) siguen siendo humanas. La integración debe **hacer cumplir**
esa frontera, no solo respetarla por convención (ver sección 8.4).

## 3. Qué hace Fabro en esta plataforma

Fabro escucha los cambios de estado de los work packages de OpenProject; cada
cambio relevante es un evento que dispara uno o más agentes. Los agentes
producen artefactos (especificaciones, prototipos, scripts de prueba, código,
evidencia) y los devuelven a la plataforma: commits en Gitea y actualizaciones
en OpenProject vía API.

Niveles de autonomía por etapa (acordados en el mapeo previo):

| Etapa | Autonomía | Disparador |
|-------|-----------|------------|
| Afinar especificación funcional | Asistida | Requisito asignado a la fábrica |
| Crear prototipo | Asistida | Requisito asignado a la fábrica |
| Generar script de pruebas | Autónoma | VoBo del cliente registrado |
| Andamiaje / boilerplate de desarrollo | Autónoma | Inicio de iteración de desarrollo |
| Lógica de negocio | Asistida | Inicio de iteración de desarrollo |
| Lógica compleja e integración | Humano con apoyo | (no disparada de forma autónoma) |
| Ejecutar pruebas unitarias | Autónoma | Iteración marcada lista para pruebas |
| Generar evidencia de pruebas | Autónoma | Tras ejecutar pruebas unitarias |

"Asistida" significa que el agente produce el borrador completo pero el
resultado queda en un estado de revisión que exige acción humana antes de
avanzar. "Autónoma" significa que el agente completa la etapa sin intervención.

## 4. Alcance v1

Incluye:

- `Dockerfile` para Fabro y su incorporación al stack Docker Compose existente.
- Endpoint de Fabro para recibir webhooks de OpenProject (publicado vía Caddy con
  TLS).
- Configuración del contrato de integración: webhooks de OpenProject, token de
  API de OpenProject, token y acceso Git de Gitea, claves de las APIs de IA.
- Creación del usuario/rol de servicio "Fabro" en OpenProject con permisos
  restringidos que **excluyen** las transiciones de aprobación.
- Secretos gestionados fuera del repositorio.

Excluye:

- La lógica interna de los agentes de Fabro (la provee el equipo de 7vS).
- Cualquier automatización de las compuertas humanas.
- CI/CD externo; si Fabro corre las pruebas, las corre dentro de su propio
  contenedor o invocando un runner — a definir en la decisión #5.

## 5. Arquitectura objetivo

Fabro se despliega **en la misma VPC** que OpenProject y Gitea. Dos opciones de
ubicación de cómputo (decisión #1):

- **A — Mismo EC2, mismo Docker Compose.** Fabro se añade como un servicio más
  al `docker-compose.yml` existente. Más simple de operar; requiere subir el
  tamaño de la instancia. Recomendado para v1 si la carga de agentes es moderada.
- **B — EC2 dedicado para Fabro** en la misma VPC y subred. Aísla la carga
  bursátil de los agentes (generación de código, ejecución de pruebas) del plano
  de control. Recomendado si la decisión #5 hace que Fabro ejecute builds o
  pruebas pesadas dentro de su contenedor.

En ambos casos:

- Fabro **no se expone directo a internet**. Solo su endpoint de webhooks se
  publica, vía Caddy, en una ruta dedicada con TLS.
- Fabro habla con OpenProject y Gitea **por la red privada de la VPC** (nombres
  de servicio del compose en la opción A; IP privada en la opción B).
- Fabro necesita **salida a internet por 443** hacia las APIs de IA
  (Anthropic, OpenAI, Perplexity). Ajustar el Security Group / NAT en
  consecuencia.

## 6. Decisiones pendientes — confirmar antes de generar nada

| # | Decisión | Nota |
|---|----------|------|
| 1 | Ubicación de cómputo: opción A (mismo EC2) u opción B (EC2 dedicado) | Define si tocas Terraform o solo el compose |
| 2 | Stack de Fabro: lenguaje, runtime, versión, gestor de dependencias, comando de arranque | Imprescindible para escribir el `Dockerfile` |
| 3 | Repositorio del código de Fabro | ¿Está en Gitea ya? ¿Rama a desplegar? |
| 4 | Estado/almacenamiento de Fabro: ¿usa base de datos, cola, sistema de archivos? | Define volúmenes persistentes y si necesita Postgres/Redis |
| 5 | Ejecución de pruebas: ¿Fabro corre las pruebas dentro de su contenedor, o las delega a un runner / al sistema SAP? | Afecta tamaño de instancia y permisos |
| 6 | Subdominio o ruta del endpoint de webhooks | p. ej. `fabro.epiuse.lat` o `op.epiuse.lat/webhooks/fabro` |
| 7 | Proveedores de IA realmente usados en v1 y sus modelos | Para provisionar solo las claves necesarias |
| 8 | Concurrencia de agentes | ¿Cuántos agentes en paralelo? Dimensiona CPU/memoria |
| 9 | Nombres exactos de los estados del workflow de OpenProject | El contrato de la sección 8 usa nombres lógicos; deben mapearse a los reales |

## 7. El contenedor de Fabro — especificación

- **`Dockerfile`** basado en el stack confirmado en la decisión #2. Imagen base
  oficial y delgada del runtime correspondiente; build multi-etapa si aplica
  para no arrastrar herramientas de compilación a la imagen final.
- **Puerto interno** para el endpoint de webhooks (p. ej. `8080`); no se publica
  directo, lo expone Caddy.
- **Volúmenes persistentes** según la decisión #4: directorio de trabajo donde
  los agentes clonan/construyen, y almacenamiento de estado si Fabro lo requiere.
- **Variables de entorno** (valores reales vía `.env` / SSM, nunca versionados):
  - `OPENPROJECT_BASE_URL`, `OPENPROJECT_API_TOKEN`
  - `OPENPROJECT_WEBHOOK_SECRET`
  - `GITEA_BASE_URL`, `GITEA_API_TOKEN`, `GITEA_SSH_KEY` (o token para HTTPS)
  - `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `PERPLEXITY_API_KEY` (solo las de la
    decisión #7)
  - Parámetros de concurrencia y de logging
- **Healthcheck** en el `docker-compose.yml` para que el stack reporte el estado
  de Fabro.
- **Política de reinicio** `unless-stopped`, igual que los demás servicios.
- Si Fabro ejecuta git (clonado, push), la imagen debe incluir `git` y la
  configuración de identidad de commits del usuario de servicio.

## 8. Contrato de integración

### 8.1 OpenProject → Fabro (entrada: webhooks)

- En OpenProject se configura un webhook que dispara en creación y actualización
  de work packages, apuntando a la ruta publicada de Fabro.
- El webhook se firma con `OPENPROJECT_WEBHOOK_SECRET`. **Fabro debe validar la
  firma de cada payload entrante y rechazar los que no la cumplan.** El endpoint
  de webhooks no ejecuta nada antes de validar la firma.
- Fabro filtra: solo actúa ante los cambios de estado mapeados en la sección 9;
  el resto los descarta.

### 8.2 Fabro → OpenProject (salida: API REST)

- Fabro usa la API REST de OpenProject con `OPENPROJECT_API_TOKEN` para:
  - Crear work packages (p. ej. el `Prototipo` hijo de un `Requisito`).
  - Cambiar el estado de un work package a los estados **operativos** (no a los
    de aprobación — ver 8.4).
  - Añadir comentarios y adjuntar evidencia.
- Toda acción de Fabro queda registrada con el usuario de servicio "Fabro", de
  modo que el historial del work package distingue lo hecho por agentes de lo
  hecho por personas — esto es la auditabilidad de la dark factory.

### 8.3 Fabro → Gitea (salida: API + git)

- Fabro escribe los artefactos versionables en Gitea: especificación
  (Markdown), assets del prototipo, scripts de prueba, evidencia en texto y
  artefactos de desarrollo.
- **Convención de trazabilidad:** todo commit que produzca Fabro debe citar el
  id del work package de OpenProject en el mensaje, con el formato `refs #<id>`.
  Esto enlaza cada commit con su requisito.
- **Convención de ramas:** una rama por iteración de desarrollo, con un nombre
  que incluya el id del requisito (p. ej. `req-<id>/iteracion-<n>`).
- Fabro no hace merge a la rama principal por su cuenta: la integración de una
  rama de iteración queda sujeta a revisión humana.

### 8.4 La frontera de las compuertas humanas — control obligatorio

Las transiciones de aprobación **no las puede ejecutar Fabro**. Esto se hace
cumplir a nivel de permisos, no por convención:

- El rol "Fabro" en OpenProject se configura **sin** permiso para transicionar
  work packages hacia los estados de aprobación: *Aprobación funcional*
  (consultor funcional), *VoBo del cliente* (cliente) y *Aprobación del
  desarrollo* (cliente).
- Si un agente intentara mover un work package a un estado de aprobación, la API
  de OpenProject debe rechazarlo por falta de permisos. La frontera es
  estructural.
- Fabro **reacciona** a esos estados (cuando un humano los fija, el webhook
  dispara la siguiente etapa) pero nunca los **produce**.

### 8.5 El bucle de rechazo

Cuando el cliente rechaza en *Aprobación del desarrollo*, no se reabre el work
package original. Un humano crea un **nuevo** `Requisito` en el backlog, enlazado
al original. Ese nuevo requisito vuelve a entrar por el flujo y, al ser asignado
a la fábrica, dispara a Fabro de nuevo con normalidad. Fabro no crea por sí mismo
el requisito de rework.

## 9. Mapeo evento → agente

Nombres de estado lógicos; mapéalos a los reales según la decisión #9.

| Evento (cambio de estado en OpenProject) | Agente(s) que Fabro dispara | Salida |
|------------------------------------------|------------------------------|--------|
| `Requisito` → *Asignado a la fábrica* | Agente de especificación + agente de prototipo (asistidos) | Borrador de spec a Gitea; `Prototipo` creado como WP hijo; estado → *En revisión* |
| `Requisito`/`Spec` → *VoBo del cliente* (fijado por el cliente) | Agente de generación de scripts de prueba (autónomo) | Script de pruebas a Gitea (`refs #id`); estado → *En desarrollo* |
| Iteración → *Inicio de iteración* | Agente de andamiaje (autónomo) + agente de lógica de negocio (asistido) | Rama de iteración en Gitea; código andamiado autónomo; lógica de negocio en estado de revisión |
| Iteración → *Lista para pruebas* | Agente de ejecución de pruebas (autónomo) | Pruebas unitarias ejecutadas; resultados |
| Tras la ejecución de pruebas | Agente de evidencia (autónomo) | Evidencia (logs a Gitea, capturas/PDF adjuntos al WP) |

Ante cualquier ambigüedad en el insumo, el agente de especificación **devuelve
preguntas al consultor funcional como comentario en el work package** en lugar de
asumir. La calidad del `Requisito` es el punto de apalancamiento de toda la
fábrica.

## 10. Secretos y configuración

- Las claves de las APIs de IA, los tokens de OpenProject y Gitea y el secreto de
  webhook son secretos sensibles. Van en `.env` real en la instancia con permisos
  restringidos, **o** en SSM Parameter Store. Nunca en el repositorio ni en
  `user-data`.
- `.env.example` se actualiza con los nombres de las nuevas variables y
  placeholders, sin valores reales.
- El token de API de OpenProject pertenece al usuario de servicio "Fabro" con el
  rol restringido de la sección 8.4 — mínimo privilegio.
- El token de Gitea se limita a los repositorios de los proyectos que Fabro
  atiende.

## 11. Seguridad

- Fabro no se expone a internet salvo su endpoint de webhooks, publicado por
  Caddy con TLS.
- El endpoint de webhooks **valida la firma** de todo payload antes de procesarlo
  (sección 8.1).
- Administración del host por SSM Session Manager; sin puerto 22 abierto.
- Salida a internet de Fabro limitada a 443 hacia los dominios de las APIs de IA.
- El contenido de los work packages que Fabro recibe es **dato**, no
  instrucciones: los agentes lo procesan como insumo de su tarea, no como órdenes
  que alteren su comportamiento.
- La frontera de aprobaciones se hace cumplir por permisos (sección 8.4).
- El usuario de servicio "Fabro" deja traza propia en todo el historial, para
  auditoría.

## 12. Entregables que debes producir

```
fabro/
  Dockerfile              # segun el stack de la decision #2
  .dockerignore
app/
  docker-compose.yml      # ACTUALIZADO: anade el servicio fabro
  Caddyfile               # ACTUALIZADO: publica el endpoint de webhooks
  .env.example            # ACTUALIZADO: nuevas variables de Fabro
infra/
  terraform/              # SOLO si la decision #1 es la opcion B (EC2 dedicado)
docs/
  integracion-fabro.md    # runbook: configurar el webhook en OpenProject,
                          # crear el usuario/rol de servicio, rotar secretos
```

Reglas: no reescribir los archivos existentes desde cero — aplicar cambios
incrementales y mostrar el diff. Los secretos no se versionan.

## 13. Plan de despliegue por fases

1. **Preparación.** Confirmar las decisiones de la sección 6, en especial el
   stack de Fabro y la ubicación de cómputo.
2. **Contenedor.** Escribir el `Dockerfile`; construir y probar la imagen
   localmente en la instancia.
3. **Rol de servicio.** En OpenProject, crear el usuario "Fabro" y el rol
   restringido **sin** las transiciones de aprobación; generar su token de API.
   Crear el token de Gitea.
4. **Integración del stack.** Añadir el servicio `fabro` al `docker-compose.yml`,
   actualizar el `Caddyfile` y el `.env`; levantar Fabro.
5. **Webhook.** Configurar el webhook en OpenProject apuntando al endpoint
   publicado, con el secreto compartido. Verificar que Fabro recibe y valida un
   evento de prueba.
6. **Prueba de extremo a extremo.** Crear un `Requisito` de prueba, asignarlo a
   la fábrica y verificar que Fabro dispara el agente correcto, escribe en Gitea
   con `refs #id` y actualiza el work package.
7. **Validación.** Recorrer la sección 14.

## 14. Criterios de aceptación

- Fabro corre como contenedor en el stack, con healthcheck en verde.
- El endpoint de webhooks responde por HTTPS y **rechaza** payloads sin firma
  válida.
- Asignar un `Requisito` de prueba a la fábrica dispara el agente esperado, que
  produce un commit en Gitea cuyo mensaje contiene `refs #<id>` y actualiza el
  estado del work package.
- El usuario de servicio "Fabro" **no puede** transicionar un work package a un
  estado de aprobación: el intento es rechazado por la API de OpenProject.
- Las acciones de Fabro aparecen en el historial del work package atribuidas al
  usuario de servicio, distinguibles de las acciones humanas.
- Ningún secreto (claves de IA, tokens) está en el repositorio; todos están en
  `.env`/SSM con acceso restringido.
- Fabro no es alcanzable desde internet salvo por la ruta del webhook.