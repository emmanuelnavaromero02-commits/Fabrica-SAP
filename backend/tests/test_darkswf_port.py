from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from fabrica.cleancore.checker import CleanCoreChecker
from fabrica.estimation.calculator import (
    compute,
    generate_work_package_breakdown,
    highest_rank_size,
)
from fabrica.mcp_servers.cpimig import (
    cpi_list_flows,
    cpi_status,
    register_cpi_connection,
)
from fabrica.mcp_servers.sapcfg import (
    register_sapcfg_system,
    sapcfg_apply,
    sapcfg_bcset_read,
    sapcfg_ddic,
    sapcfg_ping,
    sapcfg_preview,
    sapcfg_read_table,
)
from fabrica.pipeline.fiori_framework import wrap_prototype
from fabrica.sap.adt_xml import OBJECT_PATHS, create_object_xml
from fabrica.sap.bridge import Assertion, assertion_findings
from fabrica.sla.calculator import calculate_business_hours


def test_clean_core_cloud_detection() -> None:
    bad_source = """
    REPORT zbad_cloud.
    SELECT * FROM mara INTO TABLE @DATA(lt_mara).
    CALL TRANSACTION 'VA01'.
    CALL SCREEN 100.
    SUBMIT zreport.
    WRITE: / 'Hello'.
    EXEC SQL.
    """
    issues = CleanCoreChecker.check_cloud_source(bad_source)
    rules = [i.rule for i in issues]
    assert "CLOUD_DIRECT_TABLE_SELECT" in rules
    assert "CLOUD_CALL_TRANSACTION" in rules
    assert "CLOUD_CALL_SCREEN" in rules
    assert "CLOUD_SUBMIT" in rules
    assert "CLOUD_WRITE" in rules
    assert "CLOUD_EXEC_SQL" in rules


def test_clean_core_cloud_allowed() -> None:
    clean_source = """
    SELECT * FROM i_product INTO TABLE @DATA(lt_prod).
    SELECT * FROM zcustom_table INTO TABLE @DATA(lt_cust).
    """
    issues = CleanCoreChecker.check_cloud_source(clean_source)
    assert len(issues) == 0


def test_clean_core_classic_detection() -> None:
    classic_source = """
    REPORT zclassic.
    MOVE-CORRESPONDING wa1 TO wa2.
    IF NOT wa1-matnr IS INITIAL.
    ENDIF.
    COMMIT WORK.
    """
    issues = CleanCoreChecker.check_classic_source(classic_source)
    rules = [i.rule for i in issues]
    assert "CLASSIC_MOVE_CORR" in rules
    assert "CLASSIC_IF_NOT" in rules
    assert "CLASSIC_COMMIT" in rules


def test_clean_core_naming_conventions() -> None:
    assert CleanCoreChecker.validate_naming("PROG", "L_BAD", "ZFAB") != []
    assert CleanCoreChecker.validate_naming("CLAS", "ZCL_OK", "ZFAB") == []
    assert CleanCoreChecker.validate_naming("DDLS", "ZI_ORDER", "ZFAB") != []
    assert CleanCoreChecker.validate_naming("DDLS", "Z_R_ORDER", "ZFAB") == []
    assert CleanCoreChecker.validate_naming("BDEF", "Z_BP_ORDER", "ZFAB") == []
    assert CleanCoreChecker.validate_naming("SRVD", "Z_SD_ORDER", "ZFAB") == []
    assert CleanCoreChecker.validate_naming("SRVB", "Z_SB_ORDER", "ZFAB") == []


def test_wrap_prototype() -> None:
    body = '<div class="f-card"><h1>Test</h1></div>'
    wrapped = wrap_prototype(body, "Mi Prototipo")
    assert "<!DOCTYPE html>" in wrapped
    assert "Mi Prototipo" in wrapped
    assert "--b: #0a6ed1" in wrapped
    assert body in wrapped
    assert "data-matchcode" in wrapped

    full_page = "<html><body>Already wrapped</body></html>"
    assert wrap_prototype(full_page) == full_page


def test_highest_rank_size() -> None:
    assert highest_rank_size([]) == "XS"
    assert highest_rank_size(["XS", "S"]) == "S"
    assert highest_rank_size(["Small", "Large", "Tiny"]) == "L"
    assert highest_rank_size(["Medium", "Very Large", "M"]) == "XL"


def test_compute_with_axes_and_integrations() -> None:
    items = [{"object": "ZOBJ", "axes": ["Small", "Large"]}]
    result = compute(items, integrations_count=2, integration_unit_hours=10.0)
    assert result.items[0]["size"] == "L"
    assert result.breakdown["integraciones"] == 20.0
    assert result.hours_base > 0
    assert result.hours_total > result.hours_base


def test_generate_work_package_breakdown() -> None:
    res = compute([{"object": "ZORDER", "size": "M"}], integrations_count=1)
    packages = generate_work_package_breakdown(
        parent_subject="Gestor de Pedidos",
        parent_id=42,
        tech_stack="ABAP_RAP",
        estimate=res,
        has_persistence=True,
        has_business_logic=True,
        has_custom_auth=True,
        integrations_count=1,
        has_frontend=True,
    )
    types = [p["type"] for p in packages]
    assert "Test Plan" in types
    assert "Development Iteration" in types
    assert "Backend Task" in types
    assert "UI Task" in types
    assert "Technical QA" in types
    assert "Functional Testing" in types
    assert "UAT" in types
    assert "Deployment" in types


def test_calculate_business_hours() -> None:
    tz = UTC
    monday_morning = datetime(2026, 10, 19, 10, 0, 0, tzinfo=tz)
    monday_afternoon = datetime(2026, 10, 19, 14, 0, 0, tzinfo=tz)
    assert calculate_business_hours(monday_morning, monday_afternoon) == 4.0

    friday_close = datetime(2026, 10, 16, 18, 0, 0, tzinfo=tz)
    monday_open = datetime(2026, 10, 19, 9, 0, 0, tzinfo=tz)
    assert calculate_business_hours(friday_close, monday_open) == 0.0

    night_start = datetime(2026, 10, 19, 20, 0, 0, tzinfo=tz)
    night_end = datetime(2026, 10, 19, 23, 0, 0, tzinfo=tz)
    assert calculate_business_hours(night_start, night_end) == 0.0


def test_adt_xml_support() -> None:
    kinds = ("PROG", "CLAS", "INTF", "DDLS", "BDEF", "SRVD", "SRVB", "DCLS")
    for kind in kinds:
        assert kind in OBJECT_PATHS
        xml = create_object_xml(kind, "ZTEST", "ZFAB", "Desc", "ES")
        assert 'adtcore:name="ZTEST"' in xml
        assert "adtcore:packageRef" in xml


def test_assertion_contract_with_bdd() -> None:
    a1 = Assertion(
        id="AST-01",
        description="Requiere check de stock",
        must_contain="check_stock",
        dado="un pedido con material",
        cuando="se guarda la orden",
        entonces="se valida disponibilidad",
    )
    a2 = Assertion(
        id="AST-02",
        description="Requiere log de auditoria",
        must_contain="write_audit",
    )
    code = "DATA(x) = check_stock( )."
    findings = assertion_findings(code, [a1, a2])
    assert len(findings) == 1
    assert "AST-02" in findings[0].message


@pytest.mark.asyncio
async def test_cpimig_tools() -> None:
    register_cpi_connection(
        "test_cpi",
        {
            "nombre": "Test CPI",
            "source": {
                "host": "source.cpi",
                "token_host": "auth.cpi",
                "oauth_id": "id",
                "oauth_secret": "sec",
                "kind": "cf",
            },
            "target": {
                "host": "target.cpi",
                "token_host": "auth.cpi",
                "oauth_id": "id",
                "oauth_secret": "sec",
                "kind": "cf",
            },
        },
    )

    with patch("fabrica.mcp_servers.cpimig._tenant_probe") as mock_probe:
        mock_probe.return_value = {"ok": True, "integration_packages": 5}
        status = await cpi_status("test_cpi")
        assert status["ok"] is True
        assert status["source"]["integration_packages"] == 5

    with patch("fabrica.mcp_servers.cpimig.CpiClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = [{"Id": "PKG_1"}]
        flows = await cpi_list_flows("test_cpi", side="source")
        assert flows["ok"] is True
        assert len(flows["packages"]) == 1


@pytest.mark.asyncio
async def test_sapcfg_tools() -> None:
    register_sapcfg_system(
        "TEST_SYS",
        {"host": "sap.test", "client": "100", "username": "user", "password": "pwd"},
    )

    with patch("fabrica.mcp_servers.sapcfg._call", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = {"ok": True, "pong": True}
        res_ping = await sapcfg_ping("TEST_SYS")
        assert res_ping["ok"] is True

        mock_call.return_value = {"ok": True, "fields": ["MANDT", "WERKS"]}
        res_ddic = await sapcfg_ddic("T001W", "TEST_SYS")
        assert res_ddic["ok"] is True

        mock_call.return_value = {"ok": True, "rows": [{"MANDT": "100", "WERKS": "1000"}]}
        res_table = await sapcfg_read_table("T001W", sap_system_id="TEST_SYS")
        assert res_table["ok"] is True

        mock_call.return_value = {"ok": True, "bcset": "ZBC_01"}
        res_read = await sapcfg_bcset_read("ZBC_01", "TEST_SYS")
        assert res_read["ok"] is True

    with patch("fabrica.mcp_servers.sapcfg._call", new_callable=AsyncMock) as mock_call:
        mock_call.side_effect = [
            {"ok": True, "written": True},
            {"ok": True, "simulation": True},
        ]
        res_prev = await sapcfg_preview("ZBC_01", "Test", [], sap_system_id="TEST_SYS")
        assert res_prev["write"]["ok"] is True
        assert res_prev["simulate"]["ok"] is True

    with patch("fabrica.mcp_servers.sapcfg._call", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = {"ok": True, "activated": True}
        res_apply = await sapcfg_apply("ZBC_01", sap_system_id="TEST_SYS")
        assert res_apply["ok"] is True
