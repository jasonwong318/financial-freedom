"""Sprint 5(UI v2 反饋)後端支援測試 —— 簡稱、回合買入資料、蝕貨 lot 明細、顧問後端切換。

呢批係為咗 UI 改版加嘅欄位/分組,鎖死佢哋唔會靜靜地行為改變。
"""
import os
from datetime import date
import pytest

from app.models import make_session
from app.importer import import_stockerx_csv
from app.fifo import rebuild_lots
from app import (metrics, behavior, config, advisor, rules, committee, income,
                 assets, buckets)

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


# ---- 規則實際要求(UI 顯示用):渲染出具體數字 ----
def test_rule_requirement_concrete():
    req = rules.rule_requirement("AVG_DOWN_LIMIT",
                                 {"n": 2, "min_drop_pct": 15, "max_size_pct": 50})
    assert "2 次" in req and "15%" in req and "50%" in req
    # MAX_POSITION_WEIGHT 而家分層:核心 40 / 地基 20 / 衛星 5
    mpw = rules.rule_requirement("MAX_POSITION_WEIGHT", {"tiered": True})
    assert "40%" in mpw and "5%" in mpw
    # 全部規則都渲染到句子(唔會 raise、唔會回空)
    for code, params in rules.DEFAULT_RULES.items():
        s = rules.rule_requirement(code, params)
        assert isinstance(s, str) and len(s) > 4


# ---- 投資委員會:快照 + preamble 事實正確;冇 key offline ----
def test_committee_snapshot(session):
    snap = committee.snapshot_text(session, PX_20260710)
    assert "TSLA" in snap and "0941.HK" in snap
    assert "累計股息" in snap
    # 0941 持有期股息 117,536 應該喺快照入面
    assert "117536" in snap.replace(",", "")


def test_committee_preamble_structure(session):
    pre = committee.preamble(session, PX_20260710, "TSLA 減唔減?")
    # 五個角色標題齊 + 含息總回報要求 + 免責
    for role in ("牛方分析師", "熊方分析師", "魔鬼代言人", "價值視角",
                 "投資組合經理裁決"):
        assert role in pre
    assert "含息總回報" in pre
    assert "TSLA 減唔減?" in pre


def test_committee_guest_personas(session):
    """客席名人視角:選咗就入 preamble,帶免責;冇選就淨係核心五角色。"""
    pre = committee.preamble(session, PX_20260710, "AI 倉點睇?",
                             personas=["serenity", "buffett"])
    assert "Serenity" in pre and "巴菲特" in pre
    assert "供應鏈" in pre                              # Serenity 框架關鍵詞
    assert committee.GUEST_DISCLAIMER in pre           # 一定要有免責
    # 核心五角色依然齊
    for role in ("牛方分析師", "投資組合經理裁決"):
        assert role in pre
    # 冇選客席 → 唔會出現名人
    base = committee.preamble(session, PX_20260710, "x")
    assert "Serenity" not in base and committee.GUEST_DISCLAIMER not in base
    # 所有 persona key 都渲染到,唔會爆
    for k in committee.PERSONAS:
        assert committee.PERSONAS[k][0] in committee.preamble(
            session, PX_20260710, "x", personas=[k])


def test_committee_bull_bear_seats(session):
    """牛方/熊方席位可由名人扮演;唔會同客席重複。"""
    pre = committee.preamble(session, PX_20260710, "AI 倉?",
                             personas=["serenity"], bull="wood", bear="burry")
    assert "牛方分析師 · Cathie Wood" in pre             # 牛方由 Wood 扮演
    assert "熊方分析師 · Michael Burry" in pre           # 熊方由 Burry 扮演
    assert "Serenity" in pre                            # 客席照在
    # 若客席同牛方揀同一位,唔會多開一個獨立客席席位(避免重複發言)
    wood_name = committee.PERSONAS["wood"][0]
    pre2 = committee.preamble(session, PX_20260710, "x",
                              personas=["wood"], bull="wood")
    assert f"【{wood_name}】" not in pre2                # 冇獨立客席 header
    assert f"牛方分析師 · {wood_name}" in pre2           # 已坐牛方席
    # 擴充名冊齊全(≥10 位)
    assert len(committee.PERSONAS) >= 10
    for k in ("graham", "wood", "ackman", "fisher", "druckenmiller"):
        assert k in committee.PERSONAS


def test_committee_offline_without_key(session, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    res = committee.convene(session, PX_20260710, [], "測試", api_key=None)
    assert res["ok"] is False and res["error"]        # 冇 key → 唔 call 網,回錯
    assert res["history"] == []                        # 唔會污染 history


# ---- 股息逐筆明細:每股派息由持股反推 ----
def test_dividend_events_per_share(session):
    from datetime import date as D
    ev = income.dividend_events(session)
    assert ev and all({"date", "symbol", "amount_hkd", "per_share_ccy"} <= set(e)
                      for e in ev)
    # 合計對數:同 metrics 總股息一致
    assert sum(e["amount_hkd"] for e in ev) == pytest.approx(242797, abs=5)
    # 2802.HK 有派息,每股派息應為正數(有持股反推到)
    h2802 = [e for e in ev if e["symbol"] == "2802.HK" and e["per_share_ccy"]]
    assert h2802 and all(e["per_share_ccy"] > 0 for e in h2802)


def test_shares_held_at(session):
    from datetime import date as D
    from app.models import Instrument
    inst = session.query(Instrument).filter_by(symbol="0941.HK").first()
    # 開倉(2019-06-21)之後應該持有 ≥ 5000 股(視乎後續買賣)
    assert income.shares_held_at(session, inst.id, D(2020, 1, 1)) > 0


# ---- Syfe 收息:公允價值入 assets_other,收息入 DIV_CASH(出現喺股息) ----
def test_record_syfe():
    from datetime import date as D
    s = make_session()
    import_stockerx_csv(s, CSV)
    rebuild_lots(s)
    assets.record_syfe(s, "收息寶 - Max", 50000, 320, "HKD", D(2026, 3, 31))
    # 公允價值入咗全資產
    syfe = [a for a in assets.latest_assets(s, category="syfe")]
    assert syfe and syfe[0]["value"] == pytest.approx(50000)
    # 收息入咗股息
    divs = metrics.dividends_by_symbol(s)
    assert divs.get("SYFE:收息寶 - Max") == pytest.approx(320)
    # 同月再記一次(更正)→ 唔會 double count
    assets.record_syfe(s, "收息寶 - Max", 51000, 350, "HKD", D(2026, 3, 31))
    divs2 = metrics.dividends_by_symbol(s)
    assert divs2.get("SYFE:收息寶 - Max") == pytest.approx(350)   # 覆蓋唔累加


# ---- 四大倉位分類 + sleeve 權重 ----
def test_bucket_classification(session):
    assert buckets.bucket_of(session, "TSLA") == "core"
    assert buckets.bucket_of(session, "0941.HK") == "foundation"
    assert buckets.bucket_of(session, "VOO") == "passive"
    assert buckets.bucket_of(session, "XYZ") == "satellite"    # 預設衛星
    # 用戶覆寫
    buckets.set_bucket(session, "XYZ", "core")
    assert buckets.bucket_of(session, "XYZ") == "core"
    buckets.set_bucket(session, "XYZ", "satellite")            # 改返


def test_sleeve_weights(session):
    sw = buckets.sleeve_weights(session, PX_20260710)
    sl = sw["sleeves"]
    assert sl["core"]["weight_pct"] == pytest.approx(50.8, abs=0.3)
    assert sl["core"]["over"] is True                          # >50%
    assert sl["satellite"]["over"] is True                     # 29% > 10%
    # TSLA 單一超核心 40%
    tsla = [h for h in sl["core"]["holdings"] if h["symbol"] == "TSLA"][0]
    assert tsla["single_over"] is True


def test_twelve_rules_seeded(session):
    codes = set(rules.enabled_rules(session))
    for c in ("NEW_FOMO_CAP", "CASH_BUFFER_RULE", "QUARTERLY_REBALANCE"):
        assert c in codes                                      # 3 條新規則
    # MAX_POSITION_WEIGHT 已升級做 tiered
    assert rules.enabled_rules(session)["MAX_POSITION_WEIGHT"].get("tiered")


def test_check_trade_bucket_aware(session):
    # 買核心 TSLA:單一上限係 40%(唔係舊 15%)
    vs = rules.check_trade(session, PX_20260710, symbol="TSLA", side="BUY",
                           price=407.76, qty=100, total_assets=6_000_000)
    mpw = [v for v in vs if v["rule"] == "MAX_POSITION_WEIGHT"]
    assert mpw and mpw[0]["limit_pct"] == 40 and mpw[0]["bucket"] == "core"
    # 買衛星股加碼:NEW_FOMO_CAP 應該彈(衛星已 29% > 10%)
    vs = rules.check_trade(session, PX_20260710, symbol="LITE", side="BUY",
                           price=802, qty=10, total_assets=6_000_000)
    assert any(v["rule"] == "NEW_FOMO_CAP" for v in vs)


def test_cash_buffer_with_data():
    from datetime import date as D
    s = make_session()
    import_stockerx_csv(s, CSV)
    rebuild_lots(s)
    rules.seed_rules(s)
    # 冇現金數據 → 提示要更新
    vs = rules.scan_portfolio(s, PX_20260710, VAL_DATE)
    cb = [v for v in vs if v["rule"] == "CASH_BUFFER_RULE"]
    assert cb and cb[0]["cash_pct"] is None
    # 加少量現金(<5%)→ 應該彈緩衝不足
    assets.upsert_asset(s, "cash", "Citibank", "HKD", 10000, D(2026, 7, 1))
    vs = rules.scan_portfolio(s, PX_20260710, VAL_DATE)
    cb = [v for v in vs if v["rule"] == "CASH_BUFFER_RULE"]
    assert cb and cb[0]["cash_pct"] is not None and cb[0]["cash_pct"] < 5


def test_advisor_context_facts_only(session):
    """快照只含事實(市值/權重/勝率/違規),唔含任何預測欄位。"""
    ctx = advisor.build_snapshot_context(session, PX_20260710)
    assert ctx["true_win_rate_with_mtm"] == pytest.approx(0.6512, abs=1e-3)
    assert ctx["total_market_value_hkd"] > 0
    assert isinstance(ctx["current_rule_violations"], list)
    # 唔應該有「預測 / forecast / target price」類 key
    assert not any("forecast" in k or "predict" in k or "target" in k
                   for k in ctx)
