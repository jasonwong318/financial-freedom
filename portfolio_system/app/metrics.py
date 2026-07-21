"""指標層 — 對應規格書 §5(Sprint 1 範圍:已實現回合 + 現時持倉 + 股息)。

三個口徑,永不混算(呢個係取代 StockerX 嘅核心原因):
1. open_position_pnl  — 現時持倉收益(只計 open lots)
2. realized(回合)     — 已實現
3. dividends          — 股息
「含息總回報」= 1 + 2 + 3,但 UI 必須分開顯示三個組成。
"""
from collections import defaultdict
from decimal import Decimal

from .models import Transaction, Lot, LotClosure, Instrument
from .config import to_hkd


# ---------- 已實現:回合聚合 ----------

def round_trips(session):
    """回合 = 同一 SELL txn 嘅所有 closures 合併。回傳 list of dict(HKD)。"""
    rows = (session.query(LotClosure, Lot, Instrument, Transaction)
            .join(Lot, LotClosure.lot_id == Lot.id)
            .join(Instrument, Lot.instrument_id == Instrument.id)
            .join(Transaction, LotClosure.close_txn_id == Transaction.id)
            .order_by(Transaction.trade_dt).all())
    agg = {}
    for cl, lot, inst, sell in rows:
        key = cl.close_txn_id
        a = agg.setdefault(key, {"symbol": inst.symbol, "ccy": inst.ccy,
                                 "sell_dt": sell.trade_dt,
                                 "sell_price": float(sell.price),
                                 "pnl_ccy": Decimal(0), "hold_days": 0,
                                 "qty": Decimal(0), "_cost": Decimal(0),
                                 "buy_dt": lot.open_dt})
        a["pnl_ccy"] += Decimal(cl.realized_pnl_ccy)
        a["hold_days"] = max(a["hold_days"], cl.hold_days)
        a["qty"] += Decimal(cl.qty)
        a["_cost"] += Decimal(cl.qty) * Decimal(lot.open_price)   # 加權買入均價用
        if lot.open_dt < a["buy_dt"]:
            a["buy_dt"] = lot.open_dt                              # 最早開倉日
    out = []
    for a in agg.values():
        a["pnl_hkd"] = to_hkd(float(a["pnl_ccy"]), a["ccy"])
        a["avg_buy_price"] = float(a["_cost"] / a["qty"]) if a["qty"] else 0.0
        a["qty"] = float(a["qty"])
        del a["_cost"]
        out.append(a)
    out.sort(key=lambda x: x["sell_dt"])
    return out


def realized_summary(session):
    """勝率/賺賠比/期望值/持倉期 — 全部以回合計。"""
    rts = round_trips(session)
    if not rts:
        return {}
    wins = [r for r in rts if r["pnl_hkd"] > 0]
    losses = [r for r in rts if r["pnl_hkd"] <= 0]
    avg_win = sum(r["pnl_hkd"] for r in wins) / len(wins) if wins else 0.0
    avg_loss = sum(r["pnl_hkd"] for r in losses) / len(losses) if losses else 0.0
    total = sum(r["pnl_hkd"] for r in rts)
    holds = sorted(r["hold_days"] for r in rts)
    max_streak = streak = 0
    for r in rts:
        streak = streak + 1 if r["pnl_hkd"] <= 0 else 0
        max_streak = max(max_streak, streak)
    return {
        "rounds": len(rts),
        "win_rate": len(wins) / len(rts),
        "avg_win_hkd": avg_win,
        "avg_loss_hkd": avg_loss,
        "pl_ratio": (avg_win / abs(avg_loss)) if avg_loss else None,
        "expectancy_hkd": total / len(rts),
        "total_realized_hkd": total,
        "median_hold_days": holds[len(holds) // 2],
        "max_loss_hkd": min(r["pnl_hkd"] for r in rts),
        "max_consecutive_losses": max_streak,
    }


# ---------- 現時持倉(open lots only — 同 StockerX 嘅根本差異) ----------

def open_positions(session):
    """回傳 {symbol: {shares, avg_cost, ccy, cost_ccy}} — 只計 qty_remaining>0。

    賣清嘅標的自然唔會出現;再買會由新 lot 重新計 — 平均成本永不混歷史。
    """
    lots = (session.query(Lot, Instrument)
            .join(Instrument, Lot.instrument_id == Instrument.id)
            .filter(Lot.qty_remaining > 0).all())
    pos = {}
    for lot, inst in lots:
        p = pos.setdefault(inst.symbol, {"shares": Decimal(0), "cost_ccy": Decimal(0),
                                         "ccy": inst.ccy})
        p["shares"] += Decimal(lot.qty_remaining)
        p["cost_ccy"] += Decimal(lot.qty_remaining) * Decimal(lot.open_price)
    for p in pos.values():
        p["avg_cost"] = float(p["cost_ccy"] / p["shares"]) if p["shares"] else 0.0
        p["shares"] = float(p["shares"])
        p["cost_ccy"] = float(p["cost_ccy"])
    return pos


def open_position_pnl(session, prices: dict):
    """現時持倉收益(HKD) — prices = {symbol: 現價(原幣)}。

    冇提供價嘅標的回傳 None(誠實面對缺數,唔好靜靜地當 0)。
    """
    out = {}
    for sym, p in open_positions(session).items():
        px = prices.get(sym)
        if px is None:
            out[sym] = None
            continue
        mv = to_hkd(px * p["shares"], p["ccy"])
        cost = to_hkd(p["cost_ccy"], p["ccy"])
        out[sym] = {"mv_hkd": mv, "cost_hkd": cost, "unreal_hkd": mv - cost,
                    "unreal_pct": (mv - cost) / cost if cost else None}
    return out


# ---------- 股息 ----------

def dividends_by_symbol(session):
    """{symbol: 累計股息 HKD};另 key '_total' = 全組合。"""
    rows = (session.query(Transaction, Instrument)
            .join(Instrument, Transaction.instrument_id == Instrument.id)
            .filter(Transaction.type == "DIV_CASH").all())
    out = defaultdict(float)
    for t, inst in rows:
        amt = to_hkd(float(t.price), t.ccy)
        out[inst.symbol] += amt
        out["_total"] += amt
    return dict(out)


def total_return_with_div(session, prices: dict):
    """每標的含息總回報(HKD)= 現時持倉未實現 + 已實現 + 股息。三組成分開回傳。"""
    upl = open_position_pnl(session, prices)
    divs = dividends_by_symbol(session)
    realized = defaultdict(float)
    for r in round_trips(session):
        realized[r["symbol"]] += r["pnl_hkd"]
    symbols = set(upl) | set(realized) | {s for s in divs if s != "_total"}
    out = {}
    for s in symbols:
        u = upl.get(s)
        out[s] = {
            "unreal_hkd": u["unreal_hkd"] if u else 0.0,
            "realized_hkd": realized.get(s, 0.0),
            "dividends_hkd": divs.get(s, 0.0),
        }
        out[s]["total_hkd"] = sum(out[s].values())
    return out
