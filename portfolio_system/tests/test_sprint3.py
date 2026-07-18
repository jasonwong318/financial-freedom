"""Sprint 3 fixtures — 規則引擎 + 行為指標 + 全資產 + 報表。

真數部分沿用 2026-07-10 價格(同 Sprint 2 一致);規則行為用合成數逐條驗證。
"""
import os
from datetime import date, datetime
import pytest

from app.models import make_session, Instrument, Transaction, Rule, RuleViolation
from app.importer import import_stockerx_csv
from app.fifo import rebuild_lots
from app import rules, behavior, assets, reports
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


def _synthetic(txns):
    """細規模合成 session:txns = [(dt, symbol, type, price, qty, fee, ccy)]。"""
    s = make_session()
    insts = {}
    for dt, sym, typ, px, qty, fee, ccy in txns:
        if sym not in insts:
            inst = Instrument(symbol=sym, market="HK" if sym.endswith(".HK") else "US",
                              ccy=ccy)
            s.add(inst)
            s.flush()
            insts[sym] = inst
        s.add(Transaction(account_id=1, instrument_id=insts[sym].id,
                          trade_dt=dt, type=typ, price=px, qty=qty,
                          fee=fee, ccy=ccy))
    s.commit()
    rebuild_lots(s)
    rules.seed_rules(s)
    return s


# ---- 規則種子:冪等 + 十條齊 ----
def test_seed_rules_idempotent(session):
    assert {r.code for r in session.query(Rule).all()} == set(rules.DEFAULT_RULES)
    rules.seed_rules(session)                       # 再跑一次唔會重複
    assert session.query(Rule).count() == len(rules.DEFAULT_RULES)


# ---- 雙軌勝率:91.7% 已實現 vs 65.1% 真實(規格書 §0 嘅核心不滿) ----
def test_dual_track_win_rate(session):
    w = behavior.dual_track_win_rate(session, PX_20260710)
    assert w["realized_win_rate"] == pytest.approx(22 / 24, abs=1e-4)
    assert w["true_win_rate"] == pytest.approx(28 / 43, abs=1e-4)   # 0.6512
    assert w["open_positions"] == 19
    assert "XYZ" in w["open_losers"] and "TSLA" in w["open_winners"]


# ---- 歷史重掃:領展手續費事件 + TSLA 沽完高追 必須捕捉到 ----
def test_scan_history_real_data(session):
    vs = rules.scan_history(session)
    fee = [v for v in vs if v["rule"] == "FEE_CHECK"]
    assert [v["symbol"] for v in fee] == ["0823.HK"]        # 得一單:領展 2019
    rebuy = [v for v in vs if v["rule"] == "REBUY_HIGHER"]
    assert any(v["symbol"] == "TSLA" for v in rebuy)        # 2021-02 沽 153 追 290
    avg = [v for v in vs if v["rule"] == "AVG_DOWN_LIMIT"]
    assert any(v["symbol"] == "TSLA" for v in avg)          # 2025 年溝足 8 次


# ---- 狀態掃描:TSLA 集中度 + XYZ/GME 死揸 ----
def test_scan_portfolio_real_data(session):
    vs = rules.scan_portfolio(session, PX_20260710, VAL_DATE)
    w = [v for v in vs if v["rule"] == "MAX_POSITION_WEIGHT"]
    assert [v["symbol"] for v in w] == ["TSLA"]
    assert w[0]["weight_pct"] == pytest.approx(50.8, abs=0.3)
    stale = {v["symbol"] for v in vs if v["rule"] == "STALE_LOSER"}
    assert {"XYZ", "GME"} <= stale                          # 死揸實錘
    stop = {v["symbol"] for v in vs if v["rule"] == "STOP_LOSS_ALERT"}
    assert "XYZ" in stop and "NVTS" in stop


# ---- 違規成本:FEE_CHECK 0823 結果必須等於該回合已實現 −1,003 ----
def test_violation_outcomes(session):
    outs, cost = behavior.violation_outcomes(session, PX_20260710)
    fee = [o for o in outs if o["rule"] == "FEE_CHECK"][0]
    assert fee["outcome_hkd"] == pytest.approx(-1003, abs=1)
    assert cost == pytest.approx(155257, abs=50)            # regression 釘死
    assert all("outcome_hkd" in o for o in outs)


# ---- 違規入庫:dedupe(nightly 重掃唔會疊加) ----
def test_record_violations_dedupe(session):
    vs = rules.scan_history(session)
    n1 = rules.record_violations(session, vs)
    assert n1 == len(vs)
    assert rules.record_violations(session, vs) == 0        # 再記一次 → 0 新增
    assert session.query(RuleViolation).count() == n1


# ---- Pre-trade:再買 TSLA 必須彈集中度 + 大注雙警示 ----
def test_check_trade_tsla_concentration(session):
    vs = rules.check_trade(session, PX_20260710, symbol="TSLA", side="BUY",
                           price=407.76, qty=100)
    codes = {v["rule"] for v in vs}
    assert "MAX_POSITION_WEIGHT" in codes
    assert "MAX_SINGLE_ENTRY" in codes                      # 31.8萬 > 5% 總資產


# ---- Pre-trade:板塊集中度(參數可調——調低上限後 MU 買入要彈) ----
def test_check_trade_sector_weight():
    s = make_session()
    import_stockerx_csv(s, CSV)
    rebuild_lots(s)
    rules.seed_rules(s)
    r = s.query(Rule).filter_by(code="MAX_SECTOR_WEIGHT").first()
    r.params = {"pct": 2}                                   # AI半導體現況 >2%
    s.commit()
    vs = rules.check_trade(s, PX_20260710, symbol="MU", side="BUY",
                           price=979.30, qty=1)
    sec = [v for v in vs if v["rule"] == "MAX_SECTOR_WEIGHT"]
    assert sec and sec[0]["sector"] == "AI半導體"


# ---- Pre-trade 合成:溝貨紀律 ----
def test_check_trade_avg_down_synthetic():
    s = _synthetic([(datetime(2026, 1, 5), "AAA.HK", "BUY", 100.0, 100, 0, "HKD")])
    # 只跌 5% 就溝 → 彈
    vs = rules.check_trade(s, {"AAA.HK": 95.0}, symbol="AAA.HK", side="BUY",
                           price=95.0, qty=100, trade_dt=datetime(2026, 2, 1))
    avg = [v for v in vs if v["rule"] == "AVG_DOWN_LIMIT"]
    assert avg and any("只跌" in r for r in avg[0]["reasons"])
    # 跌足 20% 但注碼 160% → 都要彈(注碼理由)
    vs = rules.check_trade(s, {"AAA.HK": 80.0}, symbol="AAA.HK", side="BUY",
                           price=80.0, qty=200, trade_dt=datetime(2026, 2, 1))
    avg = [v for v in vs if v["rule"] == "AVG_DOWN_LIMIT"]
    assert avg and any("注碼" in r for r in avg[0]["reasons"])
    # 高過平均成本加倉 ≠ 溝貨 → 唔彈
    vs = rules.check_trade(s, {"AAA.HK": 110.0}, symbol="AAA.HK", side="BUY",
                           price=110.0, qty=10, trade_dt=datetime(2026, 2, 1))
    assert not [v for v in vs if v["rule"] == "AVG_DOWN_LIMIT"]


# ---- Pre-trade 合成:沽完高追 ----
def test_check_trade_rebuy_higher_synthetic():
    s = _synthetic([
        (datetime(2026, 1, 5), "BBB.HK", "BUY", 100.0, 100, 0, "HKD"),
        (datetime(2026, 1, 10), "BBB.HK", "SELL", 100.0, 100, 0, "HKD"),
    ])
    vs = rules.check_trade(s, {}, symbol="BBB.HK", side="BUY",
                           price=115.0, qty=100, trade_dt=datetime(2026, 1, 20))
    assert any(v["rule"] == "REBUY_HIGHER" for v in vs)     # 10日內高 15% 追返
    vs = rules.check_trade(s, {}, symbol="BBB.HK", side="BUY",
                           price=115.0, qty=100, trade_dt=datetime(2026, 6, 20))
    assert not any(v["rule"] == "REBUY_HIGHER" for v in vs) # 過咗 90 日 → 唔算


# ---- Pre-trade 合成:一週熔斷 ----
def test_check_trade_circuit_breaker_synthetic():
    s = _synthetic([
        (datetime(2026, 1, 5), "CCC.HK", "BUY", 100.0, 1000, 0, "HKD"),
        (datetime(2026, 1, 12), "CCC.HK", "SELL", 50.0, 1000, 0, "HKD"),  # −50,000
    ])
    vs = rules.check_trade(s, {}, symbol="CCC.HK", side="BUY",
                           price=50.0, qty=100, trade_dt=datetime(2026, 1, 14),
                           total_assets=1_000_000)          # 蝕 5% > 2% 上限
    assert any(v["rule"] == "WEEKLY_CIRCUIT_BREAKER" for v in vs)
    vs = rules.check_trade(s, {}, symbol="CCC.HK", side="BUY",
                           price=50.0, qty=100, trade_dt=datetime(2026, 3, 1),
                           total_assets=1_000_000)          # 一週後 → 冇事
    assert not any(v["rule"] == "WEEKLY_CIRCUIT_BREAKER" for v in vs)


# ---- Pre-trade 合成:高位追貨(要 prices_eod 有 20 日數據) ----
def test_check_trade_chase_high_synthetic():
    s = _synthetic([(datetime(2026, 1, 5), "DDD.HK", "BUY", 90.0, 100, 0, "HKD")])
    for i in range(1, 21):                                   # 20 日 EOD,高位 100
        store_eod(s, date(2026, 1, i), {"DDD.HK": 80.0 + i})
    vs = rules.check_trade(s, {"DDD.HK": 99.0}, symbol="DDD.HK", side="BUY",
                           price=99.0, qty=10, trade_dt=datetime(2026, 1, 22))
    assert any(v["rule"] == "CHASE_HIGH" for v in vs)        # 99 喺高位 3% 內
    vs = rules.check_trade(s, {"DDD.HK": 90.0}, symbol="DDD.HK", side="BUY",
                           price=90.0, qty=10, trade_dt=datetime(2026, 1, 22))
    assert not any(v["rule"] == "CHASE_HIGH" for v in vs)


# ---- Pre-trade 合成:FEE_CHECK 沽出前警示(領展情境重演) ----
def test_check_trade_fee_check_synthetic():
    s = _synthetic([(datetime(2019, 11, 26), "0823.HK", "BUY", 82.0, 5000,
                     749.07, "HKD")])
    vs = rules.check_trade(s, {"0823.HK": 82.1}, symbol="0823.HK", side="SELL",
                           price=82.1, qty=5000, fee=754.08)
    fee = [v for v in vs if v["rule"] == "FEE_CHECK"]
    assert fee and fee[0]["gross_ccy"] == pytest.approx(500, abs=1)


# ---- 全資產:四區塊 + 淨資產(Percento 式) ----
def test_assets_net_worth(session):
    assets.upsert_asset(session, "cash", "滙豐往來", "HKD", 100_000, date(2026, 7, 1))
    assets.upsert_asset(session, "mpf", "永明MPF", "HKD", 200_000, date(2026, 7, 1))
    assets.upsert_asset(session, "liability", "稅貸", "HKD", 50_000, date(2026, 7, 1))
    # 同日覆蓋(更正);唔同日係新快照,latest 攞最新
    assets.upsert_asset(session, "cash", "滙豐往來", "HKD", 120_000, date(2026, 7, 1))
    assets.upsert_asset(session, "cash", "滙豐往來", "HKD", 130_000, date(2026, 8, 1))

    nw = assets.net_worth(session, PX_20260710)
    equity = nw["equity_mv_hkd"]
    assert equity == pytest.approx(5_381_760, rel=0.001)     # 同 snapshot NAV 一致
    assert nw["blocks"]["流動資金"] == pytest.approx(130_000)
    assert nw["blocks"]["投資"] == pytest.approx(equity + 200_000)
    assert nw["blocks"]["負債"] == pytest.approx(50_000)
    assert nw["total_assets_hkd"] == pytest.approx(equity + 330_000)
    assert nw["net_worth_hkd"] == pytest.approx(equity + 330_000 - 50_000)
    with pytest.raises(ValueError):
        assets.upsert_asset(session, "bitcoin", "BTC", "USD", 1, date(2026, 7, 1))


# ---- 報表:月度合計必須同總已實現/總股息啱數(口徑一致性) ----
def test_monthly_pnl_reconciles(session):
    mp = reports.monthly_pnl(session)
    assert sum(v["realized_hkd"] for v in mp.values()) == pytest.approx(300441, abs=50)
    assert sum(v["dividends_hkd"] for v in mp.values()) == pytest.approx(242797, abs=1)
    pivot = reports.monthly_pnl_pivot(session)
    assert not pivot.empty and pivot.index.name == "年"


# ---- 報表:回撤曲線 ----
def test_drawdown_curve():
    series = [(date(2026, 1, 1), 100.0), (date(2026, 2, 1), 120.0),
              (date(2026, 3, 1), 90.0), (date(2026, 4, 1), 130.0)]
    curve, max_dd = reports.drawdown_curve(series)
    assert max_dd == pytest.approx(-0.25)                    # 120 → 90
    assert curve[-1][2] == pytest.approx(0.0)                # 新高 → 回撤 0
