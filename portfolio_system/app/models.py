"""資料庫 Schema — 對應規格書 §2。

核心設計:lot-level accounting。
- transactions 係 immutable 原始流水(audit trail,更正用沖銷)
- lots / lot_closures 由 FIFO 引擎維護,唔准手改
- 「現時持倉收益」只計 qty_remaining > 0 嘅 lots — 呢個係同 StockerX 嘅根本差異
"""
from datetime import datetime

from sqlalchemy import (Column, Integer, Text, Numeric, TIMESTAMP, Date,
                        Boolean, ForeignKey, CheckConstraint, UniqueConstraint,
                        create_engine, JSON)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()

TXN_TYPES = ("BUY", "SELL", "DIV_CASH", "SPLIT", "BONUS_SHARES",
             "FEE", "CASH_IN", "CASH_OUT")


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False)
    broker = Column(Text)
    base_ccy = Column(Text, nullable=False, default="HKD")


class Instrument(Base):
    __tablename__ = "instruments"
    id = Column(Integer, primary_key=True)
    symbol = Column(Text, nullable=False)      # '0941.HK' / 'TSLA'
    name = Column(Text)
    market = Column(Text, nullable=False)      # 'HK' / 'US'
    ccy = Column(Text, nullable=False)
    sector = Column(Text)
    asset_class = Column(Text, nullable=False, default="equity")
    __table_args__ = (UniqueConstraint("symbol", "market"),)


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    instrument_id = Column(Integer, ForeignKey("instruments.id"))
    trade_dt = Column(TIMESTAMP, nullable=False)
    type = Column(Text, nullable=False)
    price = Column(Numeric(18, 6))
    qty = Column(Numeric(18, 6))
    fee = Column(Numeric(18, 4), default=0)
    ccy = Column(Text, nullable=False)
    note = Column(Text)
    source = Column(Text, default="manual")
    __table_args__ = (CheckConstraint(f"type IN {TXN_TYPES}"),)
    instrument = relationship("Instrument")


class Lot(Base):
    """一次買入開一個 lot;qty_remaining 由 FIFO 引擎遞減。"""
    __tablename__ = "lots"
    id = Column(Integer, primary_key=True)
    open_txn_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    instrument_id = Column(Integer, ForeignKey("instruments.id"), nullable=False)
    open_dt = Column(TIMESTAMP, nullable=False)
    open_price = Column(Numeric(18, 6), nullable=False)
    qty_opened = Column(Numeric(18, 6), nullable=False)
    qty_remaining = Column(Numeric(18, 6), nullable=False)
    fee_per_share = Column(Numeric(18, 8), default=0)
    instrument = relationship("Instrument")


class LotClosure(Base):
    """每次 SELL 配對記錄 — 已實現損益嘅原子單位(原幣,已扣兩邊按股手續費)。"""
    __tablename__ = "lot_closures"
    id = Column(Integer, primary_key=True)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=False)
    close_txn_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    qty = Column(Numeric(18, 6), nullable=False)
    realized_pnl_ccy = Column(Numeric(18, 4), nullable=False)
    hold_days = Column(Integer, nullable=False)
    lot = relationship("Lot")
    close_txn = relationship("Transaction")


class PriceEOD(Base):
    __tablename__ = "prices_eod"
    instrument_id = Column(Integer, ForeignKey("instruments.id"), primary_key=True)
    date = Column(Date, primary_key=True)
    close = Column(Numeric(18, 6))
    adj_close = Column(Numeric(18, 6))


class FxRate(Base):
    __tablename__ = "fx_rates"
    ccy_pair = Column(Text, primary_key=True)   # 'USDHKD'
    date = Column(Date, primary_key=True)
    rate = Column(Numeric(12, 6))


class AssetOther(Base):
    __tablename__ = "assets_other"
    id = Column(Integer, primary_key=True)
    category = Column(Text, nullable=False)     # cash/syfe/mpf/bond/insurance
    name = Column(Text, nullable=False)
    ccy = Column(Text, nullable=False)
    value = Column(Numeric(18, 2), nullable=False)
    as_of = Column(Date, nullable=False)


class SnapshotDaily(Base):
    __tablename__ = "snapshots_daily"
    date = Column(Date, primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), primary_key=True)
    nav_hkd = Column(Numeric(18, 2))
    equity_mv_hkd = Column(Numeric(18, 2))
    cash_hkd = Column(Numeric(18, 2))
    exposure = Column(JSON)


class Rule(Base):
    __tablename__ = "rules"
    id = Column(Integer, primary_key=True)
    code = Column(Text, unique=True, nullable=False)
    params = Column(JSON, nullable=False)
    enabled = Column(Boolean, default=True)


class RuleViolation(Base):
    __tablename__ = "rule_violations"
    id = Column(Integer, primary_key=True)
    rule_id = Column(Integer, ForeignKey("rules.id"))
    txn_id = Column(Integer, ForeignKey("transactions.id"))
    violated_at = Column(TIMESTAMP, default=datetime.now)
    detail = Column(JSON)
    acknowledged = Column(Boolean, default=False)
    rule = relationship("Rule")


def make_session(url: str = "sqlite:///:memory:"):
    """開發用 SQLite;正式版換 postgresql:// URL 即可。"""
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()
