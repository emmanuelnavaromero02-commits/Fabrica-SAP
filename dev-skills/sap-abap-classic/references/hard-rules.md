# Hard rules — ABAP Classic / ZDSWF Legacy

Absolute. No exceptions without documented override in `handoff/decision-gates.md`.

## 1. SAP standard

1.1. **No modifications to SAP standard objects.** No "Modification Assistant" entries, no `SE38` edits of SAP code. Use BAdIs, enhancement spots, append structures, or copy-and-modify into `Z*` space.
1.2. **No direct INSERT/UPDATE/DELETE on SAP standard tables.** Use the BAPI or update FM. If neither exists, escalate — do not write raw SQL on SAP tables.
1.3. **No SE38 ad-hoc edits to production code.** Every change goes through ADT + transport.

## 2. Language constructs — modern even in classic

2.1. **Modern syntax mandatory** (ABAP 7.40+):
   - `DATA(lv) = ...` inline declarations.
   - `VALUE #( ... )`, `FOR ... IN ...`, table expressions `lt[ key = ... ]`.
   - `COND #( WHEN ... THEN ... )`, `SWITCH #( ... )`.
   - `CORRESPONDING #( a = b MAPPING ... )` instead of `MOVE-CORRESPONDING`.
   - `REDUCE`, `FILTER`, `MAP`, `LET` where they clarify intent.
2.2. **Forbidden legacy syntax:**
   - `MOVE x TO y` → assignment operator.
   - `MOVE-CORRESPONDING` → `CORRESPONDING #( )`.
   - `IF NOT lt IS INITIAL` → `IF lt IS NOT INITIAL`.
   - `READ TABLE ... WITH KEY ... INTO ...` when a table expression `lt[ key = ... ]` is clearer.
   - `WHILE` for collection iteration → `LOOP AT` or `FOR`.
2.3. **Object-orientation first.** New code in classes. `FORM ... ENDFORM` only inside module pools where the screen runtime forces it, and even then the FORM body calls a controller class.
2.4. **No globally declared `DATA` in reports** outside of constants / structures the screen runtime requires. Everything else local.

## 3. Output

3.1. **`WRITE` is forbidden in production code.** Reports return data; ALV displays it.
3.2. **ALV: `CL_SALV_TABLE`** is the default for new standalone ALV. `CL_GUI_ALV_GRID` only when embedded in a Dynpro custom container (where SALV cannot run). `REUSE_ALV_*` function modules are forbidden in new code.
3.3. **Smartforms only for maintenance.** New forms use Adobe Forms.
3.4. **SAPscript: forbidden for new development.**

## 4. Integration

4.1. **BAPI before BDC.** If a BAPI exists for the operation, BDC is forbidden.
4.2. **`COMMIT WORK AND WAIT`** after every BAPI that writes. Never bare `COMMIT WORK`. Never forget the commit (asynchronous BAPIs without commit are silent data loss).
4.3. **Check `BAPIRET2`** after every BAPI call. Aggregate messages in a return table.
4.4. **`BAPI_TRANSACTION_ROLLBACK`** on any error path before exit.
4.5. **RFC destinations** referenced by `Z*` logical name only, never hardcoded RFC destination strings.
4.6. **IDoc processing:** use standard process codes when possible. Custom Z-function modules registered in `WE57` for new IDoc types only — and always via the BAdI `IDOC_INTERFACE` when modifying SAP standard inbound.

## 5. Dynpro / module pool

5.1. **`OK_CODE` cleared in PBO** at the start of every PBO module before the next round-trip. Failure causes phantom commands on the next user action.
5.2. **`SY-UCOMM` is read-only.** Copy to `OK_CODE` immediately in PAI, then process.
5.3. **`AT EXIT-COMMAND`** for back/cancel/exit modules. These bypass field validation correctly — using a normal PAI module for cancel breaks the screen flow.
5.4. **`CHAIN ... ENDCHAIN`** for multi-field validations that should re-prompt all chained fields on error.
5.5. **`LEAVE TO SCREEN 0`** to exit a Dynpro stack. Never `STOP`, never `EXIT` to leave a transaction.
5.6. **`SET TITLEBAR`** and **`SET PF-STATUS`** in PBO of every screen. No screen runs with default status.
5.7. **Field types match the dictionary.** No raw `c(n)` for business fields — use the proper data element.

## 6. Locking

6.1. **Enqueue/dequeue** for every multi-step update. Use SE11-defined lock objects, never raw row locks.
6.2. **`SCOPE` parameter** set explicitly on `ENQUEUE_*` (default scope is rarely what you want).
6.3. **Dequeue in every exit path**, including error paths and `AT EXIT-COMMAND`.

## 7. Authorization

7.1. **`AUTHORITY-CHECK`** on every transaction entry, every export action, every reporting selection.
7.2. **Check `sy-subrc` immediately** and exit if not 0. No "log and continue" on auth failures.
7.3. **Auth objects referenced by data dictionary**, not by literal string.

## 8. Unicode and encoding

8.1. **No manual byte offsets** on character data. Use `cl_abap_codepage` for any byte / char conversion.
8.2. **`OPEN DATASET ... IN <encoding> MODE`** explicit — no defaults.
8.3. **`RANGE OF`** must match the underlying type — no `range of c` against numc fields.

## 9. Background and parallel

9.1. **Background jobs via `JOB_OPEN/SUBMIT/JOB_CLOSE`** with `VIA JOB` clause, not `SUBMIT IN UPDATE TASK`.
9.2. **Parallel processing** via `CALL FUNCTION ... STARTING NEW TASK` with destination from RFC group; never via `WAIT UP TO ... SECONDS` polling hacks.
9.3. **No infinite-loop jobs.** Every background program has a guaranteed exit.

## 10. Testing

10.1. **ABAP Unit test class for every controller class.** Module pool screens cannot be unit-tested, so business logic lives in classes that are tested.
10.2. **Coverage: 70% lines on controller classes minimum** for handoff (lower than cloud because UI code is hard to unit-test).
10.3. **No test that requires SAP GUI** — those are integration tests, separate harness.

## 11. ATC

11.1. ATC variant: the customer's classic transport variant, or `SLIN` baseline as fallback. Verify with Basis which is in effect.
11.2. **Zero priority 1 and priority 2 findings** in scope of the modified objects. Pre-existing findings in untouched code don't block (but are recorded).
11.3. ATC report goes in `handoff/atc-report.txt`.

## 12. Sunset signal

12.1. Every classic Z-object is logged in `handoff/decision-gates.md` under "Sunset plan" with target migration date and migration ticket reference.
12.2. Any classic Z-object older than 24 months without a sunset plan is flagged on every handoff that touches it.

## 13. Comentarios y literales

13.1. **El código se comenta.** Cada clase y cada método público lleva comentario de cabecera con su PROPÓSITO (el porqué, no repetir el código). Los bloques de lógica no obvia llevan un comentario de intención de una línea. No dejar código comentado/muerto.
13.2. **Prohibidas las cadenas constantes (literales) en el código.** Todo texto que se muestra o se usa como mensaje/título/encabezado/etiqueta va por **text symbols (`text-NNN`) / text elements** o **clase de mensajes (`MESSAGE ... TYPE`)** — nunca literales en línea — para que sea traducible. Se mandan **como texto** (text symbol/OTR), no como string constante.
13.3. **Constantes técnicas** (claves, códigos, tipos) → `CONSTANTS` con nombre con significado; nunca literales mágicos dispersos en el código.
