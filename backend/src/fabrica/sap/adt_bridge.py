from __future__ import annotations

from typing import Any

import httpx

from fabrica.sap import adt_xml
from fabrica.sap.bridge import Assertion, CheckResult, SapObject, assertion_findings
from fabrica.sap.systems import SapSystem

STATEFUL = {"X-sap-adt-sessiontype": "stateful"}
ADT_XML = "application/*"


class AdtError(RuntimeError): ...


class AdtSap:
    def __init__(self, system: SapSystem, http: httpx.AsyncClient | None = None) -> None:
        self.system = system.name
        self._config = system
        self._http = http
        self._csrf = ""
        self._kinds: dict[str, str] = {}

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            user, password = self._config.credentials()
            self._http = httpx.AsyncClient(
                base_url=self._config.url,
                auth=(user, password),
                params={"sap-client": self._config.client, "sap-language": self._config.language},
                verify=self._config.verify_tls,
                timeout=180,
            )
        return self._http

    async def _fetch_csrf(self) -> str:
        resp = await self._client().get(
            "/sap/bc/adt/discovery",
            headers={"x-csrf-token": "fetch", "Accept": "application/atomsvc+xml"},
        )
        self._csrf = resp.headers.get("x-csrf-token", "")
        if resp.status_code >= 400 or not self._csrf:
            raise AdtError(f"No se obtuvo token CSRF de {self.system} ({resp.status_code})")
        return self._csrf

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        extra_headers: dict[str, str] = kwargs.pop("headers", {})
        for _ in range(2):
            headers = {"x-csrf-token": self._csrf or await self._fetch_csrf(), **extra_headers}
            resp = await self._client().request(method, url, headers=headers, **kwargs)
            if (
                resp.status_code == 403
                and resp.headers.get("x-csrf-token", "").lower() == "required"
            ):
                self._csrf = ""
                continue
            if resp.status_code >= 400:
                raise AdtError(f"{method} {url} → {resp.status_code}: {resp.text[:300]}")
            return resp
        raise AdtError(f"{method} {url} rechazado por CSRF")

    async def _kind_of(self, name: str) -> str:
        if name.upper() in self._kinds:
            return self._kinds[name.upper()]
        for kind in adt_xml.OBJECT_PATHS:
            resp = await self._client().get(adt_xml.object_uri(kind, name))
            if resp.status_code == 200:
                self._kinds[name.upper()] = kind
                return kind
        raise AdtError(f"{name} no existe en {self.system}")

    async def _uri(self, name: str) -> str:
        return adt_xml.object_uri(await self._kind_of(name), name)

    async def read_object(self, name: str) -> SapObject | None:
        try:
            kind = await self._kind_of(name)
        except AdtError:
            return None
        uri = adt_xml.object_uri(kind, name)
        meta = await self._client().get(uri, headers={"Accept": ADT_XML})
        source = await self._client().get(f"{uri}/source/main", headers={"Accept": "text/plain"})
        return SapObject(name.upper(), kind, adt_xml.package_of(meta.text), source.text)

    async def ensure_transport(self, obj: SapObject, text: str, current: str | None) -> str:
        if current:
            return current
        uri = adt_xml.object_uri(obj.type, obj.name)
        resp = await self._send(
            "POST",
            "/sap/bc/adt/cts/transports",
            content=adt_xml.transport_request_xml(obj.package, text, uri),
            headers={"Content-Type": "text/plain", "Accept": "text/plain"},
        )
        number = resp.text.strip().rsplit("/", 1)[-1]
        if not number:
            raise AdtError("SAP no devolvió número de orden de transporte")
        return number

    async def write_object(self, obj: SapObject, transport: str) -> None:
        uri = adt_xml.object_uri(obj.type, obj.name)
        exists = (await self._client().get(uri)).status_code == 200
        if not exists:
            await self._send(
                "POST",
                adt_xml.OBJECT_PATHS[obj.type],
                params={"corrNr": transport},
                content=adt_xml.create_object_xml(
                    obj.type, obj.name, obj.package, obj.name, self._config.language
                ),
                headers={"Content-Type": ADT_XML},
            )
        self._kinds[obj.name.upper()] = obj.type
        lock = await self._send(
            "POST",
            uri,
            params={"_action": "LOCK", "accessMode": "MODIFY"},
            headers={**STATEFUL, "Accept": ADT_XML},
        )
        handle = adt_xml.lock_handle(lock.text)
        try:
            await self._send(
                "PUT",
                f"{uri}/source/main",
                params={"lockHandle": handle, "corrNr": transport},
                content=obj.source.encode(),
                headers={**STATEFUL, "Content-Type": "text/plain; charset=utf-8"},
            )
        finally:
            await self._send(
                "POST", uri, params={"_action": "UNLOCK", "lockHandle": handle}, headers=STATEFUL
            )

    async def syntax_check(self, name: str) -> CheckResult:
        resp = await self._send(
            "POST",
            "/sap/bc/adt/checkruns",
            params={"reporters": "abapCheckRun"},
            content=adt_xml.check_run_xml(await self._uri(name)),
            headers={"Content-Type": ADT_XML, "Accept": ADT_XML},
        )
        return CheckResult(adt_xml.check_findings(resp.text))

    async def activate(self, name: str) -> CheckResult:
        resp = await self._send(
            "POST",
            "/sap/bc/adt/activation",
            params={"method": "activate", "preauditRequested": "true"},
            content=adt_xml.object_references_xml(await self._uri(name), name.upper()),
            headers={"Content-Type": ADT_XML, "Accept": ADT_XML},
        )
        return CheckResult(adt_xml.activation_findings(resp.text))

    async def run_atc(self, name: str) -> CheckResult:
        uri = await self._uri(name)
        worklist = await self._send(
            "POST",
            "/sap/bc/adt/atc/worklists",
            params={"checkVariant": self._config.atc_variant},
            headers={"Accept": "text/plain"},
        )
        worklist_id = worklist.text.strip()
        await self._send(
            "POST",
            "/sap/bc/adt/atc/runs",
            params={"worklistId": worklist_id},
            content=adt_xml.atc_run_xml(uri),
            headers={"Content-Type": ADT_XML, "Accept": ADT_XML},
        )
        result = await self._client().get(
            f"/sap/bc/adt/atc/worklists/{worklist_id}",
            headers={"Accept": "application/atc.worklist.v1+xml"},
        )
        return CheckResult(adt_xml.atc_findings(result.text))

    async def run_unit(self, name: str, assertions: list[Assertion]) -> CheckResult:
        resp = await self._send(
            "POST",
            "/sap/bc/adt/abapunit/testruns",
            content=adt_xml.unit_run_xml(await self._uri(name)),
            headers={"Content-Type": ADT_XML, "Accept": ADT_XML},
        )
        source = await self.read_object(name)
        findings = adt_xml.unit_findings(resp.text)
        findings += assertion_findings(source.source if source else "", assertions)
        return CheckResult(findings)
