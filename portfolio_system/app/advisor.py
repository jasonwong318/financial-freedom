"""AI 顧問 — 對應規格書 §7 POST /advisor/ask、§1「AI 顧問」。

將 lot-level 快照 + 行為背景餵入 Claude,回覆必須掛「唔構成投資建議」免責。
設計鐵律:
- 只餵事實快照(持倉/勝率/違規/集中度),唔餵預測
- 用 Anthropic Messages API;冇 API key 就回 offline 模式(唔爆)
- 系統 prompt 強制中立語氣 + 免責聲明
"""
import os
import json

from .metrics import (open_positions, open_position_pnl, realized_summary,
                      dividends_by_symbol)
from .behavior import dual_track_win_rate, disposition_stats
from .rules import scan_portfolio
from .config import fx_note

DISCLAIMER = "⚠️ 以上內容只屬個人資料分析,唔構成任何投資建議或要約。投資涉及風險。"

SYSTEM_PROMPT = """你係一個私人投資組合分析助手,服務對象係香港活躍交易者。
語氣:直接、中立、用繁體中文(香港)。
鐵律:
1. 你只根據用戶提供嘅組合快照事實回答,唔准預測股價、唔准叫人買賣邊隻。
2. 可以指出行為模式問題(集中度、處置效應、違規紀律),因為呢啲係用戶自己數據嘅客觀描述。
3. 每次回覆最後必須加上免責聲明。
4. 唔好吹捧,唔好講「一定」「保證」。見到風險就直接講。"""


def build_snapshot_context(session, prices: dict) -> dict:
    """組合事實快照 — 餵 prompt 用。純事實,冇預測。"""
    upl = open_position_pnl(session, prices)
    mv = {s: v["mv_hkd"] for s, v in upl.items() if v}
    total_mv = sum(mv.values())
    weights = {s: round(m / total_mv, 4) for s, m in mv.items()} if total_mv else {}
    w = dual_track_win_rate(session, prices)
    disp = disposition_stats(session, prices)
    rs = realized_summary(session)
    violations = [v["message"] for v in scan_portfolio(session, prices)]
    return {
        "fx_note": fx_note(),
        "total_market_value_hkd": round(total_mv),
        "position_weights": dict(sorted(weights.items(),
                                        key=lambda x: -x[1])),
        "realized_win_rate": round(w["realized_win_rate"], 4)
        if w["realized_win_rate"] else None,
        "true_win_rate_with_mtm": round(w["true_win_rate"], 4)
        if w["true_win_rate"] else None,
        "profit_loss_ratio": rs.get("pl_ratio"),
        "expectancy_hkd_per_round": round(rs["expectancy_hkd"])
        if rs.get("expectancy_hkd") else None,
        "avg_hold_days_winners": disp["avg_hold_win_days"],
        "avg_hold_days_losers": disp["avg_hold_loss_days"],
        "current_rule_violations": violations,
        "dividends_total_hkd": round(dividends_by_symbol(session).get("_total", 0)),
    }


def ask(session, prices: dict, question: str, *, model=None, api_key=None,
        base_url=None, max_tokens=1024) -> dict:
    """問 AI 顧問。回傳 {answer, offline, context}。

    支援兩種後端(都行 Anthropic Messages 協議):
      1. Anthropic 官方 —— 唔傳 base_url,model 預設 claude-opus-4-8
      2. 火山引擎方舟(ark)—— base_url = https://ark.cn-beijing.volces.com/api/plan,
         model = ark-code-latest,api_key = 你嘅火山 API Key

    參數優先於環境變數:
      api_key   ← ANTHROPIC_API_KEY / ARK_API_KEY
      base_url  ← ANTHROPIC_BASE_URL(設咗即用兼容端點)
      model     ← ADVISOR_MODEL(預設 claude-opus-4-8)
    冇 key / 冇 SDK → offline 模式,回事實快照,唔爆。
    """
    context = build_snapshot_context(session, prices)
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ARK_API_KEY")
    base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL")
    model = model or os.environ.get("ADVISOR_MODEL") or "claude-opus-4-8"

    if not api_key:
        return {"offline": True, "context": context,
                "answer": ("(離線模式:未設定 API Key)\n"
                           "以下係你嘅組合事實快照,設定 Key 後可問 AI 分析:\n"
                           + json.dumps(context, ensure_ascii=False, indent=2)
                           + "\n\n" + DISCLAIMER)}
    try:
        import anthropic
    except ImportError:
        return {"offline": True, "context": context,
                "answer": "(未安裝 anthropic SDK:pip install anthropic)\n" + DISCLAIMER}

    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url          # 火山方舟 / 其他 Anthropic 兼容端點
    client = anthropic.Anthropic(**kwargs)
    user_msg = (f"組合快照(JSON):\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
                f"用戶問題:{question}")
    try:
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}])
    except Exception as e:                       # 網路/鑑權/模型名錯 → 唔好成頁冧
        return {"offline": True, "context": context,
                "answer": f"(呼叫 AI 失敗:{type(e).__name__}: {e})\n\n" + DISCLAIMER}
    answer = "".join(b.text for b in resp.content if hasattr(b, "text"))
    if DISCLAIMER not in answer:
        answer = answer.rstrip() + "\n\n" + DISCLAIMER
    return {"offline": False, "context": context, "answer": answer,
            "model": model, "endpoint": base_url or "anthropic"}
