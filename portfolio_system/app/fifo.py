"""FIFO 引擎 — 對應規格書 §3。成個系統嘅心臟。

邏輯:
- BUY → 開 lot(手續費按股攤分掛喺 lot)
- SELL → 由最舊嘅未平 lot 逐批扣,每批寫一條 lot_closure
- 已實現 PnL(原幣) = (賣價 − 開倉價) × 股數 − 買方按股費 − 賣方按股費
- SELL 超過持股 → ValueError(業主無沽空,超賣一定係數據錯)

「回合(round trip)」= 同一筆 SELL 產生嘅所有 closures 合併 — 勝率/賺賠比用回合計。
"""
from decimal import Decimal
from collections import defaultdict

from .models import Transaction, Lot, LotClosure

EPS = Decimal("0.000001")


def rebuild_lots(session):
    """由 transactions 全量重建 lots + lot_closures(冪等:先清後建)。

    交易編輯/沖銷之後 call 呢個 function 就會重算成個持倉狀態 —
    簡單粗暴但正確,業主數據量(百幾筆)毫秒級完成。
    """
    session.query(LotClosure).delete()
    session.query(Lot).delete()
    session.flush()

    open_lots = defaultdict(list)   # instrument_id -> [Lot,...] 按時間序

    txns = (session.query(Transaction)
            .filter(Transaction.type.in_(("BUY", "SELL")))
            .order_by(Transaction.trade_dt, Transaction.id).all())

    for t in txns:
        qty = Decimal(t.qty)
        if t.type == "BUY":
            lot = Lot(open_txn_id=t.id, instrument_id=t.instrument_id,
                      open_dt=t.trade_dt, open_price=t.price,
                      qty_opened=qty, qty_remaining=qty,
                      fee_per_share=(Decimal(t.fee) / qty) if t.fee else 0)
            session.add(lot)
            session.flush()          # 即攞 lot.id,俾稍後 closure 引用
            open_lots[t.instrument_id].append(lot)
        else:  # SELL
            remain = qty
            sell_fee_ps = (Decimal(t.fee) / qty) if t.fee else Decimal(0)
            queue = open_lots[t.instrument_id]
            while remain > EPS and queue:
                lot = queue[0]
                take = min(remain, Decimal(lot.qty_remaining))
                pnl = ((Decimal(t.price) - Decimal(lot.open_price)) * take
                       - Decimal(lot.fee_per_share) * take
                       - sell_fee_ps * take)
                session.add(LotClosure(
                    lot_id=lot.id, close_txn_id=t.id, qty=take,
                    realized_pnl_ccy=pnl,
                    hold_days=(t.trade_dt - lot.open_dt).days))
                lot.qty_remaining = Decimal(lot.qty_remaining) - take
                remain -= take
                if Decimal(lot.qty_remaining) <= EPS:
                    queue.pop(0)
            if remain > EPS:
                raise ValueError(
                    f"超賣:{t.trade_dt} instrument_id={t.instrument_id} "
                    f"尚欠 {remain} 股冇 lot 可配 — 檢查數據")
    session.commit()
