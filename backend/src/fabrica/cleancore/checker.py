from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ComplianceIssue:
    line_number: int
    rule: str
    message: str
    snippet: str


FORBIDDEN_CLOUD_CONSTRUCTS: tuple[tuple[str, str, str], ...] = (
    (r"\bCALL\s+TRANSACTION\b", "CLOUD_CALL_TRANSACTION", "CALL TRANSACTION forbidden in cloud"),
    (r"\bCALL\s+SCREEN\b", "CLOUD_CALL_SCREEN", "CALL SCREEN forbidden in cloud"),
    (r"\bSUBMIT\s+\w+", "CLOUD_SUBMIT", "SUBMIT forbidden in cloud"),
    (r"\bWRITE\s*[:/]", "CLOUD_WRITE", "Classical WRITE forbidden in cloud"),
    (r"\bOPEN\s+DATASET\b", "CLOUD_OPEN_DATASET", "OPEN DATASET forbidden in cloud"),
    (r"\bEXEC\s+SQL\b", "CLOUD_EXEC_SQL", "EXEC SQL forbidden in cloud"),
    (r"\bCL_GUI_FRONTEND_SERVICES\b", "CLOUD_GUI", "GUI services unavailable in cloud"),
    (r"/UI2/CL_JSON", "CLOUD_UI2_JSON", "/UI2/CL_JSON deprecated; use XCO JSON"),
    (r"\bMODULE\s+\w+\s+(INPUT|OUTPUT)\b", "CLOUD_DYNPRO", "Dynpro modules forbidden in cloud"),
    (r"\bDEFINE\s+VIEW\s+(?!ENTITY)", "CLOUD_VIEW", "Legacy DEFINE VIEW obsolete; use ENTITY"),
    (r"\bAUTHORITY-CHECK\b", "CLOUD_AUTH_CHECK", "Direct AUTHORITY-CHECK discouraged; use DCL"),
    (r"\bENQUEUE_\w+\s*\(", "CLOUD_ENQUEUE", "Classical ENQUEUE FM forbidden; use RAP lock master"),
    (r"\bDEQUEUE_\w+\s*\(", "CLOUD_DEQUEUE", "Classical DEQUEUE FM forbidden; use RAP lock master"),
)

FORBIDDEN_CLOUD_CLASSES: tuple[tuple[str, str, str], ...] = (
    (r"\bCL_ABAP_LIST_LINE\w*\b", "CLOUD_LIST", "Classical list classes forbidden in cloud"),
    (r"\bCL_GUI_ALV_GRID\b", "CLOUD_GUI_ALV", "GUI ALV forbidden in cloud; use Fiori"),
    (r"\bCL_SALV_TABLE\b", "CLOUD_SALV", "CL_SALV_TABLE is classic; use RAP analytical in cloud"),
    (r"\bCL_DD_DOCUMENT\b", "CLOUD_DD_DOC", "Document rendering forbidden in cloud"),
)

CLASSIC_ANTI_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (r"\bMOVE-CORRESPONDING\b", "CLASSIC_MOVE_CORR", "Use CORRESPONDING #( ) instead"),
    (r"\bIF\s+NOT\s+[\w\-><]+\s+IS\s+INITIAL\b", "CLASSIC_IF_NOT", "Use 'IF x IS NOT INITIAL'"),
    (r"\bMOVE\s+\S+\s+TO\s+\S+\.", "CLASSIC_MOVE", "Use assignment = instead of MOVE TO"),
    (r"^\s*COMMIT\s+WORK\s*\.\s*$", "CLASSIC_COMMIT", "Use COMMIT WORK AND WAIT"),
    (r"\bCALL\s+TRANSACTION\s+'[A-Z0-9]+'\s+USING\b", "CLASSIC_BDC", "Avoid BDC; prefer BAPI"),
    (r"\bGUID_CREATE\b", "CLASSIC_GUID", "Use CL_SYSTEM_UUID=>create_uuid_x16_static"),
    (r"\bSTART_FORM\b|\bWRITE_FORM\b|\bEND_FORM\b", "CLASSIC_FORM", "SAPscript is obsolete"),
    (
        r"\bBAPI_TRANSACTION_COMMIT\b(?![^.]*\bWAIT\s*=\s*ABAP_TRUE\b)",
        "CLASSIC_BAPI_WAIT",
        "BAPI_TRANSACTION_COMMIT requires WAIT = ABAP_TRUE",
    ),
)

ALLOWED_SELECT_PREFIXES: tuple[str, ...] = ("I_", "C_", "R_", "Z", "Y", "TADIR")


class CleanCoreChecker:
    @staticmethod
    def check_cloud_source(source: str) -> list[ComplianceIssue]:
        issues: list[ComplianceIssue] = []
        lines = source.splitlines()

        for idx, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if stripped.startswith("*"):
                continue
            code_line = line.split('"', 1)[0]
            upper = code_line.upper()

            for pattern, rule, message in FORBIDDEN_CLOUD_CONSTRUCTS:
                if re.search(pattern, upper):
                    issues.append(ComplianceIssue(idx, rule, message, line.strip()))

            for pattern, rule, message in FORBIDDEN_CLOUD_CLASSES:
                if re.search(pattern, upper):
                    issues.append(ComplianceIssue(idx, rule, message, line.strip()))

            match_select = re.search(r"\bSELECT\b.*?\bFROM\s+([A-Z0-9_]+)", upper)
            if match_select:
                table_target = match_select.group(1)
                if not any(table_target.startswith(prefix) for prefix in ALLOWED_SELECT_PREFIXES):
                    issues.append(
                        ComplianceIssue(
                            idx,
                            "CLOUD_DIRECT_TABLE_SELECT",
                            f"Direct SELECT FROM '{table_target}' forbidden; use released I_* view",
                            line.strip(),
                        )
                    )

        return issues

    @staticmethod
    def check_classic_source(source: str) -> list[ComplianceIssue]:
        issues: list[ComplianceIssue] = []
        lines = source.splitlines()

        for idx, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if stripped.startswith("*"):
                continue
            code_line = line.split('"', 1)[0]
            upper = code_line.upper()

            for pattern, rule, message in CLASSIC_ANTI_PATTERNS:
                if re.search(pattern, upper):
                    issues.append(ComplianceIssue(idx, rule, message, line.strip()))

        return issues

    @staticmethod
    def validate_naming(object_type: str, object_name: str, package: str) -> list[str]:
        violations: list[str] = []
        norm_type = object_type.upper()
        norm_name = object_name.upper()
        norm_pkg = package.upper()

        if not (norm_name.startswith("Z") or norm_name.startswith("Y")):
            violations.append(f"Object {object_name} must begin with customer prefix Z or Y")

        if not (norm_pkg.startswith("Z") or norm_pkg.startswith("Y") or norm_pkg == "$TMP"):
            violations.append(f"Package {package} must begin with customer prefix Z, Y or $TMP")

        rap_prefix_map: dict[str, tuple[str, ...]] = {
            "DDLS": ("_R_", "_I_", "_C_", "_E_", "_TF_"),
            "BDEF": ("_R_", "_C_", "_BP_"),
            "SRVD": ("_SD_",),
            "SRVB": ("_SB_",),
            "DCLS": ("_I_", "_C_", "_R_"),
        }

        if norm_type in rap_prefix_map:
            expected = rap_prefix_map[norm_type]
            if not any(marker in norm_name for marker in expected):
                valid_str = ", ".join(expected)
                violations.append(
                    f"{norm_type} {object_name} must contain RAP pattern: ({valid_str})"
                )

        return violations
