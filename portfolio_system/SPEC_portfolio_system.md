# 開發規格書:個人交易績效與資產追蹤系統(取代 StockerX)

> 交俾 Claude Code(Opus)跟進用。業主:Jason(香港,活躍交易者,偏好 vibe coding:自然語言描述邏輯 → AI 產碼,重視可讀性同邏輯解釋)。全部 UI 文案用繁體中文(香港)。

---

## 0. 背景與設計動機(必讀)

業主由 StockerX 遷出,核心不滿(全部要喺本系統修正):

1. **持倉收益混入歷史已實現**:StockerX 以 symbol 維度累計,例如 MU 現時 20 股 @1050 蝕緊,但因為之前一輪已賺 +21,372,app 顯示綠色 +10,395。本系統一律以 **lot 維度**:「現時持倉收益」只計 open lots;lifetime 收益另欄顯示,永不混合。
2. **平均成本價計埋已沽清嘅歷史批次**:賣清歸零,再買重新開 lot 計。
3. **只追蹤股票**:要 Percento 式全資產(現金、Syfe 基金、MPF、債券,手動月度輸入可接受)。
4. **廣告/bug/慢**:self-host,本地優先。
5. **勝率誤導**:已實現勝率 91.7% 係倖存者偏差(輸家唔沽);要雙軌顯示「已實現勝率」+「含 mark-to-market 真實勝率」(現約 65%)。

已知數據特性(用真實 CSV 驗證過,test fixtures 見 §8):

- 日期格式 `DD/MM/YYYY HH:MM:SS`;Type ∈ {Buy, Sell, DividendCash};港美混合、HKD/USD 混合。
- 有零股零價錯誤行(MRVL 03/06/2026)→ validation 要擋。
- 有 $0 送股(NVDA 1.35297 股)→ 屬 corporate action,成本 0 合法。
- 時間戳係手動事後補記,唔可靠;日期同價格可靠。
- TSLA 價格已按 2022 拆股調整。

---

## 0.5 功能對標:StockerX × Percento × 本系統

> 註:對標係功能概念層面(佢哋公開嘅功能描述 + 業主截圖),我哋見唔到佢哋 source code,亦唔會複製佢哋 UI 素材;所有實作獨立完成。

| 功能 | StockerX | Percento | 本系統 |
|---|---|---|---|
| 逐筆交易記錄 + 已實現/未實現 | ✅ 但 symbol-level 混歷史 | ❌(餘額記帳法,唔記逐筆) | ✅ lot-level 雙軌(核心差異化) |
| 股價自動更新 | ✅ | ✅(高級版,港美A股基金) | ✅ yfinance 免費 |
| 全資產五大類(流動資金/投資/固定資產/負債/應收) | ❌ 只有股票 | ✅(**借鏡**:首頁四區塊 + 資產分配比例圖) | ✅ 照抄呢個資訊架構,加「MPF/Syfe」預設分類 |
| 多幣種自動匯率 | 部分 | ✅(**借鏡**) | ✅ fx_rates 表 |
| 淨資產趨勢圖 / Treemap | 部分 | ✅(**借鏡**:淨資產趨勢 + 矩形樹狀圖) | ✅ 加 15% 集中度上限線(超越) |
| 帳戶間轉帳(複式記帳) | ❌ | ✅(**借鏡**) | ✅ CASH_IN/OUT + transfer 配對 |
| 定期項目自動記(糧/供款) | ❌ | ✅(**借鏡**:「自動記」) | Phase 3 nice-to-have |
| 沽清持倉後歷史淨值失真 | — | ⚠️ 用戶投訴中(App Store 評論) | ✅ snapshots_daily 用當日實際持倉,唔會追溯改寫(修正) |
| 行為規則引擎/違規成本 | ❌ | ❌ | ✅ **獨有殺手功能** |
| 勝率/賺賠比/期望值/TWRR/回撤 | ❌ | ❌ | ✅ 獨有 |
| 股息含息總回報 | 部分(收益混計) | ❌ | ✅ 三口徑分開 |
| 本地優先/私隱 | ❌(雲+廣告) | ✅ 本地+iCloud(**借鏡理念**) | ✅ self-host Docker |
| AI 顧問 | ❌ | ❌ | ✅ 獨有 |

一句定位:**Percento 嘅全資產資訊架構 + 券商級 lot accounting + 行為風控**,三樣嘢市面冇一個 app 齊。

## 1. 技術棧(已同業主敲定)

| 層 | 選型 | 備註 |
|---|---|---|
| 後端 | Python 3.12 + FastAPI + pandas | 指標計算用 pandas,API 層薄 |
| DB | PostgreSQL 16(開發期可 SQLite,SQLAlchemy 抽象) | Alembic 做 migration |
| 行情 | **yfinance**(港股 `.HK` 後綴 + 美股都支援);設 `PriceProvider` interface,預留 Longbridge OpenAPI(longport SDK)同 Alpha Vantage adapter(注意:AV 唔支援港股,只可做美股備援) | EOD 日線為主,唔需要實時 |
| 前端 | Streamlit(Phase 1)→ Next.js(Phase 3 先考慮) | |
| 部署 | 本地 Docker Compose(db + api + ui) | 私隱優先,唔上雲 |
| AI 顧問 | Anthropic API `/v1/messages`,`/advisor` endpoint 將 lot-level 快照餵入 prompt | Phase 3 |

## 2. 資料庫 Schema(DDL 級數)

```sql
CREATE TABLE accounts (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,            -- e.g. '長橋主戶口'
  broker TEXT,
  base_ccy CHAR(3) NOT NULL DEFAULT 'HKD'
);

CREATE TABLE instruments (
  id SERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,          -- '0941.HK' / 'TSLA'
  name TEXT,
  market TEXT NOT NULL,          -- 'HK' / 'US'
  ccy CHAR(3) NOT NULL,
  sector TEXT,                   -- 自定板塊,e.g. 'AI半導體','高息ETF'
  asset_class TEXT NOT NULL DEFAULT 'equity',  -- equity/etf/fund/bond/cash/mpf
  UNIQUE(symbol, market)
);

CREATE TABLE transactions (
  id SERIAL PRIMARY KEY,
  account_id INT NOT NULL REFERENCES accounts(id),
  instrument_id INT REFERENCES instruments(id),
  trade_dt TIMESTAMP NOT NULL,
  type TEXT NOT NULL CHECK (type IN ('BUY','SELL','DIV_CASH','SPLIT','BONUS_SHARES','FEE','CASH_IN','CASH_OUT')),
  price NUMERIC(18,6),
  qty NUMERIC(18,6),
  fee NUMERIC(18,4) DEFAULT 0,
  ccy CHAR(3) NOT NULL,
  note TEXT,
  source TEXT DEFAULT 'manual',  -- manual / csv_import
  created_at TIMESTAMP DEFAULT now()   -- audit trail,唔准 UPDATE 原始行,更正用沖銷
);
CREATE INDEX idx_txn_inst_dt ON transactions(instrument_id, trade_dt);

CREATE TABLE lots (               -- FIFO 引擎維護,唔手改
  id SERIAL PRIMARY KEY,
  open_txn_id INT NOT NULL REFERENCES transactions(id),
  instrument_id INT NOT NULL REFERENCES instruments(id),
  open_dt TIMESTAMP NOT NULL,
  open_price NUMERIC(18,6) NOT NULL,
  qty_opened NUMERIC(18,6) NOT NULL,
  qty_remaining NUMERIC(18,6) NOT NULL,
  fee_per_share NUMERIC(18,8) DEFAULT 0
);

CREATE TABLE lot_closures (       -- 每次 SELL 配對記錄 → 已實現損益嘅原子單位
  id SERIAL PRIMARY KEY,
  lot_id INT NOT NULL REFERENCES lots(id),
  close_txn_id INT NOT NULL REFERENCES transactions(id),
  qty NUMERIC(18,6) NOT NULL,
  realized_pnl_ccy NUMERIC(18,4) NOT NULL,   -- 原幣,已扣買賣兩邊按股攤分手續費
  hold_days INT NOT NULL
);

CREATE TABLE prices_eod (
  instrument_id INT REFERENCES instruments(id),
  date DATE NOT NULL,
  close NUMERIC(18,6),
  adj_close NUMERIC(18,6),
  PRIMARY KEY (instrument_id, date)
);

CREATE TABLE fx_rates (
  ccy_pair CHAR(6),               -- 'USDHKD'
  date DATE,
  rate NUMERIC(12,6),
  PRIMARY KEY (ccy_pair, date)
);

CREATE TABLE assets_other (       -- Percento 式全資產
  id SERIAL PRIMARY KEY,
  category TEXT NOT NULL,         -- cash / syfe / mpf / bond / insurance
  name TEXT NOT NULL,
  ccy CHAR(3) NOT NULL,
  value NUMERIC(18,2) NOT NULL,
  as_of DATE NOT NULL
);

CREATE TABLE snapshots_daily (
  date DATE,
  account_id INT REFERENCES accounts(id),
  nav_hkd NUMERIC(18,2),
  equity_mv_hkd NUMERIC(18,2),
  cash_hkd NUMERIC(18,2),
  exposure JSONB,                 -- {"TSLA":0.508,...} 市值權重
  PRIMARY KEY (date, account_id)
);

CREATE TABLE rules (
  id SERIAL PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,      -- 見 §4
  params JSONB NOT NULL,
  enabled BOOLEAN DEFAULT true
);

CREATE TABLE rule_violations (
  id SERIAL PRIMARY KEY,
  rule_id INT REFERENCES rules(id),
  txn_id INT REFERENCES transactions(id),
  violated_at TIMESTAMP DEFAULT now(),
  detail JSONB,
  acknowledged BOOLEAN DEFAULT false
);
```

**匯率口徑(業主已確認)**:Phase 1 用固定 7.80(存 config,全系統單一來源);Phase 2 加 `fx_rates` 歷史匯率模式(yfinance `USDHKD=X` 日線 backfill + 每日 append),config 一鍵切換 `fx_mode: fixed | historical`。港元聯匯 band 7.75–7.85,兩口徑最大偏差 ±0.6%,報表要註明使用中嘅口徑。

**冇入金/出金記錄(業主決定唔用)**:所有資金加權指標由交易現金流推導——XIRR 現金流 = 買入(流出) + 沽出(流入) + 股息(流入) + 期末市值(終值);TWRR 用 snapshots,當日淨買賣額視為 external flow。`CASH_IN/CASH_OUT` 類型保留喺 schema 但 optional,唔准任何指標依賴佢。

## 3. FIFO 引擎(核心,Phase 1 第一件事)

- BUY → 開 lot;SELL → 由最舊 `qty_remaining>0` 嘅 lot 逐批扣,寫 `lot_closures`。
- 已實現 PnL(原幣)= `(sell_px − open_px) × qty − 買方 fee_per_share × qty − 賣方 fee_per_share × qty`。
- **回合(round trip)定義**:同一 SELL 事件配對出嚟嘅所有 closures 合併為一個回合(勝率/賺賠比用回合計,唔用 closure 計)。
- 現時持倉平均成本 = Σ(open lots 成本)/Σ(qty_remaining)。**嚴禁引用已平倉批次。**
- SPLIT/BONUS_SHARES:調整現有 lots 嘅 qty 同 price(qty×k, price÷k),$0 送股開新 lot(price=0)。
- SELL 超過現有持股 → 報錯擋落,唔准變負數(業主無沽空)。

## 4. 行為規則引擎(業主指定最高優先,對應佢已確診嘅交易弱點)

| code | 邏輯 | 預設 params | 對應弱點 |
|---|---|---|---|
| `MAX_POSITION_WEIGHT` | 買入後該標的市值權重 > x% 就 flag | {"pct":15} | TSLA 集中度 50%+ |
| `MAX_SECTOR_WEIGHT` | 板塊合計 > x% | {"sector":"AI","pct":40} | AI 全鏈重倉 |
| `MAX_SINGLE_ENTRY` | 單筆買入金額 > 總資產 x% | {"pct":5} | 情緒大注(2026-04 TSLA 50股) |
| `AVG_DOWN_LIMIT` | 同一標的溝貨 >n 次,或跌幅 <y% 就溝,或注碼 >原注 z% | {"n":2,"min_drop_pct":15,"max_size_pct":50} | 無上限溝貨 |
| `CHASE_HIGH` | 買入價喺 20 日高位 3% 之內 | {"pct":3} | 高位追貨(TSLA 2025Q4) |
| `STALE_LOSER` | open lot 帳面蝕 >10% 且持有 >180 日 → 強制檢討提示 | {"loss_pct":10,"days":180} | XYZ/GME 死揸 |
| `STOP_LOSS_ALERT` | 衛星倉浮虧穿 −15%(投機倉 −20%)發提示 | {"satellite":15,"spec":20} | 唔肯認衰 |
| `WEEKLY_CIRCUIT_BREAKER` | 一週已實現虧損 > 總資產 2% → 建議停新倉 5 個交易日 | {"pct":2,"cooloff_days":5} | 報復性交易 |
| `FEE_CHECK` | 預期毛利 < 3× 來回手續費 | {"mult":3} | 領展蝕手續費事件 |
| `REBUY_HIGHER` | 沽出後 90 日內以高 >10% 價買返同一標的 | {"days":90,"pct":10} | BABA $120沽$158追 |

實作:錄入交易時同步跑(pre-trade 提示),另有 nightly batch 重掃。每次違規寫 `rule_violations`,dashboard 統計「違規成本」(違規交易嘅後續 PnL vs 假如守規)。

## 5. 指標計算 DAG(nightly job 順序)

```
transactions → lots/lot_closures(immutable, audit trail)
            → realized 回合表 → 勝率/賺賠比/期望值/持倉期(滾動12個月 + 全歷史)
prices_eod + fx_rates + open lots → snapshots_daily(nav, exposure)
snapshots_daily → 月度 TWRR(當日淨買賣額 = external flow)、月回報
              → XIRR:現金流 = 全部買入(−) + 沽出(+) + 股息(+) + 期末市值(+),scipy/numpy 解 IRR
              → 年度:年化回報、最大回撤、Sharpe(rf 用 3個月美債或 0)
              → 真實勝率 = (贏回合 + 賺緊 open positions)/(總回合 + open positions)
```

**股息處理(業主明文要求,唔准忽略)**:

- `DIV_CASH` 按 instrument 累計,分「現有持倉期內收取」同「全歷史」兩軌。
- 每個持倉三個回報數:①價差未實現;②含息未實現 = 價差 + 持有期內股息;③yield-on-cost = 滾動 12 個月股息 ÷ open lots 成本。
- TWRR/MWRR 一律將股息當現金流入計入(total return),另提供 price-only return 做對照。
- 收息倉(0941/0883/2802/3416/3466 類)嘅任何評估/排序預設用含息總回報。

基準對比:VOO、^HSI(yfinance 攞;基準亦用 total return 口徑先公平)。

## 6. UI 規格:StockerX 重建 + 修正(Streamlit 頁面)

> 業主提供咗 11 張 StockerX 截圖,以下係逆向還原嘅畫面規格。原則:**資訊架構照跟(業主用開順手),數字口徑全部修正,視覺唔抄佢**。

### 6.1 總覽頁(對應 StockerX 首頁/概覽 tab)
- 頂部卡:總資產(HKD,可切換幣種顯示;有「遮罩」開關隱藏金額)、總收益 + 今日收益(金額+%)、市值/現金比例條。
- 甜甜圈圖:持倉市值權重,中心顯示最大持倉百分比;旁邊權重列表(TSLA 50.9% 呢種)。**修正:加 15% 單一標的上限紅線標記,超標者高亮。**
- 選中個股詳情卡:市值、股數、今日收益、收益。**修正:預設顯示「現時持倉收益」(open lots only),lifetime 收益以次要小字顯示,兩個數並列唔混算——直接解決 MU 顯示綠色但現倉蝕緊嘅誤導。**
- 資產圖表:5D/1M/3M/6M/YTD/1Y/2Y/全部,三線(總資產/市值/現金),可疊加基準(VOO/恒指)。**修正:歷史點用當日 snapshot,沽清持倉唔會追溯改寫曲線(Percento 用戶投訴嘅失真問題)。**

### 6.2 收益頁(對應「收益」tab)
- 詳細收益:成本、收益 = 持倉 + 已實現 + 股息(各附金額同 %)。**修正:% 嘅分母口徑喺 UI 註明(現時持倉成本 or 歷史總投入,config 決定);另加 XIRR 年化(StockerX 冇)。**
- 收益圖表:模式切換「收益 / 收益% / TWRR」,可疊加普爾500/恒指/納指等基準。
- 股息圖表:月度股息柱狀 + 滾動 12 個月股息、yield-on-cost。

### 6.3 持倉頁(對應「持倉」tab)
- 列表:股票/現價/收益三欄,可排序;每行收益 = **現時持倉收益**(唔係 lifetime)。
- 展開個股詳情:市值、平均持倉價、**平均成本價(只計 open lots,賣清歸零)**、成本、收益四分解、追蹤年度股息收益率、該股交易記錄(可編輯,編輯後觸發 FIFO 重算)。

### 6.4 已平倉頁
- 回合列表(收益金額+%),點入睇配對明細(邊批 lot 對邊筆賣出、持有日數)。

### 6.5 交易記錄頁 + 新增交易
- 全類別時序流水(買入/沽出/股息/現金),可編輯(以沖銷方式,保留 audit trail)。
- 新增交易 form:提交前跑 §4 規則引擎,violations 以警示卡彈出(可 override 但記錄在案)。

### 6.6 本系統獨有頁
- **行為儀表板**:違規清單、違規成本、平均賺 vs 蝕持倉期(處置效應監測)、雙軌勝率(已實現 vs 含 mark-to-market)。
- **全資產頁(Percento 式)**:流動資金/投資/固定資產/負債四區塊、淨資產趨勢、Treemap。
- 月度回報熱力圖、Drawdown curve、已實現 vs 未實現瀑布圖。

## 7. API(FastAPI,Phase 2)

`POST /import/csv`(StockerX 格式,含 validation report)· `POST /transactions`(pre-trade 規則檢查,回傳 violations)· `GET /positions?basis=open_lots|lifetime` · `GET /metrics/summary` · `GET /snapshots` · `POST /assets_other` · `POST /advisor/ask`(Phase 3,組合快照+行為背景 → Claude,回覆掛「唔構成投資建議」)

## 8. 測試 fixtures(用業主真數,必須全 pass)

1. 領展 2019:5000股 買82 沽82.1,fees 749.07+754.08 → 淨 **−1,003**(手續費致虧)
2. TSLA 全史 FIFO 後 open = **860 股,平均成本 341.6562**
3. MU:先 20股@1075均價 沽@1202(已實現 +2,740 USD... 以逐 lot 算),之後 20股@1050 open;「現時持倉收益」以 979.30 計 = **−1,414 USD**,同 lifetime 分開
4. NVDA $0 送股 1.35297 股:cost=0,唔准除以零
5. MRVL 零股零價行:import 要 reject 並列入 validation report
6. 9888.HK open = 2400 股,avg 140.9167
7. 全史已實現(24 回合)≈ HKD +300,441(FX 7.8),勝率 91.7%,賺賠比 2.37
8. 股息累計:全歷史 HKD **242,797**(44 筆);按標的:0941.HK=117,536、0883.HK=71,100、3416.HK=22,660、2802.HK=20,464、0823.HK=7,273、0001.HK=3,720、3466.HK=44。0941.HK 含息總回報必須 = 價差未實現 +134,850 + 股息 117,536 = **+252,386**(同 StockerX 截圖吻合)

## 9. 里程碑(建議 4 個 sprint)

| Sprint | 交付 |
|---|---|
| 1 | Schema + CSV importer(validation)+ FIFO 引擎 + §8 測試全綠 |
| 2 | yfinance price/fx pipeline + snapshots + 核心指標 + Streamlit 總覽/持倉頁 |
| 3 | 規則引擎 + 行為儀表板 + 全資產模組 |
| 4 | 報表全套 + /advisor + Docker Compose 打包 |

## 10. 明確唔做(scope 外)

實時串流報價、自動落單/券商寫入、沽空/槓桿/期權、多用戶。
