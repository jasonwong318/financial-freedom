# Portfolio System — Sprint 1 初稿(已通過全部 fixtures)

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
python -m pytest tests/ -v        # 必須 8 passed
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

## Sprint 3 待做(Opus 接手)
規則引擎(§4 十條)、行為儀表板、全資產頁(Percento 式)、月度熱力圖/回撤曲線
