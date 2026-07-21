"""行為儀表板指標 — 對應規格書 §6.6。

三件事:
1. 雙軌勝率 — 已實現勝率(91.7%)係倖存者偏差,含 mark-to-market 先係真相(~65%)
2. 處置效應 — 贏就快沽、蝕就死揸嘅量化證據(平均持倉期對比 + open 蝕貨賬齡)
3. 違規成本 — 每單歷史違規交易嘅最終結果(賺定蝕),俾業主睇到唔守紀律嘅價錢
"""
from .models import Transaction, Lot, LotClosure, Instrument
from .config import to_hkd
from .metrics import realized_summary, round_trips, open_position_pnl
from .rules import scan_history


def dual_track_win_rate(session, prices):
    """雙軌勝率。真實勝率 =(贏回合 + 賺緊 open positions)/(總回合 + open positions)。

    冇價嘅 open position 唔計入分母(誠實面對缺數)。
    """
    rs = realized_summary(session)
    rounds = rs.get("rounds", 0)
    realized_wins = round(rs.get("win_rate", 0) * rounds)
    upl = open_position_pnl(session, prices)
    priced = {s: v for s, v in upl.items() if v is not None}
    open_winners = [s for s, v in priced.items() if v["unreal_hkd"] > 0]
    open_losers = [s for s, v in priced.items() if v["unreal_hkd"] <= 0]
    denom = rounds + len(priced)
    return {
        "realized_win_rate": rs.get("win_rate"),
        "true_win_rate": (realized_wins + len(open_winners)) / denom if denom else None,
        "rounds": rounds,
        "realized_wins": realized_wins,
        "open_positions": len(priced),
        "open_winners": sorted(open_winners),
        "open_losers": sorted(open_losers),
    }


def disposition_stats(session, prices, on_date=None):
    """處置效應監測:贏回合 vs 蝕回合平均持倉期 + open 蝕貨嘅賬齡。

    健康形態係「贏長蝕短」;業主嘅病係已實現「蝕貨快沽/唔沽」,
    真正嘅蝕貨全部匿埋喺 open lots(XYZ/GME 死揸)— 所以要連 open 賬齡一齊睇。
    """
    from datetime import datetime, date as Date
    on_date = on_date or Date.today()
    ref_dt = datetime.combine(on_date, datetime.min.time())

    rts = round_trips(session)
    win_holds = [r["hold_days"] for r in rts if r["pnl_hkd"] > 0]
    loss_holds = [r["hold_days"] for r in rts if r["pnl_hkd"] <= 0]

    upl = open_position_pnl(session, prices)
    loser_ages = []
    lots = (session.query(Lot, Instrument)
            .join(Instrument, Lot.instrument_id == Instrument.id)
            .filter(Lot.qty_remaining > 0).all())
    # 逐 lot(蝕緊嘅)明細:買入日、買入價、股數、按現價計嘅該 lot 浮虧
    for lot, inst in lots:
        v = upl.get(inst.symbol)
        px = prices.get(inst.symbol)
        if not (v and v["unreal_hkd"] <= 0) or px is None:
            continue
        open_px = float(lot.open_price)
        qty = float(lot.qty_remaining)
        lot_unreal = to_hkd((px - open_px) * qty, inst.ccy)
        loser_ages.append({
            "symbol": inst.symbol,
            "buy_date": lot.open_dt.date(),
            "buy_price": open_px,
            "shares": qty,
            "days": (ref_dt - lot.open_dt).days,
            "unreal_hkd": round(lot_unreal),
        })
    return {
        "avg_hold_win_days": sum(win_holds) / len(win_holds) if win_holds else None,
        "avg_hold_loss_days": sum(loss_holds) / len(loss_holds) if loss_holds else None,
        "open_loser_lots": sorted(loser_ages, key=lambda x: -x["days"]),
    }


def violation_outcomes(session, prices):
    """每單歷史違規嘅最終結果(HKD):
    - 違規係 BUY(REBUY_HIGHER / AVG_DOWN)→ 該筆買入開嘅 lots:已實現 + 未實現
    - 違規係 SELL(FEE_CHECK)→ 該回合已實現
    回傳 (list, 違規成本合計) — 成本 = 所有負結果加埋(正結果唔攞嚟溝淡,
    因為「違咗規但好彩賺咗」唔代表規則錯)。
    """
    out = []
    for v in scan_history(session):
        tid = v.get("txn_id")
        if tid is None:
            continue
        txn = session.get(Transaction, tid)
        inst = session.get(Instrument, txn.instrument_id)
        if txn.type == "BUY":
            outcome = 0.0
            for lot in session.query(Lot).filter_by(open_txn_id=tid).all():
                for cl in session.query(LotClosure).filter_by(lot_id=lot.id).all():
                    outcome += to_hkd(float(cl.realized_pnl_ccy), inst.ccy)
                remain = float(lot.qty_remaining)
                px = prices.get(inst.symbol)
                if remain > 0 and px is not None:
                    outcome += to_hkd((px - float(lot.open_price)) * remain, inst.ccy)
        else:  # SELL
            outcome = sum(to_hkd(float(cl.realized_pnl_ccy), inst.ccy)
                          for cl in session.query(LotClosure)
                          .filter_by(close_txn_id=tid).all())
        out.append({**v, "outcome_hkd": round(outcome)})
    cost = -sum(o["outcome_hkd"] for o in out if o["outcome_hkd"] < 0)
    return out, cost
