# Portfolio System — Sprint 4(收益頁 + 基準 + 歷史匯率 + FastAPI + AI顧問 + Docker)

取代 StockerX 嘅自建績效追蹤系統。完整需求見 `SPEC_portfolio_system.md`(另附)。

## 而家有咩
- `app/models.py` — 完整 schema(SQLAlchemy;開發用 SQLite,正式換 Postgres URL 即可)
- `app/importer.py` — StockerX CSV 匯入 + validation report(壞行擋落唔入庫)
- `app/fifo.py` — FIFO 引擎:lots + lot_closures,冪等全量重建
- `app/metrics.py` — 三口徑分離:現時持倉收益(open lots only)/已實現(回合)/股息
- `app/config.py` — 匯率單一來源(固定 7.80;Phase 2 切歷史匯率)
- `tests/test_fixtures.py` — 8 條真數 fixtures,**全部由 StockerX 畫面逆向對數得出**

## 跑測試
```bash
pip install -r requirements.txt
python -m pytest tests/ -v        # 必須 47 passed
```

## 跑起成套(三種方式)
```bash
# 1. 本地 Streamlit UI(SQLite,首次自動匯入 CSV)
streamlit run ui/app.py

# 2. 本地 API
uvicorn api.main:app --reload        # http://localhost:8000/docs

# 3. 全套 Docker(db + api + ui,規格書 §1 本地優先部署)
docker compose up --build            # UI:8501 · API:8000 · Postgres:5432
# AI 顧問要 key:喺同層放 .env 寫 ANTHROPIC_API_KEY=sk-...(冇就離線降級)
```

## 接手鐵律
1. 任何改動都要保持全部 47 條 fixtures 全綠(regression 基準,真數逆向對數得出)。
2. UI/API 口徑鐵律:「現時持倉收益」同 lifetime 收益永遠分開顯示,唔准相加做單一數字。
3. 匯率折算一律經 `config.to_hkd()`,唔准喺其他地方寫死;報表要用 `config.fx_note()` 註明口徑。
4. transactions 係 immutable audit trail,更正用沖銷,唔准 UPDATE 原始行;改完 call `rebuild_lots()`。

## Sprint 2 已完成(本稿)
- `app/prices.py` — Provider 制:YFinanceProvider(正式)/ManualPriceProvider(離線對數)
- `app/performance.py` — XIRR(交易現金流推導,免入金記錄)、snapshots、TWRR(鏈式)
- `ui/app.py` — Streamlit 三頁:總覽(KPI+集中度超標警示)/持倉(雙軌口徑分欄)/已平倉
- 測試 15 條全綠;**組合 XIRR 基準:6.91% 年化 @2026-07-10**(regression 釘死)

## Sprint 3 已完成(本稿)
- `app/rules.py` — §4 十條規則齊:pre-trade check_trade() / 狀態 scan_portfolio() /
  歷史 scan_history();違規寫 rule_violations(dedupe,nightly 重掃唔會疊加)
- `app/behavior.py` — 雙軌勝率(已實現 91.7% vs 真實 65.1%)、處置效應監測、
  違規成本(真數基準:HKD 155,257,主要嚟自 XYZ/9888 溝貨)
- `app/assets.py` — Percento 式四區塊(流動資金/投資/固定資產/負債+應收),
  月度快照式輸入,歷史值永不改寫;total_assets_hkd() 做規則引擎分母
- `app/reports.py` — 月度已實現+股息熱力圖(同總數對數)、NAV 回撤曲線
- `ui/app.py` — 加四頁:新增交易(pre-trade 警示卡,可 override 但記錄在案)/
  行為儀表板 / 全資產 / 報表
- 測試 31 條全綠;真數規則基準釘死:FEE_CHECK 只中領展一單(−1,003)、
  TSLA 2025 年溝貨 8 次、XYZ/GME STALE_LOSER、TSLA 集中度 50.8%

## Sprint 4 已完成(本稿)
- `app/income.py` — 收益頁(§6.2)+ 股息三口徑(§5):價差未實現 / 含息未實現 /
  yield-on-cost;詳細收益分母口徑可切(現時持倉成本 vs 歷史總投入),UI 明文註明
- `app/benchmark.py` — VOO/^HSI/^IXIC total return 基準(adj_close 口徑,同組合含息對等)
- `app/fx.py` — 歷史匯率模式(§2):USDHKD=X backfill + as-of 二分查表 + 一鍵切換;
  fixed 口徑完全唔受影響(regression 保證)
- `app/advisor.py` — `/advisor`(§7):lot-level 快照 + 行為背景 → Claude,強制掛免責;
  冇 ANTHROPIC_API_KEY 自動離線降級(回事實快照,唔爆)
- `api/main.py` — FastAPI 七個 endpoint(§7):/import/csv · /transactions(pre-trade
  規則檢查,預設擋違規,override=true 先錄) · /positions?basis · /metrics/summary ·
  /snapshots · /assets_other · /advisor/ask
- `ui/app.py` — 加 收益 + AI顧問 兩頁(共九頁);全資產加 Treemap;側欄加匯率口徑切換
- `Dockerfile` + `docker-compose.yml` — db(Postgres 16)+ api + ui 本地一鍵起
- `make_session` 加 SQLite 執行緒安全(check_same_thread=False + StaticPool),配 FastAPI
- 測試 31 → 47 條全綠。收益基準釘死:**0941 含息未實現 = 252,386**(§8 fixture 8 對數)、
  滾動12個月股息 26,350、yield-on-cost 10.2%

## 里程碑狀態
Sprint 1-4 全部交付完成。SPEC §9 四個 sprint 已行完;§10 scope 外項目(實時串流、
自動落單、沽空/期權、多用戶)按約唔做。

## 之後可做(scope 外 / nice-to-have)
- 規則引擎 nightly cron(scan_portfolio + record_violations 已 dedupe,直接排程得)
- Alembic migration(而家靠 create_all;上 Postgres 生產環境建議加)
- 定期項目自動記(糧/供款,SPEC §0.5 標 Phase 3 nice-to-have)
- Next.js 前端(SPEC 標 Phase 3 先考慮)
