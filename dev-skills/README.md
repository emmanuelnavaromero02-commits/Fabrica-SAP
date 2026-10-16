# Skills ABAP para el desarrollador (Claude Code)

Skills de **Claude Code** para desarrollar los requerimientos de la fábrica con
el estándar correcto. Son las **mismas** que usa la fábrica (catálogo de
arquitectura), empaquetadas para instalarse en el equipo de cada desarrollador.

| Skill | Para qué | Cubre |
|---|---|---|
| **sap-abap-classic** | ABAP clásico (legacy) | SAP GUI Dynpros, module pools, reports, ALV (CL_GUI_ALV_GRID), SAPscript/Smartforms, BDC, RFC/BAPI, IDocs, enhancements (user/customer exits, BAdIs), SE80/SE38/SE11. Antes de generar código, **interroga** si se puede hacer en Cloud y, si sí, escala a `sap-abap-cloud`. |
| **sap-abap-cloud** | ABAP Cloud / Clean Core | RAP (behavior def/impl), CDS view entities, service def/binding, **OData V4**, **Fiori Elements**, freestyle SAPUI5, BTP ABAP Environment (Steampunk), released APIs, extensibilidad de 3 capas, draft, MDK. Target: S/4HANA 2023, ABAP for Cloud Development. |

Claude Code elige la skill correcta **según el contexto** del requerimiento
(las descripciones tienen disparadores `Use when…`). Instala **ambas**.

## Instalar (una vez por máquina)

Se instalan como **personal skills** en `~/.claude/skills/`, así quedan
disponibles en todos los repos de requerimiento que clones.

### Windows (PowerShell)
```powershell
cd dev-skills
./install-skills.ps1
```

### Mac / Linux
```bash
cd dev-skills
chmod +x install-skills.sh
./install-skills.sh
```

### Manual (cualquier SO)
Copia las carpetas `sap-abap-classic/` y `sap-abap-cloud/` dentro de
`~/.claude/skills/` (en Windows: `%USERPROFILE%\.claude\skills\`). Debe quedar:
```
~/.claude/skills/sap-abap-classic/SKILL.md
~/.claude/skills/sap-abap-cloud/SKILL.md
```

## Verificar
Abre el repo del requerimiento con Claude Code y ejecuta `/skills`: deben
aparecer `sap-abap-classic` y `sap-abap-cloud`.

## Actualizar
Vuelve a correr el instalador (con `-Force` / `--force` para sobreescribir sin
preguntar). Estas skills se mantienen en el catálogo de arquitectura de la
fábrica (`seed/fabrica-arquitectura/skills/*.skill`); este paquete es la copia
distribuible para los equipos.
