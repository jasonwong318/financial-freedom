"""Streamlit UI — 規格書 §6(九頁)。

視覺:Linear × Bloomberg terminal 暗色主題,由 ui/theme.py + .streamlit/config.toml
提供(near-black canvas、Inter/tabular numbers、hairline 邊、lavender accent、
損益紅綠色碼、TradingView 式圖表)。主題純外觀,唔改任何功能或數據口徑。

跑法:
    pip install streamlit yfinance
    streamlit run ui/app.py

口徑鐵律(§6.3):「現時持倉收益」同 lifetime 收益分開兩欄,永不相加做單一數。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# `import theme`:ui/ 加去 path 最後(唔可以放前,否則 ui/app.py 會遮蔽 app/ 套件)
if os.path.dirname(__file__) not in sys.path:
    sys.path.append(os.path.dirname(__file__))

from datetime import date, datetime
import streamlit as st
import pandas as pd

from app.models import make_session, Transaction, Instrument, RuleViolation, Rule
from app.importer import import_stockerx_csv
from app.fifo import rebuild_lots
from app import (metrics, rules, behavior, assets, reports, income, benchmark,
                 fx, advisor, committee)
from app.prices import YFinanceProvider, ManualPriceProvider, store_eod, latest_prices
from app.performance import portfolio_xirr, build_snapshot, twrr
from app import config
from app.config import to_hkd
import theme

DB_URL = os.environ.get("PORTFOLIO_DB_URL", "sqlite:///portfolio.db")
CSV_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "tests", "data",
                           "Stock-20260711.csv")

st.set_page_config(page_title="Investment Committee 投資委員會", layout="wide",
                   initial_sidebar_state="expanded")
theme.inject(st)          # Linear × Bloomberg 暗色主題(純外觀,無改功能)


@st.cache_resource
def get_session():
    s = make_session(DB_URL)
    if not s.query(Transaction).first():          # 首次啟動自動匯入
        import_stockerx_csv(s, CSV_DEFAULT)
        rebuild_lots(s)
    rules.seed_rules(s)
    return s


session = get_session()
st.title("Investment Committee · 投資委員會")
st.caption(f"lot-level 口徑 · {config.fx_note()} · 取代 StockerX")

# ---- 側欄:匯入 CSV + 攞價 + 匯率口徑 ----
with st.sidebar:
    st.header("匯入交易 CSV")
    up = st.file_uploader("StockerX 匯出格式(.csv)", type="csv")
    st.caption("每次由券商匯出一份完整 CSV,撳下面「清空並重新匯入」就更新晒。")
    if up is not None and st.button("清空並重新匯入", type="primary"):
        import tempfile
        from app.models import Lot, LotClosure
        with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as tmp:
            tmp.write(up.getvalue())
            tmp_path = tmp.name
        try:
            session.query(LotClosure).delete()
            session.query(Lot).delete()
            session.query(Transaction).delete()
            session.commit()
            report = import_stockerx_csv(session, tmp_path)
            rebuild_lots(session)
        finally:
            os.unlink(tmp_path)
        msg = f"匯入 {report.imported} 筆"
        if report.rejected:
            msg += f",擋咗 {len(report.rejected)} 筆壞行"
        st.success(msg + " ✓ 已重算 FIFO")
        if report.rejected:
            with st.expander(f"驗證報告:{len(report.rejected)} 筆被擋(唔入庫)"):
                st.dataframe(pd.DataFrame(
                    [{"行號": r[0], "原因": r[1],
                      "代號": r[2].get("Stock Symbol"),
                      "日期": r[2].get("Trade Date")} for r in report.rejected]),
                    use_container_width=True, hide_index=True)
        st.rerun()

    st.header("價格更新")
    if st.button("yfinance 攞最新 EOD"):
        pos = metrics.open_positions(session)
        got = YFinanceProvider().get_eod(list(pos), date.today())
        n = store_eod(session, date.today(), got)
        st.success(f"更新咗 {n} 隻")

    st.header("匯率口徑(§2)")
    mode = st.radio("USDHKD 模式", ["固定 7.80", "歷史(fx_rates)"],
                    index=0 if config.FX_MODE == "fixed" else 1)
    if mode.startswith("固定"):
        fx.use_fixed()
    else:
        try:
            fx.load_rates(session)
        except ValueError:
            if st.button("backfill USDHKD=X(yfinance)"):
                from app.benchmark import YFinanceBenchmarkProvider

                class _P:                       # 包成 fx provider 介面
                    def series(self, t, s, e):
                        return YFinanceBenchmarkProvider().series(t, s, e)
                fx.backfill_usdhkd(session, date(2019, 1, 1), date.today(), _P())
                fx.load_rates(session)
                st.rerun()
            st.info("未有歷史匯率數據,撳上面 backfill")
    st.caption(config.fx_note())

    prices = latest_prices(session)
    missing = [s for s in metrics.open_positions(session) if s not in prices]
    if missing:
        st.warning(f"欠價:{', '.join(missing)}(可手動輸入)")
        for sym in missing:
            v = st.number_input(f"{sym} 現價", min_value=0.0, key=f"px_{sym}")
            if v > 0:
                prices[sym] = v

tab1, tab_inc, tab2, tab3, tab4, tab5, tab6, tab7, tab_ai = st.tabs(
    ["總覽", "收益", "持倉", "已平倉", "新增交易", "行為儀表板",
     "全資產", "報表", "投資委員會"])

# ---- 總覽(§6.1) ----
with tab1:
    upl = metrics.open_position_pnl(session, prices)
    have_all = all(v is not None for v in upl.values())
    mv = sum(v["mv_hkd"] for v in upl.values() if v)
    unreal = sum(v["unreal_hkd"] for v in upl.values() if v)
    divs = metrics.dividends_by_symbol(session)
    rs = metrics.realized_summary(session)
    realized = rs.get("total_realized_hkd", 0.0)

    # 五格:總市值 / 持倉損益 / 已實現損益 / 股息 / XIRR —— 損益紅綠色碼
    c1, c2, c3, c4, c5 = st.columns(5)
    theme.stat(c1, "總市值 HKD", f"{mv:,.0f}")
    theme.stat(c2, "持倉收益/虧損", f"{unreal:+,.0f}", tone=f"auto:{unreal}")
    theme.stat(c3, "已實現收益/虧損", f"{realized:+,.0f}", tone=f"auto:{realized}")
    theme.stat(c4, "股息(全歷史)", f"{divs['_total']:+,.0f}", tone="pos")
    if have_all:
        r = portfolio_xirr(session, date.today(), prices)
        theme.stat(c5, "XIRR 年化", f"{r*100:.2f}%" if r is not None else "N/A",
                   tone=("auto:1" if (r or 0) > 0 else "auto:-1"))
    else:
        theme.stat(c5, "XIRR 年化", "欠價", tone="neutral")

    # 集中度甜甜圈(§6.1:15% 單一標的上限,超標扇區紅色)
    wser = pd.Series({s: v["mv_hkd"] for s, v in upl.items() if v}).sort_values(ascending=False)
    st.subheader("持倉市值佔比(紅色扇區 = 超 15% 單一標的上限)")
    breach = (wser / mv)[wser / mv > 0.15]
    if len(breach):
        st.error("集中度超標:" + ", ".join(
            f"{config.short_name(s)} {v:.1%}" for s, v in breach.items()))
    colL, colR = st.columns([3, 2])
    fig = theme.donut([(config.short_name(s), float(v)) for s, v in wser.items()],
                      breach=0.15)
    if fig is not None:
        colL.plotly_chart(fig, use_container_width=True)
    # 權重列表(旁邊)
    colR.dataframe(pd.DataFrame(
        [{"標的": config.short_name(s), "代號": s, "佔比": f"{v/mv:.1%}"}
         for s, v in wser.items()]),
        use_container_width=True, hide_index=True, height=340)

# ---- 收益(§6.2:三組成分開 + 分母口徑註明 + 股息 + yield-on-cost) ----
with tab_inc:
    basis = st.radio("收益 % 分母口徑", ["open_cost", "total_in"],
                     format_func=lambda b: "現時持倉成本" if b == "open_cost"
                     else "歷史總投入(累計買入)", horizontal=True)
    inc = income.income_summary(session, prices, basis=basis)
    st.caption(f"分母:{inc['denom_label']} = HKD {inc['denom_hkd']:,.0f} · {config.fx_note()}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("持倉收益(價差)", f"{inc['holding_unreal_hkd']:+,.0f}",
              f"{inc['holding_unreal_pct']*100:.1f}%" if inc["holding_unreal_pct"] else None)
    c2.metric("已實現", f"{inc['realized_hkd']:+,.0f}",
              f"{inc['realized_pct']*100:.1f}%" if inc["realized_pct"] else None)
    c3.metric("股息", f"{inc['dividends_hkd']:+,.0f}",
              f"{inc['dividends_pct']*100:.1f}%" if inc["dividends_pct"] else None)
    try:
        r = portfolio_xirr(session, date.today(), prices)
        c4.metric("XIRR 年化(StockerX 冇)", f"{r*100:.2f}%" if r is not None else "N/A")
    except ValueError:
        c4.metric("XIRR 年化", "欠價")
    st.caption(f"含息總回報(參考)= {inc['total_return_hkd']:+,.0f} — "
               "三組成永遠分開,唔會擠埋做一個誤導綠色數字。")

    st.subheader("每持倉三口徑回報(§5)")
    prows = []
    for sym, r in income.position_returns(session, prices).items():
        prows.append({
            "簡稱": config.short_name(sym), "代號": sym,
            "股數": r["shares"], "現價": prices.get(sym),
            "成本HKD": round(r["cost_hkd"]),
            "①價差未實現": round(r["price_only_unreal_hkd"]) if r["price_only_unreal_hkd"] is not None else None,
            "②含息未實現": round(r["with_div_unreal_hkd"]) if r["with_div_unreal_hkd"] is not None else None,
            "持有期股息": round(r["held_div_hkd"]),
            "③yield-on-cost": f"{r['yield_on_cost']*100:.1f}%" if r["yield_on_cost"] else "—",
        })
    _pdf = pd.DataFrame(prows).sort_values("②含息未實現",
                                           ascending=False, na_position="last")
    st.dataframe(theme.color_pnl(_pdf, ["①價差未實現", "②含息未實現", "持有期股息"]),
                 use_container_width=True, hide_index=True)
    st.caption("收息倉排序用「②含息未實現」— 業主鐵律:評估收息股一律含息總回報。")

    st.subheader("月度股息(HKD)")
    md = income.monthly_dividends(session)
    if md:
        mdf = pd.DataFrame([{"月份": f"{y}-{m:02d}", "股息HKD": round(v)}
                            for (y, m), v in sorted(md.items())]).set_index("月份")
        st.bar_chart(mdf)

    st.subheader("收益 vs 基準(total return 口徑,§5)")
    picks = st.multiselect("基準", list(benchmark.BENCHMARKS),
                           default=["VOO", "^HSI"],
                           format_func=lambda t: f"{t} {benchmark.BENCHMARKS[t]['name']}")
    if st.button("攞基準(yfinance,近一年)") and picks:
        prov = benchmark.YFinanceBenchmarkProvider()
        cmp = benchmark.compare(prov, picks, date(date.today().year - 1,
                                date.today().month, date.today().day), date.today(),
                                portfolio_twrr=twrr(session))
        st.dataframe(pd.DataFrame([{"基準": v["name"],
                     "區間總回報": f"{v['total_return']*100:.1f}%" if v["total_return"] is not None else "—"}
                     for v in cmp.values()]), use_container_width=True, hide_index=True)

# ---- 持倉(§6.3:雙軌口徑分兩欄) ----
with tab2:
    rows = []
    pos = metrics.open_positions(session)
    tr = metrics.total_return_with_div(session, prices)
    for sym, p in pos.items():
        u = upl.get(sym)
        rows.append({
            "簡稱": config.short_name(sym), "代號": sym, "股數": p["shares"],
            "平均成本": round(p["avg_cost"], 3), "現價": prices.get(sym),
            "市值HKD": round(u["mv_hkd"]) if u else None,
            "現時持倉收益HKD": round(u["unreal_hkd"]) if u else None,
            "lifetime已實現HKD": round(tr[sym]["realized_hkd"]),
            "累計股息HKD": round(tr[sym]["dividends_hkd"]),
        })
    df = (pd.DataFrame(rows).sort_values("現時持倉收益HKD",
                                          ascending=True, na_position="last"))
    st.dataframe(theme.color_pnl(df, ["現時持倉收益HKD", "lifetime已實現HKD",
                                      "累計股息HKD"]),
                 use_container_width=True, hide_index=True)
    st.caption("平均成本只計現有 lot(賣清歸零)· StockerX 會將三欄加埋做一個誤導數字,本系統永遠分開。")

# ---- 已平倉(§6.4) ----
with tab3:
    rts = metrics.round_trips(session)
    df = pd.DataFrame([{"簡稱": config.short_name(r["symbol"]), "代號": r["symbol"],
                        "買入日": r["buy_dt"].date(), "買入均價": round(r["avg_buy_price"], 3),
                        "沽出日": r["sell_dt"].date(), "沽出價": round(r["sell_price"], 3),
                        "股數": r["qty"], "持有日數": r["hold_days"],
                        "已實現HKD": round(r["pnl_hkd"])} for r in rts])
    st.dataframe(theme.color_pnl(df.sort_values("沽出日", ascending=False),
                                 ["已實現HKD"]),
                 use_container_width=True, hide_index=True)
    st.caption(f"共 {rs['rounds']} 回合 · 勝率 {rs['win_rate']:.1%} · "
               f"賺賠比 {rs['pl_ratio']:.2f} · 期望值 {rs['expectancy_hkd']:,.0f}/回合")

# ---- 新增交易(§6.5:提交前跑規則引擎,violations 彈警示卡,可 override) ----
RULE_DESC = {
    "MAX_POSITION_WEIGHT": "單一標的市值佔比上限",
    "MAX_SECTOR_WEIGHT": "板塊合計市值佔比上限",
    "MAX_SINGLE_ENTRY": "單筆買入佔總資產上限",
    "AVG_DOWN_LIMIT": "溝貨紀律(次數/跌幅/注碼)",
    "CHASE_HIGH": "買價貼近 20 日高位",
    "STALE_LOSER": "蝕住又揸太耐,強制檢討",
    "STOP_LOSS_ALERT": "浮虧穿止蝕線提示",
    "WEEKLY_CIRCUIT_BREAKER": "一週虧損超標,建議停手",
    "FEE_CHECK": "預期毛利不足來回手續費倍數",
    "REBUY_HIGHER": "沽出後短期高追返",
}
with tab4:
    with st.expander("十條行為規則(實際要求)", expanded=False):
        rrows = []
        for r in session.query(Rule).order_by(Rule.code).all():
            rrows.append({"規則": r.code, "類別": RULE_DESC.get(r.code, ""),
                          "實際要求": rules.rule_requirement(r.code, r.params),
                          "啟用": "✓" if r.enabled else "✗"})
        st.dataframe(pd.DataFrame(rrows), use_container_width=True, hide_index=True)
        st.caption("錄入交易時會自動全部過一次;違規彈警示卡但可 override(記錄在案)。")

    st.subheader("新增交易(提交前自動過十條行為規則)")
    with st.form("new_txn"):
        c1, c2, c3 = st.columns(3)
        f_symbol = c1.text_input("代號(e.g. TSLA / 0941.HK)").strip().upper()
        f_side = c2.selectbox("方向", ["BUY", "SELL"])
        f_date = c3.date_input("交易日", value=date.today())
        c4, c5, c6 = st.columns(3)
        f_price = c4.number_input("價格(原幣)", min_value=0.0, format="%.4f")
        f_qty = c5.number_input("股數", min_value=0.0, format="%.4f")
        f_fee = c6.number_input("手續費(原幣)", min_value=0.0, format="%.2f")
        checked = st.form_submit_button("檢查規則")
    if checked and f_symbol and f_qty > 0:
        total = assets.total_assets_hkd(session, prices)
        vs = rules.check_trade(session, prices, symbol=f_symbol, side=f_side,
                               price=f_price, qty=f_qty, fee=f_fee,
                               trade_dt=datetime.combine(f_date, datetime.min.time()),
                               total_assets=total)
        st.session_state["pending_txn"] = dict(
            symbol=f_symbol, side=f_side, price=f_price, qty=f_qty,
            fee=f_fee, dt=f_date, violations=vs)
        if vs:
            for v in vs:
                st.warning(f"**{v['rule']}** — {v['message']}")
        else:
            st.success("十條規則全部過關")
    p = st.session_state.get("pending_txn")
    if p:
        label = ("照錄唔改(override,違規記錄在案)" if p["violations"]
                 else "確認錄入")
        if st.button(label):
            inst = (session.query(Instrument)
                    .filter_by(symbol=p["symbol"]).first())
            if not inst:
                mkt = "HK" if p["symbol"].endswith(".HK") else "US"
                inst = Instrument(symbol=p["symbol"], market=mkt,
                                  ccy="HKD" if mkt == "HK" else "USD")
                session.add(inst)
                session.flush()
            txn = Transaction(account_id=1, instrument_id=inst.id,
                              trade_dt=datetime.combine(p["dt"], datetime.min.time()),
                              type=p["side"], price=p["price"], qty=p["qty"],
                              fee=p["fee"], ccy=inst.ccy, source="manual")
            session.add(txn)
            session.flush()
            rules.record_violations(session, p["violations"], txn_id=txn.id)
            session.commit()
            rebuild_lots(session)
            del st.session_state["pending_txn"]
            st.success(f"已錄入並重算 FIFO:{p['side']} {p['symbol']} "
                       f"{p['qty']:g} @ {p['price']}")
            st.rerun()

    st.subheader("記一筆股息(收到現金派息就喺度記)")
    with st.form("new_div"):
        d1, d2, d3, d4 = st.columns(4)
        dv_symbol = d1.text_input("代號(e.g. 0941.HK)").strip().upper()
        dv_date = d2.date_input("派息日", value=date.today(), key="dv_date")
        dv_amt = d3.number_input("股息總額(原幣現金)", min_value=0.0, format="%.2f")
        dv_ccy = d4.selectbox("幣種", ["HKD", "USD"], key="dv_ccy")
        if st.form_submit_button("記低股息") and dv_symbol and dv_amt > 0:
            inst = session.query(Instrument).filter_by(symbol=dv_symbol).first()
            if not inst:
                mkt = "HK" if dv_symbol.endswith(".HK") else "US"
                inst = Instrument(symbol=dv_symbol, market=mkt, ccy=dv_ccy)
                session.add(inst)
                session.flush()
            # DIV_CASH:price 欄 = 股息總金額(同 CSV importer 一致);qty 留空
            session.add(Transaction(
                account_id=1, instrument_id=inst.id,
                trade_dt=datetime.combine(dv_date, datetime.min.time()),
                type="DIV_CASH", price=dv_amt, qty=None, fee=0,
                ccy=dv_ccy, source="manual"))
            session.commit()
            st.success(f"已記:{dv_symbol} 股息 {dv_ccy} {dv_amt:,.2f}("
                       f"{dv_date})— 收益/股息頁即時反映")
            st.rerun()
    st.caption("股息唔影響 FIFO 持倉,直接入賬;收益頁「股息」同含息總回報會即時更新。")

# ---- 行為儀表板(§6.6:業主獨有殺手功能) ----
with tab5:
    w = behavior.dual_track_win_rate(session, prices)
    outs, cost = behavior.violation_outcomes(session, prices)
    disp = behavior.disposition_stats(session, prices)

    st.subheader("雙軌勝率 — 唔好再被 91.7% 呃自己")
    c1, c2, c3 = st.columns(3)
    c1.metric("已實現勝率(倖存者偏差)", f"{w['realized_win_rate']:.1%}")
    c2.metric("真實勝率(含 mark-to-market)",
              f"{w['true_win_rate']:.1%}" if w["true_win_rate"] else "N/A",
              delta=f"{(w['true_win_rate']-w['realized_win_rate'])*100:.1f} pp",
              delta_color="inverse")
    c3.metric("歷史違規成本 HKD", f"{cost:,.0f}")
    st.caption(f"賺緊 {len(w['open_winners'])} 隻 / 蝕緊 {len(w['open_losers'])} 隻"
               f"(open positions 計入分母先係真相)")

    st.subheader("處置效應監測(贏快沽、蝕死揸)")
    c1, c2 = st.columns(2)
    if disp["avg_hold_win_days"] is not None:
        c1.metric("贏回合平均持倉", f"{disp['avg_hold_win_days']:.0f} 日")
        c2.metric("蝕回合平均持倉", f"{disp['avg_hold_loss_days']:.0f} 日")
    if disp["open_loser_lots"]:
        st.caption("蝕緊嘅持倉 —— 預設按標的合計,展開睇每批幾時買、幾錢買:")
        # 按標的 group,click expander 先 break 開逐批
        by_sym = {}
        for lot in disp["open_loser_lots"]:
            by_sym.setdefault(lot["symbol"], []).append(lot)
        # group 層排序:總浮虧最深行先
        groups = sorted(by_sym.items(),
                        key=lambda kv: sum(l["unreal_hkd"] for l in kv[1]))
        for sym, lots in groups:
            tot = sum(l["unreal_hkd"] for l in lots)
            oldest = max(l["days"] for l in lots)
            head = (f"{config.short_name(sym)}（{sym}）· {len(lots)} 批 · "
                    f"浮虧 HKD {tot:,.0f} · 最耐揸 {oldest} 日")
            with st.expander(head, expanded=False):
                _ldf = pd.DataFrame([{
                    "買入日": l["buy_date"], "買入價": round(l["buy_price"], 3),
                    "股數": l["shares"], "揸咗(日)": l["days"],
                    "浮虧HKD": l["unreal_hkd"]} for l in
                    sorted(lots, key=lambda x: -x["days"])])
                st.dataframe(theme.color_pnl(_ldf, ["浮虧HKD"]),
                             use_container_width=True, hide_index=True)

    st.subheader("現時違規(狀態掃描)")
    for v in rules.scan_portfolio(session, prices):
        st.error(f"**{v['rule']}** — {v['message']}")

    st.subheader("歷史違規回顧(含每單最終結果)")
    if outs:
        _odf = pd.DataFrame([{"規則": o["rule"], "標的": o["symbol"],
                              "詳情": o["message"],
                              "最終結果HKD": o["outcome_hkd"]} for o in outs])
        st.dataframe(theme.color_pnl(_odf, ["最終結果HKD"]),
                     use_container_width=True, hide_index=True)
        st.caption("違規成本 = 所有負結果合計;「違咗規但好彩賺咗」唔會攞嚟溝淡。")

# ---- 全資產(§6.6:Percento 式四區塊) ----
with tab6:
    nw = assets.net_worth(session, prices)
    st.subheader("淨資產總覽")
    c1, c2, c3 = st.columns(3)
    c1.metric("淨資產 HKD", f"{nw['net_worth_hkd']:,.0f}")
    c2.metric("總資產 HKD", f"{nw['total_assets_hkd']:,.0f}")
    c3.metric("股票市值(自動)", f"{nw['equity_mv_hkd']:,.0f}")
    blocks = {k: v for k, v in nw["blocks"].items() if v}
    if blocks:
        st.bar_chart(pd.Series(blocks))

    # Treemap:持倉市值矩形樹狀圖(§6.6,Percento 借鏡)
    st.subheader("持倉 Treemap(市值權重)")
    upl_a = metrics.open_position_pnl(session, prices)
    tm = [{"標的": s, "市值HKD": v["mv_hkd"],
           "板塊": (session.query(Instrument).filter_by(symbol=s).first().sector
                   or "未分類")}
          for s, v in upl_a.items() if v and v["mv_hkd"] > 0]
    if tm:
        try:
            import plotly.express as px_
            fig = px_.treemap(pd.DataFrame(tm), path=["板塊", "標的"],
                              values="市值HKD",
                              title="市值集中度(面積 = 權重大)",
                              color_discrete_sequence=[theme.ACCENT, theme.POS,
                                                       theme.AMBER, "#7a7fad"])
            st.plotly_chart(theme.plotly_dark(fig), use_container_width=True)
        except ImportError:
            st.dataframe(pd.DataFrame(tm).sort_values("市值HKD", ascending=False),
                         use_container_width=True, hide_index=True)

    rows = assets.latest_assets(session)
    if rows:
        st.dataframe(pd.DataFrame(rows)
                     .rename(columns={"block": "區塊", "category": "類別",
                                      "name": "名稱", "ccy": "幣種",
                                      "value": "原幣值", "value_hkd": "HKD",
                                      "as_of": "更新日"}),
                     use_container_width=True, hide_index=True)
    st.subheader("手動記一項(月度輸入)")
    with st.form("asset_form"):
        c1, c2, c3 = st.columns(3)
        a_cat = c1.selectbox("類別", assets.CATEGORIES)
        a_name = c2.text_input("名稱(e.g. 滙豐往來 / 永明MPF)")
        a_ccy = c3.selectbox("幣種", ["HKD", "USD"])
        c4, c5 = st.columns(2)
        a_val = c4.number_input("價值(原幣)", min_value=0.0, format="%.2f")
        a_date = c5.date_input("估值日", value=date.today())
        if st.form_submit_button("記低") and a_name:
            assets.upsert_asset(session, a_cat, a_name, a_ccy, a_val, a_date)
            st.rerun()

# ---- 報表(§6.6:月度熱力圖 + 回撤曲線) ----
with tab7:
    st.subheader("月度已實現 + 股息(HKD)")
    pivot = reports.monthly_pnl_pivot(session)
    if not pivot.empty:
        try:                                    # 熱力圖上色要 matplotlib
            styled = (pivot.style.background_gradient(cmap="RdYlGn", axis=None)
                      .format("{:,.0f}", na_rep="—"))
            st.dataframe(styled, use_container_width=True)
        except ImportError:
            st.dataframe(pivot, use_container_width=True)
        st.caption("口徑:已實現回合 + 股息現金(唔含未實現)— 同已平倉頁/收益頁完全對數。")
    st.subheader("NAV 回撤曲線(snapshots)")
    series = reports.nav_series(session)
    if len(series) >= 2:
        curve, max_dd = reports.drawdown_curve(series)
        df = pd.DataFrame(curve, columns=["date", "nav", "dd"]).set_index("date")
        st.line_chart(df["dd"])
        st.caption(f"最大回撤:{max_dd:.1%}")
    else:
        st.info("要儲低 ≥2 日 snapshot 先畫到回撤曲線 — 側欄撳「寫入今日 snapshot」。")
    with st.sidebar:
        if st.button("寫入今日 snapshot"):
            try:
                nav, _ = build_snapshot(session, date.today(), prices)
                st.success(f"NAV HKD {nav:,.0f} 已寫入")
            except ValueError as e:
                st.error(str(e))

# ---- 投資委員會(多角度辯論:牛/熊/魔鬼代言人/價值/PM 裁決,多輪 + 續寫) ----
def _cm_render_turn(t):
    """一輪對話:user 灰底,ai 把【角色】做小標題。"""
    if t["kind"] == "user":
        st.markdown(f'<div style="background:{theme.SURFACE_2};border-radius:8px;'
                    f'padding:8px 12px;color:{theme.INK_SUBTLE};font-size:.85rem;'
                    f'margin:6px 0;">🙋 {t["text"]}</div>', unsafe_allow_html=True)
    else:
        body = t["text"].replace("【", f'<b style="color:{theme.AMBER}">【').replace(
            "】", "】</b>")
        st.markdown(f'<div style="background:{theme.SURFACE_1};border:1px solid '
                    f'{theme.HAIRLINE};border-radius:8px;padding:12px 14px;'
                    f'margin:6px 0;white-space:pre-wrap;font-size:.9rem;">{body}</div>',
                    unsafe_allow_html=True)


with tab_ai:
    st.subheader("投資委員會")
    st.caption("五個角色(牛方 / 熊方 / 魔鬼代言人 / 價值視角 / PM 裁決)辯論你嘅真實組合。"
               "可多輪追問,委員會記住上文。只用嚟逼自己諗多幾個角度,唔構成投資建議。")

    with st.expander("① 設定後端(Anthropic 官方 或 火山引擎方舟)",
                     expanded=not st.session_state.get("cm_key")):
        provider = st.radio("供應商", ["火山引擎方舟(ark)", "Anthropic 官方"],
                            horizontal=True, key="cm_provider")
        if provider.startswith("火山"):
            default_url, default_model = "https://ark.cn-beijing.volces.com/api/plan", "ark-code-latest"
            st.caption("喺方舟「開通模型」攞 API Key;Base URL 用官方『兼容 Anthropic 接口協議』嗰條。")
        else:
            default_url, default_model = "", "claude-opus-4-8"
        cc1, cc2 = st.columns(2)
        st.session_state["cm_url"] = cc1.text_input("Base URL(官方留空)", value=default_url)
        st.session_state["cm_model"] = cc2.text_input("模型", value=default_model)
        st.session_state["cm_key"] = st.text_input(
            "API Key", type="password", value=st.session_state.get("cm_key", ""),
            help="只留喺呢個 session,唔會寫入檔案")

    st.session_state.setdefault("cm_history", [])
    st.session_state.setdefault("cm_thread", [])
    st.session_state.setdefault("cm_truncated", False)

    # 委員會陣容(名人投資框架)—— 只喺開新會(首輪)生效
    _pk = list(committee.PERSONAS)
    _fmt = lambda k: "(通用分析師)" if k == "" else committee.PERSONAS[k][0]
    with st.expander("② 委員會陣容(名人投資框架,首輪生效)", expanded=True):
        sc1, sc2 = st.columns(2)
        bull = sc1.selectbox("牛方由邊位扮演", [""] + _pk, format_func=_fmt,
                             key="cm_bull")
        bear = sc2.selectbox("熊方由邊位扮演", [""] + _pk, format_func=_fmt,
                             key="cm_bear")
        guest_keys = st.multiselect(
            "加開客席委員", options=_pk,
            format_func=lambda k: committee.PERSONAS[k][0],
            default=st.session_state.get("cm_personas", ["serenity"]),
            help="以該投資者公開嘅分析風格模擬角度,唔代表本人實際意見。")
        st.session_state["cm_personas"] = guest_keys
        seats = ([f"牛方={_fmt(bull)}"] if bull else []) + \
                ([f"熊方={_fmt(bear)}"] if bear else []) + \
                (["客席:" + "、".join(committee.PERSONAS[k][0] for k in guest_keys)]
                 if guest_keys else [])
        if seats:
            st.caption(" · ".join(seats) + " · " + committee.GUEST_DISCLAIMER)

    def _cm_call(question, display):
        cfg = dict(api_key=st.session_state.get("cm_key") or None,
                   base_url=st.session_state.get("cm_url") or None,
                   model=st.session_state.get("cm_model") or None,
                   personas=st.session_state.get("cm_personas") or None,
                   bull=st.session_state.get("cm_bull") or None,
                   bear=st.session_state.get("cm_bear") or None)
        if display is not None:
            st.session_state["cm_thread"].append({"kind": "user", "text": display})
        with st.spinner("委員會開緊會…"):
            res = committee.convene(session, prices, st.session_state["cm_history"],
                                    question, **cfg)
        if not res["ok"]:
            st.session_state["cm_thread"].append(
                {"kind": "ai", "text": f"(連線失敗:{res['error']})"})
        else:
            st.session_state["cm_history"] = res["history"]
            st.session_state["cm_thread"].append({"kind": "ai", "text": res["text"]})
            st.session_state["cm_truncated"] = res["truncated"]

    first_round = not st.session_state["cm_history"]
    # 預設問題 chips
    st.caption("快速議題:")
    chip_cols = st.columns(len(committee.CHIPS))
    for i, (label, qtext) in enumerate(committee.CHIPS):
        if chip_cols[i].button(label, key=f"cm_chip_{i}"):
            _cm_call(qtext, qtext)
            st.rerun()

    # 對話串
    for t in st.session_state["cm_thread"]:
        _cm_render_turn(t)

    q = st.text_area("問題" if first_round else "追問(委員會記得上文)",
                     key="cm_q",
                     placeholder="輸入問題;開完會之後呢度變追問框…")
    b1, b2, b3 = st.columns([2, 2, 6])
    if b1.button("召開委員會" if first_round else "追問", type="primary",
                 key="cm_ask"):
        if q.strip():
            _cm_call(q.strip(), q.strip())
            st.rerun()
    if st.session_state["cm_truncated"]:
        if b2.button("繼續生成", key="cm_more"):
            r = committee.continue_generation(
                session, prices, st.session_state["cm_history"],
                api_key=st.session_state.get("cm_key") or None,
                base_url=st.session_state.get("cm_url") or None,
                model=st.session_state.get("cm_model") or None)
            if r["ok"]:
                st.session_state["cm_history"] = r["history"]
                st.session_state["cm_thread"].append({"kind": "ai", "text": r["text"]})
                st.session_state["cm_truncated"] = r["truncated"]
            st.rerun()
    if b3.button("重開會議", key="cm_reset"):
        st.session_state["cm_history"] = []
        st.session_state["cm_thread"] = []
        st.session_state["cm_truncated"] = False
        st.rerun()

    with st.expander("預覽會餵入委員會嘅持倉快照(純事實)"):
        st.code(committee.snapshot_text(session, prices))
