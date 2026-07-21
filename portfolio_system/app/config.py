"""全域設定 — 匯率口徑嘅單一來源(業主確認:Phase 1 固定 7.80)。

Phase 2 切換歷史匯率:改 FX_MODE = "historical" 並實作 fx_rates 查表,
所有折算一律經 to_hkd(),唔准喺其他地方寫死匯率。
"""

FX_MODE = "fixed"          # fixed | historical(一鍵切換,見 app/fx.py)
USDHKD_FIXED = 7.80

# historical 模式嘅匯率查詢器(由 fx.load_rates() 注入;None = 未載入)
_HISTORICAL_USDHKD = None


def register_historical_usdhkd(converter):
    """由 app/fx.py 注入 HistoricalUsdHkd(有 .rate(on_date))。單一來源鐵律。"""
    global _HISTORICAL_USDHKD
    _HISTORICAL_USDHKD = converter


def current_usdhkd(on_date=None) -> float:
    """使用緊嘅 USDHKD 匯率(報表註明口徑用)。"""
    if FX_MODE == "historical" and _HISTORICAL_USDHKD is not None:
        r = _HISTORICAL_USDHKD.rate(on_date)
        if r is not None:
            return r
    return USDHKD_FIXED


def fx_note() -> str:
    """報表要註明使用中嘅口徑(規格書 §2)。"""
    if FX_MODE == "historical" and _HISTORICAL_USDHKD is not None:
        return f"匯率:歷史 USDHKD(fx_rates 表,最新 {current_usdhkd():.4f})"
    return f"匯率:固定 USDHKD {USDHKD_FIXED}"

# 板塊標籤(MAX_SECTOR_WEIGHT 規則用;業主可喺 UI / DB 改)
# 對應業主弱點:AI 全鏈重倉 — 半導體成條鏈標埋一齊先睇到合計集中度
SECTOR_MAP = {
    "NVDA": "AI半導體", "MU": "AI半導體", "MRVL": "AI半導體",
    "LITE": "AI半導體", "NVTS": "AI半導體",
    "0941.HK": "高息收租", "0883.HK": "高息收租", "2802.HK": "高息收租",
    "3416.HK": "高息收租", "3466.HK": "高息收租", "0823.HK": "高息收租",
}

def to_hkd(amount: float, ccy: str, on_date=None) -> float:
    """原幣金額折算 HKD。on_date 傳咗且 historical 模式就用 as-of 匯率。"""
    if ccy == "HKD":
        return amount
    if ccy == "USD":
        return amount * current_usdhkd(on_date)
    raise ValueError(f"未支援幣種: {ccy}")
