"""規格書 §8 測試 fixtures — 全部用業主真實交易數據驗證。

呢八條測試係由 StockerX 畫面數字逆向對數得出:全綠 = 引擎同舊 app 接得上。
任何改動(FIFO/importer/metrics)都要保持全綠先准 merge。
"""
import os
import pytest

from app.models import make_session
from app.importer import import_stockerx_csv
from app.fifo import rebuild_lots
from app import metrics

CSV = os.path.join(os.path.dirname(__file__), "data", "Stock-20260711.csv")


@pytest.fixture(scope="module")
def session():
    s = make_session()
    report = import_stockerx_csv(s, CSV)
    rebuild_lots(s)
    s.info["report"] = report
    return s


# ---- Fixture 5:MRVL 零股零價行必須被 validation 擋落 ----
def test_5_zero_qty_row_rejected(session):
    report = session.info["report"]
    reasons = [r[1] for r in report.rejected]
    assert any("股數必須 > 0" in r for r in reasons), "零股行冇被擋"
    rejected_syms = [r[2].get("Stock Symbol") for r in report.rejected]
    assert "MRVL" in rejected_syms


# ---- Fixture 1:領展 2019 — 淨蝕 1,003(蝕在手續費) ----
def test_1_link_reit_fee_loss(session):
    rts = [r for r in metrics.round_trips(session) if r["symbol"] == "0823.HK"]
    assert len(rts) == 1
    assert rts[0]["pnl_hkd"] == pytest.approx(-1003.15, abs=0.5)


# ---- Fixture 2:TSLA open = 860 股,平均成本 341.6562 ----
def test_2_tsla_open_position(session):
    pos = metrics.open_positions(session)["TSLA"]
    assert pos["shares"] == pytest.approx(860, abs=1e-6)
    assert pos["avg_cost"] == pytest.approx(341.6562, abs=0.001)


# ---- Fixture 3:MU 現時持倉收益 −1,414 USD;lifetime 已實現另計 ----
def test_3_mu_open_vs_lifetime(session):
    upl = metrics.open_position_pnl(session, {"MU": 979.30})["MU"]
    assert upl["unreal_hkd"] == pytest.approx(-1414 * 7.8, abs=5)
    realized = sum(r["pnl_hkd"] for r in metrics.round_trips(session)
                   if r["symbol"] == "MU")
    assert realized == pytest.approx(2740 * 7.8, abs=5)   # 兩個口徑必須分開
    # StockerX 會顯示 realized+unreal=+10,343(綠色)— 我哋唔准咁做


# ---- Fixture 4:NVDA $0 送股 — cost=0,唔准除以零 ----
def test_4_nvda_bonus_shares(session):
    pos = metrics.open_positions(session)["NVDA"]
    assert pos["shares"] == pytest.approx(1.35297, abs=1e-5)
    assert pos["cost_ccy"] == pytest.approx(0.0, abs=1e-6)
    upl = metrics.open_position_pnl(session, {"NVDA": 210.96})["NVDA"]
    assert upl["unreal_pct"] is None          # 成本 0 → % 回 None 而唔係爆 ZeroDivisionError
    assert upl["unreal_hkd"] == pytest.approx(1.35297 * 210.96 * 7.8, abs=1)


# ---- Fixture 6:9888.HK open = 2400 股 @ 140.9167 ----
def test_6_bidu_open_position(session):
    pos = metrics.open_positions(session)["9888.HK"]
    assert pos["shares"] == pytest.approx(2400, abs=1e-6)
    assert pos["avg_cost"] == pytest.approx(140.9167, abs=0.001)


# ---- Fixture 7:全史已實現 24 回合,+300,441,勝率 91.7%,賺賠比 2.37 ----
def test_7_realized_summary(session):
    s = metrics.realized_summary(session)
    assert s["rounds"] == 24
    assert s["total_realized_hkd"] == pytest.approx(300441, abs=50)
    assert s["win_rate"] == pytest.approx(22 / 24, abs=0.001)
    assert s["pl_ratio"] == pytest.approx(2.37, abs=0.02)
    assert s["max_consecutive_losses"] == 1


# ---- Fixture 8:股息 — 全史 242,797;0941 含息總回報 = +252,386 ----
def test_8_dividends(session):
    divs = metrics.dividends_by_symbol(session)
    assert divs["_total"] == pytest.approx(242797, abs=1)
    assert divs["0941.HK"] == pytest.approx(117536, abs=1)
    assert divs["0883.HK"] == pytest.approx(71100, abs=1)
    assert divs["3416.HK"] == pytest.approx(22660, abs=1)
    assert divs["2802.HK"] == pytest.approx(20464, abs=1)

    tr = metrics.total_return_with_div(session, {"0941.HK": 78.75})["0941.HK"]
    assert tr["unreal_hkd"] == pytest.approx(134850, abs=5)
    assert tr["unreal_hkd"] + tr["dividends_hkd"] == pytest.approx(252386, abs=5)
    # ↑ 同 StockerX 截圖嘅中移動「總收益 252,386」完全吻合
