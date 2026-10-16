# Arquitectura — Plataforma objetivo RISE (ABAP Cloud, Clean-Core)

## Vision

Plataforma interna para construir capacidades sobre **RISE** (S/4HANA +
extensiones BTP) bajo **modelo Clean-Core**, asistida por una fabrica de
software autonoma con compuertas humanas ("ejecucion a oscuras, verificacion
encendida").

Este documento describe la **plataforma objetivo**: el sistema SAP RISE donde
aterriza el codigo que produce la fabrica. El agente lo lee en cada corrida y
debe anclar TODA propuesta de diseno en estas reglas.

(La arquitectura de la *plataforma de la fabrica* — Temporal, OpenProject,
Gitea, el orquestador — NO vive aqui: es documentacion de desarrollo, no
premisa de diseno SAP. Ver `docs/arquitectura-fabrica.md`.)

## Stack tecnologico

### Core ABAP
- **ABAP Cloud** (ABAP for Cloud Development): el lenguaje base. Solo APIs,
  CDS views, BAdIs y BDEFs marcados como **Released for Cloud Development**.
- **CDS** (Core Data Services) como modelo de datos canonico.
- **ABAP RAP** (RESTful Application Programming) como framework de servicios:
  - **Managed RAP** cuando la entidad de negocio es nueva o no reusa
    transacciones legacy.
  - **Unmanaged RAP** cuando hay que envolver logica legacy via API released.
  - **Service Definitions + Service Bindings** publicando **OData v4**.
- **Behavior Implementations** (BIs) para validaciones, determinaciones,
  acciones y autorizaciones a nivel de entidad.

### Frontend
- **Fiori Elements** es la PRIMERA opcion para toda pantalla transaccional:
  - **List Report + Object Page** para CRUD de entidades.
  - **Analytical List Page** para analitica con drill-down.
  - **Overview Page** para landing por rol.
  - **Form Entry Object Page** para creacion guiada.
  - La UI se define por **annotations** sobre CDS — no por JS.
- **SAPUI5 freestyle** solo cuando los floorplans de Fiori Elements no
  alcanzan (UX no estandar, dashboards complejos, flujos atipicos). Cada
  excepcion debe justificarse en la spec.
- **BTP Mobile Services + MDK** (Mobile Development Kit) para apps con
  capacidad offline:
  - Sincronizacion delta de OData v4.
  - Push notifications via SAP Cloud Platform Mobile Push.
  - Auth via SAP IAS (Identity Authentication Service).
  - **NO** apps nativas iOS/Android — todo MDK cross-platform.

### Distribucion y shell
- **Fiori Launchpad** (FLP) como shell unico:
  - **Spaces & Pages** para organizar por rol/proceso.
  - **Business Catalogs** + **Business Roles** SAP_BR_*.
  - Cada app desplegada debe registrar: catalogo, grupo, business role,
    tile, icono.
- **BTP Work Zone** opcional si hay distribucion multi-tenant o portal
  cliente.

### Excepcion: Dynpro / ABAP clasico
- Permitido SOLO para mantenimiento de objetos legacy ya en SE80.
- **NO** se aceptan capacidades nuevas en Dynpro — toda capacidad nueva va a
  Fiori Elements o SAPUI5.
- Si se hace mantenimiento: namespace de cliente (Z* o /RISE/*), con
  justificacion explicita en la spec y plan de migracion a Fiori
  documentado.

## Modelo Clean-Core — contrato vinculante

Clean-Core es la regla suprema del proyecto. Cualquier propuesta de diseno
que la viole debe ser rechazada por el agente (mode=blocked si no hay otra
via, o rediseno propuesto si hay alternativa).

### Que significa Clean-Core en este proyecto

1. **CERO modificaciones a objetos SAP estandar.** Ni enhancements del kernel,
   ni "modificaciones" en SE80, ni appends en tablas estandar.
2. **Solo se consumen APIs, CDS y BDEFs marcados como Released** para Cloud
   Development en SAP Notes y SAP API Business Hub. Si un objeto NO es
   released, se busca alternativa o se solicita liberacion a SAP — no se
   "engancha" igual.
3. **Extensiones SOLO en dos vias**:
   - **In-app**: Key User extensibility (Custom Fields, Custom Logic, Custom
     CDS) o Developer Extensibility con namespace de cliente.
   - **Side-by-side**: BTP — CAP, RAP en BTP ABAP Environment, MTA, Mobile.
4. **Datos custom** van en tablas Z/RISE/* en namespace de cliente o en BTP
   Custom Business Objects.
5. **Enhancements solo via BAdIs released** — nada de implicit enhancements
   ni explicit en objetos sin BAdI declarado.
6. **Upgrade-safety por diseno**: cada extension debe sobrevivir un upgrade
   del estandar sin reescribirse.

## Topologia

```
                       +-------------------------------+
                       |   IdP: SAP IAS (+ IPS)        |
                       |   SSO unico para todo         |
                       +---------------+---------------+
                                       |
              +------------------------+------------------------+
              |                                                 |
   +----------v-----------+                          +----------v-----------+
   |   S/4HANA (Core)     |                          |   BTP Subaccount(s)  |
   |   - ABAP Cloud       |  Communication Arrangs.  |   - CAP / RAP BTP    |
   |   - RAP services     |<------------------------>|   - Mobile Services  |
   |   - Fiori Elements   |                          |   - Integration Suite|
   |   - Embedded FLP     |     (Cloud Connector si  |     (CPI, API Mgmt)  |
   |                      |      hay on-premise)     |   - Event Mesh       |
   +----------------------+                          |   - Work Zone (opc)  |
                                                     +----------------------+
                                                                |
                                                  +-------------v-------------+
                                                  | Frontends end-user        |
                                                  | - FLP central             |
                                                  | - SAPUI5 apps             |
                                                  | - MDK Mobile apps         |
                                                  +---------------------------+
```

- **Conectividad**: SAP Cloud Connector para alcanzar sistemas on-premise
  desde BTP. Communication Arrangements para flujos S/4 -> BTP y a la
  inversa.
- **Identidad**: SAP IAS como IdP unico; IPS para aprovisionar usuarios.
  Federacion opcional contra el AD/Entra ID del cliente. MFA segun politica.
- **Integraciones externas**: SIEMPRE via **Integration Suite (CPI)**. No
  RFCs directos a sistemas externos.
- **Eventos**: Event Mesh cuando hay flujos asincronos side-by-side.

## Matriz capacidad -> tecnologia

| Tipo de capacidad nueva | Primera opcion | Alternativa valida | NO aceptable |
|--------------------------|----------------|---------------------|--------------|
| CRUD transaccional sobre entidad nueva | Fiori Elements (List Report + Object Page) sobre RAP managed | SAPUI5 freestyle si el flujo no es estandar | Dynpro |
| Maestro complejo con guided journey | Fiori Elements Form Entry Object Page | SAPUI5 freestyle | Dynpro |
| Reportes / analitica | Embedded Analytics (CDS Analytical Cube/Query + ALP) | CDS custom + SAC | Classic REPORT |
| App movil con offline | BTP Mobile Services + MDK | SAPUI5 hybrid con offline OData | App nativa, web responsive |
| Servicio OData consumido por terceros | RAP Service Binding OData v4 + API Mgmt | CAP en BTP | SOAP, REST a mano |
| Logica de negocio sobre datos SAP | RAP Behavior Implementation (en S/4) | CAP service handler en BTP (side-by-side) | Classic ABAP en SAP_HOME |
| Proceso largo asincrono | Background job ABAP + Notifications API | BTP Workflow Service / Process Automation | Batch input, jobs Dynpro |
| Integracion entre sistemas | Integration Suite (CPI iFlows) | RAP + Communication Arrangement | RFC, IDoc directo a aplicacion |
| Custom field en entidad estandar | Key User extensibility (Custom Fields) | Developer Extensibility (BTP) | Append struct + modif |
| Custom logic en proceso estandar | BAdI released + Custom Logic (in-app) | Side-by-side via evento | Implicit enhancement |

## NFRs especificos del entorno SAP

- **Cadencia de release**: cambios a productivo via solicitud de transporte
  (CTS) o pipeline gCTS/BTP MTA. **Sin** despliegues ad-hoc al productivo.
  Cadencia minima negociable por release window.
- **Performance OData**: P95 < 2s en List Report con paginacion 30; P95 <
  500ms en Object Page por entidad. Baselines verificables con
  `/sap/bc/adt/performance` o SAT.
- **CDS view design**: solo CDS released como base; usar `@AccessControl`,
  asociaciones eficientes, evitar SELECTs anidados en WHERE.
- **Authorization**: PFCG via templates de Business Roles. Cada RAP service
  controla autorizacion a nivel de entidad y de campo cuando contiene PII o
  datos sensibles.
- **Auditabilidad**: cambios trazables via Change Documents (CDHDR/CDPOS)
  para entidades de negocio; tabla de auditoria propia (Z*) para datos
  nuevos no cubiertos por CDS estandar.
- **i18n**: idiomas minimos **es, en**. Toda UI con i18n.properties (UI5) o
  Text Symbols / OTR (ABAP). Sin literales en codigo.
- **Accessibility**: Fiori 3 cumple WCAG 2.1 AA por defecto; toda
  personalizacion debe preservarlo (contraste, navegacion teclado, ARIA).
- **Mobile offline**: features clave en MDK deben definir explicitamente:
  estrategia online/offline, sincronizacion (delta, full), resolucion de
  conflictos (last-wins + override manual por default, salvo justificacion),
  validacion cliente vs servidor.
- **Seguridad de datos**: PII y datos sensibles cifrados en reposo (SAP HANA
  data-at-rest); en transito siempre TLS 1.2+. Logs no contienen PII.
- **Compliance**: si aplica GDPR/LGPD, retencion y derecho al olvido por
  entidad documentado.

## Restricciones obligatorias para features nuevas (Clean-Core enforcement)

El agente DEBE rechazar (o redisenar) cualquier feature que viole alguna de
estas reglas:

1. **Todo objeto nuevo en namespace de cliente** (Z* o /RISE/*).
2. **Cero modificaciones a objetos SAP estandar** — usa BAdI released, Key
   User Extensibility o side-by-side BTP.
3. **Acceso a datos via CDS/BDEF released** — no SELECT directo a tablas
   estandar ni a tablas de otro modulo sin CDS released de por medio.
4. **Toda UI nueva evaluada PRIMERO contra Fiori Elements**; SAPUI5
   freestyle solo si se justifica explicitamente.
5. **NO Dynpro para capacidades nuevas**. Mantenimiento legacy requiere
   justificacion en la spec.
6. **NO RFC directo a sistemas externos** — Integration Suite (CPI) o
   Communication Arrangements.
7. **Toda accion OData publicada** debe tener: autorizacion configurada,
   validacion server-side, release notes, y catalogo + business role
   asignado si se expone en FLP.
8. **Migraciones de diccionario** (tablas Z nuevas, anadir campos a tablas
   propias) en **transporte separado** de cambios funcionales — facilita
   rollback.
9. **Toda app desplegada al FLP** debe declarar: business catalog, business
   role, business group, tile (icono, titulo, subtitulo, semantic object,
   action).
10. **Toda feature movil** debe definir explicitamente: scope online/offline,
    sincronizacion, conflictos, push relevante, almacenamiento local.
11. **Authorization-as-code**: nunca confiar solo en autorizacion de UI; el
    chequeo vive en el backend (BDEF Authorization Control o handler CAP).
12. **i18n desde el primer commit** — no se aceptan literales hardcoded.