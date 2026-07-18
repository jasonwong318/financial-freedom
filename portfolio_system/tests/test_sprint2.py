"""Sprint 2 fixtures — 價格層 + XIRR + snapshot + TWRR。

價格用 2026-07-10 收市(StockerX 截圖對過數),離線環境唔行 yfinance。
"""
import os
from datetime import date
import pytest

from app.models import make_session
from app.importer import import_stockerx_csv
from app.fifo import rebuild_lots
from app.prices import ManualPriceProvider, store_eod, latest_prices
from app import performance as perf

CSV = os.path.join(os.path.dirname(__file__), "data", "Stock-20260711.csv")

PX_20260710 = {
    "TSLA": 407.76, "XYZ": 77.30, "LITE": 802.01, "SPCX": 145.30,
    "MRVL": 235.81, "NVTS": 13.47, "GME": 21.68, "GOOGL": 357.18,
    "VOO": 693.86, "MU": 979.30, "NVDA": 210.96,
    "9988.HK": 110.20, "9888.HK": 115.40, "3896.HK": 5.68, "2802.HK": 7.24,
    "3416.HK": 8.575, "3466.HK": 19.06, "0883.HK": 21.82, "0941.HK": 78.75,
}
VAL_DATE = date(2026, 7, 10)


@pytest.fixture(scope="module")
def session():
    s = make_session()
    import_stockerx_csv(s, CSV)
    rebuild_lots(s)
    return s


# ---- XIRR 基本正確性:−100 一年後變 +110 → 10% ----
def test_xirr_synthetic():
    r = perf.xirr([(date(2020, 1, 1), -100), (date(2021, 1, 1), 110)])
    assert r == pytest.approx(0.10, abs=1e-3)


def test_xirr_no_solution_returns_none():
    assert perf.xirr([(date(2020, 1, 1), -100)]) is None   # 冇正流 → None,唔爆


# ---- 價格層:store + latest ----
def test_prices_store_and_latest(session):
    provider = ManualPriceProvider(PX_20260710)
    got = provider.get_eod(list(PX_20260710), VAL_DATE)
    n = store_eod(session, VAL_DATE, got)
    assert n == len(PX_20260710)
    lp = latest_prices(session)
    assert lp["TSLA"] == pytest.approx(407.76)
    assert lp["0941.HK"] == pytest.approx(78.75)


# ---- Snapshot:NAV 必須同人手對數結果一致(±0.1%) ----
def test_snapshot_nav(session):
    nav, exposure = perf.build_snapshot(session, VAL_DATE, PX_20260710)
    assert nav == pytest.approx(5_381_760, rel=0.001)      # 之前逆向對數:StockerX 顯示 5.40M(即時FX)
    assert exposure["TSLA"] == pytest.approx(0.508, abs=0.005)   # 集中度 50.8%
    assert abs(sum(exposure.values()) - 1.0) < 0.01


# ---- 組合 XIRR:由交易現金流推導,唔使入金記錄 ----
def test_portfolio_xirr(session):
    r = perf.portfolio_xirr(session, VAL_DATE, PX_20260710)
    assert r is not None and 0.0 < r < 1.0                  # 合理範圍
    # 釘死當前值做 regression 基準(改 FIFO/現金流邏輯如果影響到佢,必須解釋)
    assert r == pytest.approx(0.0691, abs=0.003)


def test_portfolio_xirr_missing_price_raises(session):
    px = {k: v for k, v in PX_20260710.items() if k != "TSLA"}
    with pytest.raises(ValueError):
        perf.portfolio_xirr(session, VAL_DATE, px)          # 欠價要爆,唔准俾錯數


# ---- TWRR:兩個 snapshot 鏈式(合成例) ----
def test_twrr_synthetic():
    s = make_session()
    import_stockerx_csv(s, CSV)
    rebuild_lots(s)
    perf.build_snapshot(s, date(2026, 7, 9),
                        {k: v * 0.99 for k, v in PX_20260710.items()})
    perf.build_snapshot(s, date(2026, 7, 10), PX_20260710)
    r = perf.twrr(s)
    assert r is not None
    assert r == pytest.approx(1 / 0.99 - 1, abs=1e-6)       # 兩日之間冇交易 → 純價格回報
