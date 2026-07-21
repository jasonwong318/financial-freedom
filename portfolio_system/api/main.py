"""FastAPI 層 — 對應規格書 §7。

Endpoints:
  POST /import/csv       StockerX CSV 匯入(+ validation report)
  POST /transactions     錄入交易(pre-trade 規則檢查,回 violations,可 override)
  GET  /positions        ?basis=open_lots|lifetime
  GET  /metrics/summary   勝率/賺賠比/XIRR/雙軌勝率一站攞
  GET  /snapshots         snapshots_daily 序列 + 回撤
  POST /assets_other      全資產月度輸入
  POST /advisor/ask       組合快照 → Claude(掛免責)

API 層薄:所有計算落 app/*;呢度淨係接 HTTP + session 管理。
價格由 query/body 傳入或用 prices_eod latest(唔喺 API 內部撞 yfinance,快同可測)。
"""
import os
import tempfile
from datetime import date as Date, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from pydantic import BaseModel

from app.models import (make_session, Transaction, Instrument, RuleViolation,
                        Rule)
from app.importer import import_stockerx_csv
from app.fifo import rebuild_lots
from app import metrics, rules, behavior, assets, reports, income, advisor
from app.prices import latest_prices, store_eod
from app.performance import portfolio_xirr
from app.config import fx_note

DB_URL = os.environ.get("PORTFOLIO_DB_URL", "sqlite:///portfolio.db")

app = FastAPI(title="Portfolio System API", version="0.4")
_session = None


def db():
    global _session
    if _session is None:
        _session = make_session(DB_URL)
        rules.seed_rules(_session)
    return _session


def _prices(session, overrides: dict = None):
    """latest EOD + 呼叫方 overrides(手動價)。"""
    p = latest_prices(session)
    if overrides:
        p.update({k: v for k, v in overrides.items() if v is not None})
    return p


# ---------- Schemas ----------

class TxnIn(BaseModel):
    symbol: str
    side: str                      # BUY / SELL
    price: float
    qty: float
    fee: float = 0.0
    ccy: Optional[str] = None
    trade_dt: Optional[datetime] = None
    override: bool = False         # True = 明知違規照錄
    prices: Optional[dict] = None  # 規則檢查用嘅現價快照


class AssetIn(BaseModel):
    category: str
    name: str
    ccy: str
    value: float
    as_of: Optional[Date] = None


class AdvisorIn(BaseModel):
    question: str
    prices: Optional[dict] = None


# ---------- Endpoints ----------

@app.get("/health")
def health():
    return {"status": "ok", "fx": fx_note()}


@app.post("/import/csv")
async def import_csv(file: UploadFile = File(...),
                     account_name: str = "主戶口"):
    session = db()
    suffix = os.path.splitext(file.filename or "upload.csv")[1] or ".csv"
    with tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        path = tmp.name
    try:
        report = import_stockerx_csv(session, path, account_name)
        rebuild_lots(session)
    finally:
        os.unlink(path)
    return {"imported": report.imported,
            "rejected": [{"row": r[0], "reason": r[1], "raw": r[2]}
                         for r in report.rejected]}


@app.post("/transactions")
def create_transaction(txn: TxnIn):
    session = db()
    prices = _prices(session, txn.prices)
    total = assets.total_assets_hkd(session, prices)
    trade_dt = txn.trade_dt or datetime.now()
    violations = rules.check_trade(
        session, prices, symbol=txn.symbol, side=txn.side, price=txn.price,
        qty=txn.qty, fee=txn.fee, trade_dt=trade_dt, total_assets=total)

    if violations and not txn.override:
        # 預設擋落:回 violations 俾前端顯示,要 override=true 先真正錄入
        return {"committed": False, "violations": violations,
                "hint": "override=true 可照錄(違規會記錄在案)"}

    inst = session.query(Instrument).filter_by(symbol=txn.symbol).first()
    if not inst:
        mkt = "HK" if txn.symbol.endswith(".HK") else "US"
        inst = Instrument(symbol=txn.symbol, market=mkt,
                          ccy=txn.ccy or ("HKD" if mkt == "HK" else "USD"))
        session.add(inst)
        session.flush()
    row = Transaction(account_id=1, instrument_id=inst.id, trade_dt=trade_dt,
                      type=txn.side, price=txn.price, qty=txn.qty,
                      fee=txn.fee, ccy=inst.ccy, source="api")
    session.add(row)
    session.flush()
    n = rules.record_violations(session, violations, txn_id=row.id)
    session.commit()
    rebuild_lots(session)
    return {"committed": True, "txn_id": row.id,
            "violations_recorded": n, "violations": violations}


@app.get("/positions")
def positions(basis: str = Query("open_lots", pattern="^(open_lots|lifetime)$"),
              prices: str = ""):
    session = db()
    px = _prices(session)
    if basis == "open_lots":
        pos = metrics.open_positions(session)
        upl = metrics.open_position_pnl(session, px)
        return {"basis": basis, "positions": {
            s: {**{k: p[k] for k in ("shares", "avg_cost", "ccy", "cost_ccy")},
                "unreal_hkd": (upl[s]["unreal_hkd"] if upl.get(s) else None)}
            for s, p in pos.items()}}
    # lifetime:含已實現 + 股息(三口徑分開,唔混算)
    tr = metrics.total_return_with_div(session, px)
    return {"basis": basis, "positions": tr}


@app.get("/metrics/summary")
def metrics_summary():
    session = db()
    px = _prices(session)
    rs = metrics.realized_summary(session)
    w = behavior.dual_track_win_rate(session, px)
    divs = metrics.dividends_by_symbol(session)
    try:
        xirr = portfolio_xirr(session, Date.today(), px)
    except ValueError:
        xirr = None
    return {"fx": fx_note(), "realized": rs, "win_rate_dual_track": w,
            "dividends_total_hkd": divs.get("_total", 0.0),
            "portfolio_xirr": xirr,
            "income": income.income_summary(session, px)}


@app.get("/snapshots")
def snapshots(account_id: int = 1):
    session = db()
    series = reports.nav_series(session, account_id)
    curve, max_dd = reports.drawdown_curve(series)
    return {"nav_series": [{"date": str(d), "nav_hkd": n} for d, n in series],
            "drawdown": [{"date": str(d), "nav_hkd": n, "dd_pct": dd}
                         for d, n, dd in curve],
            "max_drawdown_pct": max_dd}


@app.post("/assets_other")
def add_asset(a: AssetIn):
    session = db()
    try:
        row = assets.upsert_asset(session, a.category, a.name, a.ccy, a.value,
                                  a.as_of or Date.today())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": row.id, "category": a.category, "name": a.name,
            "net_worth": assets.net_worth(session, _prices(session))}


@app.post("/advisor/ask")
def advisor_ask(a: AdvisorIn):
    session = db()
    px = _prices(session, a.prices)
    return advisor.ask(session, px, a.question)
