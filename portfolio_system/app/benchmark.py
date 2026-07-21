"""基準對比 — 對應規格書 §5「基準對比:VOO、^HSI(基準亦用 total return 口徑先公平)」。

Provider 制同 prices.py 一致:YFinance 攞真數,Manual 餵測試/離線數。
基準用 adj_close(已還原派息拆股)= total return 口徑,同組合含息回報對等比較。
"""
from datetime import date as Date

BENCHMARKS = {
    "VOO": {"name": "標普500 ETF", "ticker": "VOO"},
    "^HSI": {"name": "恒生指數", "ticker": "^HSI"},
    "^IXIC": {"name": "納斯達克綜合", "ticker": "^IXIC"},
}


class ManualBenchmarkProvider:
    """測試/離線:餵 {ticker: {date: adj_close}}。"""

    def __init__(self, series: dict):
        self._series = series

    def series(self, ticker, start: Date, end: Date):
        s = self._series.get(ticker, {})
        return {d: v for d, v in s.items() if start <= d <= end}


class YFinanceBenchmarkProvider:
    """正式:yfinance adj_close 日線(total return 口徑)。lazy import。"""

    def series(self, ticker, start: Date, end: Date):
        import yfinance as yf
        hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
        return {d.date(): float(c) for d, c in hist["Close"].items()}


def normalized_curve(series: dict, base: float = 100.0):
    """{date: price} → {date: 指數化(首日=base)}。方便同組合 NAV 疊圖對比。"""
    if not series:
        return {}
    items = sorted(series.items())
    first = items[0][1]
    if first == 0:
        return {}
    return {d: base * v / first for d, v in items}


def total_return(series: dict):
    """區間總回報 %(用 adj_close 首尾)。冇數據回 None。"""
    if len(series) < 2:
        return None
    items = sorted(series.items())
    first, last = items[0][1], items[-1][1]
    return (last / first - 1) if first else None


def compare(provider, tickers, start: Date, end: Date, portfolio_twrr=None):
    """回傳 {ticker: {name, total_return, curve}} + 可選組合 TWRR 一齊排。"""
    out = {}
    for t in tickers:
        s = provider.series(t, start, end)
        out[t] = {
            "name": BENCHMARKS.get(t, {}).get("name", t),
            "total_return": total_return(s),
            "curve": normalized_curve(s),
        }
    if portfolio_twrr is not None:
        out["_portfolio"] = {"name": "本組合(TWRR)",
                             "total_return": portfolio_twrr, "curve": {}}
    return out
