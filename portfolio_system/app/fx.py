"""歷史匯率模式 — 對應規格書 §2 匯率口徑:「Phase 2 加 fx_rates 歷史匯率模式
(yfinance USDHKD=X 日線 backfill + 每日 append),config 一鍵切換」。

設計取捨:全 codebase 24 個 to_hkd() call site 大多冇傳日期(手邊冇 session/date)。
與其把 session+date 穿過每個 call(大 refactor,易錯),改為模組級「匯率表快照」:
  1. backfill_usdhkd() 由 yfinance 灌 fx_rates 表
  2. load_rates(session) 一次過讀入記憶體,register 落 config
  3. config.to_hkd 喺 historical 模式查表;有日期用當日(揾唔到用最近較早),
     冇日期(大多 call site)用最新一筆 — 兩種都喺聯匯 band 內,偏差 ≤0.6%

fixed 模式完全唔受影響(呢個係 regression 保證)。港元聯匯 7.75–7.85。
"""
from datetime import date as Date, timedelta
import bisect

from .models import FxRate
from . import config

PAIR = "USDHKD"


def backfill_usdhkd(session, start: Date, end: Date, provider=None) -> int:
    """由數據源灌 USDHKD 日線入 fx_rates(upsert)。回傳寫入數。

    provider 需有 series(ticker, start, end) → {date: rate}(同 benchmark provider
    介面一致);唔傳就用 yfinance 'USDHKD=X'。
    """
    if provider is None:
        provider = _YFinanceFx()
    series = provider.series("USDHKD=X", start, end)
    n = 0
    for d, rate in series.items():
        row = session.get(FxRate, (PAIR, d))
        if row:
            row.rate = rate
        else:
            session.add(FxRate(ccy_pair=PAIR, date=d, rate=rate))
        n += 1
    session.commit()
    return n


class _YFinanceFx:
    def series(self, ticker, start: Date, end: Date):
        import yfinance as yf
        hist = yf.Ticker(ticker).history(start=start, end=end)
        return {d.date(): float(c) for d, c in hist["Close"].items()}


class HistoricalUsdHkd:
    """記憶體匯率表:日期 → USDHKD。查詢 as-of(用當日或最近較早一筆)。"""

    def __init__(self, rows):
        # rows: [(date, rate)],建有序陣列做 as-of 二分查
        self._dates = [d for d, _ in sorted(rows)]
        self._rates = [r for _, r in sorted(rows)]

    def rate(self, on_date: Date = None):
        if not self._dates:
            return None
        if on_date is None:
            return self._rates[-1]                 # 冇日期 → 最新一筆
        i = bisect.bisect_right(self._dates, on_date)
        if i == 0:
            return self._rates[0]                   # 早過最舊 → 用最舊(避免爆)
        return self._rates[i - 1]                    # as-of:當日或最近較早


def load_rates(session):
    """由 fx_rates 讀 USDHKD 入記憶體並 register 落 config,切 historical 模式。

    冇任何 fx_rates 數據就 raise(唔准靜靜地跌返 fixed,口徑要明確)。
    """
    rows = [(r.date, float(r.rate)) for r in
            session.query(FxRate).filter_by(ccy_pair=PAIR).all()]
    if not rows:
        raise ValueError("fx_rates 冇 USDHKD 數據 — 先 backfill_usdhkd() 至可切 historical")
    conv = HistoricalUsdHkd(rows)
    config.register_historical_usdhkd(conv)
    config.FX_MODE = "historical"
    return len(rows)


def use_fixed():
    """切返固定 7.80 模式(測試 / 用戶一鍵切換)。"""
    config.FX_MODE = "fixed"
    config.register_historical_usdhkd(None)
