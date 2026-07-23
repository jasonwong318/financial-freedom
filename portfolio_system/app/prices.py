"""價格層 — 對應規格書 §1「行情」+ §5 DAG 嘅 prices_eod 輸入。

Provider 制:呼叫方只認 get_eod() 介面,換數據源唔使改業務邏輯。
- YFinanceProvider:正式用(港股 `.HK` 後綴原生支援;美股照用)
- ManualPriceProvider:離線/測試/手動對數用

規格書鐵律:冇價嘅標的回 None,唔准靜靜地當 0。
"""
from datetime import date as Date

from .models import Instrument, PriceEOD


class ManualPriceProvider:
    """手動餵價 — 測試、離線環境、或者業主想用券商截圖價對數時用。"""

    def __init__(self, prices: dict):
        self._prices = dict(prices)          # {symbol: close(原幣)}

    def get_eod(self, symbols, on_date: Date) -> dict:
        return {s: self._prices.get(s) for s in symbols}


class YFinanceProvider:
    """正式數據源。lazy import,離線環境唔會因為冇裝 yfinance 而爆。

    HK 代號直接用(e.g. '0941.HK');美股原樣。攞唔到嘅回 None。
    """

    def get_eod(self, symbols, on_date: Date) -> dict:
        import yfinance as yf                 # noqa: 只喺真正用到先 import
        out = {}
        for s in symbols:
            try:
                # 只用 period 攞最近 N 個交易日,取最後一個收市。
                # (唔可以同時傳 start=on_date + period:yfinance 會當「on_date 之後」,
                #  而今日未收市 → 回空 → 美股更新唔到,呢個係之前嘅 bug。)
                hist = yf.Ticker(s).history(period="5d", auto_adjust=False)
                out[s] = float(hist["Close"].iloc[-1]) if len(hist) else None
            except Exception:
                out[s] = None
        return out


def store_eod(session, on_date: Date, prices: dict) -> int:
    """寫入 prices_eod(upsert)。回傳成功寫入數。"""
    insts = {i.symbol: i for i in session.query(Instrument).all()}
    n = 0
    for sym, px in prices.items():
        if px is None or sym not in insts:
            continue
        row = session.get(PriceEOD, (insts[sym].id, on_date))
        if row:
            row.close = px
        else:
            session.add(PriceEOD(instrument_id=insts[sym].id, date=on_date, close=px))
        n += 1
    session.commit()
    return n


def latest_prices(session) -> dict:
    """{symbol: close} — 每標的攞最近一日 EOD。"""
    rows = (session.query(PriceEOD, Instrument)
            .join(Instrument, PriceEOD.instrument_id == Instrument.id)
            .order_by(PriceEOD.date).all())
    out = {}
    for p, inst in rows:                      # 按日期升序,後面覆蓋前面 = 最新
        out[inst.symbol] = float(p.close)
    return out
