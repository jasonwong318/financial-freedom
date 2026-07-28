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

## 3. 推薦架構:全部喺 VPS 度跑(app + bot 同一個 DB)

**重點:唔好 app 喺 PC、bot 喺 VPS(咁會兩個 DB 唔同步)。** 正確做法係 Streamlit app
同 bot **兩樣都喺 Oracle VPS 跑,共用同一個 `portfolio.db`**。好處:

- PC 關機都冇影響(全部喺 VPS 24 小時跑)
- 喺 app 記完交易/股息,bot 即刻同步(同一部機、同一個 db)—— **唔使手動 update VPS**
- 你想睇 app 就由 PC 開瀏覽器連上 VPS(見下面安全連法)

觀念澄清:
- **Python(語言)**:喺 VPS 用 `apt` 裝,唔係喺 GitHub download。
- **App 的 code**:用 `git clone` 由 GitHub 攞落 VPS。
- `export ...` 係 **Linux(bash)指令**,喺你 SSH 入咗 VPS 之後打。PowerShell 只係用嚟 SSH 登入。

### 完整流程(由 PC 開始)

```powershell
# 【喺你 PC 嘅 PowerShell】SSH 登入 Oracle VPS(用你嘅 key + VPS public IP)
ssh -i C:\path\to\oracle_key.key ubuntu@<VPS_PUBLIC_IP>
```

```bash
# 【以下全部喺 VPS 嘅 bash 度打】

# 1) 裝 Python + git(Oracle 通常係 Ubuntu;一次過)
sudo apt update && sudo apt install -y python3 python3-pip git

# 2) 攞 code(GitHub → VPS)
git clone https://github.com/jasonwong318/financial-freedom.git
cd financial-freedom/portfolio_system
pip3 install -r requirements.txt

# 3) 環境變數(BotFather 攞 token;@userinfobot 攞 chat_id)
export TELEGRAM_BOT_TOKEN=123456:AA...
export TELEGRAM_CHAT_ID=你的chat_id
export PORTFOLIO_DB_URL=sqlite:////home/ubuntu/financial-freedom/portfolio_system/portfolio.db
#                        ↑ 四個斜線 = 絕對路徑;app 同 bot 都指呢一個

# 4) 起 app(背景長跑)+ bot
nohup streamlit run ui/app.py --server.address 127.0.0.1 --server.port 8501 &
python3 -m bot.telegram_bot        # 前景測試;OK 之後改用下面 systemd
```

### 之後想 update code(GitHub 有新版本)
```bash
cd ~/financial-freedom && git pull      # 就係咁,唔使 download 成個
sudo systemctl restart committee-bot committee-app   # 有用 systemd 就重啟
```

### 喺 PC 安全咁睇個 app(唔好公開 8501 落公網!)
你嘅財務數據唔應該喺公網裸露。用 **SSH tunnel** 最穩陣:
```powershell
# 【PC PowerShell】開一條隧道,VPS 嘅 8501 映射到你 PC 嘅 localhost:8501
ssh -i C:\path\to\oracle_key.key -L 8501:127.0.0.1:8501 ubuntu@<VPS_PUBLIC_IP>
# 然後 PC 瀏覽器開:http://localhost:8501
```
（唔想每次開隧道?可以喺 Oracle security list 只開你屋企 IP + 加登入密碼,但隧道最簡單又最安全。)

### 兩個 systemd service(開機自動 + 崩潰重啟)
見下面第 3.1 節。

---

## 3.1 systemd 部署細節(app + bot 兩個 service,共用同一個 DB)

先確定共用嘅 DB 絕對路徑,例如 `/home/ubuntu/financial-freedom/portfolio_system/portfolio.db`。
兩個 service 都指同一個 `PORTFOLIO_DB_URL` → **app 一寫,bot 即刻讀到,零手動同步**。

**Service 1 — Streamlit app** `/etc/systemd/system/committee-app.service`：
```ini
[Unit]
Description=Investment Committee App
After=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/financial-freedom/portfolio_system
Environment=PORTFOLIO_DB_URL=sqlite:////home/ubuntu/financial-freedom/portfolio_system/portfolio.db
ExecStart=/usr/bin/python3 -m streamlit run ui/app.py --server.address 127.0.0.1 --server.port 8501
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Service 2 — Telegram 守門 bot** `/etc/systemd/system/committee-bot.service`：
```ini
[Unit]
Description=Investment Committee Telegram Gate Bot
After=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/financial-freedom/portfolio_system
Environment=TELEGRAM_BOT_TOKEN=123456:AA...
Environment=TELEGRAM_CHAT_ID=你的chat_id
Environment=PORTFOLIO_DB_URL=sqlite:////home/ubuntu/financial-freedom/portfolio_system/portfolio.db
ExecStart=/usr/bin/python3 -m bot.telegram_bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now committee-app committee-bot
journalctl -u committee-bot -f          # 睇 bot log
```

搞掂之後:兩個 service 24 小時喺 VPS 跑,**PC 閂機都冇影響**。你喺 app 記完交易,
Telegram 問 bot 就已經係最新倉位。

> 註:SQLite 俾 app + bot 同時讀寫,量少冇問題;將來想更穩就轉 Postgres
> (`PORTFOLIO_DB_URL=postgresql+psycopg2://...`,見 `docker-compose.yml`)。

### 用法(直接打俾 bot)
```
買 TSLA 100 @ 407
我想用 5萬 買 中移動
沽 9988.HK 500
/status
/help
```

---

## 4. 接 Hermes Agent(Nous 框架 + 火山引擎 LLM,Oracle VPS)

我嘅設定:**Hermes(Nous 出嘅 agent 框架)+ 火山引擎 agent plan 做 LLM,跑喺 Oracle Cloud VPS。**
(順帶:呢個 app 嘅「投資委員會」tab 已經支援火山方舟 base_url `https://ark.cn-beijing.volces.com/api/plan`
+ 模型 `ark-code-latest`,同一條 key/endpoint 可以重用。)

目前 bot 用正則解析自然語言,夠用但唔算「聰明」。想借 Hermes 做更自然嘅前端理解,用**混合架構**:

```
用戶自由講嘢 → Hermes(火山 LLM)理解意圖 → 呼叫 tool: check_trade(symbol, side, qty, price)
                                              │
                                              ▼
                                     app/rules.check_trade()  ← 確定性 gate,最終真相
```

- Hermes 只負責**理解語言 + 對話**;**得唔得永遠由確定性規則話事**(唔可以俾 LLM 亂放行)。
- 接法:把 `bot/agent.py` 嘅 `evaluate(session, prices, intent)` 同 `portfolio_status(session)`
  包成 Hermes 嘅兩個 tool/function。Hermes 解析用戶句子 → 填 `intent` → 呼叫 `evaluate` → 回覆。
- 兩個 tool 都係純 Python、讀同一個 `portfolio.db`,冇額外狀態,好易接。

- LLM 只負責**理解語言 + 對話**;**最終得唔得由確定性規則話事**(唔可以俾 LLM 亂放行)。
- 要接嘅話,把 `bot/agent.py` 的 `evaluate()` / `portfolio_status()` 包成該 agent 框架嘅一個 tool/function 即可。
- **請 Claude chat 幫手前,先話俾佢知你個「Hermes Agent」具體係邊個**(GitHub link / 文檔),因為呢個名有幾個唔同嘅嘢。

---

## 5. 想 Claude(chat)幫你做嘅嘢(建議清單)

1. 幫我喺 **Oracle Cloud VPS** 行第 3 節步驟(app + bot 兩個 systemd service,共用一個 DB)。
2. 幫我設好 **SSH tunnel**,喺 PC 安全咁開 `http://localhost:8501` 睇 app。
3. 我用 **Hermes(Nous)+ 火山引擎 agent plan**,幫我把 `bot/agent.py` 嘅
   `evaluate()` / `portfolio_status()` 包成 Hermes 兩個 tool(見第 4 節)。
4. (可選)加每日早晨自動推送 `/status`。

---

## 6. 一句總結俾對方

> 我有個 self-host 投資系統(repo 上面),已經寫好一個**唔靠 LLM、純規則**嘅 Telegram 守門 bot(`portfolio_system/bot/`),交易前審 12 條分倉守則。幫我落 VPS 長跑 + 解決 DB 同步 +(如需)接我個 Hermes agent 做前端。
