"""投資委員會 —— 多角度模擬辯論。搬自業主 Fable 5 版 investment_committee.html。

角色:牛方分析師 / 熊方分析師 / 魔鬼代言人 / 價值視角 / 投資組合經理裁決。
行為(同原版一致):
- 首輪:五個角色,每個最多兩句,引用真實數字,結構化【】標題,結尾免責
- 追問:自由格式、精簡;委員會記住成段上文(多輪對話)
- max_tokens 預設 1000;截斷(stop_reason=max_tokens)可「繼續生成」
- 重開會議:清空上文,下一條問題用最新持倉快照重新開始
- 收息倉檢討:評收息股一律用「含息總回報」(未實現 + 累計股息)

後端經 advisor.build_client,支援 Anthropic 官方同火山引擎方舟(ark)。
"""
from .metrics import open_positions, open_position_pnl, dividends_by_symbol
from .income import dividends_detail
from .config import to_hkd, short_name
from . import advisor

# 業主已診斷嘅行為弱點 + 策略框架(原版 BEHAVIOR 常量)
BEHAVIOR = (
    "用戶已知行為弱點:①處置效應(贏就快沽、蝕就死揸,例:XYZ 揸 4.5 年蝕 >50% 未沽);"
    "②TSLA 集中度 >50%(信仰驅動);③溝貨無規則;④已實現勝率 91.7% 有倖存者偏差,"
    "mark-to-market 真實勝率約 65%。策略框架:核心(VOO+高息收息)50-60% / "
    "衛星(AI 個股,單一 ≤15%)/ 投機 ≤5%;衛星止蝕 -15%;溝貨最多 2 次、"
    "每次 ≤原注 50% 且要跌 ≥15%。股息係佢組合重要一環:評估收息倉時要用含息總回報。")

# 客席委員 —— 以知名投資者「公開嘅分析框架 / 投資風格」模擬嘅角度。
# 鐵律:只係風格化嘅分析鏡頭,唔係扮真人、唔代表本人實際意見或背書(見 GUEST_DISCLAIMER)。
# 靈感參考 olaxbt/ai-market-maker 嘅「乾淨 persona」概念,但呢度係投資者視角而非交易 desk。
PERSONAS = {
    "serenity": (
        "Serenity(AI 供應鏈瓶頸視角)",
        "專睇 AI / 半導體供應鏈邊個環節係物理極限、卡住條鏈嘅咽喉(先進封裝、HBM、"
        "光通訊、互連、電力散熱)= 定價權所在。核心問題:你嘅 AI 持倉係咪真係喺瓶頸"
        "環節、有結構性稀缺,定係下游易被取代?留意產能週期同庫存拐點。"),
    "buffett": (
        "巴菲特(價值 / 護城河)",
        "只買能力圈內、有寬闊護城河、可預測現金流嘅生意;睇內在價值同安全邊際,"
        "嫌集中度風險但只集中喺睇得明嘅嘢;長揸,唔理短期市場情緒。"),
    "munger": (
        "芒格(逆向 / 多元思維)",
        "先諗『點樣輸』再逆轉(invert);避免愚蠢多過追求聰明;用多元思維模型同"
        "機會成本審視;最憎為咗興奮而交易同過度自信。"),
    "burry": (
        "Michael Burry(逆向 / 泡沫警覺)",
        "對估值極端同市場狂熱高度警覺;專搵下行風險、槓桿同流動性陷阱;會直接質疑"
        "共識同集中度,提醒『幾時泡沫爆』嘅尾部風險。"),
    "lynch": (
        "Peter Lynch(增長合理價 / 識你所揸)",
        "買你真正了解嘅生意;睇 PEG(增長對估值)同基本面拐點;分辨『十倍股』同"
        "『價值陷阱』;唔明就唔沾手。"),
    "dalio": (
        "Dalio(宏觀 / 風險平價)",
        "由經濟週期同宏觀環境睇資產配置;強調分散同風險平價,唔好單一注押身家;"
        "問:呢個組合喺唔同宏觀情境下點表現?"),
}

GUEST_DISCLAIMER = ("客席委員只係以該投資者『公開嘅分析框架 / 風格』模擬嘅角度,"
                    "純為多角度思考,唔係本人實際意見、唔代表佢哋會咁睇、亦非任何背書。")


# 預設問題(chips)
CHIPS = [
    ("TSLA 減唔減倉?",
     "TSLA 而家佔組合超過一半,我長期信仰但知道集中度風險。委員會點睇?應該點樣有紀律咁處理?"),
    ("LITE 點處理?",
     "LITE 我 80 股平均成本 891,而家蝕緊約 10%。基於 AI 光通訊嘅前景,應該止蝕、持有定溝貨?"),
    ("XYZ 死揸值唔值?",
     "XYZ (Block) 揸咗 4 年半,蝕緊超過一半。用機會成本角度分析,係咪應該認錯離場?"),
    ("收息倉檢討",
     "我啲高息持倉(中移動、中海油、2802、3416)累計股息相當可觀。評估吓我嘅收息倉策略,"
     "同埋股息喺我成個組合回報入面嘅角色。"),
    ("3 個月行動清單",
     "根據我嘅持倉同行為弱點,委員會俾我未來 3 個月最重要嘅三個行動建議。"),
]


def snapshot_text(session, prices: dict) -> str:
    """組合快照(逐持倉:股數/成本/現價/未實現/累計股息)—— 餵 prompt 用,純事實。"""
    pos = open_positions(session)
    upl = open_position_pnl(session, prices)
    held = dividends_detail(session)                 # 持有期內累計股息
    total_mv = sum(v["mv_hkd"] for v in upl.values() if v)
    total_div = 0.0
    lines = []
    for sym, p in pos.items():
        v = upl.get(sym)
        d = held.get(sym, {}).get("held_hkd", 0.0)
        total_div += d
        unreal = round(v["unreal_hkd"]) if v else "欠價"
        px = prices.get(sym, "欠價")
        lines.append(
            f"{sym}({short_name(sym)}): {p['shares']:g}股 成本{p['avg_cost']:.2f} "
            f"現價{px} 未實現HKD {unreal} 累計股息HKD {round(d)}")
    header = (f"總市值HKD {round(total_mv)};現有持倉累計股息HKD {round(total_div)}")
    return header + "\n" + "\n".join(lines)


def _role_block(personas=None):
    """砌首輪回答嘅角色結構。核心四角色 + 客席名人視角 +(最後)PM 裁決。"""
    lines = [
        "【牛方分析師】",
        "【熊方分析師】",
        "【魔鬼代言人】",
        "【價值視角】(能力圈/安全邊際/機會成本/股息現金流)",
    ]
    guest_notes = []
    for key in (personas or []):
        if key in PERSONAS:
            name, lens = PERSONAS[key]
            lines.append(f"【{name}】")
            guest_notes.append(f"- {name}:{lens}")
    lines.append("【投資組合經理裁決】(具體可執行)")
    block = "\n".join(lines)
    if guest_notes:
        block += ("\n\n客席委員請嚴格以下面框架嘅角度發言(唔好背離佢嘅風格):\n"
                  + "\n".join(guest_notes) + f"\n\n{GUEST_DISCLAIMER}")
    return block


def preamble(session, prices: dict, question: str, personas=None) -> str:
    """首輪 user message:快照 + 行為背景 + 角色結構要求 + 問題。

    personas:客席名人視角 key 列表(見 PERSONAS),None = 淨係核心五角色。
    """
    return (
        "你係一個投資委員會,為香港散戶 Jason 分析佢嘅真實組合。"
        "持倉快照(含累計股息,評估收息股請用「未實現 + 股息」嘅含息總回報):\n"
        f"{snapshot_text(session, prices)}\n\n{BEHAVIOR}\n\n"
        f"問題:{question}\n\n"
        "用繁體中文(香港書面語)。首次回答用以下結構,每個角色最多兩句、"
        "直接講重點、引用真實數字:\n"
        f"{_role_block(personas)}\n"
        "之後嘅追問可以自由格式,精簡回答。"
        "每次結尾一句:以上係多角度推理,唔係投資建議。")


def convene(session, prices, history, question, *, personas=None, api_key=None,
            base_url=None, model=None, max_tokens=1000):
    """開會 / 追問一輪。

    history:[{role, content}] 之前嘅對話(唔含今次問題);function 唔會就地改佢。
    personas:客席名人視角 key 列表(只喺首輪 preamble 生效)。
    回傳 {ok, text, truncated, history(更新後), error}。
      - history 空 → 今次 user message 用 preamble(帶快照);否則用純問題
      - truncated = True 代表撞到 max_tokens,UI 可顯示「繼續生成」
    """
    api_key, base_url, model = advisor.resolve_config(api_key, base_url, model)
    client, err = advisor.build_client(api_key, base_url)
    if err:
        return {"ok": False, "text": "", "truncated": False,
                "history": history, "error": err}

    user_content = (preamble(session, prices, question, personas) if not history
                    else question)
    messages = history + [{"role": "user", "content": user_content}]
    try:
        resp = client.messages.create(model=model, max_tokens=max_tokens,
                                       messages=messages)
    except Exception as e:
        return {"ok": False, "text": "", "truncated": False,
                "history": history, "error": f"{type(e).__name__}: {e}"}
    text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    new_history = messages + [{"role": "assistant", "content": text}]
    return {"ok": True, "text": text, "truncated": resp.stop_reason == "max_tokens",
            "history": new_history, "error": None, "model": model,
            "endpoint": base_url or "anthropic"}


def continue_generation(session, prices, history, **kw):
    """撞到長度上限後接住寫(唔重複)。history 尾必為 assistant。"""
    return convene(session, prices, history,
                   "繼續(接住上一段寫落去,唔好重複)", **kw)
