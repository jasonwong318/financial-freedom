"""報表層 — 月度回報熱力圖 + 回撤曲線(規格書 §6.6)。

月度熱力圖用「已實現 + 股息」現金口徑(由交易記錄直接得出,唔使歷史股價);
snapshots 儲夠一段日子之後,TWRR 月度版會喺 Phase 4 補上做對照。
回撤曲線用 snapshots_daily 嘅 NAV 序列。
"""
from collections import defaultdict

from .models import SnapshotDaily
from .config import to_hkd
from .metrics import round_trips
from .models import Transaction, Instrument


def monthly_pnl(session):
    """{(year, month): {"realized_hkd", "dividends_hkd", "total_hkd"}}。"""
    out = defaultdict(lambda: {"realized_hkd": 0.0, "dividends_hkd": 0.0})
    for r in round_trips(session):
        key = (r["sell_dt"].year, r["sell_dt"].month)
        out[key]["realized_hkd"] += r["pnl_hkd"]
    rows = (session.query(Transaction, Instrument)
            .join(Instrument, Transaction.instrument_id == Instrument.id)
            .filter(Transaction.type == "DIV_CASH").all())
    for t, inst in rows:
        key = (t.trade_dt.year, t.trade_dt.month)
        out[key]["dividends_hkd"] += to_hkd(float(t.price), t.ccy)
    for v in out.values():
        v["total_hkd"] = v["realized_hkd"] + v["dividends_hkd"]
    return dict(out)


def monthly_pnl_pivot(session):
    """年 × 月 DataFrame(total_hkd)— 直接餵落熱力圖 / st.dataframe。"""
    import pandas as pd
    data = monthly_pnl(session)
    if not data:
        return pd.DataFrame()
    years = sorted({y for y, _ in data})
    df = pd.DataFrame(index=years, columns=range(1, 13), dtype=float)
    for (y, m), v in data.items():
        df.loc[y, m] = round(v["total_hkd"])
    df.index.name = "年"
    df.columns = [f"{m}月" for m in range(1, 13)]
    return df


def nav_series(session, account_id: int = 1):
    """[(date, nav_hkd)] — 由 snapshots_daily 攞,升序。"""
    snaps = (session.query(SnapshotDaily)
             .filter_by(account_id=account_id)
             .order_by(SnapshotDaily.date).all())
    return [(s.date, float(s.nav_hkd)) for s in snaps]


def drawdown_curve(series):
    """輸入 [(date, nav)],回傳 ([(date, nav, dd_pct)], max_dd_pct)。

    dd_pct = 由歷史高位回落幾多(負數);max_dd 係最深嗰下。
    """
    peak, out, max_dd = None, [], 0.0
    for d, nav in series:
        peak = nav if peak is None else max(peak, nav)
        dd = (nav / peak - 1) if peak > 0 else 0.0
        max_dd = min(max_dd, dd)
        out.append((d, nav, dd))
    return out, max_dd
