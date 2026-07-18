"""StockerX CSV 匯入器 — 對應規格書 §7 POST /import/csv。

職責:
1. 解析 StockerX 匯出格式(DD/MM/YYYY HH:MM:SS;Buy/Sell/DividendCash)
2. Validation:壞行唔入庫,寫入 report(業主真數有零股零價行,必須擋)
3. 建 instruments(symbol+market 去重)、寫 transactions

已知數據特性(見規格書 §0):時間戳係手動補記,只信日期同價格。
"""
from dataclasses import dataclass, field
from datetime import datetime
import csv

from .models import Account, Instrument, Transaction
from .config import SECTOR_MAP

TYPE_MAP = {"Buy": "BUY", "Sell": "SELL", "DividendCash": "DIV_CASH"}


@dataclass
class ImportReport:
    imported: int = 0
    rejected: list = field(default_factory=list)   # [(row_no, reason, raw)]

    def reject(self, row_no, reason, raw):
        self.rejected.append((row_no, reason, dict(raw)))


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def import_stockerx_csv(session, path: str, account_name: str = "主戶口") -> ImportReport:
    report = ImportReport()
    acct = session.query(Account).filter_by(name=account_name).first()
    if not acct:
        acct = Account(name=account_name, base_ccy="HKD")
        session.add(acct)
        session.flush()

    inst_cache = {}

    def get_instrument(symbol, name, market, ccy):
        key = (symbol, market)
        if key not in inst_cache:
            inst = session.query(Instrument).filter_by(symbol=symbol, market=market).first()
            if not inst:
                asset_class = "etf" if symbol in ("VOO", "7500.HK", "2802.HK", "3416.HK", "3466.HK") else "equity"
                inst = Instrument(symbol=symbol, name=name, market=market,
                                  ccy=ccy, asset_class=asset_class,
                                  sector=SECTOR_MAP.get(symbol))
                session.add(inst)
                session.flush()
            inst_cache[key] = inst
        return inst_cache[key]

    with open(path, newline="", encoding="utf-8-sig") as f:
        for row_no, row in enumerate(csv.DictReader(f), start=2):
            raw_type = (row.get("Type") or "").strip()
            if raw_type not in TYPE_MAP:
                report.reject(row_no, f"未知 Type: {raw_type!r}", row)
                continue
            ttype = TYPE_MAP[raw_type]

            try:
                dt = datetime.strptime(row["Trade Date"].strip(), "%d/%m/%Y %H:%M:%S")
            except (KeyError, ValueError):
                report.reject(row_no, "日期格式錯", row)
                continue

            symbol = (row.get("Stock Symbol") or "").strip()
            if not symbol:
                report.reject(row_no, "冇股票代號", row)
                continue

            ccy = (row.get("Currency Type") or "").strip()
            price = _num(row.get("Price"))
            qty = _num(row.get("Number Of Shares"))
            fee = _num(row.get("Fee"), 0.0) or 0.0
            market = "HK" if symbol.endswith(".HK") else "US"

            if ttype in ("BUY", "SELL"):
                # Validation:零股/負股擋落(業主真數 MRVL 03/06/2026 零股零價行)
                if qty is None or qty <= 0:
                    report.reject(row_no, "買賣股數必須 > 0", row)
                    continue
                # $0 買入合法(券商送股,e.g. NVDA 1.35297 股),但負價唔准
                if price is None or price < 0:
                    report.reject(row_no, "價格缺失或為負", row)
                    continue
            else:  # DIV_CASH:Price 欄係股息總金額
                if price is None or price <= 0:
                    report.reject(row_no, "股息金額必須 > 0", row)
                    continue
                qty = None

            inst = get_instrument(symbol, (row.get("Stock Name") or "").strip(), market, ccy)
            session.add(Transaction(
                account_id=acct.id, instrument_id=inst.id, trade_dt=dt,
                type=ttype, price=price, qty=qty, fee=fee, ccy=ccy,
                source="csv_import"))
            report.imported += 1

    session.commit()
    return report
