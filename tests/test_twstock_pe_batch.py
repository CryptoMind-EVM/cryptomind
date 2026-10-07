"""台股本益比批次端點：一次查多檔，欄位與單檔端點一致（2026-09-26）。

台股頁以前每檔各打一次 /api/twstock/opendata/pe_ratio/{symbol}，一次 10 個請求。
後端本來就是抓 TWSE 全表（有快取）再篩代號，批次端點只是一次篩多個。
"""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

BWIBBU_D = [
    {"Code": "2330", "Name": "台積電", "Date": "1150926", "PEratio": "25.1", "DividendYield": "1.6", "PBratio": "7.2", "DividendYear": "114", "FiscalYearQuarter": "114/2"},
    {"Code": "2317", "Name": "鴻海", "Date": "1150926", "PEratio": "14.0", "DividendYield": "3.1", "PBratio": "1.9", "DividendYear": "114", "FiscalYearQuarter": "114/2"},
    {"Code": "2317", "Name": "重複列（不該被取用）", "PEratio": "99"},
]
BWIBBU_ALL = [
    {"Code": "6505", "Name": "台塑化", "Date": "1150926", "PEratio": "40.0", "DividendYield": "2.0", "PBratio": "2.1"},
]


@pytest.fixture
def client(monkeypatch):
    from api.routers import twstock

    calls = []

    async def fake_fetch(url, params=None, cache_key=None):
        calls.append(cache_key)
        return BWIBBU_D if url.endswith("BWIBBU_d") else BWIBBU_ALL

    monkeypatch.setattr(twstock, "_fetch_twse", fake_fetch)
    from api_server import app

    c = TestClient(app)
    c.calls = calls
    return c


def test_batch_returns_rows_in_request_order_with_single_endpoint_fields(client):
    res = client.get("/api/twstock/opendata/pe_ratio?symbols=2317,2330,6505,9999")
    assert res.status_code == 200
    data = res.json()["data"]
    assert [d["code"] for d in data] == ["2317", "2330", "6505"]  # 9999 查無 → 略過
    assert data[0]["name"] == "鴻海" and data[0]["pe_ratio"] == "14.0"  # 同代號取第一筆
    single = client.get("/api/twstock/opendata/pe_ratio/2330").json()
    assert data[1] == single, "批次每一筆必須跟單檔端點完全相同"


def test_batch_fetches_each_twse_table_at_most_once(client):
    client.get("/api/twstock/opendata/pe_ratio?symbols=2330,2317,6505")
    assert client.calls.count("twse_pe_all") == 1
    assert client.calls.count("twse_pe_all2") == 1  # 6505 不在 BWIBBU_d → 查一次後備表


def test_batch_skips_fallback_table_when_all_found(client):
    client.get("/api/twstock/opendata/pe_ratio?symbols=2330,2317")
    assert "twse_pe_all2" not in client.calls


@pytest.mark.parametrize("qs", ["", "?symbols=", "?symbols=2330,../etc", "?symbols=" + "A" * 11])
def test_batch_rejects_bad_symbols(client, qs):
    assert client.get("/api/twstock/opendata/pe_ratio" + qs).status_code == 400


def test_batch_caps_at_20_symbols(client):
    codes = ",".join(["2330"] * 5 + [str(1000 + i) for i in range(30)])
    res = client.get("/api/twstock/opendata/pe_ratio?symbols=" + codes)
    assert res.status_code == 200  # 去重後取前 20 個；只有 2330 查得到
    assert [d["code"] for d in res.json()["data"]] == ["2330"]


def test_single_endpoint_unchanged(client):
    assert client.get("/api/twstock/opendata/pe_ratio/6505").json()["name"] == "台塑化"
    assert client.get("/api/twstock/opendata/pe_ratio/9999").status_code == 404
