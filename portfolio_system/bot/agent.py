"""交易守門 Agent 核心 —— 純邏輯(唔靠網絡,可單元測試)。

俾 Telegram bot(或者任何 chat 介面)call:
- parse_trade(text):由自然語言抽出擬交易(方向/代號/股數/價/金額)
- evaluate(session, prices, intent):跑 rules.check_trade,回傳裁決文字
- portfolio_status(session, prices):倉位配置 + 現時違規摘要

設計:重用 app 嘅規則引擎(12 條分倉守則),bot 只係薄殼 + 文字格式化。
"""
import re
from datetime import datetime

from app import rules, buckets, config
from app.metrics import open_positions
from app.assets import total_assets_hkd
from app.prices import latest_prices

# 中文簡稱 → 代號(俾用戶打「買中移動」都認到)
_NAME_TO_SYM = {v: k for k, v in config.NAME_MAP.items()}

_BUY_WORDS = ("買", "buy", "加倉", "溝")
_SELL_WORDS = ("賣", "沽", "sell", "減倉", "清倉")


def _to_number(tok: str):
    """'5萬'/'5万'/'50k'/'12,000'/'3.5' → float。認唔到回 None。"""
    t = tok.replace(",", "").replace("$", "").strip().lower()
    mult = 1.0
    for suf, m in (("萬", 1e4), ("万", 1e4), ("k", 1e3), ("m", 1e6)):
        if t.endswith(suf):
            t = t[: -len(suf)]
            mult = m
            break
    try:
        return float(t) * mult
    except ValueError:
        return None


def parse_trade(text: str):
    """由自然語言抽出擬交易。回傳 dict 或 None(認唔到)。

    支援:
      "買 TSLA 100 @ 407"        → qty + price
      "我想用 5萬 買 中移動"       → amount(之後用現價換股數)
      "沽 9988.HK 500"            → SELL
    """
    t = text.strip()
    side = None
    for w in _BUY_WORDS:
        if w in t.lower():
            side = "BUY"
            break
    if side is None:
        for w in _SELL_WORDS:
            if w in t.lower():
                side = "SELL"
                break
    if side is None:
        return None

    # 代號:NNNN.HK 或 一串大寫字母;或者中文簡稱
    sym = None
    m = re.search(r"\b(\d{4,5}\.HK)\b", t, re.I)
    if m:
        sym = m.group(1).upper()
    if not sym:
        m = re.search(r"\b([A-Za-z]{1,6})\b(?![a-z])", t)
        # 排除英文動詞
        cand = [x for x in re.findall(r"\b([A-Za-z]{2,6})\b", t)
                if x.lower() not in ("buy", "sell")]
        if cand:
            sym = cand[0].upper()
    if not sym:
        for name, s in _NAME_TO_SYM.items():
            if name in t:
                sym = s
                break
    if not sym:
        return None

    # 價:@ 後面,或者「價 X」
    price = None
    m = re.search(r"@\s*([\d,.]+)", t)
    if m:
        price = _to_number(m.group(1))

    # 金額:「用 X 買」/「$X」/「X蚊」
    amount = None
    m = re.search(r"(?:用|使)\s*([\d,.]+\s*[萬万kKmM]?)", t)
    if m:
        amount = _to_number(m.group(1))
    if amount is None:
        m = re.search(r"\$\s*([\d,.]+\s*[萬万kKmM]?)", t)
        if m:
            amount = _to_number(m.group(1))

    # 股數:「X 股」或者一個淨數字(唔係價、唔係金額)
    qty = None
    m = re.search(r"([\d,.]+)\s*股", t)
    if m:
        qty = _to_number(m.group(1))
    if qty is None and amount is None:
        # 攞唔係緊跟 @ 嘅第一個純數字做股數
        nums = re.findall(r"(?<![@$])\b(\d[\d,]*)\b", t)
        cand = [n for n in nums if _to_number(n) not in (price,)]
        if cand:
            qty = _to_number(cand[-1])

    return {"side": side, "symbol": sym, "price": price, "qty": qty,
            "amount": amount}


def evaluate(session, prices, intent, total_assets=None):
    """跑規則引擎,回傳裁決文字(Telegram Markdown)。"""
    sym = intent["symbol"]
    side = intent["side"]
    price = intent.get("price")
    qty = intent.get("qty")
    amount = intent.get("amount")

    if price is None:
        price = (prices or {}).get(sym)
    if price is None:
        return (f"⚠️ 冇 *{sym}* 嘅價,我計唔到。請寫明價,例如「買 {sym} 100 @ 407」。")
    if qty is None and amount is not None:
        qty = amount / price
    if qty is None or qty <= 0:
        return f"⚠️ 唔知買幾多股。請寫股數或者金額,例如「用 5萬 買 {sym}」。"

    total = total_assets if total_assets else total_assets_hkd(session, prices)
    b = buckets.bucket_of(session, sym)
    bname = config.BUCKETS[b]["name"]
    vs = rules.check_trade(session, prices, symbol=sym, side=side, price=price,
                           qty=qty, trade_dt=datetime.now(), total_assets=total)
    amt = price * qty
    head = (f"*{('買入' if side=='BUY' else '沽出')} {config.short_name(sym)}"
            f"({sym})*  {qty:g} 股 @ {price:g}\n"
            f"金額 ≈ {amt:,.0f} · 倉位:{bname}\n")
    if not vs:
        return head + "\n🟢 *過關* —— 12 條守則全部冇問題。"
    lines = [head, f"\n🔴 *有 {len(vs)} 個問題,建議三思:*"]
    for v in vs:
        lines.append(f"• ⚠️ *{v['rule']}* — {v['message']}")
    lines.append("\n_只係提醒,最後你話事。_")
    return "\n".join(lines)


def portfolio_status(session, prices=None):
    """倉位配置 + 現時違規摘要(/status)。"""
    prices = prices or latest_prices(session)
    if not open_positions(session):
        return "組合暫時冇持倉。"
    sw = buckets.sleeve_weights(session, prices)
    lines = ["*四大倉位配置*"]
    for b in config.BUCKET_ORDER:
        s = sw["sleeves"].get(b, {})
        meta = config.BUCKETS[b]
        hi = meta.get("sleeve_max")
        flag = " 🔴超標" if s.get("over") else (" 🟡未達下限" if s.get("under") else " 🟢")
        lines.append(f"• {meta['name']}:{s.get('weight_pct', 0):.1f}% (上限 {hi}%){flag}")
    viol = rules.scan_portfolio(session, prices)
    if viol:
        lines.append(f"\n*現時 {len(viol)} 項提示:*")
        for v in viol[:12]:
            lines.append(f"• {v['message']}")
        if len(viol) > 12:
            lines.append(f"…仲有 {len(viol) - 12} 項")
    else:
        lines.append("\n🟢 冇違規提示。")
    return "\n".join(lines)
