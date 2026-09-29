from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ObjectKind = Literal["TABL", "DDLS", "DCLS", "BDEF", "INTF", "CLAS", "PROG", "DDLX", "SRVD", "SRVB"]

ObjectRole = Literal[
    "table",
    "draft_table",
    "root",
    "interface",
    "projection",
    "access_control",
    "behavior",
    "behavior_pool",
    "metadata_extension",
    "service_definition",
    "service_binding",
    "class",
    "test_class",
    "abap_interface",
    "program",
]


@dataclass(frozen=True)
class KindInfo:
    kind: ObjectKind
    label: str
    build_order: int
    has_source: bool
    max_name_length: int


KINDS: dict[str, KindInfo] = {
    info.kind: info
    for info in (
        KindInfo("TABL", "Tabla de base de datos", 10, True, 16),
        KindInfo("DDLS", "Vista CDS", 20, True, 30),
        KindInfo("DCLS", "Control de acceso CDS", 30, True, 30),
        KindInfo("BDEF", "Definición de comportamiento", 40, True, 30),
        KindInfo("INTF", "Interfaz ABAP", 45, True, 30),
        KindInfo("CLAS", "Clase ABAP", 50, True, 30),
        KindInfo("PROG", "Programa ABAP", 60, True, 30),
        KindInfo("DDLX", "Extensión de metadatos", 70, True, 30),
        KindInfo("SRVD", "Definición de servicio", 80, True, 30),
        KindInfo("SRVB", "Enlace de servicio", 90, False, 26),
    )
}

ROLE_KIND: dict[str, ObjectKind] = {
    "table": "TABL",
    "draft_table": "TABL",
    "root": "DDLS",
    "interface": "DDLS",
    "projection": "DDLS",
    "access_control": "DCLS",
    "behavior": "BDEF",
    "behavior_pool": "CLAS",
    "metadata_extension": "DDLX",
    "service_definition": "SRVD",
    "service_binding": "SRVB",
    "class": "CLAS",
    "test_class": "CLAS",
    "abap_interface": "INTF",
    "program": "PROG",
}

BINDING_TYPES = ("ODATA_V4_UI", "ODATA_V4_WEB_API", "ODATA_V2_UI", "ODATA_V2_WEB_API")


def is_kind(value: str) -> bool:
    return value.upper() in KINDS


def build_order(kind: str) -> int:
    info = KINDS.get(kind.upper())
    return info.build_order if info else 100
