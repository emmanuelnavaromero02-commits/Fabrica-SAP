# Released APIs — verification protocol

The agent does not memorize the released-API list. The list changes per release. The agent **verifies** each API at the time of use and records the verification in `handoff/released-apis-used.md`.

## Verification methods (in order of authority)

1. **ADT API State tab.** Open the object in ADT → *Properties* → *API State*. Look for one of:
   - `Released for Cloud Development` → OK to use in Tier 2 / Tier 3.
   - `Use System-Internally` → NOT for cloud.
   - `Use with Restrictions` → read the restriction note before using.
   - `Not Released` → forbidden.
   - blank / no API State → treat as not released.
2. **SAP Business Accelerator Hub** (`api.sap.com`) — lists CDS views and ABAP APIs with release state per S/4HANA release. Cross-check against #1.
3. **System release notes** in `SAP Help` for the target release.

If sources disagree: trust ADT in the actual target system.

## Where to look first

| Need | Where to find |
|---|---|
| Read business data | `I_*` CDS views — search by entity name in ADT |
| String / number / date utilities | `xco_cp` library |
| HTTP outbound | `xco_cp_http_destination` |
| JSON | `xco_cp_json` (replaces `/ui2/cl_json`) |
| UUIDs | `xco_cp=>uuid( )` |
| Currency conversion | CDS `currency_conversion( )` or `xco_cp` currency API |
| Logging | `cl_bali_*` (Business Application Log released subset) |
| File system | `xco_cp_fs_*` |
| Background scheduling | Application Job templates |
| Send email | Released `cl_bcs_*` subset, or BTP Email service |

## The XCO library — first stop

`xco_*` is the SAP-curated utility library for ABAP Cloud. Reach for it before considering classic FMs.

| Module | Purpose |
|---|---|
| `xco_cp` | Core protocol — strings, numbers, booleans, UUIDs |
| `xco_cp_json` | JSON serialization / deserialization |
| `xco_cp_time` | Time arithmetic |
| `xco_cp_calendar` | Date arithmetic, factory calendars |
| `xco_cp_zip` | Zip handling |
| `xco_cp_csv` | CSV reading/writing |
| `xco_cp_xml` | XML handling |
| `xco_cp_sql` | Dynamic SQL builder (for the rare case `SELECT (lv_dynamic)` would have been needed) |
| `xco_cp_http_destination` | Outbound HTTP via destinations |
| `xco_cp_fs` | File system abstraction |

## Anti-patterns the agent must replace

| Tempting code | Use instead |
|---|---|
| `CALL FUNCTION 'GUID_CREATE'` | `xco_cp=>uuid( )->value` |
| `CALL FUNCTION '/UI2/CL_JSON=>SERIALIZE'` | `xco_cp_json=>data->from_abap( ... )->to_string( )` |
| `CL_GUI_FRONTEND_SERVICES` | nothing — no frontend services in cloud |
| `CALL FUNCTION 'CONVERSION_EXIT_*'` | `xco_cp=>string( ... )->value` with formatting helpers, or domain-defined output |
| `cl_abap_random_int=>create` | `xco_cp=>uuid( )` for ID-like randomness; for true random, use `cl_abap_random_*` (released subset — verify) |
| `OPEN DATASET ... FOR INPUT` | `xco_cp_fs_*` — verify scenario is supported |
| `cl_salv_table` in Tier 2 | not allowed — no SALV in cloud; expose data as CDS analytical query |

## Verification log format

In `handoff/released-apis-used.md`:

```markdown
# Released APIs used — task <id>

| API | Type | Release state | Verified via | Notes |
|---|---|---|---|---|
| `I_Customer` | CDS view | Released for Cloud Development | ADT API State, 2026-05-29 | used for read |
| `xco_cp_json` | Class | Released for Cloud Development | ADT API State, 2026-05-29 | serialization |
| `cl_bali_log` | Class | Released for Cloud Development | ADT API State, 2026-05-29 | error logging |
| `I_JournalEntryItem` | CDS view | Released for Cloud Development | ADT API State + api.sap.com, 2026-05-29 | read GL items |

Total released APIs used: <n>
Unreleased APIs used: 0 (or list with G5 exemption reference)

Signed: agent claude, 2026-05-29.
```

If any unreleased API was used (only with G5 exemption), it must be listed here with the exemption justification reference.

## What `scripts/check_released_apis.py` does

The script (see `scripts/`) parses generated ABAP for:
- `SELECT ... FROM <t>` where `<t>` is not in the allowed prefix set.
- `CALL FUNCTION '<n>'` where `<n>` is not on an allow-list maintained by the factory.
- `CALL METHOD <cls>=>` against forbidden classes.
- Reference to forbidden language constructs.

It produces a report and a non-zero exit code if it finds anything suspect. Handoff blocked until clean.
