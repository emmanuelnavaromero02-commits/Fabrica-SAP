from __future__ import annotations

from collections.abc import Iterator
from html import escape
from xml.etree.ElementTree import Element

from defusedxml.ElementTree import fromstring

from fabrica.sap.bridge import Finding, Severity

ADTCORE = "http://www.sap.com/adt/core"
MESSAGE_SEVERITY: dict[str, Severity] = {"E": "error", "A": "error", "W": "warning"}
XML_HEAD = '<?xml version="1.0" encoding="UTF-8"?>'

OBJECT_PATHS = {
    "PROG": "/sap/bc/adt/programs/programs",
    "CLAS": "/sap/bc/adt/oo/classes",
    "INTF": "/sap/bc/adt/oo/interfaces",
}


def object_uri(kind: str, name: str) -> str:
    return f"{OBJECT_PATHS[kind]}/{name.lower()}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr(element: Element, name: str) -> str:
    for key, value in element.attrib.items():
        if _local(key) == name:
            return value
    return ""


def _find_all(root: Element, name: str) -> Iterator[Element]:
    return (e for e in root.iter() if _local(e.tag) == name)


def _text_of(element: Element, name: str) -> str:
    for child in _find_all(element, name):
        if child.text:
            return child.text.strip()
    return ""


def parse(xml: str) -> Element:
    root: Element = fromstring(xml)
    return root


def create_object_xml(kind: str, name: str, package: str, description: str, lang: str) -> str:
    root, ns, adt_type, extra = {
        "PROG": ("program:abapProgram", "http://www.sap.com/adt/programs/programs", "PROG/P", ""),
        "CLAS": (
            "class:abapClass",
            "http://www.sap.com/adt/oo/classes",
            "CLAS/OC",
            ' class:final="true" class:visibility="public"',
        ),
        "INTF": ("intf:abapInterface", "http://www.sap.com/adt/oo/interfaces", "INTF/OI", ""),
    }[kind]
    prefix = root.split(":")[0]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<{root} xmlns:{prefix}="{ns}" xmlns:adtcore="{ADTCORE}"'
        f' adtcore:description="{escape(description[:60])}" adtcore:name="{escape(name.upper())}"'
        f' adtcore:type="{adt_type}" adtcore:masterLanguage="{escape(lang)}"{extra}>'
        f'<adtcore:packageRef adtcore:name="{escape(package.upper())}"/>'
        f"</{root}>"
    )


def object_references_xml(uri: str, name: str) -> str:
    return (
        f'{XML_HEAD}<adtcore:objectReferences xmlns:adtcore="{ADTCORE}">'
        f'<adtcore:objectReference adtcore:uri="{escape(uri)}" adtcore:name="{escape(name)}"/>'
        "</adtcore:objectReferences>"
    )


def check_run_xml(uri: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<chkrun:checkObjectList xmlns:chkrun="http://www.sap.com/adt/checkrun"'
        f' xmlns:adtcore="{ADTCORE}">'
        f'<chkrun:checkObject adtcore:uri="{escape(uri)}" chkrun:version="inactive"/>'
        "</chkrun:checkObjectList>"
    )


def _object_set(uri: str) -> str:
    return (
        f'<objectSet kind="inclusive"><adtcore:objectReferences>'
        f'<adtcore:objectReference adtcore:uri="{escape(uri)}"/>'
        "</adtcore:objectReferences></objectSet>"
    )


def atc_run_xml(uri: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<atc:run maximumVerdicts="500" xmlns:atc="http://www.sap.com/adt/atc">'
        f'<objectSets xmlns:adtcore="{ADTCORE}">{_object_set(uri)}</objectSets></atc:run>'
    )


def unit_run_xml(uri: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<aunit:runConfiguration xmlns:aunit="http://www.sap.com/adt/aunit">'
        '<external><coverage active="false"/></external><options><uriType value="semantic"/>'
        '<testDeterminationStrategy sameProgram="true" assignedTests="false"/>'
        '<testRiskLevels harmless="true" dangerous="true" critical="true"/>'
        '<testDurations short="true" medium="true" long="true"/></options>'
        f'<adtcore:objectSets xmlns:adtcore="{ADTCORE}">{_object_set(uri)}</adtcore:objectSets>'
        "</aunit:runConfiguration>"
    )


def transport_request_xml(package: str, text: str, uri: str) -> str:
    return (
        '<?xml version="1.0" encoding="ASCII"?>'
        '<asx:abap xmlns:asx="http://www.sap.com/abapxml" version="1.0"><asx:values><DATA>'
        f"<OPERATION>I</OPERATION><DEVCLASS>{escape(package.upper())}</DEVCLASS>"
        f"<REQUEST_TEXT>{escape(text[:60])}</REQUEST_TEXT><REF>{escape(uri)}</REF>"
        "</DATA></asx:values></asx:abap>"
    )


def lock_handle(xml: str) -> str:
    handle = _text_of(parse(xml), "LOCK_HANDLE")
    if not handle:
        raise ValueError("SAP no devolvió LOCK_HANDLE")
    return handle


def package_of(xml: str) -> str:
    for ref in _find_all(parse(xml), "packageRef"):
        return _attr(ref, "name")
    return ""


def check_findings(xml: str) -> list[Finding]:
    return [
        Finding("syntax", MESSAGE_SEVERITY[_attr(m, "type")], _attr(m, "shortText"))
        for m in _find_all(parse(xml), "checkMessage")
        if _attr(m, "type") in MESSAGE_SEVERITY
    ]


def activation_findings(xml: str) -> list[Finding]:
    if not xml.strip():
        return []
    return [
        Finding(
            "activation",
            MESSAGE_SEVERITY[_attr(m, "type")],
            _text_of(m, "txt") or _attr(m, "objDescr"),
        )
        for m in _find_all(parse(xml), "msg")
        if _attr(m, "type") in MESSAGE_SEVERITY
    ]


def atc_findings(xml: str) -> list[Finding]:
    findings = []
    for f in _find_all(parse(xml), "finding"):
        priority = _attr(f, "priority")
        if priority not in ("1", "2"):
            continue
        title = _attr(f, "messageTitle") or _attr(f, "checkTitle")
        findings.append(Finding("atc", "error" if priority == "1" else "warning", title))
    return findings


def unit_findings(xml: str) -> list[Finding]:
    return [
        Finding("unit", "error", _text_of(a, "title") or _attr(a, "kind"))
        for a in _find_all(parse(xml), "alert")
        if _attr(a, "severity") in ("critical", "fatal")
    ]
