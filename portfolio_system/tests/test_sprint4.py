"""Sprint 4 fixtures — 收益層(§6.2)+ 基準(§5)+ 歷史匯率(§2)+ FastAPI(§7)+ 顧問。

真數沿用 2026-07-10 價格。收益三口徑用 SPEC §8 fixture 8 對數(0941 含息 = 252,386)。
"""
import os
import warnings
from datetime import date, datetime
import pytest

warnings.filterwarnings("ignore")

from app.models import make_session
from app.importer import import_stockerx_csv
from app.fifo import rebuild_lots
from app import income, benchmark, fx, config, rules
from app.prices import store_eod

from tests.test_sprint2 import PX_20260710, VAL_DATE

CSV = os.path.join(os.path.dirname(__file__), "data", "Stock-20260711.csv")


@pytest.fixture(scope="module")
def session():
    s = make_session()
    import_stockerx_csv(s, CSV)
    rebuild_lots(s)
    rules.seed_rules(s)
    return s


# ---- 收益三口徑:0941 含息未實現 = 252,386(同 SPEC §8 fixture 8 對數) ----
def test_position_returns_three_tracks(session):
    pr = income.position_returns(session, PX_20260710, VAL_DATE)["0941.HK"]
    assert pr["price_only_unreal_hkd"] == pytest.approx(134850, abs=5)
    assert pr["held_div_hkd"] == pytest.approx(117536, abs=1)
    assert pr["with_div_unreal_hkd"] == pytest.approx(252386, abs=5)   # ①+持有期股息
    # yield-on-cost = 滾動12個月股息 ÷ open lots 成本(唔靠現價)
    assert pr["yield_on_cost"] is not None and 0.05 < pr["yield_on_cost"] < 0.20


def test_dividends_detail_three_windows(session):
    dd = income.dividends_detail(session, VAL_DATE)["0941.HK"]
    assert dd["lifetime_hkd"] == pytest.approx(117536, abs=1)
    assert dd["held_hkd"] == pytest.approx(117536, abs=1)     # open lots 揸足全程
    assert dd["rolling_12m_hkd"] == pytest.approx(26350, abs=1)   # 近一年 3 期


def test_dividends_detail_reconciles_total(session):
    """三口徑嘅 lifetime 合計必須 = metrics 總股息 242,797(口徑一致)。"""
    from app import metrics
    total = sum(d["lifetime_hkd"] for d in income.dividends_detail(session).values())
    assert total == pytest.approx(metrics.dividends_by_symbol(session)["_total"], abs=1)
    assert total == pytest.approx(242797, abs=1)


# ---- 詳細收益(§6.2):三組成分開 + 分母口徑可切 ----
def test_income_summary_basis(session):
    open_b = income.income_summary(session, PX_20260710, VAL_DATE, basis="open_cost")
    total_b = income.income_summary(session, PX_20260710, VAL_DATE, basis="total_in")
    # 三組成金額口徑一致(唔隨 basis 變)
    assert open_b["realized_hkd"] == total_b["realized_hkd"] == pytest.approx(300441, abs=50)
    assert open_b["dividends_hkd"] == pytest.approx(242797, abs=1)
    # 分母唔同 → % 唔同,但標籤要講清楚
    assert open_b["denom_label"] == "現時持倉成本"
    assert total_b["denom_label"].startswith("歷史總投入")
    assert total_b["denom_hkd"] > open_b["denom_hkd"]        # 累計買入 > 現時持倉成本


def test_monthly_dividends_reconciles(session):
    assert sum(income.monthly_dividends(session).values()) == pytest.approx(242797, abs=1)


# ---- 基準:total return 口徑 + 指數化曲線 ----
def test_benchmark_total_return():
    prov = benchmark.ManualBenchmarkProvider(
        {"VOO": {date(2026, 1, 1): 600.0, date(2026, 7, 10): 693.86}})
    c = benchmark.compare(prov, ["VOO"], date(2026, 1, 1), date(2026, 7, 10),
                          portfolio_twrr=0.05)
    assert c["VOO"]["total_return"] == pytest.approx(693.86 / 600 - 1, abs=1e-6)
    assert c["VOO"]["curve"][date(2026, 1, 1)] == pytest.approx(100.0)
    assert c["_portfolio"]["total_return"] == 0.05


def test_benchmark_empty_series_none():
    prov = benchmark.ManualBenchmarkProvider({})
    assert benchmark.total_return(prov.series("VOO", date(2026, 1, 1),
                                              date(2026, 7, 10))) is None


# ---- 歷史匯率:backfill + as-of 查表 + 一鍵切換(fixed 口徑保證唔破壞) ----
def test_fx_historical_mode():
    s = make_session()
    prov = benchmark.ManualBenchmarkProvider(
        {"USDHKD=X": {date(2026, 1, 2): 7.79, date(2026, 4, 1): 7.83,
                      date(2026, 7, 9): 7.82}})
    n = fx.backfill_usdhkd(s, date(2026, 1, 1), date(2026, 7, 10), provider=prov)
    assert n == 3
    try:
        fx.load_rates(s)
        assert config.FX_MODE == "historical"
        assert config.to_hkd(100, "USD", date(2026, 7, 9)) == pytest.approx(782.0)
        assert config.to_hkd(100, "USD", date(2026, 3, 1)) == pytest.approx(779.0)  # as-of 較早
        assert config.to_hkd(100, "USD", date(2025, 1, 1)) == pytest.approx(779.0)  # 早過最舊→用最舊
        assert config.to_hkd(100, "USD") == pytest.approx(782.0)                    # 冇日期→最新
        assert "歷史" in config.fx_note()
        # 聯匯 band 內:歷史同固定偏差 ≤0.6%
        assert abs(config.to_hkd(100, "USD") / (100 * config.USDHKD_FIXED) - 1) < 0.006
    finally:
        fx.use_fixed()                                        # 一定切返,唔污染其他測試
    assert config.FX_MODE == "fixed"
    assert config.to_hkd(100, "USD") == pytest.approx(780.0)


def test_fx_load_without_data_raises():
    s = make_session()
    with pytest.raises(ValueError):
        fx.load_rates(s)                                      # 冇 backfill → 唔准靜靜跌返 fixed


# ---- FastAPI(§7 七個 endpoint) ----
@pytest.fixture(scope="module")
def client():
    os.environ["PORTFOLIO_DB_URL"] = "sqlite:///:memory:"
    from fastapi.testclient import TestClient
    import api.main as apimod
    apimod._session = None                                    # 每次乾淨 in-memory
    s = apimod.db()
    import_stockerx_csv(s, CSV)
    rebuild_lots(s)
    store_eod(s, VAL_DATE, PX_20260710)
    return TestClient(apimod.app)


def test_api_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_api_positions_both_basis(client):
    open_r = client.get("/positions?basis=open_lots").json()
    assert open_r["positions"]["TSLA"]["shares"] == pytest.approx(860)
    assert client.get("/positions?basis=lifetime").json()["basis"] == "lifetime"
    assert client.get("/positions?basis=bad").status_code == 422  # 只准兩個口徑


def test_api_metrics_summary(client):
    ms = client.get("/metrics/summary").json()
    assert ms["realized"]["rounds"] == 24
    assert ms["win_rate_dual_track"]["true_win_rate"] == pytest.approx(0.6512, abs=1e-3)
    assert ms["dividends_total_hkd"] == pytest.approx(242797, abs=1)


def test_api_transaction_blocks_then_override(client):
    body = {"symbol": "TSLA", "side": "BUY", "price": 407.76, "qty": 100,
            "prices": PX_20260710}
    blocked = client.post("/transactions", json=body).json()
    assert blocked["committed"] is False
    assert {v["rule"] for v in blocked["violations"]} >= {"MAX_POSITION_WEIGHT"}
    ok = client.post("/transactions", json={**body, "override": True}).json()
    assert ok["committed"] is True and ok["violations_recorded"] >= 1


def test_api_assets_and_bad_category(client):
    r = client.post("/assets_other",
                    json={"category": "cash", "name": "滙豐", "ccy": "HKD",
                          "value": 100000})
    assert r.status_code == 200
    assert "流動資金" in r.json()["net_worth"]["blocks"]
    assert client.post("/assets_other",
                       json={"category": "xyz", "name": "x", "ccy": "HKD",
                             "value": 1}).status_code == 400


def test_api_advisor_offline(client):
    """冇 API key → offline 模式回快照,唔爆;快照含集中度事實。"""
    r = client.post("/advisor/ask",
                    json={"question": "我集中度風險?", "prices": PX_20260710})
    j = r.json()
    assert j["offline"] is True
    assert "TSLA" in j["context"]["position_weights"]
    assert "唔構成" in j["answer"] or "免責" in j["answer"] or "投資建議" in j["answer"]


def test_api_import_csv_upload(client):
    with open(CSV, "rb") as f:
        r = client.post("/import/csv", files={"file": ("Stock.csv", f, "text/csv")})
    assert r.status_code == 200
    # MRVL 零股行必須喺 validation report(同 fixture 5 一致)
    assert any(x["raw"].get("Stock Symbol") == "MRVL" for x in r.json()["rejected"])
