"""績效層 — 對應規格書 §5。

業主決定:唔用入金/出金記錄。所以——
- XIRR 由交易現金流推導:買入(−) 沽出(+) 股息(+) 期末市值(+)
- TWRR 用 snapshots,當日淨買賣額視為 external flow
兩個口徑並列展示,唔互相取代。
"""
from datetime import date as Date
from collections import defaultdict

from .models import Transaction, SnapshotDaily
from .config import to_hkd
from .metrics import open_positions


# ---------- 現金流 ----------

def trade_cashflows_hkd(session):
    """[(date, amount_hkd)] — 買入負、沽出正、股息正。全部折 HKD。"""
    flows = []
    for t in (session.query(Transaction)
              .filter(Transaction.type.in_(("BUY", "SELL", "DIV_CASH")))
              .order_by(Transaction.trade_dt).all()):
        if t.type == "BUY":
            amt = -(float(t.price) * float(t.qty) + float(t.fee or 0))
        elif t.type == "SELL":
            amt = float(t.price) * float(t.qty) - float(t.fee or 0)
        else:                                   # DIV_CASH:price 欄 = 股息總額
            amt = float(t.price)
        flows.append((t.trade_dt.date(), to_hkd(amt, t.ccy)))
    return flows


# ---------- XIRR ----------

def xirr(cashflows, guess_lo=-0.95, guess_hi=10.0, tol=1e-8) -> float | None:
    """年化內部回報率 — 二分法解 NPV=0(唔依賴 scipy,穩陣)。

    cashflows: [(date, amount)],至少一負一正,否則回 None。
    """
    if not cashflows:
        return None
    amounts = [a for _, a in cashflows]
    if not (any(a < 0 for a in amounts) and any(a > 0 for a in amounts)):
        return None
    d0 = min(d for d, _ in cashflows)

    def npv(rate):
        return sum(a / (1 + rate) ** ((d - d0).days / 365.0) for d, a in cashflows)

    lo, hi = guess_lo, guess_hi
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None                            # 無解喺範圍內 — 誠實回 None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def portfolio_xirr(session, valuation_date: Date, prices: dict) -> float | None:
    """組合 XIRR:交易現金流 + 期末市值做終值。

    prices 必須覆蓋所有 open positions,欠價會 raise — 寧願爆,唔好俾錯數。
    """
    flows = trade_cashflows_hkd(session)
    terminal = 0.0
    for sym, p in open_positions(session).items():
        if prices.get(sym) is None:
            raise ValueError(f"XIRR 欠 {sym} 嘅估值價")
        terminal += to_hkd(prices[sym] * p["shares"], p["ccy"])
    flows.append((valuation_date, terminal))
    return xirr(flows)


# ---------- Snapshots + TWRR ----------

def build_snapshot(session, on_date: Date, prices: dict, account_id: int = 1):
    """寫一日 snapshot(nav + 權重 exposure)。歷史點永不追溯改寫(規格書 §6.1)。"""
    mv_total = 0.0
    exposure = {}
    for sym, p in open_positions(session).items():
        px = prices.get(sym)
        if px is None:
            raise ValueError(f"snapshot 欠 {sym} 嘅價")
        mv = to_hkd(px * p["shares"], p["ccy"])
        exposure[sym] = mv
        mv_total += mv
    exposure = {s: round(v / mv_total, 4) for s, v in exposure.items()} if mv_total else {}
    row = session.get(SnapshotDaily, (on_date, account_id))
    if row:
        row.nav_hkd, row.equity_mv_hkd, row.exposure = mv_total, mv_total, exposure
    else:
        session.add(SnapshotDaily(date=on_date, account_id=account_id,
                                  nav_hkd=mv_total, equity_mv_hkd=mv_total,
                                  cash_hkd=0, exposure=exposure))
    session.commit()
    return mv_total, exposure


def _net_trade_flow_by_date(session):
    """{date: 當日淨買賣現金(HKD)} — TWRR 嘅 external flow(買入為正流入組合)。"""
    out = defaultdict(float)
    for d, amt in trade_cashflows_hkd(session):
        # 買入(amt<0)= 資金流入組合 → flow = -amt;股息當組合內部收益,唔算 external
        pass
    for t in (session.query(Transaction)
              .filter(Transaction.type.in_(("BUY", "SELL"))).all()):
        amt = float(t.price) * float(t.qty) + float(t.fee or 0)
        signed = amt if t.type == "BUY" else -(float(t.price) * float(t.qty) - float(t.fee or 0))
        out[t.trade_dt.date()] += to_hkd(signed, t.ccy)
    return dict(out)


def twrr(session, account_id: int = 1) -> float | None:
    """時間加權回報(鏈式):每段 r = (V1 − flow) / V0 − 1。

    需要 ≥2 個 snapshots;flow 用當日淨買賣額。價格 backfill 後先有完整曲線。
    """
    snaps = (session.query(SnapshotDaily)
             .filter_by(account_id=account_id)
             .order_by(SnapshotDaily.date).all())
    if len(snaps) < 2:
        return None
    flows = _net_trade_flow_by_date(session)
    total = 1.0
    for prev, cur in zip(snaps, snaps[1:]):
        v0, v1 = float(prev.nav_hkd), float(cur.nav_hkd)
        if v0 <= 0:
            continue
        f = sum(v for d, v in flows.items() if prev.date < d <= cur.date)
        total *= (v1 - f) / v0
    return total - 1
