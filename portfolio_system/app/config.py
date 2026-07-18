"""全域設定 — 匯率口徑嘅單一來源(業主確認:Phase 1 固定 7.80)。

Phase 2 切換歷史匯率:改 FX_MODE = "historical" 並實作 fx_rates 查表,
所有折算一律經 to_hkd(),唔准喺其他地方寫死匯率。
"""

FX_MODE = "fixed"          # fixed | historical(Phase 2)
USDHKD_FIXED = 7.80

def to_hkd(amount: float, ccy: str, on_date=None) -> float:
    """原幣金額折算 HKD。on_date 留俾 historical 模式用。"""
    if ccy == "HKD":
        return amount
    if ccy == "USD":
        if FX_MODE == "fixed":
            return amount * USDHKD_FIXED
        raise NotImplementedError("historical fx_mode 係 Phase 2 範圍")
    raise ValueError(f"未支援幣種: {ccy}")
