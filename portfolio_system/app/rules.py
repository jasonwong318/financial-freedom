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
from .config import to_hkd, BUCKETS
from .metrics import open_positions, open_position_pnl
from . import buckets as bk

# 行為規則預設參數(業主 2026 分倉框架,12 條 + 保留 FEE_CHECK)。可喺 rules 表逐條改。
# tiered=True 嘅規則按四大倉位(config.BUCKETS)分層,唔用單一 pct。
DEFAULT_RULES = {
    "MAX_POSITION_WEIGHT":    {"tiered": True},   # 核心40/地基20/衛星5(sleeve 合計亦查)
    "MAX_SECTOR_WEIGHT":      {"pct": 40},
    "MAX_SINGLE_ENTRY":       {"pct": 5},
    "AVG_DOWN_LIMIT":         {"n": 2, "min_drop_pct": 15, "max_size_pct": 50},
    "CHASE_HIGH":             {"pct": 3},
    "STALE_LOSER":            {"loss_pct": 20, "days": 180, "force_sell_half": True},
    "STOP_LOSS_ALERT":        {"tiered": True},   # 衛星-20強制/核心地基-30檢討/被動免
    "WEEKLY_CIRCUIT_BREAKER": {"pct": 2, "cooloff_days": 5},
    "REBUY_HIGHER":           {"days": 90, "pct": 10},
    "NEW_FOMO_CAP":           {"monthly_pct": 2, "total_pct": 10},
    "CASH_BUFFER_RULE":       {"min_pct": 5},
    "QUARTERLY_REBALANCE":    {},
}
# 已移除:FEE_CHECK(業主決定唔理手續費——粒數 immaterial、少記錄、券商已平)


def rule_requirement(code: str, params: dict) -> str:
    """將規則參數渲染成「實際要求」句子(UI 顯示用,唔淨係列 raw params)。"""
    p = params or {}
    try:
        if code == "MAX_POSITION_WEIGHT":
            return ("分層單一標的上限:核心信仰倉 ≤40%、地基股息倉 ≤20%、"
                    "衛星投機倉 ≤5%(sleeve 合計亦有上限)")
        if code == "MAX_SECTOR_WEIGHT":
            return f"同一板塊合計市值唔可以超過組合 {p['pct']}%"
        if code == "MAX_SINGLE_ENTRY":
            return f"單筆買入金額唔可以超過總資產 {p['pct']}%"
        if code == "AVG_DOWN_LIMIT":
            return (f"同一隻溝貨最多 {p['n']} 次;要跌 ≥{p['min_drop_pct']}% 先可以溝;"
                    f"單次注碼唔可以超過現倉成本 {p['max_size_pct']}%")
        if code == "CHASE_HIGH":
            return f"買入價唔可以喺 20 日高位 {p['pct']}% 之內(避免高追)"
        if code == "STALE_LOSER":
            tail = "→ 強制賣出一半、餘下檢討" if p.get("force_sell_half") else "→ 強制檢討"
            return (f"非核心倉:帳面蝕 >{p['loss_pct']}% 又揸超過 {p['days']} 日 "
                    f"{tail}(核心信仰倉豁免)")
        if code == "STOP_LOSS_ALERT":
            return ("分層止蝕:衛星投機倉 −20% 強制止蝕;核心/地基倉 −30% 檢討;"
                    "被動收入倉唔止蝕")
        if code == "WEEKLY_CIRCUIT_BREAKER":
            return (f"一週已實現虧損超過總資產 {p['pct']}% → 建議停新倉 "
                    f"{p['cooloff_days']} 個交易日")
        if code == "REBUY_HIGHER":
            return f"沽出後 {p['days']} 日內唔好以高 >{p['pct']}% 價買返同一隻"
        if code == "NEW_FOMO_CAP":
            return (f"每月新增投機倉 ≤總資產 {p['monthly_pct']}%;"
                    f"整個投機倉合計 ≤{p['total_pct']}%")
        if code == "CASH_BUFFER_RULE":
            return (f"任何時候維持 ≥{p['min_pct']}% 現金;沽出資金先補足現金緩衝"
                    "再買新標的")
        if code == "QUARTERLY_REBALANCE":
            return ("每季尾提示再平衡:超權重減到上限、處理 STALE/止蝕標的、"
                    "回收資金按目標權重補地基/被動倉")
    except KeyError:
        pass
    return str(p)


def seed_rules(session):
    """規則入庫(冪等)。新規則自動加;呢幾條由舊版(單一 pct)升級到分倉框架,
    強制同步 params,其餘保留用戶改過嘅參數。"""
    resync = {"MAX_POSITION_WEIGHT", "STOP_LOSS_ALERT", "STALE_LOSER"}
    existing = {r.code: r for r in session.query(Rule).all()}
    for code, params in DEFAULT_RULES.items():
        if code not in existing:
            session.add(Rule(code=code, params=params, enabled=True))
        elif code in resync and existing[code].params != params:
            existing[code].params = params      # 升級到分倉框架
    # 清走已淘汰嘅規則(FEE_CHECK)
    for code in ("FEE_CHECK",):
        if code in existing:
            session.delete(existing[code])
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
        b = bk.bucket_of(session, symbol)
        bmeta = BUCKETS.get(b, {})
        # MAX_POSITION_WEIGHT:買入後按倉位單一上限(核心40/地基20/衛星5)
        r = rules.get("MAX_POSITION_WEIGHT")
        if r and nav:
            smax = bmeta.get("single_max")
            if smax is not None:
                w = (mv.get(symbol, 0) + amt_hkd) / (nav + amt_hkd) * 100
                if w > smax:
                    out.append(_v("MAX_POSITION_WEIGHT", symbol,
                                  f"買入後 {symbol} 佔組合 {w:.1f}%,超過"
                                  f"{bmeta.get('name', b)}單一上限 {smax}%",
                                  weight_pct=round(w, 1), limit_pct=smax, bucket=b))

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

        # NEW_FOMO_CAP:買衛星/投機倉 → 月度新增 + 總量上限
        r = rules.get("NEW_FOMO_CAP")
        if r and total and b == "satellite":
            sw = bk.sleeve_weights(session, prices)
            cur = sw["sleeves"].get("satellite", {}).get("weight_pct", 0)
            after = (cur * total / 100 + amt_hkd) / (total) * 100 if total else 0
            if after > r["total_pct"]:
                out.append(_v("NEW_FOMO_CAP", symbol,
                              f"買入後衛星/投機倉合計 {after:.1f}%,超過總量上限 "
                              f"{r['total_pct']}% — 唔好再加 FOMO",
                              weight_pct=round(after, 1), limit_pct=r["total_pct"]))
            month_new = _satellite_new_this_month(session, trade_dt) + amt_hkd
            if month_new / total * 100 > r["monthly_pct"]:
                out.append(_v("NEW_FOMO_CAP", symbol,
                              f"本月新增投機倉 HKD {month_new:,.0f} 佔 "
                              f"{month_new/total*100:.1f}%,超過每月上限 {r['monthly_pct']}%",
                              monthly_pct=round(month_new / total * 100, 1)))

        # CASH_BUFFER_RULE:買入後現金會唔會跌穿 5% 緩衝
        r = rules.get("CASH_BUFFER_RULE")
        if r:
            cash, total_assets = _cash_and_total(session, prices)
            if cash is not None and total_assets > 0:
                after_cash = cash - amt_hkd
                if after_cash / total_assets * 100 < r["min_pct"]:
                    out.append(_v("CASH_BUFFER_RULE", symbol,
                                  f"買入後現金剩 {after_cash/total_assets*100:.1f}%,"
                                  f"低過 {r['min_pct']}% 緩衝 — 先留返現金",
                                  cash_pct_after=round(after_cash / total_assets * 100, 1)))

    # SELL:目前冇 pre-trade 沽出規則(FEE_CHECK 已移除)
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

    # MAX_POSITION_WEIGHT(分倉:單一標的按倉位上限 + sleeve 合計上限)
    r = rules.get("MAX_POSITION_WEIGHT")
    if r and nav:
        sw = bk.sleeve_weights(session, prices)
        for b, s in sw["sleeves"].items():
            meta = BUCKETS.get(b, {})
            for h in s["holdings"]:
                if h["single_over"]:
                    out.append(_v("MAX_POSITION_WEIGHT", h["symbol"],
                                  f"{h['name']}({h['symbol']})現佔 {h['weight_pct']:.1f}%,"
                                  f"超過{meta.get('name', b)}單一上限 {h['single_max']}%",
                                  weight_pct=round(h["weight_pct"], 1),
                                  limit_pct=h["single_max"], bucket=b))
            if s.get("over"):
                out.append(_v("MAX_POSITION_WEIGHT", f"_sleeve_{b}",
                              f"{s['name']}合計佔 {s['weight_pct']:.1f}%,"
                              f"超過倉位上限 {s['sleeve_max']}%",
                              weight_pct=round(s["weight_pct"], 1),
                              limit_pct=s["sleeve_max"], bucket=b, sleeve=True))

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

    # STALE_LOSER:非核心倉 lot 級 — 蝕超過 loss_pct% 且持有超過 days 日
    r = rules.get("STALE_LOSER")
    if r:
        lots = (session.query(Lot, Instrument)
                .join(Instrument, Lot.instrument_id == Instrument.id)
                .filter(Lot.qty_remaining > 0).all())
        for lot, inst in lots:
            if bk.bucket_of(session, inst.symbol) == "core":
                continue                        # 核心信仰倉豁免
            px = prices.get(inst.symbol)
            open_px = float(lot.open_price)
            if px is None or open_px == 0:
                continue                        # $0 送股冇成本,唔存在「蝕」
            loss_pct = (px - open_px) / open_px * 100
            days = (ref_dt - lot.open_dt).days
            if loss_pct <= -r["loss_pct"] and days > r["days"]:
                act = "強制賣出一半、餘下檢討" if r.get("force_sell_half") else "強制檢討"
                out.append(_v("STALE_LOSER", inst.symbol,
                              f"{config_short(inst)} 有一批 {float(lot.qty_remaining):g} 股"
                              f"蝕緊 {abs(loss_pct):.0f}%、揸咗 {days} 日 — {act}",
                              lot_id=lot.id, loss_pct=round(loss_pct, 1),
                              hold_days=days))

    # STOP_LOSS_ALERT:分倉止蝕(衛星 −20 強制 / 核心地基 −30 檢討 / 被動免)
    r = rules.get("STOP_LOSS_ALERT")
    if r:
        for sym, v in upl.items():
            if not v or v["unreal_pct"] is None:
                continue
            b = bk.bucket_of(session, sym)
            meta = BUCKETS.get(b, {})
            th = meta.get("stop_pct")
            if th is None:
                continue                        # 被動收入倉唔止蝕
            if v["unreal_pct"] * 100 <= -th:
                forced = meta.get("force_stop")
                act = "止蝕提醒(建議處理,唔好深套)" if forced else "檢討"
                out.append(_v("STOP_LOSS_ALERT", sym,
                              f"{bk.short_name(sym)}({sym})浮虧 "
                              f"{abs(v['unreal_pct'])*100:.0f}% 穿 {meta.get('name', b)} "
                              f"−{th}% 止蝕線 — {act}",
                              unreal_pct=round(v["unreal_pct"] * 100, 1),
                              threshold_pct=th, bucket=b, forced=forced))

    # NEW_FOMO_CAP:整個衛星/投機倉合計上限
    r = rules.get("NEW_FOMO_CAP")
    if r and nav:
        sw = bk.sleeve_weights(session, prices)
        sat = sw["sleeves"].get("satellite", {})
        w = sat.get("weight_pct", 0)
        if w > r["total_pct"]:
            out.append(_v("NEW_FOMO_CAP", "_sleeve_satellite",
                          f"衛星/投機倉合計 {w:.1f}%,超過總量上限 {r['total_pct']}% "
                          "— 停止新增 FOMO,先減磅",
                          weight_pct=round(w, 1), limit_pct=r["total_pct"]))

    # CASH_BUFFER_RULE:現金緩衝(需要全資產有現金數據)
    r = rules.get("CASH_BUFFER_RULE")
    if r:
        cash, total_assets = _cash_and_total(session, prices)
        if total_assets > 0:
            if cash is None:
                out.append(_v("CASH_BUFFER_RULE", "_cash",
                              "未有現金/銀行結餘數據 — 去全資產頁更新先計到緩衝",
                              cash_pct=None))
            elif cash / total_assets * 100 < r["min_pct"]:
                out.append(_v("CASH_BUFFER_RULE", "_cash",
                              f"現金只佔 {cash/total_assets*100:.1f}%,低過 "
                              f"{r['min_pct']}% 緩衝下限 — 補回現金先好買新標的",
                              cash_pct=round(cash / total_assets * 100, 1)))

    # QUARTERLY_REBALANCE:季尾提示 + 列出要處理嘅超權重/止蝕標的
    r = rules.get("QUARTERLY_REBALANCE")
    if r is not None:
        todo = [x for x in out if x["rule"] in
                ("MAX_POSITION_WEIGHT", "STOP_LOSS_ALERT", "STALE_LOSER")]
        if _is_quarter_end(on_date) and todo:
            out.append(_v("QUARTERLY_REBALANCE", "_portfolio",
                          f"季尾再平衡:有 {len(todo)} 項超權重/止蝕待處理,"
                          "減磅至上限並把資金補回地基/被動倉",
                          items=len(todo)))

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


def config_short(inst):
    """instrument → 簡稱(name fallback)。"""
    return bk.short_name(inst.symbol, inst.name)


def _cash_and_total(session, prices):
    """(現金HKD, 總資產HKD)— 現金來自 assets_other category cash;冇就 (None, 股票市值)。"""
    from .assets import latest_assets, total_assets_hkd
    cash_rows = latest_assets(session, category="cash")
    cash = sum(a["value_hkd"] for a in cash_rows) if cash_rows else None
    total = total_assets_hkd(session, prices)
    return cash, total


def _is_quarter_end(on_date: Date) -> bool:
    """喺季度最後一個月(3/6/9/12)嘅後段(≥25 號)就當季尾。"""
    return on_date.month in (3, 6, 9, 12) and on_date.day >= 25


def _satellite_new_this_month(session, ref_dt):
    """當月(ref_dt 所屬月)已買入嘅衛星倉金額(HKD)。NEW_FOMO_CAP 月度上限用。"""
    start = datetime(ref_dt.year, ref_dt.month, 1)
    rows = (session.query(Transaction, Instrument)
            .join(Instrument, Transaction.instrument_id == Instrument.id)
            .filter(Transaction.type == "BUY",
                    Transaction.trade_dt >= start,
                    Transaction.trade_dt <= ref_dt).all())
    return sum(to_hkd(float(t.price) * float(t.qty), t.ccy)
               for t, inst in rows if bk.bucket_of(session, inst.symbol) == "satellite")


# ---------- 3. 歷史重掃(交易紀律回顧,行為儀表板核心輸入) ----------

def scan_history(session):
    """全量重掃歷史交易 — 只掃齋靠交易記錄就判到嘅規則:
    REBUY_HIGHER / AVG_DOWN_LIMIT。(權重類規則要歷史股價先判到,唔喺呢度靠估。)
    """
    rules = enabled_rules(session)
    out = []

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
