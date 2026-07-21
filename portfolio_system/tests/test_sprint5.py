"""Sprint 5(UI v2 反饋)後端支援測試 —— 簡稱、回合買入資料、蝕貨 lot 明細、顧問後端切換。

呢批係為咗 UI 改版加嘅欄位/分組,鎖死佢哋唔會靜靜地行為改變。
"""
import os
from datetime import date
import pytest

from app.models import make_session
from app.importer import import_stockerx_csv
from app.fifo import rebuild_lots
from app import metrics, behavior, config, advisor, rules

from tests.test_sprint2 import PX_20260710, VAL_DATE

CSV = os.path.join(os.path.dirname(__file__), "data", "Stock-20260711.csv")


@pytest.fixture(scope="module")
def session():
    s = make_session()
    import_stockerx_csv(s, CSV)
    rebuild_lots(s)
    rules.seed_rules(s)
    return s


# ---- 中文簡稱 ----
def test_short_name():
    assert config.short_name("0941.HK") == "中移動"
    assert config.short_name("0883.HK") == "中海油"
    assert config.short_name("TSLA") == "Tesla"
    # 查唔到 → fallback 到傳入英文名,再冇 → symbol 本身
    assert config.short_name("ZZZZ", "SOME CORP") == "SOME CORP"
    assert config.short_name("ZZZZ") == "ZZZZ"


# ---- round_trips 新增買入/沽出資料(已平倉頁用),唔影響原有數 ----
def test_round_trips_entry_exit_fields(session):
    rts = metrics.round_trips(session)
    assert len(rts) == 24                                  # 原有 fixture 7 唔變
    for r in rts:
        assert {"buy_dt", "avg_buy_price", "sell_price", "qty"} <= set(r)
        assert r["buy_dt"] <= r["sell_dt"]                 # 買一定早過沽
        assert r["avg_buy_price"] > 0 and r["qty"] > 0
    # 抽一單核數:MU 2026-06-22 沽 20 股 @1202,買入均價 1075 附近
    mu = [r for r in rts if r["symbol"] == "MU"][0]
    assert mu["sell_price"] == pytest.approx(1202, abs=1)
    assert mu["qty"] == pytest.approx(20, abs=1e-6)
    # 買入均價 1065:(1202−1065)×20 = 2,740 USD 已實現(對 fixture 3)
    assert mu["avg_buy_price"] == pytest.approx(1065, abs=1)


# ---- 蝕貨 lot 明細(行為儀表板分組用):含買入日/買入價/股數 ----
def test_disposition_open_loser_lots_detail(session):
    disp = behavior.disposition_stats(session, PX_20260710, VAL_DATE)
    lots = disp["open_loser_lots"]
    assert lots and all(
        {"symbol", "buy_date", "buy_price", "shares", "days", "unreal_hkd"} <= set(l)
        for l in lots)
    # XYZ(Block)兩批蝕貨,合計 ≈ -169,151(同持倉頁現時持倉收益對數)
    xyz = [l for l in lots if l["symbol"] == "XYZ"]
    assert len(xyz) == 2
    assert sum(l["unreal_hkd"] for l in xyz) == pytest.approx(-169151, abs=50)
    # 每隻出現嘅標的都係「net 蝕緊」(symbol 層 <=0);個別 lot 可以有賺(溝到平),
    # 分組展示要見晒全部 lot 先睇到全貌 —— 所以校驗 symbol 合計而非逐 lot。
    from collections import defaultdict
    by_sym = defaultdict(float)
    for l in lots:
        by_sym[l["symbol"]] += l["unreal_hkd"]
    assert all(v <= 0 for v in by_sym.values())


# ---- 顧問後端切換:冇 key 一律 offline,唔會撞網 ----
def test_advisor_offline_no_key(session, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    res = advisor.ask(session, PX_20260710, "測試", api_key=None)
    assert res["offline"] is True
    assert "TSLA" in res["context"]["position_weights"]


def test_advisor_context_facts_only(session):
    """快照只含事實(市值/權重/勝率/違規),唔含任何預測欄位。"""
    ctx = advisor.build_snapshot_context(session, PX_20260710)
    assert ctx["true_win_rate_with_mtm"] == pytest.approx(0.6512, abs=1e-3)
    assert ctx["total_market_value_hkd"] > 0
    assert isinstance(ctx["current_rule_violations"], list)
    # 唔應該有「預測 / forecast / target price」類 key
    assert not any("forecast" in k or "predict" in k or "target" in k
                   for k in ctx)
