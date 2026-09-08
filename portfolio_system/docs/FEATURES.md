# Investment Committee 投資委員會 — 功能清單 / Handoff List

> 自建個人交易績效與資產追蹤系統(取代 StockerX)。業主:Jason(香港,活躍交易者)。
> UI 全繁體中文(香港)。Repo:`jasonwong318/financial-freedom` → `portfolio_system/`。
> 最後更新:2026 年(v6,Telegram bot + VPS 部署完成)。

---

## 0. 一句定位
Percento 式全資產資訊架構 + 券商級 lot-level accounting + 分倉行為風控 + AI 投資委員會 —
三樣嘢市面冇一個 app 齊。**本地/自架、私隱優先、無廣告。**

---

## 1. 核心會計引擎(同 StockerX 嘅根本差異)

| 功能 | 說明 |
|---|---|
| **lot-level FIFO 引擎** | 每次買入開一個 lot;沽出由最舊 lot 逐批扣,寫 lot_closures。`app/fifo.py` |
| **三口徑永不混算** | ①現時持倉收益(只計 open lots)②已實現(回合)③股息 — 三者分開顯示,唔會擠埋做一個誤導綠色數字(StockerX 嘅核心問題) |
| **平均成本只計現有 lot** | 賣清歸零,再買重新開 lot;永不引用已平倉批次 |
| **回合(round trip)** | 同一 SELL 事件配對出嘅所有 closures 合併為一個回合;勝率/賺賠比用回合計 |
| **CSV 匯入 + 驗證** | StockerX 格式;壞行(零股零價)擋落並列驗證報告。`app/importer.py` |
| **immutable audit trail** | transactions 唔改原始行,更正用沖銷;改完 call `rebuild_lots()` 全量重算 |
| **$0 送股 / 拆股** | NVDA 1.35 股 cost=0 唔除以零;SPLIT/BONUS 調整 lot |

**真數 regression 基準(釘死)**:TSLA open 860 股 @341.66 · 9888.HK 2400 @140.92 ·
全史 24 回合 +300,441 · 勝率 91.7% · 賺賠比 2.37 · 全史股息 242,797。

---

## 2. 指標 / 績效

- **XIRR**:由交易現金流推導(買−/沽+/息+ + 期末市值),免入金記錄。`app/performance.py`
- **TWRR**:snapshots 鏈式,當日淨買賣額做 external flow
- **雙軌勝率**:已實現 91.7%(倖存者偏差)vs 含 mark-to-market 真實 65.1%。`app/behavior.py`
- **處置效應監測**:贏回合 vs 蝕回合平均持倉期 + 蝕貨 open lots 賬齡
- **違規成本**:每單歷史違規交易嘅最終賺蝕合計
- **股息三口徑**:全歷史 / 持有期內 / 滾動 12 個月;yield-on-cost = 滾動12月股息 ÷ 持倉成本。`app/income.py`
- **每股派息**:由持股反推(dividend_events)
- **基準對比**:VOO / ^HSI / ^IXIC,total return 口徑。`app/benchmark.py`
- **報表**:逐年回報、月度盈虧熱力圖、NAV 回撤曲線。`app/reports.py`

---

## 3. 四大倉位(sleeve)分類

| 倉位 | 單一上限 | 整倉上限 | 止蝕 | 預設持倉 |
|---|---|---|---|---|
| 核心信仰倉 | 40% | 50% | −30% 檢討(唔強制) | TSLA |
| 地基股息倉 | 20% | 20–30% | −20% 檢討 | 中移(0941)、中油(0883) |
| 長期被動收入倉 | — | 15–25% | 唔止蝕 | VOO、2802、3416、3466 |
| 衛星/FOMO投機倉 | 5% | 10% | −20% 止蝕提醒 | 其餘全部 |

- 分倉可喺「持倉」頁「調整分倉」隨時改(存 `instrument_buckets` 表)。`app/buckets.py`
- 總覽頁有「四大倉位配置」panel:即時權重 vs 目標,超標紅 / 未達下限黃。

---

## 4. 12 條行為規則(分倉感知,`app/rules.py`)

1. **AVG_DOWN_LIMIT** 溝貨:最多 2 次、要跌 ≥15%、注碼 ≤原倉 50%
2. **CHASE_HIGH** 避免高追:唔喺 20 日高位 3% 內買
3. **MAX_POSITION_WEIGHT** 分層單一上限(核心40/地基20/衛星5)+ sleeve 合計上限
4. **MAX_SECTOR_WEIGHT** 板塊 ≤40%
5. **MAX_SINGLE_ENTRY** 單筆 ≤總資產 5%
6. **REBUY_HIGHER** 沽出後 90 日內唔好高 >10% 追返
7. **STALE_LOSER** 非核心蝕>20%又揸>180日 → 提醒賣一半(核心豁免)
8. **STOP_LOSS_ALERT** 分倉止蝕:衛星−20 提醒 / 核心地基−30 檢討 / 被動免
9. **WEEKLY_CIRCUIT_BREAKER** 一週已實現虧損>總資產2% → 停手 5 日
10. **NEW_FOMO_CAP** 每月新增投機 ≤2%、總投機 ≤10%
11. **CASH_BUFFER_RULE** 維持 ≥5% 現金
12. **QUARTERLY_REBALANCE** 季尾再平衡提示

- **三個入口**:錄入前 `check_trade`(pre-trade 攔截)、狀態 `scan_portfolio`、歷史 `scan_history`
- **規則只提示/攔截,唔自動落單**;「強制」= 標紅 + 彈警示,最後你話事
- (已移除 FEE_CHECK — 業主決定唔理手續費)

---

## 5. Streamlit UI — 10 頁(`ui/app.py`)

| 頁 | 內容 |
|---|---|
| **總覽** | 5 KPI(市值/持倉損益/已實現/股息/XIRR,損益紅綠)+ 甜甜圈佔比(最大倉置頂,細倉聚合)+ 四大倉位配置 panel |
| **收益** | 三口徑 breakdown 可展開 + 成本價 + yield-on-cost + 基準對比 |
| **持倉** | 倉位欄 + 每股賺蝕% + 三欄口徑分開 + 調整分倉編輯器 |
| **已平倉** | 逐年總結 + 年份 filter + 買入日/均價/沽出價/每股賺蝕% |
| **股息** | 每月收息柱狀 + 逐隻累計 + 逐筆每股派息(含 Syfe) |
| **新增交易** | pre-trade 12 規則審查(可 override 記錄在案)+ 記股息 + 十二規則清單 |
| **行為儀表板** | 雙軌勝率 + 處置效應 + 蝕貨按股分組展開 + 違規成本 |
| **全資產** | 淨資產 + Treemap + 銀行/MPF/Syfe 可編輯表(月度快照) |
| **報表** | 逐年回報 + 月度熱力圖 + NAV 回撤 |
| **投資委員會** | AI 多角色辯論(見 §6) |

- **視覺**:Linear × Bloomberg terminal 暗色主題(Inter + tabular numbers、hairline、損益紅綠、`ui/theme.py`)
- **側欄**:CSV 上載(清空重匯入)· yfinance 攞價 · 匯率口徑(固定 7.80 / 歷史)· 寫 snapshot

---

## 6. 投資委員會(AI,`app/committee.py`)

- **核心 5 角色**:牛方 / 熊方 / 魔鬼代言人 / 價值視角 / PM 裁決
- **11 位客席名人框架**:Serenity(AI 供應鏈瓶頸,已用 serenity-skill 方法論強化)、
  巴菲特、芒格、格雷厄姆、Cathie Wood、Ackman、Fisher、Druckenmiller、Burry、Lynch、Dalio
- **牛/熊席位可指定名人扮演** + 加開客席;客席只模擬公開分析風格,附免責
- **多輪對話**記上文、截斷可「繼續生成」、「重開會議」用最新快照
- **收息倉檢討**:評收息股一律用含息總回報
- **後端**:Anthropic 官方 或 **火山引擎方舟**(base_url `https://ark.cn-beijing.volces.com/api/plan`,模型 `ark-code-latest`);冇 key 離線降級

---

## 7. Telegram 交易守門 Bot(`bot/`)

- **唔綁 LLM,純規則計數**:收到訊息 → 讀同一個 `portfolio.db` → 跑 12 條分倉守則 → 回覆
- **自然語言**:`買 TSLA 100 @ 407` / `用 5萬 買 中移動` / `沽 9988.HK 500` / `/status`
- **秒回、免費、每次一樣**;手機都用到(唔使開 PC)
- `bot/agent.py`(可測試核心)+ `bot/telegram_bot.py`(requests 長輪詢)

---

## 8. API(選用,`api/main.py`,FastAPI)
`/import/csv` · `/transactions`(pre-trade 檢查) · `/positions?basis` · `/metrics/summary` ·
`/snapshots` · `/assets_other` · `/advisor/ask`

---

## 9. 部署

- **本機**:`streamlit run ui/app.py`(SQLite,首次自動匯入 CSV)
- **VPS(現行)**:Oracle Cloud,app + bot 兩個 systemd service 共用一個 `portfolio.db`;
  PC 用 SSH tunnel 睇 app(`localhost:8501`)。一鍵 `setup_vps.sh`。詳見 `docs/HANDOFF_telegram_vps.md`
- **Docker**:`docker-compose.yml`(db Postgres16 + api + ui)
- **DB**:SQLite(本地)/ Postgres(正式);`portfolio.db` 唔入 git(私隱)
- **匯率**:固定 7.80 或 歷史(yfinance USDHKD=X);`config.fx_note()` 註明口徑

---

## 10. 檔案結構
```
portfolio_system/
├── app/         models fifo importer metrics performance prices config fx
│                income benchmark rules buckets behavior assets reports advisor committee
├── ui/          app.py(10 頁) theme.py(暗色主題)
├── bot/         agent.py telegram_bot.py(Telegram 守門)
├── api/         main.py(FastAPI 7 endpoints)
├── tests/       ~73 條(全綠;真數 regression 釘死)
├── docs/        HANDOFF_telegram_vps.md  FEATURES.md(本檔)
├── setup_vps.sh Dockerfile docker-compose.yml requirements.txt
└── SPEC_portfolio_system.md(原始規格)
```

---

## 11. 明確唔做(scope 外)
實時串流報價、自動落單/券商寫入、沽空/槓桿/期權、多用戶。
規則只提示,唔代你交易。

---

## 12. 日常操作速查

| 想做 | 點做 |
|---|---|
| 睇/記倉位(app) | PC:`ssh -i ...oracle_hermes -L 8501:127.0.0.1:8501 ubuntu@168.138.190.53` → Chrome `localhost:8501` |
| 落單前問得唔得 | Telegram:`買 TSLA 100 @ 407` / `/status` |
| 攞即時價 | app 側欄「yfinance 攞最新 EOD」 |
| 更新 code | VPS:`cd ~/financial-freedom/portfolio_system && git pull && sudo systemctl restart committee-app committee-bot` |
| 換自己 CSV | app 側欄「上載 CSV → 清空並重新匯入」 |
| 每月更新資產 | app 全資產頁,填銀行/MPF/Syfe 結餘 → 儲存 |
