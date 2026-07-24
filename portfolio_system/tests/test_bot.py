"""Telegram 守門 bot 核心測試(全離線,唔撞網絡)。"""
import os
import pytest

from app.models import make_session
from app.importer import import_stockerx_csv
from app.fifo import rebuild_lots
from app import rules, buckets, assets
from bot import agent

from tests.test_sprint2 import PX_20260710

CSV = os.path.join(os.path.dirname(__file__), "data", "Stock-20260711.csv")


@pytest.fixture(scope="module")
def session():
    s = make_session()
    import_stockerx_csv(s, CSV)
    rebuild_lots(s)
    rules.seed_rules(s)
    buckets.assign_defaults(s)
    return s


# ---- 自然語言解析 ----
def test_parse_qty_price():
    it = agent.parse_trade("買 TSLA 100 @ 407")
    assert it["side"] == "BUY" and it["symbol"] == "TSLA"
    assert it["qty"] == 100 and it["price"] == 407


def test_parse_amount_chinese_name():
    it = agent.parse_trade("我想用 5萬 買 中移動")
    assert it["side"] == "BUY" and it["symbol"] == "0941.HK"
    assert it["amount"] == pytest.approx(50000)


def test_parse_sell_hk():
    it = agent.parse_trade("沽 9988.HK 500")
    assert it["side"] == "SELL" and it["symbol"] == "9988.HK" and it["qty"] == 500


def test_parse_none():
    assert agent.parse_trade("今日天氣好好") is None


# ---- 裁決:買核心 TSLA 大注 → 應該彈規則 ----
def test_evaluate_flags(session):
    it = agent.parse_trade("買 TSLA 100 @ 407")
    msg = agent.evaluate(session, PX_20260710, it, total_assets=6_000_000)
    assert "MAX_POSITION_WEIGHT" in msg or "MAX_SINGLE_ENTRY" in msg
    assert "核心信仰倉" in msg


def test_evaluate_amount_uses_price(session):
    it = agent.parse_trade("用 5萬 買 中移動")
    msg = agent.evaluate(session, PX_20260710, it, total_assets=6_000_000)
    assert "中移動" in msg and "0941.HK" in msg      # 用現價換到股數,計到裁決


def test_evaluate_missing_price(session):
    it = agent.parse_trade("買 ZZZZ 100")
    msg = agent.evaluate(session, {}, it)
    assert "冇" in msg and "價" in msg               # 冇價 → 叫用戶補


# ---- 倉位狀態 ----
def test_portfolio_status(session):
    txt = agent.portfolio_status(session, PX_20260710)
    assert "四大倉位配置" in txt
    assert "核心信仰倉" in txt and "衛星" in txt
