"""全資產模組 — 對應規格書 §6.6「全資產頁(Percento 式)」。

借鏡 Percento 嘅資訊架構:四大區塊(流動資金/投資/固定資產/負債),
手動月度輸入(assets_other 表,每次輸入係一個 as_of 快照,唔覆蓋歷史)。
股票市值由系統自動計,唔使手入。
"""
from datetime import date as Date

from .models import AssetOther
from .config import to_hkd
from .metrics import open_position_pnl

# category → Percento 式四區塊
CATEGORY_BLOCK = {
    "cash": "流動資金",
    "syfe": "投資", "mpf": "投資", "bond": "投資", "insurance": "投資",
    "fund": "投資",
    "property": "固定資產",
    "liability": "負債", "loan": "負債",
    "receivable": "應收",
}
CATEGORIES = list(CATEGORY_BLOCK)


def upsert_asset(session, category, name, ccy, value, as_of: Date):
    """記一項資產喺某日嘅值。同 (category,name,as_of) 已有就覆蓋(同日更正合理);
    唔同日期係新快照 — 歷史值永不改寫,淨資產趨勢靠佢。
    """
    if category not in CATEGORY_BLOCK:
        raise ValueError(f"未知 category: {category}(可用:{CATEGORIES})")
    row = (session.query(AssetOther)
           .filter_by(category=category, name=name, as_of=as_of).first())
    if row:
        row.ccy, row.value = ccy, value
    else:
        row = AssetOther(category=category, name=name, ccy=ccy,
                         value=value, as_of=as_of)
        session.add(row)
    session.commit()
    return row


def latest_assets(session, as_of: Date = None):
    """每項資產(category+name)攞最近一次輸入(≤ as_of)。回傳 list of dict(HKD)。"""
    q = session.query(AssetOther)
    if as_of:
        q = q.filter(AssetOther.as_of <= as_of)
    latest = {}
    for a in q.order_by(AssetOther.as_of).all():   # 升序,後面覆蓋 = 最新
        latest[(a.category, a.name)] = a
    return [{"category": a.category, "block": CATEGORY_BLOCK[a.category],
             "name": a.name, "ccy": a.ccy, "value": float(a.value),
             "value_hkd": to_hkd(float(a.value), a.ccy), "as_of": a.as_of}
            for a in latest.values()]


def net_worth(session, prices, as_of: Date = None):
    """Percento 式淨資產總表。股票市值自動計入「投資」區塊。

    回傳 {blocks: {區塊: HKD}, equity_mv_hkd, total_assets_hkd, net_worth_hkd}
    - total_assets = 全部正資產(唔計負債)— 規則引擎分母用呢個
    - net_worth   = 總資產 − 負債
    """
    upl = open_position_pnl(session, prices)
    equity_mv = sum(v["mv_hkd"] for v in upl.values() if v)
    blocks = {"流動資金": 0.0, "投資": equity_mv, "固定資產": 0.0,
              "負債": 0.0, "應收": 0.0}
    for a in latest_assets(session, as_of):
        blocks[a["block"]] += a["value_hkd"]
    total_assets = sum(v for b, v in blocks.items() if b != "負債")
    return {
        "blocks": blocks,
        "equity_mv_hkd": equity_mv,
        "total_assets_hkd": total_assets,
        "net_worth_hkd": total_assets - blocks["負債"],
    }


def total_assets_hkd(session, prices) -> float:
    """規則引擎分母(MAX_SINGLE_ENTRY / WEEKLY_CIRCUIT_BREAKER 用)。"""
    return net_worth(session, prices)["total_assets_hkd"]
