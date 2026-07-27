#!/usr/bin/env bash
# 一鍵喺 VPS 裝好 Investment Committee(app + bot 共用一個 portfolio.db)。
# 用法(喺 VPS SSH 入面):
#   curl -sL https://raw.githubusercontent.com/jasonwong318/financial-freedom/main/portfolio_system/setup_vps.sh | bash
# 或者 clone 咗之後:  bash setup_vps.sh
set -e

echo "==> 1/4 裝 Python + git"
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip git

echo "==> 2/4 攞 code(冇就 clone,有就 pull)"
cd ~
if [ -d financial-freedom ]; then
  cd financial-freedom && git pull
else
  git clone https://github.com/jasonwong318/financial-freedom.git
  cd financial-freedom
fi
cd portfolio_system

echo "==> 3/4 裝依賴"
# 新版 Ubuntu(PEP 668)會擋 system-wide pip;失敗就加 --break-system-packages 重試
pip3 install -r requirements.txt \
  || pip3 install --break-system-packages -r requirements.txt

echo "==> 4/4 建 portfolio.db(冇就自動匯入預設 CSV)"
python3 - <<'PY'
from app.models import make_session, Transaction
from app.importer import import_stockerx_csv
from app.fifo import rebuild_lots
from app import rules, buckets
s = make_session("sqlite:///portfolio.db")
if not s.query(Transaction).first():
    import_stockerx_csv(s, "tests/data/Stock-20260711.csv")
    rebuild_lots(s)
rules.seed_rules(s)
buckets.assign_defaults(s)
print("portfolio.db 已就緒")
PY

echo ""
echo "======================================================================"
echo "  裝好喇!目錄:$(pwd)"
echo ""
echo "  起 App(睇/記數據,127.0.0.1 只俾 SSH tunnel):"
echo "    python3 -m streamlit run ui/app.py --server.address 127.0.0.1 --server.port 8501"
echo ""
echo "  起 Telegram bot(換返你自己嘅 token / chat_id,注意冇反引號!):"
echo "    export TELEGRAM_BOT_TOKEN=你的token"
echo "    export TELEGRAM_CHAT_ID=你的chat_id"
echo "    export PORTFOLIO_DB_URL=sqlite:///portfolio.db"
echo "    python3 -m bot.telegram_bot"
echo ""
echo "  喺 PC PowerShell 開 tunnel 睇 app:"
echo "    ssh -i key.key -L 8501:127.0.0.1:8501 ubuntu@<VPS_IP>"
echo "    然後 Chrome 開 http://localhost:8501"
echo "======================================================================"
