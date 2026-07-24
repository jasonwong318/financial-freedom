"""Telegram 交易守門 bot —— 放喺 VPS 長跑,交易前問佢得唔得。

點跑(VPS / 本機):
    export TELEGRAM_BOT_TOKEN=123456:abc...        # BotFather 攞
    export TELEGRAM_CHAT_ID=你嘅chat_id            # 選填:只回覆你自己
    export PORTFOLIO_DB_URL=sqlite:///portfolio.db  # 同 app 共用同一個 DB
    python -m bot.telegram_bot

用法(直接打俾 bot):
    買 TSLA 100 @ 407
    我想用 5萬 買 中移動
    沽 9988.HK 500
    /status            → 倉位配置 + 現時提示
    /help

設計:純 requests 長輪詢(long-polling),唔使裝 telegram SDK,任何 VPS 都跑到。
規則邏輯全部喺 bot/agent.py(可單元測試),呢度只管收發訊息。
"""
import os
import time

import requests

from app.models import make_session
from app.prices import latest_prices
from bot import agent

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")           # 選填:限定只回你
DB_URL = os.environ.get("PORTFOLIO_DB_URL", "sqlite:///portfolio.db")
API = f"https://api.telegram.org/bot{TOKEN}"

HELP = (
    "*投資委員會 · 交易守門 bot*\n"
    "交易前打俾我,我用你嘅 12 條分倉守則即場審一次:\n"
    "• `買 TSLA 100 @ 407`\n"
    "• `我想用 5萬 買 中移動`\n"
    "• `沽 9988.HK 500`\n"
    "• `/status` — 倉位配置 + 現時提示\n"
    "_只係提醒,最後你話事。_")


def _send(chat_id, text):
    try:
        requests.post(f"{API}/sendMessage", timeout=20, json={
            "chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
    except Exception as e:
        print("send error:", e)


def _handle(session, chat_id, text):
    text = (text or "").strip()
    if text in ("/start", "/help"):
        return _send(chat_id, HELP)
    if text.startswith("/status"):
        return _send(chat_id, agent.portfolio_status(session))
    intent = agent.parse_trade(text)
    if not intent:
        return _send(chat_id, "睇唔明。試下:`買 TSLA 100 @ 407` 或 `/help`")
    prices = latest_prices(session)
    _send(chat_id, agent.evaluate(session, prices, intent))


def main():
    if not TOKEN:
        raise SystemExit("請先 export TELEGRAM_BOT_TOKEN")
    session = make_session(DB_URL)
    print("bot 啟動,long-polling 中… (Ctrl+C 停)")
    offset = None
    while True:
        try:
            r = requests.get(f"{API}/getUpdates", timeout=35,
                             params={"timeout": 30, "offset": offset})
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                cid = str(msg["chat"]["id"])
                if CHAT_ID and cid != str(CHAT_ID):
                    continue                        # 唔係你 → 唔理
                _handle(session, cid, msg.get("text"))
        except requests.exceptions.RequestException as e:
            print("poll error:", e)
            time.sleep(3)
        except KeyboardInterrupt:
            print("bye")
            break


if __name__ == "__main__":
    main()
