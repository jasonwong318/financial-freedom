"""四大倉位(sleeve)分類 —— 對應業主 2026 策略框架。

核心信仰倉 / 地基股息倉 / 長期被動收入倉 / 衛星FOMO投機倉,各有分層紀律(見 config.BUCKETS)。
分倉解析:instrument_buckets 表(用戶改動)> config.BUCKET_MAP(預設)> DEFAULT_BUCKET。
"""
from .models import InstrumentBucket, Instrument
from .config import (BUCKETS, BUCKET_MAP, BUCKET_ORDER, DEFAULT_BUCKET,
                     short_name)
from .metrics import open_position_pnl


def bucket_of(session, symbol: str) -> str:
    """個股屬邊個倉。優先用戶覆寫,否則預設 map,再否則衛星倉。"""
    row = session.get(InstrumentBucket, symbol)
    if row:
        return row.bucket
    return BUCKET_MAP.get(symbol, DEFAULT_BUCKET)


def set_bucket(session, symbol: str, bucket: str):
    if bucket not in BUCKETS:
        raise ValueError(f"未知倉位:{bucket}(可用:{list(BUCKETS)})")
    row = session.get(InstrumentBucket, symbol)
    if row:
        row.bucket = bucket
    else:
        session.add(InstrumentBucket(symbol=symbol, bucket=bucket))
    session.commit()


def bucket_meta(bucket: str) -> dict:
    return BUCKETS[bucket]


def sleeve_weights(session, prices: dict):
    """每個倉位嘅市值同佔比 + 每隻標的分倉。

    回傳 {
      "total_mv": HKD,
      "sleeves": {bucket: {"name","mv","weight_pct","single_max","sleeve_max",
                            "sleeve_min","over": bool, "under": bool,
                            "holdings": [{symbol, name, mv, weight_pct, single_max,
                                          single_over}]}},
    }
    """
    upl = open_position_pnl(session, prices)
    mv = {s: v["mv_hkd"] for s, v in upl.items() if v}
    total = sum(mv.values()) or 1.0
    sleeves = {b: {"name": BUCKETS[b]["name"], "mv": 0.0, "holdings": [],
                   "single_max": BUCKETS[b]["single_max"],
                   "sleeve_max": BUCKETS[b]["sleeve_max"],
                   "sleeve_min": BUCKETS[b]["sleeve_min"]}
               for b in BUCKET_ORDER}
    for sym, m in mv.items():
        b = bucket_of(session, sym)
        s = sleeves.setdefault(b, {"name": BUCKETS.get(b, {}).get("name", b),
                                   "mv": 0.0, "holdings": [],
                                   "single_max": BUCKETS.get(b, {}).get("single_max"),
                                   "sleeve_max": BUCKETS.get(b, {}).get("sleeve_max"),
                                   "sleeve_min": BUCKETS.get(b, {}).get("sleeve_min")})
        w = m / total * 100
        smax = s["single_max"]
        s["holdings"].append({
            "symbol": sym, "name": short_name(sym), "mv": m, "weight_pct": w,
            "single_max": smax, "single_over": (smax is not None and w > smax)})
        s["mv"] += m
    for b, s in sleeves.items():
        w = s["mv"] / total * 100
        s["weight_pct"] = w
        s["over"] = (s["sleeve_max"] is not None and w > s["sleeve_max"])
        s["under"] = (s["sleeve_min"] is not None and 0 < w < s["sleeve_min"])
        s["holdings"].sort(key=lambda h: -h["mv"])
    return {"total_mv": total, "sleeves": sleeves}


def assign_defaults(session):
    """把 config.BUCKET_MAP 嘅預設寫入 instrument_buckets(只補未有嘅,唔覆蓋用戶改動)。"""
    existing = {r.symbol for r in session.query(InstrumentBucket).all()}
    n = 0
    for sym in {i.symbol for i in session.query(Instrument).all()}:
        if sym in existing:
            continue
        session.add(InstrumentBucket(symbol=sym,
                                     bucket=BUCKET_MAP.get(sym, DEFAULT_BUCKET)))
        n += 1
    session.commit()
    return n
