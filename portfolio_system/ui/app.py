"""Streamlit UI — 規格書 §6 嘅第一版(總覽/持倉/已平倉)。

跑法:
    pip install streamlit yfinance
    streamlit run ui/app.py

口徑鐵律(§6.3):「現時持倉收益」同 lifetime 收益分開兩欄,永不相加做單一數。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date
import streamlit as st
import pandas as pd

from app.models import make_session
from app.importer import import_stockerx_csv
from app.fifo import rebuild_lots
from app import metrics
from app.prices import YFinanceProvider, ManualPriceProvider, store_eod, latest_prices
from app.performance import portfolio_xirr, build_snapshot
from app.config import to_hkd

DB_URL = "sqlite:///portfolio.db"
CSV_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "tests", "data",
                           "Stock-20260711.csv")

st.set_page_config(page_title="Portfolio System", layout="wide")


@st.cache_resource
def get_session():
    s = make_session(DB_URL)
    from app.models import Transaction
    if not s.query(Transaction).first():          # 首次啟動自動匯入
        import_stockerx_csv(s, CSV_DEFAULT)
        rebuild_lots(s)
    return s


session = get_session()
st.title("Portfolio System")
st.caption("lot-level 口徑 · 匯率 7.80(固定) · 取代 StockerX")

# ---- 側欄:攞價 ----
with st.sidebar:
    st.header("價格更新")
    if st.button("yfinance 攞最新 EOD"):
        pos = metrics.open_positions(session)
        got = YFinanceProvider().get_eod(list(pos), date.today())
        n = store_eod(session, date.today(), got)
        st.success(f"更新咗 {n} 隻")
    prices = latest_prices(session)
    missing = [s for s in metrics.open_positions(session) if s not in prices]
    if missing:
        st.warning(f"欠價:{', '.join(missing)}(可手動輸入)")
        for sym in missing:
            v = st.number_input(f"{sym} 現價", min_value=0.0, key=f"px_{sym}")
            if v > 0:
                prices[sym] = v

tab1, tab2, tab3 = st.tabs(["總覽", "持倉", "已平倉"])

# ---- 總覽(§6.1) ----
with tab1:
    upl = metrics.open_position_pnl(session, prices)
    have_all = all(v is not None for v in upl.values())
    mv = sum(v["mv_hkd"] for v in upl.values() if v)
    unreal = sum(v["unreal_hkd"] for v in upl.values() if v)
    divs = metrics.dividends_by_symbol(session)
    rs = metrics.realized_summary(session)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("總市值 HKD", f"{mv:,.0f}")
    c2.metric("未實現(價差)", f"{unreal:+,.0f}")
    c3.metric("已實現 + 股息", f"{rs['total_realized_hkd'] + divs['_total']:+,.0f}")
    if have_all:
        r = portfolio_xirr(session, date.today(), prices)
        c4.metric("XIRR 年化", f"{r*100:.2f}%" if r is not None else "N/A")
    else:
        c4.metric("XIRR 年化", "欠價")

    # 集中度(規格書:15% 上限線)
    w = pd.Series({s: v["mv_hkd"] for s, v in upl.items() if v}).sort_values(ascending=False) / mv
    st.subheader("持倉權重(紅線 = 15% 單一標的上限)")
    breach = w[w > 0.15]
    if len(breach):
        st.error("超標:" + ", ".join(f"{s} {v:.1%}" for s, v in breach.items()))
    st.bar_chart(w)

# ---- 持倉(§6.3:雙軌口徑分兩欄) ----
with tab2:
    rows = []
    pos = metrics.open_positions(session)
    tr = metrics.total_return_with_div(session, prices)
    for sym, p in pos.items():
        u = upl.get(sym)
        rows.append({
            "標的": sym, "股數": p["shares"], "平均成本(只計現有lot)": round(p["avg_cost"], 3),
            "現價": prices.get(sym),
            "現時持倉收益HKD": round(u["unreal_hkd"]) if u else None,
            "lifetime已實現HKD": round(tr[sym]["realized_hkd"]),
            "累計股息HKD": round(tr[sym]["dividends_hkd"]),
        })
    df = (pd.DataFrame(rows).sort_values("現時持倉收益HKD",
                                          ascending=True, na_position="last"))
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption("StockerX 會將三欄加埋做一個誤導數字;本系統永遠分開。")

# ---- 已平倉(§6.4) ----
with tab3:
    rts = metrics.round_trips(session)
    df = pd.DataFrame([{"標的": r["symbol"], "平倉日": r["sell_dt"].date(),
                        "持有日數": r["hold_days"],
                        "已實現HKD": round(r["pnl_hkd"])} for r in rts])
    st.dataframe(df.sort_values("平倉日", ascending=False),
                 use_container_width=True, hide_index=True)
    st.caption(f"共 {rs['rounds']} 回合 · 勝率 {rs['win_rate']:.1%} · "
               f"賺賠比 {rs['pl_ratio']:.2f} · 期望值 {rs['expectancy_hkd']:,.0f}/回合")
