# Handoff:喺 VPS 部署 Telegram 交易守門 Bot

> 呢份俾你(Jason)接住去問 Claude(chat)/ 落 VPS 部署用。已包含所有背景,對方唔使睇成個 repo 都跟到。

---

## 0. 一句話

有一個自建投資組合系統(取代 StockerX),入面有一套**12 條分倉行為守則**。我想喺 VPS 跑一個 **Telegram bot**,交易前打一句「買 TSLA 100 @ 407」,佢即刻用嗰 12 條守則審一次,話我得唔得、邊條有問題。**Bot 已經寫好**,呢份係部署 + 延伸指引。

---

## 1. 個系統係咩(背景)

- **Repo**:`https://github.com/jasonwong318/financial-freedom`,主程式喺 `portfolio_system/`
- **技術**:Python 3.12 · SQLAlchemy(SQLite 本地 / Postgres 正式)· Streamlit UI · FastAPI · 規則引擎
- **資料庫**:一個檔案 `portfolio.db`(SQLite)。所有持倉、交易、股息、現價、分倉都喺入面。
- **核心概念:四大倉位(sleeve)**
  | 倉位 | 單一上限 | 整倉上限 | 止蝕 |
  |---|---|---|---|
  | 核心信仰倉 | 40% | 50% | −30% 檢討(唔強制) |
  | 地基股息倉 | 20% | 20–30% | −20% 檢討 |
  | 長期被動收入倉 | — | 15–25% | 唔止蝕 |
  | 衛星/FOMO投機倉 | 5% | 10% | −20% 止蝕提醒 |

  預設分倉:TSLA=核心;中移(0941.HK)+中油(0883.HK)=地基;VOO/2802/3416/3466=被動;其餘=衛星。

- **12 條行為守則**(`portfolio_system/app/rules.py` 的 `DEFAULT_RULES`):
  1. AVG_DOWN_LIMIT 溝貨紀律(最多 2 次、要跌 ≥15%、注碼 ≤原倉 50%)
  2. CHASE_HIGH 避免高追(20 日高位 3% 內)
  3. MAX_POSITION_WEIGHT 分層單一上限(核心40/地基20/衛星5)+ sleeve 合計上限
  4. MAX_SECTOR_WEIGHT 板塊 ≤40%
  5. MAX_SINGLE_ENTRY 單筆 ≤總資產 5%
  6. REBUY_HIGHER 沽出後 90 日內唔好高 >10% 追返
  7. STALE_LOSER 非核心蝕>20%又揸>180日 → 提醒賣一半(核心豁免)
  8. STOP_LOSS_ALERT 分倉止蝕(衛星−20 提醒 / 核心地基−30 檢討 / 被動免)
  9. WEEKLY_CIRCUIT_BREAKER 一週已實現虧損>總資產2% → 停手 5 日
  10. NEW_FOMO_CAP 每月新增投機 ≤2%、總投機 ≤10%
  11. CASH_BUFFER_RULE 維持 ≥5% 現金
  12. QUARTERLY_REBALANCE 季尾再平衡提示

---

## 2. Bot 架構(重點:唔綁 LLM)

```
Telegram 訊息
   │  「買 TSLA 100 @ 407」
   ▼
bot/telegram_bot.py   ← 純 requests 長輪詢,只管收發
   │
   ▼
bot/agent.py
   ├─ parse_trade(text)      ← 正則解析,冇 AI
   └─ evaluate(...)          ← 叫 app/rules.check_trade()
                                （12 條守則,純數學,確定性)
   │  讀
   ▼
portfolio.db  ← 同 Streamlit app 共用同一個 DB(持倉/現價/分倉)
```

- **判斷點嚟**:`check_trade()` 係確定性運算(加減乘除比上限),**冇 LLM、冇 token 成本、即時、每次一樣**。
- **點知倉位**:讀同一個 `portfolio.db`。app 記咗咩,bot 即刻見到。
- **成套唯一用 LLM 嘅地方**:Streamlit 的「投資委員會」tab(Claude / 火山方舟),同守門 bot 完全分開。

### 檔案
- `portfolio_system/bot/agent.py` — 解析 + 裁決 + `/status`(可單元測試,`tests/test_bot.py` 8 條)
- `portfolio_system/bot/telegram_bot.py` — Telegram 長輪詢 loop

---

## 3. VPS 部署步驟

```bash
# 1) 攞 code
git clone https://github.com/jasonwong318/financial-freedom.git
cd financial-freedom/portfolio_system
pip install -r requirements.txt        # 或至少:sqlalchemy pandas requests yfinance

# 2) BotFather(Telegram)開 bot 攞 token;攞自己 chat_id(可用 @userinfobot)

# 3) 環境變數
export TELEGRAM_BOT_TOKEN=123456:AA...       # BotFather 俾
export TELEGRAM_CHAT_ID=你的chat_id          # 選填,只回你自己
export PORTFOLIO_DB_URL=sqlite:////absolute/path/portfolio.db   # 見下面同步

# 4) 起動
python -m bot.telegram_bot
```

### 用法(直接打俾 bot)
```
買 TSLA 100 @ 407
我想用 5萬 買 中移動
沽 9988.HK 500
/status
/help
```

### systemd(開機自動 + 崩潰重啟)
`/etc/systemd/system/committee-bot.service`：
```ini
[Unit]
Description=Investment Committee Telegram Gate Bot
After=network-online.target

[Service]
WorkingDirectory=/home/USER/financial-freedom/portfolio_system
Environment=TELEGRAM_BOT_TOKEN=123456:AA...
Environment=TELEGRAM_CHAT_ID=你的chat_id
Environment=PORTFOLIO_DB_URL=sqlite:////home/USER/financial-freedom/portfolio_system/portfolio.db
ExecStart=/usr/bin/python3 -m bot.telegram_bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now committee-bot
journalctl -u committee-bot -f      # 睇 log
```

### ⚠️ DB 同步(要諗)
Bot 要讀到你**最新持倉**先準。三個選項:
- **(A) 最簡單**:喺同一部 VPS 都跑 Streamlit app,兩者指同一個 `portfolio.db`。你喺 app 記交易 → bot 即刻同步。
- **(B) 升級 Postgres**:`PORTFOLIO_DB_URL=postgresql+psycopg2://...`,app 同 bot 都連同一個 DB(最穩,見 `docker-compose.yml`)。
- **(C) 純本地 app + VPS bot**:要定期 rsync/scp `portfolio.db` 上 VPS(易漏更新,唔建議)。

---

## 4. 想接 Hermes Agent / 令 bot 更聰明(可選)

目前 bot 用正則解析自然語言,夠用但唔算「聰明」。如果想接一個 function-calling LLM(例如 Nous Hermes、或你講嗰個 Hermes Agent runtime),建議**混合架構**:

```
用戶自由講嘢 → LLM(Hermes)理解意圖 → 呼叫 tool: check_trade(symbol, side, qty, price)
                                              │
                                              ▼
                                     app/rules.check_trade()  ← 確定性 gate,最終真相
```

- LLM 只負責**理解語言 + 對話**;**最終得唔得由確定性規則話事**(唔可以俾 LLM 亂放行)。
- 要接嘅話,把 `bot/agent.py` 的 `evaluate()` / `portfolio_status()` 包成該 agent 框架嘅一個 tool/function 即可。
- **請 Claude chat 幫手前,先話俾佢知你個「Hermes Agent」具體係邊個**(GitHub link / 文檔),因為呢個名有幾個唔同嘅嘢。

---

## 5. 想 Claude(chat)幫你做嘅嘢(建議清單)

1. 幫我喺 VPS(邊間?e.g. DigitalOcean/Vultr）行第 3 節步驟,包 systemd。
2. DB 同步用邊個方案(A/B/C)最啱我?
3. 我個 Hermes Agent 係 ___(貼 link),幫我把 `agent.evaluate()` 接做佢一個 tool。
4. (可選)加每日早晨自動推送 `/status`。

---

## 6. 一句總結俾對方

> 我有個 self-host 投資系統(repo 上面),已經寫好一個**唔靠 LLM、純規則**嘅 Telegram 守門 bot(`portfolio_system/bot/`),交易前審 12 條分倉守則。幫我落 VPS 長跑 + 解決 DB 同步 +(如需)接我個 Hermes agent 做前端。
