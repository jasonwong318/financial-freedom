"""視覺主題注入 —— Linear DESIGN.md × Bloomberg terminal。

設計來源:上傳嘅 Linear DESIGN.md(near-black canvas #010102、四層 surface ladder、
hairline 邊、lavender #5e6ad2 唯一 accent、Inter/tabular numbers、無漸變/無玻璃/無霓虹)。
疊上 Bloomberg terminal 嘅資料密度:緊湊行高、表格 hairline、tabular 數字、sticky header
(st.dataframe 原生)。

呢個模組只做外觀:app.py 開頭 call inject() 一次即可,唔改任何功能、文案或數據。
損益用「克制、非霓虹」嘅語意色(綠 #3fa863 / 紅 #d0504f / Bloomberg 琥珀 #c8933a),
因為升跌係資訊而唔係裝飾 —— 但飽和度壓低,維持機構級冷靜感。
"""

# ---- Design tokens(直接對應 DESIGN.md) ----
CANVAS = "#010102"
SURFACE_1 = "#0f1011"
SURFACE_2 = "#141516"
SURFACE_3 = "#18191a"
HAIRLINE = "#23252a"
HAIRLINE_STRONG = "#34343a"
INK = "#f7f8f8"
INK_MUTED = "#d0d6e0"
INK_SUBTLE = "#8a8f98"
INK_TERTIARY = "#62666d"
ACCENT = "#5e6ad2"
ACCENT_HOVER = "#828fff"
POS = "#3fa863"          # 升 / 賺 —— 壓低飽和度嘅綠
NEG = "#d0504f"          # 跌 / 蝕 —— 壓低飽和度嘅紅
AMBER = "#c8933a"        # 警示 —— Bloomberg 琥珀

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

:root {{
  --canvas:{CANVAS}; --s1:{SURFACE_1}; --s2:{SURFACE_2}; --s3:{SURFACE_3};
  --hair:{HAIRLINE}; --hair2:{HAIRLINE_STRONG};
  --ink:{INK}; --ink-muted:{INK_MUTED}; --ink-subtle:{INK_SUBTLE}; --ink-3:{INK_TERTIARY};
  --accent:{ACCENT}; --accent-hover:{ACCENT_HOVER};
  --pos:{POS}; --neg:{NEG}; --amber:{AMBER};
}}

/* ---------- 基底 ---------- */
html, body, [class*="css"], .stApp, [data-testid="stAppViewContainer"] {{
  font-family: 'Inter', -apple-system, system-ui, 'Segoe UI', Roboto, sans-serif;
  font-feature-settings: 'tnum' 1, 'cv01' 1, 'cv03' 1;
  font-variant-numeric: tabular-nums;
  -webkit-font-smoothing: antialiased;
}}
.stApp {{ background: var(--canvas); color: var(--ink); }}

/* 緊湊資料密度:收窄整體留白,拉闊內容寬度 */
.block-container {{ padding: 1.4rem 2.2rem 3rem !important; max-width: 1500px; }}
[data-testid="stHeader"] {{ background: transparent; height: 0; }}
[data-testid="stToolbar"] {{ right: 1rem; }}

/* ---------- 標題 typography(Linear:負字距、weight 600) ---------- */
h1, h2, h3 {{ font-family:'Inter'; color: var(--ink); letter-spacing:-0.02em; font-weight:600; }}
h1 {{ font-size: 1.55rem !important; letter-spacing:-0.04em; margin-bottom:.1rem; }}
h2 {{ font-size: 1.05rem !important; margin: .6rem 0 .3rem; }}
h3 {{ font-size: .92rem !important; color: var(--ink-muted); }}
[data-testid="stCaptionContainer"], .stCaption, small {{ color: var(--ink-subtle) !important; }}
/* 副標題(caption)做成 eyebrow 感 */
.stApp [data-testid="stCaptionContainer"] p {{
  font-size:.72rem; letter-spacing:.02em; color: var(--ink-subtle);
}}

/* ---------- Metric 卡(clean border + minimal shadow) ---------- */
[data-testid="stMetric"] {{
  background: var(--s1);
  border: 1px solid var(--hair);
  border-radius: 8px;
  padding: 12px 14px;
  box-shadow: 0 1px 0 rgba(255,255,255,.02) inset;
}}
[data-testid="stMetricLabel"] p {{
  font-size:.68rem !important; text-transform:uppercase; letter-spacing:.06em;
  color: var(--ink-subtle) !important; font-weight:500;
}}
[data-testid="stMetricValue"] {{
  font-size:1.5rem !important; font-weight:600; letter-spacing:-0.02em;
  font-variant-numeric: tabular-nums; color: var(--ink);
}}
[data-testid="stMetricDelta"] {{ font-variant-numeric: tabular-nums; font-size:.8rem; }}
[data-testid="stMetricDelta"] svg {{ display:none; }}   /* 去箭嘴,留純數字 */

/* ---------- Tabs(Bloomberg 分頁條:hairline 底線 + lavender 選中) ---------- */
.stTabs [data-baseweb="tab-list"] {{
  gap: 2px; border-bottom: 1px solid var(--hair); background: transparent;
}}
.stTabs [data-baseweb="tab"] {{
  height: 34px; padding: 0 14px; background: transparent;
  color: var(--ink-subtle); font-size:.82rem; font-weight:500; letter-spacing:-.01em;
  border-radius: 6px 6px 0 0;
}}
.stTabs [aria-selected="true"] {{ color: var(--ink) !important; background: var(--s1); }}
.stTabs [data-baseweb="tab-highlight"] {{ background: var(--accent) !important; height:2px; }}

/* ---------- 側欄 ---------- */
[data-testid="stSidebar"] {{ background: var(--s1); border-right: 1px solid var(--hair); }}
[data-testid="stSidebar"] .block-container {{ padding-top: 1.2rem; }}
[data-testid="stSidebar"] h2 {{
  font-size:.72rem !important; text-transform:uppercase; letter-spacing:.06em;
  color: var(--ink-subtle); font-weight:600; margin-top:1rem;
}}

/* ---------- 按鈕 ---------- */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
  background: var(--s2); color: var(--ink);
  border: 1px solid var(--hair); border-radius: 8px;
  font-size:.82rem; font-weight:500; padding: 6px 14px; transition: all .12s ease;
}}
.stButton > button:hover, .stFormSubmitButton > button:hover {{
  border-color: var(--hair2); background: var(--s3);
}}
/* primary(type="primary")→ lavender */
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primaryFormSubmit"] {{
  background: var(--accent); border-color: var(--accent); color:#fff;
}}
.stButton > button[kind="primary"]:hover {{ background: var(--accent-hover); border-color: var(--accent-hover); }}

/* ---------- 輸入 / 下拉 / 上載 ---------- */
[data-baseweb="input"], [data-baseweb="select"] > div, .stTextInput input,
.stNumberInput input, [data-testid="stDateInput"] input, [data-testid="stTextArea"] textarea {{
  background: var(--s1) !important; border:1px solid var(--hair) !important;
  border-radius: 8px !important; color: var(--ink) !important;
  font-variant-numeric: tabular-nums;
}}
[data-testid="stFileUploaderDropzone"] {{
  background: var(--s1); border:1px dashed var(--hair2); border-radius: 8px;
}}
input:focus, textarea:focus {{ border-color: var(--accent) !important; }}

/* Radio / multiselect pill —— Bloomberg 式緊湊 toggle */
[data-baseweb="tag"] {{ background: var(--s3) !important; border-radius: 4px !important; }}
.stRadio [role="radiogroup"] label {{ font-size:.8rem; }}

/* ---------- 表格(Bloomberg:hairline、tabular、sticky header 原生) ---------- */
[data-testid="stDataFrame"], [data-testid="stTable"] {{
  border: 1px solid var(--hair); border-radius: 8px; overflow: hidden;
  font-variant-numeric: tabular-nums;
}}
[data-testid="stDataFrame"] * {{ font-variant-numeric: tabular-nums; }}

/* ---------- Expander ---------- */
[data-testid="stExpander"] {{ border:1px solid var(--hair); border-radius:8px; background: var(--s1); }}
[data-testid="stExpander"] summary {{ font-size:.82rem; color: var(--ink-muted); }}

/* ---------- 提示卡(去霓虹,機構冷色 + 左邊語意色條) ---------- */
[data-testid="stAlert"], [data-baseweb="notification"] {{
  background: var(--s2) !important; border:1px solid var(--hair) !important;
  border-left: 3px solid var(--ink-subtle) !important; border-radius: 6px !important;
  color: var(--ink-muted) !important; box-shadow:none !important; padding:.55rem .8rem !important;
}}
[data-testid="stAlert"] p {{ font-size:.82rem !important; }}
/* 語意左邊條:success/info/warning/error */
[data-testid="stAlertContentSuccess"] {{ border-left-color: var(--pos) !important; }}
[data-testid="stAlertContentInfo"]    {{ border-left-color: var(--accent) !important; }}
[data-testid="stAlertContentWarning"] {{ border-left-color: var(--amber) !important; }}
[data-testid="stAlertContentError"]   {{ border-left-color: var(--neg) !important; }}

/* ---------- 分隔線 ---------- */
hr {{ border-color: var(--hair); }}

/* ---------- 捲軸(細身、暗色) ---------- */
::-webkit-scrollbar {{ width: 9px; height: 9px; }}
::-webkit-scrollbar-track {{ background: var(--canvas); }}
::-webkit-scrollbar-thumb {{ background: var(--hair2); border-radius: 6px; }}
::-webkit-scrollbar-thumb:hover {{ background: var(--ink-3); }}

/* ---------- 連結 ---------- */
a, a:visited {{ color: var(--accent); text-decoration: none; }}
a:hover {{ color: var(--accent-hover); }}
</style>
"""


def inject(st):
    """喺 app 開頭 call 一次:注入字型 + 全套暗色 CSS。純外觀,無副作用。"""
    st.markdown(_CSS, unsafe_allow_html=True)
    _register_altair_theme()


# ---------- 圖表:TradingView 風(暗底、hairline grid、細線) ----------

def _register_altair_theme():
    """註冊並啟用暗色 Altair 主題,令 st.line_chart / st.bar_chart 有 TradingView 感。"""
    try:
        import altair as alt
    except Exception:
        return

    def _theme():
        return {
            "config": {
                "background": CANVAS,
                "view": {"stroke": "transparent"},
                "font": "Inter",
                "axis": {
                    "domainColor": HAIRLINE, "gridColor": HAIRLINE,
                    "gridOpacity": 0.5, "tickColor": HAIRLINE,
                    "labelColor": INK_SUBTLE, "titleColor": INK_SUBTLE,
                    "labelFontSize": 11, "titleFontSize": 11,
                    "labelFont": "Inter", "titleFont": "Inter",
                },
                "legend": {"labelColor": INK_MUTED, "titleColor": INK_SUBTLE,
                           "labelFontSize": 11},
                "range": {"category": [ACCENT, POS, AMBER, "#7a7fad", NEG,
                                       INK_SUBTLE]},
                "bar": {"fill": ACCENT},
                "line": {"stroke": ACCENT, "strokeWidth": 1.5},
                "point": {"filled": True, "size": 14},
            }
        }
    try:
        # Altair 5.5+ 用 ThemeRegistry.register;舊版用 themes.register
        alt.themes.register("terminal", _theme)
        alt.themes.enable("terminal")
    except Exception:
        pass


def stat(container, label, value, sub=None, tone="neutral"):
    """彩色 stat 卡(HTML)—— tone: pos(綠)/neg(紅)/neutral(白)。

    st.metric 無法按數值正負上色,所以損益類數字改用呢個自繪卡片。
    tone 亦可傳 "auto:<number>":自動按正負決定顏色。
    """
    if isinstance(tone, str) and tone.startswith("auto:"):
        try:
            n = float(tone.split(":", 1)[1])
            tone = "pos" if n > 0 else "neg" if n < 0 else "neutral"
        except ValueError:
            tone = "neutral"
    color = {"pos": POS, "neg": NEG, "neutral": INK}.get(tone, INK)
    sub_html = (f'<div style="font-size:.72rem;color:{INK_SUBTLE};margin-top:2px">'
                f'{sub}</div>') if sub else ""
    # HTML 必須頂格單行:Streamlit markdown 會把縮排 HTML 當 code block,漏出 </div>
    html = (
        f'<div style="background:{SURFACE_1};border:1px solid {HAIRLINE};'
        f'border-radius:8px;padding:12px 14px;">'
        f'<div style="font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;'
        f'color:{INK_SUBTLE};font-weight:500;margin-bottom:4px;">{label}</div>'
        f'<div style="font-size:1.5rem;font-weight:600;letter-spacing:-.02em;'
        f'font-variant-numeric:tabular-nums;color:{color};">{value}</div>'
        f'{sub_html}</div>')
    container.markdown(html, unsafe_allow_html=True)


def donut(pairs, hole=0.55, breach=None):
    """甜甜圈圖(規格書 §6.1)。pairs = [(label, value), ...]。

    breach:> 呢個比例(如 0.15)嘅扇區用紅色標示(15% 集中度上限)。
    回傳 plotly fig(已套暗色);冇 plotly 就回 None。
    """
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None
    labels = [p[0] for p in pairs]
    values = [p[1] for p in pairs]
    total = sum(values) or 1
    palette = [ACCENT, "#7a7fad", POS, AMBER, "#5b8def", "#9b8cff",
               INK_SUBTLE, "#6b7280"]
    colors = []
    for i, v in enumerate(values):
        if breach is not None and v / total > breach:
            colors.append(NEG)                       # 超標扇區紅色
        else:
            colors.append(palette[i % len(palette)])
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=hole, sort=True, direction="clockwise",
        marker=dict(colors=colors, line=dict(color=CANVAS, width=1.5)),
        textinfo="label+percent", textfont=dict(size=11, color=INK),
        hovertemplate="%{label}: HKD %{value:,.0f} (%{percent})<extra></extra>"))
    fig.update_layout(
        paper_bgcolor=CANVAS, plot_bgcolor=CANVAS, showlegend=False,
        font=dict(color=INK, family="Inter"), margin=dict(t=10, l=10, r=10, b=10),
        height=340)
    return fig


def color_pnl(df, cols):
    """回傳 pandas Styler:指定損益欄正數綠、負數紅(克制色),右對齊 tabular。

    純外觀 —— st.dataframe 收到 Styler 之後,排序 / sticky header 全部照用。
    冇 matplotlib 都 work(唔靠 gradient)。
    """
    import pandas as _pd
    cols = [c for c in cols if c in df.columns]

    def _c(v):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return ""
        if x > 0:
            return f"color:{POS};"
        if x < 0:
            return f"color:{NEG};"
        return f"color:{INK_SUBTLE};"

    # 數字格式:P&L 欄用千分位整數;其他數值欄剪走多餘小數(唔好變 200.000000)
    def _trim(v):
        if v is None or (isinstance(v, float) and v != v):   # None / NaN
            return "—"
        f = float(v)
        return f"{f:,.0f}" if f == int(f) else f"{f:,.4f}".rstrip("0").rstrip(".")

    fmt = {}
    for c in df.columns:
        if not _pd.api.types.is_numeric_dtype(df[c]):
            continue
        fmt[c] = (lambda v: "—" if (v is None or (isinstance(v, float) and v != v))
                  else f"{float(v):,.0f}") if c in cols else _trim

    return (df.style
            .map(_c, subset=cols)
            .format(fmt, na_rep="—")
            .set_properties(subset=cols, **{"font-variant-numeric": "tabular-nums"}))


def plotly_dark(fig):
    """將 Plotly figure(如 Treemap)套暗色 template。回傳同一 fig。"""
    fig.update_layout(
        paper_bgcolor=CANVAS, plot_bgcolor=CANVAS,
        font=dict(color=INK, family="Inter", size=12),
        margin=dict(t=40, l=8, r=8, b=8),
    )
    try:
        fig.update_traces(marker=dict(line=dict(color=HAIRLINE, width=1)))
    except Exception:
        pass
    return fig
