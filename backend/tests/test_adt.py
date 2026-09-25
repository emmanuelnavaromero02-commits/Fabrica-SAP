from __future__ import annotations

import httpx
import pytest

from fabrica.sap.adt_bridge import AdtError, AdtSap
from fabrica.sap.bridge import Assertion, SapObject
from fabrica.sap.systems import SapSystem

URI = "/sap/bc/adt/programs/programs/zrep"
META = (
    '<p xmlns:adtcore="http://www.sap.com/adt/core"><adtcore:packageRef adtcore:name="ZFAB"/></p>'
)

LOCK = (
    '<asx:abap xmlns:asx="http://www.sap.com/abapxml"><asx:values><DATA>'
    "<LOCK_HANDLE>H123</LOCK_HANDLE><CORRNR/></DATA></asx:values></asx:abap>"
)
CHECK = (
    '<chkrun:checkRunReports xmlns:chkrun="http://www.sap.com/adt/checkrun"><chkrun:checkReport>'
    '<chkrun:checkMessageList><chkrun:checkMessage chkrun:type="E" chkrun:shortText="Falta punto"/>'
    '<chkrun:checkMessage chkrun:type="I" chkrun:shortText="info"/></chkrun:checkMessageList>'
    "</chkrun:checkReport></chkrun:checkRunReports>"
)
ATC = (
    '<atcworklist:worklist xmlns:atcworklist="http://www.sap.com/adt/atc/worklist"'
    ' xmlns:atcfinding="http://www.sap.com/adt/atc/finding"><atcworklist:objects><atcobject:object'
    ' xmlns:atcobject="http://www.sap.com/adt/atc/object"><atcobject:findings>'
    '<atcfinding:finding atcfinding:priority="1" atcfinding:messageTitle="SELECT * prohibido"/>'
    '<atcfinding:finding atcfinding:priority="3" atcfinding:messageTitle="Info"/>'
    "</atcobject:findings></atcobject:object></atcworklist:objects></atcworklist:worklist>"
)
UNIT = (
    '<aunit:runResult xmlns:aunit="http://www.sap.com/adt/aunit"><program><testClasses><testClass>'
    '<testMethods><testMethod><alerts><alert kind="failedAssertion" severity="critical">'
    "<title>Suma incorrecta</title></alert></alerts></testMethod></testMethods></testClass>"
    "</testClasses></program></aunit:runResult>"
)


class FakeSap:
    def __init__(self) -> None:
        self.created = False
        self.source = ""
        self.csrf_expired_once = True
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method, params = request.url.path, request.method, dict(request.url.params)
        self.calls.append((method, path, params))
        assert params.get("sap-client") == "100"
        if path == "/sap/bc/adt/discovery":
            return httpx.Response(200, headers={"x-csrf-token": "tok"})
        if method != "GET" and request.headers.get("x-csrf-token") != "tok":
            return httpx.Response(403, headers={"x-csrf-token": "Required"})
        if path == "/sap/bc/adt/cts/transports" and self.csrf_expired_once:
            self.csrf_expired_once = False
            return httpx.Response(403, headers={"x-csrf-token": "Required"})
        return self._route(request, method, path, params)

    def _route(
        self, request: httpx.Request, method: str, path: str, params: dict[str, str]
    ) -> httpx.Response:
        match (method, path):
            case ("GET", "/sap/bc/adt/programs/programs/zrep"):
                return httpx.Response(200 if self.created else 404, text=META)
            case ("GET", "/sap/bc/adt/oo/classes/zrep") | ("GET", "/sap/bc/adt/oo/interfaces/zrep"):
                return httpx.Response(404)
            case ("POST", "/sap/bc/adt/programs/programs"):
                assert params["corrNr"] == "DEVK900321"
                assert b'adtcore:name="ZREP"' in request.content
                self.created = True
                return httpx.Response(201)
            case ("POST", "/sap/bc/adt/cts/transports"):
                return httpx.Response(200, text="/com.sap.cts/object_record/DEVK900321")
            case ("POST", "/sap/bc/adt/programs/programs/zrep"):
                assert request.headers["X-sap-adt-sessiontype"] == "stateful"
                return httpx.Response(200, text=LOCK if params["_action"] == "LOCK" else "")
            case ("PUT", "/sap/bc/adt/programs/programs/zrep/source/main"):
                assert params == {"sap-client": "100", "sap-language": "ES",
                                  "lockHandle": "H123", "corrNr": "DEVK900321"}  # fmt: skip
                self.source = request.content.decode()
                return httpx.Response(200)
            case ("GET", "/sap/bc/adt/programs/programs/zrep/source/main"):
                return httpx.Response(200, text=self.source)
            case ("POST", "/sap/bc/adt/checkruns"):
                return httpx.Response(200, text=CHECK)
            case ("POST", "/sap/bc/adt/activation"):
                return httpx.Response(200, text="")
            case ("POST", "/sap/bc/adt/atc/worklists"):
                return httpx.Response(200, text="WL1")
            case ("POST", "/sap/bc/adt/atc/runs"):
                return httpx.Response(200, text="<atcworklist:worklistRun/>")
            case ("GET", "/sap/bc/adt/atc/worklists/WL1"):
                return httpx.Response(200, text=ATC)
            case ("POST", "/sap/bc/adt/abapunit/testruns"):
                return httpx.Response(200, text=UNIT)
        return httpx.Response(500, text=f"inesperado {method} {path}")


@pytest.fixture
def sap() -> tuple[AdtSap, FakeSap]:
    fake = FakeSap()
    http = httpx.AsyncClient(
        base_url="https://sap.test",
        params={"sap-client": "100", "sap-language": "ES"},
        transport=httpx.MockTransport(fake),
    )
    return AdtSap(SapSystem(name="CLI-DEV", kind="adt", client="100"), http), fake


async def test_write_creates_object_in_transport_and_unlocks(sap: tuple[AdtSap, FakeSap]) -> None:
    adt, fake = sap
    obj = SapObject("ZREP", "PROG", "ZFAB", "REPORT zrep.\nWRITE 'hola'.\n")
    transport = await adt.ensure_transport(obj, "Fabrica #1", None)
    assert transport == "DEVK900321"
    assert await adt.ensure_transport(obj, "Fabrica #1", transport) == transport
    await adt.write_object(obj, transport)
    assert fake.source == obj.source
    actions = [c[2].get("_action") for c in fake.calls if c[1] == URI and c[0] == "POST"]
    assert actions == ["LOCK", "UNLOCK"]
    read = await adt.read_object("ZREP")
    assert read is not None and read.package == "ZFAB" and read.source == obj.source


async def test_checks_parse_sap_findings(sap: tuple[AdtSap, FakeSap]) -> None:
    adt, fake = sap
    fake.created = True
    fake.source = "REPORT zrep."
    syntax = await adt.syntax_check("ZREP")
    assert [f.message for f in syntax.findings] == ["Falta punto"] and not syntax.ok
    assert (await adt.activate("ZREP")).ok
    atc = await adt.run_atc("ZREP")
    assert [(f.severity, f.message) for f in atc.findings] == [("error", "SELECT * prohibido")]
    unit = await adt.run_unit("ZREP", [Assertion("AS-1", "Usa ALV", "cl_salv_table")])
    assert [f.message for f in unit.findings] == [
        "Suma incorrecta",
        "AS-1 no se cumple: Usa ALV",
    ]


async def test_unknown_object_and_http_errors(sap: tuple[AdtSap, FakeSap]) -> None:
    adt, _ = sap
    assert await adt.read_object("ZNADA") is None
    with pytest.raises(AdtError):
        await adt.syntax_check("ZNADA")
