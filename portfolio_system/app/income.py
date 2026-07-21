"""收益頁邏輯 — 對應規格書 §5(股息三口徑)+ §6.2(詳細收益)。

規格書 §5 明文要求每個持倉三個回報數,永不混算:
  ① 價差未實現        = open lots 市值 − 成本(唔含股息)
  ② 含息未實現        = ① + 持有期內股息(current open lots 開倉後收到嘅)
  ③ yield-on-cost     = 滾動 12 個月股息 ÷ open lots 成本

收息倉排序預設用「含息總回報」(②+ 已實現),呢個係業主明文鐵律。
"""
from collections import defaultdict
from datetime import date as Date, timedelta

from .models import Transaction, Instrument, Lot, LotClosure
from .config import to_hkd
from .metrics import open_positions, open_position_pnl, round_trips


def _earliest_open_dt(session):
    """{symbol: 現有 open lots 最早開倉日} — 「持有期內股息」嘅起點。"""
    rows = (session.query(Lot, Instrument)
            .join(Instrument, Lot.instrument_id == Instrument.id)
            .filter(Lot.qty_remaining > 0).all())
    out = {}
    for lot, inst in rows:
        if inst.symbol not in out or lot.open_dt < out[inst.symbol]:
            out[inst.symbol] = lot.open_dt
    return out


def dividends_detail(session, as_of: Date = None):
    """每標的股息三軌(HKD):全歷史 / 持有期內 / 滾動12個月。

    - 全歷史:所有 DIV_CASH(同 metrics.dividends_by_symbol 對數)
    - 持有期內:current open lots 最早開倉日之後收到嘅(冇 open lot 就 0)
    - 滾動12個月:as_of 前 365 日內(yield-on-cost 用)
    """
    as_of = as_of or Date.today()
    roll_cut = as_of - timedelta(days=365)
    open_dt = _earliest_open_dt(session)
    rows = (session.query(Transaction, Instrument)
            .join(Instrument, Transaction.instrument_id == Instrument.id)
            .filter(Transaction.type == "DIV_CASH").all())
    out = defaultdict(lambda: {"lifetime_hkd": 0.0, "held_hkd": 0.0,
                               "rolling_12m_hkd": 0.0})
    for t, inst in rows:
        amt = to_hkd(float(t.price), t.ccy)
        d = out[inst.symbol]
        d["lifetime_hkd"] += amt
        start = open_dt.get(inst.symbol)
        if start is not None and t.trade_dt >= start:
            d["held_hkd"] += amt
        if t.trade_dt.date() >= roll_cut:
            d["rolling_12m_hkd"] += amt
    return dict(out)


def position_returns(session, prices: dict, as_of: Date = None):
    """每個 open position 三個回報數(§5)。回傳 {symbol: {...}}。

    冇價嘅標的 price-only / 含息 回 None(誠實),yield-on-cost 照計(唔靠現價)。
    """
    pos = open_positions(session)
    upl = open_position_pnl(session, prices)
    divd = dividends_detail(session, as_of)
    out = {}
    for sym, p in pos.items():
        u = upl.get(sym)
        held = divd.get(sym, {}).get("held_hkd", 0.0)
        roll = divd.get(sym, {}).get("rolling_12m_hkd", 0.0)
        cost_hkd = to_hkd(p["cost_ccy"], p["ccy"])
        out[sym] = {
            "shares": p["shares"],
            "avg_cost": p["avg_cost"],
            "cost_hkd": cost_hkd,
            "price_only_unreal_hkd": u["unreal_hkd"] if u else None,
            "price_only_unreal_pct": u["unreal_pct"] if u else None,
            "with_div_unreal_hkd": (u["unreal_hkd"] + held) if u else None,
            "held_div_hkd": held,
            "yield_on_cost": (roll / cost_hkd) if cost_hkd else None,
        }
    return out


def income_summary(session, prices: dict, as_of: Date = None,
                   basis: str = "open_cost"):
    """詳細收益總表(§6.2)— 三組成分開 + 各自 %。

    basis 決定 % 分母口徑(UI 要註明):
      "open_cost"  → 現時持倉成本(現時持倉收益 % 嘅自然分母)
      "total_in"   → 歷史總投入(所有買入金額,已實現/股息 % 用呢個先有意義)
    回傳 dict:三組成金額 + % + 分母 + basis 標籤。
    """
    upl = open_position_pnl(session, prices)
    holding_unreal = sum(v["unreal_hkd"] for v in upl.values() if v)
    open_cost = sum(to_hkd(p["cost_ccy"], p["ccy"])
                    for p in open_positions(session).values())
    realized = sum(r["pnl_hkd"] for r in round_trips(session))
    divs = dividends_detail(session, as_of)
    div_total = sum(d["lifetime_hkd"] for d in divs.values())

    total_buy = 0.0
    for t in session.query(Transaction).filter_by(type="BUY").all():
        total_buy += to_hkd(float(t.price) * float(t.qty), t.ccy)

    denom = open_cost if basis == "open_cost" else total_buy
    denom_label = "現時持倉成本" if basis == "open_cost" else "歷史總投入(累計買入)"

    def pct(x):
        return (x / denom) if denom else None

    return {
        "basis": basis,
        "denom_hkd": denom,
        "denom_label": denom_label,
        "holding_unreal_hkd": holding_unreal,
        "holding_unreal_pct": pct(holding_unreal),
        "realized_hkd": realized,
        "realized_pct": pct(realized),
        "dividends_hkd": div_total,
        "dividends_pct": pct(div_total),
        "open_cost_hkd": open_cost,
        "total_buy_hkd": total_buy,
        # 鐵律:三組成分開,總數只作參考(UI 要標明係「含息總回報」而唔係單一收益)
        "total_return_hkd": holding_unreal + realized + div_total,
    }


def monthly_dividends(session):
    """{(year, month): HKD} — 月度股息柱狀圖用。"""
    out = defaultdict(float)
    rows = (session.query(Transaction, Instrument)
            .join(Instrument, Transaction.instrument_id == Instrument.id)
            .filter(Transaction.type == "DIV_CASH").all())
    for t, inst in rows:
        out[(t.trade_dt.year, t.trade_dt.month)] += to_hkd(float(t.price), t.ccy)
    return dict(out)
