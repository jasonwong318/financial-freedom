"""行為規則引擎 — 對應規格書 §4(業主指定最高優先)。

三個入口:
1. check_trade()    — pre-trade:錄入交易前即場檢查,回傳 violations(唔寫 DB)
2. scan_portfolio() — 依現時持倉 + 現價掃描狀態類規則(STALE_LOSER 等)
3. scan_history()   — 全量重掃歷史交易(FEE_CHECK / REBUY_HIGHER / AVG_DOWN_LIMIT)

違規唔會擋住交易(業主可以 override),但一定要記錄 — 行為儀表板
用 record_violations() 寫入 rule_violations,計「違規成本」。
"""
from datetime import date as Date, datetime, timedelta
from collections import defaultdict

from .models import (Rule, RuleViolation, Transaction, Lot, LotClosure,
                     Instrument)
from .config import to_hkd
from .metrics import open_positions, open_position_pnl

# 規格書 §4 十條規則嘅預設參數 — 可喺 rules 表逐條改
DEFAULT_RULES = {
    "MAX_POSITION_WEIGHT":    {"pct": 15},
    "MAX_SECTOR_WEIGHT":      {"pct": 40},
    "MAX_SINGLE_ENTRY":       {"pct": 5},
    "AVG_DOWN_LIMIT":         {"n": 2, "min_drop_pct": 15, "max_size_pct": 50},
    "CHASE_HIGH":             {"pct": 3},
    "STALE_LOSER":            {"loss_pct": 10, "days": 180},
    "STOP_LOSS_ALERT":        {"satellite": 15, "spec": 20},
    "WEEKLY_CIRCUIT_BREAKER": {"pct": 2, "cooloff_days": 5},
    "FEE_CHECK":              {"mult": 3},
    "REBUY_HIGHER":           {"days": 90, "pct": 10},
}


def seed_rules(session):
    """預設十條規則入庫(冪等:已存在嘅唔郁,保留業主改過嘅參數)。"""
    existing = {r.code for r in session.query(Rule).all()}
    for code, params in DEFAULT_RULES.items():
        if code not in existing:
            session.add(Rule(code=code, params=params, enabled=True))
    session.commit()


def enabled_rules(session) -> dict:
    """{code: params} — 只回傳 enabled 嘅規則。"""
    return {r.code: r.params for r in
            session.query(Rule).filter_by(enabled=True).all()}


def _v(code, symbol, message, **extra):
    """violation dict — detail 直接寫入 rule_violations.detail(JSON)。"""
    return {"rule": code, "symbol": symbol, "message": message, **extra}


def _mv_by_symbol(session, prices):
    """{symbol: 市值HKD}(只計有價嘅)+ 合計。"""
    upl = open_position_pnl(session, prices)
    mv = {s: v["mv_hkd"] for s, v in upl.items() if v}
    return mv, sum(mv.values())


# ---------- 1. Pre-trade 檢查 ----------

def check_trade(session, prices, *, symbol, side, price, qty, fee=0.0,
                trade_dt=None, total_assets=None):
    """錄入前檢查一筆擬交易。回傳 violations list(空 = 過關)。

    total_assets:規則分母(HKD)。唔提供就用現時股票市值 —
    有用開全資產模組嘅話,傳 assets.total_assets_hkd() 入嚟更準。
    """
    rules = enabled_rules(session)
    trade_dt = trade_dt or datetime.now()
    inst = session.query(Instrument).filter_by(symbol=symbol).first()
    ccy = inst.ccy if inst else ("HKD" if symbol.endswith(".HK") else "USD")
    amt_hkd = to_hkd(price * qty, ccy)
    mv, nav = _mv_by_symbol(session, prices)
    total = total_assets if total_assets else nav
    pos = open_positions(session)
    out = []

    if side == "BUY":
        # MAX_POSITION_WEIGHT:買入後單一標的權重超標
        r = rules.get("MAX_POSITION_WEIGHT")
        if r and nav:
            w = (mv.get(symbol, 0) + amt_hkd) / (nav + amt_hkd) * 100
            if w > r["pct"]:
                out.append(_v("MAX_POSITION_WEIGHT", symbol,
                              f"買入後 {symbol} 佔組合 {w:.1f}%,超過上限 {r['pct']}%",
                              weight_pct=round(w, 1), limit_pct=r["pct"]))

        # MAX_SECTOR_WEIGHT:板塊合計超標(標的要有 sector 標籤先計到)
        r = rules.get("MAX_SECTOR_WEIGHT")
        if r and nav and inst and inst.sector:
            sector_syms = [i.symbol for i in session.query(Instrument)
                           .filter_by(sector=inst.sector).all()]
            sec_mv = sum(mv.get(s, 0) for s in sector_syms) + amt_hkd
            w = sec_mv / (nav + amt_hkd) * 100
            if w > r["pct"]:
                out.append(_v("MAX_SECTOR_WEIGHT", symbol,
                              f"買入後「{inst.sector}」板塊佔 {w:.1f}%,超過上限 {r['pct']}%",
                              sector=inst.sector, weight_pct=round(w, 1),
                              limit_pct=r["pct"]))

        # MAX_SINGLE_ENTRY:單筆注碼超過總資產 x%
        r = rules.get("MAX_SINGLE_ENTRY")
        if r and total:
            pct = amt_hkd / total * 100
            if pct > r["pct"]:
                out.append(_v("MAX_SINGLE_ENTRY", symbol,
                              f"單筆買入 HKD {amt_hkd:,.0f} 佔總資產 {pct:.1f}%,"
                              f"超過上限 {r['pct']}%(情緒大注警示)",
                              entry_pct=round(pct, 1), limit_pct=r["pct"]))

        # AVG_DOWN_LIMIT:溝貨紀律(只喺買價低過現時平均成本先算溝)
        r = rules.get("AVG_DOWN_LIMIT")
        p = pos.get(symbol)
        if r and p and price < p["avg_cost"]:
            reasons = []
            prior_dips = _count_open_dip_lots(session, symbol)
            if prior_dips >= r["n"]:
                reasons.append(f"已溝 {prior_dips} 次(上限 {r['n']})")
            drop = (p["avg_cost"] - price) / p["avg_cost"] * 100
            if drop < r["min_drop_pct"]:
                reasons.append(f"只跌 {drop:.1f}% 就溝(要求 ≥{r['min_drop_pct']}%)")
            size_pct = (price * qty) / p["cost_ccy"] * 100 if p["cost_ccy"] else 0
            if size_pct > r["max_size_pct"]:
                reasons.append(f"溝貨注碼係現倉成本 {size_pct:.0f}%(上限 {r['max_size_pct']}%)")
            if reasons:
                out.append(_v("AVG_DOWN_LIMIT", symbol,
                              f"溝貨警示:{';'.join(reasons)}",
                              reasons=reasons))

        # CHASE_HIGH:買價貼近 20 日高位(要 prices_eod 有數據先計到)
        r = rules.get("CHASE_HIGH")
        if r and inst:
            high20 = _high_20d(session, inst.id, trade_dt)
            if high20 and price >= high20 * (1 - r["pct"] / 100):
                out.append(_v("CHASE_HIGH", symbol,
                              f"買入價 {price} 喺 20 日高位 {high20:.2f} 嘅 "
                              f"{r['pct']}% 之內(高位追貨警示)",
                              high_20d=round(high20, 4)))

        # REBUY_HIGHER:沽出後短期高追返
        r = rules.get("REBUY_HIGHER")
        if r and inst:
            last_sell = (session.query(Transaction)
                         .filter_by(instrument_id=inst.id, type="SELL")
                         .order_by(Transaction.trade_dt.desc()).first())
            if last_sell and (trade_dt - last_sell.trade_dt).days <= r["days"]:
                sell_px = float(last_sell.price)
                if price > sell_px * (1 + r["pct"] / 100):
                    up = (price / sell_px - 1) * 100
                    out.append(_v("REBUY_HIGHER", symbol,
                                  f"{(trade_dt - last_sell.trade_dt).days} 日前以 "
                                  f"{sell_px} 沽出,而家高 {up:.0f}% 追返",
                                  sold_at=sell_px, rebuy_up_pct=round(up, 1)))

        # WEEKLY_CIRCUIT_BREAKER:一週已實現虧損超標 → 停新倉
        r = rules.get("WEEKLY_CIRCUIT_BREAKER")
        if r and total:
            wk = _realized_last_7d(session, trade_dt)
            if wk < -(r["pct"] / 100) * total:
                out.append(_v("WEEKLY_CIRCUIT_BREAKER", symbol,
                              f"過去 7 日已實現虧損 HKD {wk:,.0f},超過總資產 "
                              f"{r['pct']}% — 建議停新倉 {r['cooloff_days']} 個交易日",
                              week_realized_hkd=round(wk),
                              cooloff_days=r["cooloff_days"]))

    else:  # SELL
        # FEE_CHECK:預期毛利 < mult × 來回手續費(領展事件)
        r = rules.get("FEE_CHECK")
        p = pos.get(symbol)
        if r and p:
            gross, buy_fees = _fifo_preview(session, symbol, price, qty)
            fees = buy_fees + fee
            if fees > 0 and gross < r["mult"] * fees:
                out.append(_v("FEE_CHECK", symbol,
                              f"預期毛利 HKD {to_hkd(gross, ccy):,.0f} 不足來回手續費 "
                              f"HKD {to_hkd(fees, ccy):,.0f} 嘅 {r['mult']} 倍",
                              gross_ccy=round(gross, 2), fees_ccy=round(fees, 2)))

    return out


def _count_open_dip_lots(session, symbol):
    """現有 open lots 入面,開倉價低過最舊一批嘅數目 ≈ 溝貨次數。"""
    lots = (session.query(Lot).join(Instrument)
            .filter(Instrument.symbol == symbol, Lot.qty_remaining > 0)
            .order_by(Lot.open_dt).all())
    if len(lots) < 2:
        return 0
    first_px = float(lots[0].open_price)
    return sum(1 for l in lots[1:] if float(l.open_price) < first_px)


def _high_20d(session, instrument_id, trade_dt):
    """prices_eod 最近 20 個交易日高位;少過 5 個數據點回 None(唔夠數唔亂判)。"""
    from .models import PriceEOD
    rows = (session.query(PriceEOD)
            .filter(PriceEOD.instrument_id == instrument_id,
                    PriceEOD.date < (trade_dt.date() if isinstance(trade_dt, datetime)
                                     else trade_dt))
            .order_by(PriceEOD.date.desc()).limit(20).all())
    if len(rows) < 5:
        return None
    return max(float(r.close) for r in rows if r.close is not None)


def _fifo_preview(session, symbol, sell_price, qty):
    """模擬 FIFO 沽出 qty 股:回傳(預期毛利, 攤分買方手續費)— 原幣。"""
    lots = (session.query(Lot).join(Instrument)
            .filter(Instrument.symbol == symbol, Lot.qty_remaining > 0)
            .order_by(Lot.open_dt).all())
    remain, gross, buy_fees = qty, 0.0, 0.0
    for lot in lots:
        if remain <= 0:
            break
        take = min(remain, float(lot.qty_remaining))
        gross += (sell_price - float(lot.open_price)) * take
        buy_fees += float(lot.fee_per_share or 0) * take
        remain -= take
    return gross, buy_fees


def _realized_last_7d(session, ref_dt):
    """過去 7 日已實現 PnL(HKD)— WEEKLY_CIRCUIT_BREAKER 用。"""
    since = ref_dt - timedelta(days=7)
    rows = (session.query(LotClosure, Transaction, Instrument)
            .join(Transaction, LotClosure.close_txn_id == Transaction.id)
            .join(Lot, LotClosure.lot_id == Lot.id)
            .join(Instrument, Lot.instrument_id == Instrument.id)
            .filter(Transaction.trade_dt > since,
                    Transaction.trade_dt <= ref_dt).all())
    return sum(to_hkd(float(cl.realized_pnl_ccy), inst.ccy)
               for cl, _, inst in rows)


# ---------- 2. 狀態掃描(nightly batch / 儀表板即場) ----------

def scan_portfolio(session, prices, on_date: Date = None):
    """依現時持倉掃狀態類規則。回傳 violations(唔寫 DB,寫用 record_violations)。"""
    on_date = on_date or Date.today()
    ref_dt = datetime.combine(on_date, datetime.min.time())
    rules = enabled_rules(session)
    mv, nav = _mv_by_symbol(session, prices)
    upl = open_position_pnl(session, prices)
    out = []

    # MAX_POSITION_WEIGHT(現況版:唔使等買入先知超標)
    r = rules.get("MAX_POSITION_WEIGHT")
    if r and nav:
        for sym, m in mv.items():
            w = m / nav * 100
            if w > r["pct"]:
                out.append(_v("MAX_POSITION_WEIGHT", sym,
                              f"{sym} 現佔組合 {w:.1f}%,超過上限 {r['pct']}%",
                              weight_pct=round(w, 1), limit_pct=r["pct"]))

    # MAX_SECTOR_WEIGHT(現況版)
    r = rules.get("MAX_SECTOR_WEIGHT")
    if r and nav:
        sec_mv = defaultdict(float)
        for i in session.query(Instrument).filter(Instrument.sector.isnot(None)):
            if i.symbol in mv:
                sec_mv[i.sector] += mv[i.symbol]
        for sec, m in sec_mv.items():
            w = m / nav * 100
            if w > r["pct"]:
                out.append(_v("MAX_SECTOR_WEIGHT", sec,
                              f"「{sec}」板塊現佔 {w:.1f}%,超過上限 {r['pct']}%",
                              sector=sec, weight_pct=round(w, 1)))

    # STALE_LOSER:lot 級 — 蝕超過 loss_pct% 且持有超過 days 日
    r = rules.get("STALE_LOSER")
    if r:
        lots = (session.query(Lot, Instrument)
                .join(Instrument, Lot.instrument_id == Instrument.id)
                .filter(Lot.qty_remaining > 0).all())
        for lot, inst in lots:
            px = prices.get(inst.symbol)
            open_px = float(lot.open_price)
            if px is None or open_px == 0:
                continue                        # $0 送股冇成本,唔存在「蝕」
            loss_pct = (px - open_px) / open_px * 100
            days = (ref_dt - lot.open_dt).days
            if loss_pct <= -r["loss_pct"] and days > r["days"]:
                out.append(_v("STALE_LOSER", inst.symbol,
                              f"{inst.symbol} 有一批 {float(lot.qty_remaining):g} 股"
                              f"蝕緊 {abs(loss_pct):.0f}%、揸咗 {days} 日 — 強制檢討",
                              lot_id=lot.id, loss_pct=round(loss_pct, 1),
                              hold_days=days))

    # STOP_LOSS_ALERT:倉位級浮虧穿線(sector 標「投機」用 spec 門檻)
    r = rules.get("STOP_LOSS_ALERT")
    if r:
        spec_syms = {i.symbol for i in session.query(Instrument)
                     .filter(Instrument.sector == "投機").all()}
        for sym, v in upl.items():
            if not v or v["unreal_pct"] is None:
                continue
            th = r["spec"] if sym in spec_syms else r["satellite"]
            if v["unreal_pct"] * 100 <= -th:
                out.append(_v("STOP_LOSS_ALERT", sym,
                              f"{sym} 浮虧 {abs(v['unreal_pct'])*100:.0f}% "
                              f"已穿 −{th}% 止蝕警戒線",
                              unreal_pct=round(v["unreal_pct"] * 100, 1),
                              threshold_pct=th))

    # WEEKLY_CIRCUIT_BREAKER(現況版)
    r = rules.get("WEEKLY_CIRCUIT_BREAKER")
    if r and nav:
        wk = _realized_last_7d(session, ref_dt)
        if wk < -(r["pct"] / 100) * nav:
            out.append(_v("WEEKLY_CIRCUIT_BREAKER", "_portfolio",
                          f"過去 7 日已實現虧損 HKD {wk:,.0f} 超過總資產 {r['pct']}%"
                          f" — 建議停新倉 {r['cooloff_days']} 個交易日",
                          week_realized_hkd=round(wk)))

    return out


# ---------- 3. 歷史重掃(交易紀律回顧,行為儀表板核心輸入) ----------

def scan_history(session):
    """全量重掃歷史交易 — 只掃齋靠交易記錄就判到嘅規則:
    FEE_CHECK(回合毛利 vs 手續費)/ REBUY_HIGHER / AVG_DOWN_LIMIT。
    (權重類規則要歷史股價先判到,唔喺呢度靠估。)
    """
    rules = enabled_rules(session)
    out = []

    # FEE_CHECK:逐個已完成回合對數
    r = rules.get("FEE_CHECK")
    if r:
        per_sell = {}
        rows = (session.query(LotClosure, Lot, Transaction, Instrument)
                .join(Lot, LotClosure.lot_id == Lot.id)
                .join(Transaction, LotClosure.close_txn_id == Transaction.id)
                .join(Instrument, Lot.instrument_id == Instrument.id).all())
        for cl, lot, sell, inst in rows:
            a = per_sell.setdefault(sell.id, {
                "symbol": inst.symbol, "ccy": inst.ccy, "dt": sell.trade_dt,
                "gross": 0.0, "fees": float(sell.fee or 0)})
            a["gross"] += (float(sell.price) - float(lot.open_price)) * float(cl.qty)
            a["fees"] += float(lot.fee_per_share or 0) * float(cl.qty)
        for sid, a in per_sell.items():
            if a["fees"] > 0 and a["gross"] < r["mult"] * a["fees"]:
                out.append(_v("FEE_CHECK", a["symbol"],
                              f"{a['dt'].date()} {a['symbol']} 回合毛利 "
                              f"{a['gross']:,.0f}({a['ccy']})不足手續費 "
                              f"{a['fees']:,.0f} 嘅 {r['mult']} 倍",
                              txn_id=sid, gross_ccy=round(a["gross"], 2),
                              fees_ccy=round(a["fees"], 2)))

    # REBUY_HIGHER + AVG_DOWN_LIMIT:按時序 replay
    r_rebuy = rules.get("REBUY_HIGHER")
    r_avg = rules.get("AVG_DOWN_LIMIT")
    if r_rebuy or r_avg:
        lots = defaultdict(list)        # inst_id -> [[price, qty], ...]
        last_sell = {}                  # inst_id -> (dt, price)
        dip_count = defaultdict(int)
        txns = (session.query(Transaction, Instrument)
                .join(Instrument, Transaction.instrument_id == Instrument.id)
                .filter(Transaction.type.in_(("BUY", "SELL")))
                .order_by(Transaction.trade_dt, Transaction.id).all())
        for t, inst in txns:
            px, q = float(t.price), float(t.qty)
            iid = inst.id
            if t.type == "SELL":
                remain = q
                while remain > 1e-9 and lots[iid]:
                    take = min(remain, lots[iid][0][1])
                    lots[iid][0][1] -= take
                    remain -= take
                    if lots[iid][0][1] <= 1e-9:
                        lots[iid].pop(0)
                last_sell[iid] = (t.trade_dt, px)
                if not lots[iid]:
                    dip_count[iid] = 0          # 沽清 → 溝貨計數重置
                continue
            # BUY
            if r_rebuy and iid in last_sell:
                sdt, spx = last_sell[iid]
                if ((t.trade_dt - sdt).days <= r_rebuy["days"]
                        and px > spx * (1 + r_rebuy["pct"] / 100)):
                    out.append(_v("REBUY_HIGHER", inst.symbol,
                                  f"{t.trade_dt.date()} {inst.symbol}:沽出價 {spx} "
                                  f"後 {(t.trade_dt - sdt).days} 日以高 "
                                  f"{(px/spx-1)*100:.0f}% 嘅 {px} 追返",
                                  txn_id=t.id, sold_at=spx,
                                  rebuy_up_pct=round((px / spx - 1) * 100, 1)))
            if r_avg and lots[iid]:
                cost = sum(p * qq for p, qq in lots[iid])
                shares = sum(qq for _, qq in lots[iid])
                avg = cost / shares
                if px < avg:                     # 溝貨事件
                    dip_count[iid] += 1
                    reasons = []
                    if dip_count[iid] > r_avg["n"]:
                        reasons.append(f"第 {dip_count[iid]} 次溝(上限 {r_avg['n']})")
                    drop = (avg - px) / avg * 100
                    if drop < r_avg["min_drop_pct"]:
                        reasons.append(f"只跌 {drop:.1f}% 就溝"
                                       f"(要求 ≥{r_avg['min_drop_pct']}%)")
                    if (px * q) / cost * 100 > r_avg["max_size_pct"]:
                        reasons.append(f"注碼係現倉 {(px*q)/cost*100:.0f}%"
                                       f"(上限 {r_avg['max_size_pct']}%)")
                    if reasons:
                        out.append(_v("AVG_DOWN_LIMIT", inst.symbol,
                                      f"{t.trade_dt.date()} {inst.symbol} 溝貨違規:"
                                      f"{';'.join(reasons)}",
                                      txn_id=t.id, reasons=reasons))
            lots[iid].append([px, q])
        # replay 完畢
    return out


# ---------- 違規入庫 ----------

def record_violations(session, violations, txn_id=None):
    """寫入 rule_violations(dedupe:同 rule+txn+symbol 只記一次)。回傳新增數。"""
    by_code = {r.code: r for r in session.query(Rule).all()}
    n = 0
    for v in violations:
        rule = by_code.get(v["rule"])
        if not rule:
            continue
        tid = v.get("txn_id", txn_id)
        dup = any((e.detail or {}).get("symbol") == v["symbol"]
                  and (e.detail or {}).get("lot_id") == v.get("lot_id")
                  for e in session.query(RuleViolation)
                  .filter_by(rule_id=rule.id, txn_id=tid).all())
        if dup:
            continue
        session.add(RuleViolation(rule_id=rule.id, txn_id=tid, detail=v))
        n += 1
    session.commit()
    return n
