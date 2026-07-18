# Portfolio System — Sprint 3(規則引擎 + 行為儀表板 + 全資產)

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
python -m pytest tests/ -v        # 必須 31 passed
```

## 俾 Opus 嘅指示
1. 任何改動都要保持 8 條 fixtures 全綠(regression 基準)。
2. 下一步 Sprint 2:yfinance price/fx pipeline、snapshots_daily、XIRR(由交易現金流推,唔准依賴入金記錄)、Streamlit 總覽/持倉頁 — 詳見 SPEC §5-§6。
3. UI 口徑鐵律:「現時持倉收益」同 lifetime 收益永遠分開顯示,唔准相加做單一數字。

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

## Sprint 4 待做(接手指示)
- 報表補完:收益頁(§6.2 詳細收益 + 基準疊加)、Treemap、淨資產趨勢圖
- FastAPI 層(§7 endpoints)+ `/advisor`(Anthropic API,組合快照入 prompt)
- fx_mode: historical(yfinance USDHKD=X backfill)
- Docker Compose 打包(db + api + ui)
- 規則引擎 nightly cron(scan_portfolio + record_violations 已 dedupe,直接排程得)
