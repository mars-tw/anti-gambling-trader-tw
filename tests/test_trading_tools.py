"""券商層 / 圖表層 / 腳架產生器的單元測試。

執行: python tests/test_trading_tools.py
或    python -m pytest tests/test_trading_tools.py -v
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.broker import (  # noqa: E402
    Order,
    OrderResult,
    OrderSide,
    OrderType,
    PaperBroker,
    list_brokers,
)
from core.broker.base import BrokerAdapter  # noqa: E402
from core.charts import build_preview_page, get_chart_lib, list_chart_libs  # noqa: E402
from core.scaffold import ScaffoldOptions, generate_project  # noqa: E402
from core.scaffold.generator import write_project  # noqa: E402
from core.verdict.judge import judge  # noqa: E402
from core.models import Market, Side, Trade, TradeLog  # noqa: E402
from datetime import datetime  # noqa: E402


# ── PaperBroker ──────────────────────────────────────────────
def test_paper_broker_buy_sell_cycle():
    b = PaperBroker(cash=100_000)
    b.connect()
    b.set_price("AAPL", 200.0)
    r = b.place_order(Order("AAPL", OrderSide.BUY, 100))
    assert r.ok
    assert b.get_positions()[0].quantity == 100
    b.set_price("AAPL", 220.0)
    # 帳戶權益應反映漲價
    assert b.get_account().equity > 100_000
    r2 = b.place_order(Order("AAPL", OrderSide.SELL, 100))
    assert r2.ok
    # 平倉後賺錢(扣成本仍應 > 起始)
    assert b.get_account().cash > 100_000


def test_paper_broker_insufficient_funds():
    b = PaperBroker(cash=1000)
    b.set_price("AAPL", 200.0)
    r = b.place_order(Order("AAPL", OrderSide.BUY, 100))  # 需 20000,只有 1000
    assert not r.ok
    assert "資金不足" in r.message


def test_paper_broker_requires_price():
    b = PaperBroker()
    try:
        b.get_price("UNKNOWN")
        assert False, "應該要拋出缺報價錯誤"
    except ValueError:
        pass


def test_order_validation():
    try:
        Order("X", OrderSide.BUY, 0).validate()
        assert False
    except ValueError:
        pass
    try:
        Order("X", OrderSide.BUY, 1, OrderType.LIMIT).validate()  # 限價單缺 limit_price
        assert False
    except ValueError:
        pass


# ── 安全閘門 ─────────────────────────────────────────────────
class _FakeLive(BrokerAdapter):
    name = "fake"
    is_live = True

    def connect(self): pass
    def get_account(self): pass
    def get_positions(self): return []
    def get_price(self, s): return 1.0
    def place_order(self, o):
        self._guard_live()
        return OrderResult(ok=True)
    def cancel_order(self, i): return False


def test_live_guard_blocks_unconfirmed():
    b = _FakeLive()
    try:
        b.place_order(Order("X", OrderSide.BUY, 1))
        assert False, "未確認時真實下單應被擋下"
    except PermissionError:
        pass


def test_confirm_requires_explicit_flag():
    b = _FakeLive()
    try:
        b.confirm_live_trading()
        assert False
    except PermissionError:
        pass
    b.confirm_live_trading(i_understand_the_risk=True)
    # 確認後應放行
    assert b.place_order(Order("X", OrderSide.BUY, 1)).ok


def test_paper_broker_is_not_live():
    assert PaperBroker().is_live is False


# ── 圖表 ─────────────────────────────────────────────────────
def test_chart_libs_registered():
    keys = {lib.key for lib in list_chart_libs()}
    assert keys == {"lightweight", "plotly", "mplfinance", "echarts"}
    for lib in list_chart_libs():
        assert "def render(" in lib.module_code


def test_chart_preview_generates():
    with tempfile.TemporaryDirectory() as d:
        out = build_preview_page(str(Path(d) / "preview.html"))
        content = Path(out).read_text(encoding="utf-8")
        assert "lightweight" in content
        assert "echarts" in content
        assert len(content) > 5000


def test_brokers_registered():
    keys = {t.key for t in list_brokers()}
    assert keys == {"binance", "ibkr", "alpaca", "shioaji"}


# ── 腳架產生器 ───────────────────────────────────────────────
def test_scaffold_generates_all_files():
    opts = ScaffoldOptions(project_name="t", broker="binance", chart="plotly")
    files = generate_project(opts)
    rels = {f.relpath for f in files}
    assert "main.py" in rels
    assert "strategy.py" in rels
    assert "charting.py" in rels
    assert "brokers/binance_broker.py" in rels
    assert "README.md" in rels


def test_scaffold_paper_has_no_broker_template_file():
    opts = ScaffoldOptions(project_name="t", broker="paper")
    rels = {f.relpath for f in generate_project(opts)}
    # paper 模式不應產生真實券商範例檔
    assert not any(r.startswith("brokers/") for r in rels)


def test_scaffold_discouraged_disables_live():
    # 用負期望交易紀錄產生裁決
    trades = [
        Trade("X", Market.US_STOCK, Side.LONG, datetime(2024, 1, 1),
              datetime(2024, 1, 2), 100, 100, 10, pnl=(-50 if i % 4 else 20))
        for i in range(40)
    ]
    verdict = judge(TradeLog(trades), n_bootstrap=500)
    assert verdict.should_discourage

    opts = ScaffoldOptions(project_name="t", verdict=verdict)
    config = next(f for f in generate_project(opts) if f.relpath == "config.example.yaml")
    assert "allow_live_trading: false" in config.content


def test_scaffold_writes_runnable_project():
    """產出的專案應能寫入磁碟,且 main.py 含安全開關。"""
    with tempfile.TemporaryDirectory() as d:
        opts = ScaffoldOptions(project_name="bot", broker="paper", chart="lightweight")
        root = write_project(opts, d)
        assert (root / "main.py").exists()
        main_src = (root / "main.py").read_text(encoding="utf-8")
        assert "ALLOW_LIVE_TRADING = False" in main_src
        # 圖表模組應含 render
        assert "def render(" in (root / "charting.py").read_text(encoding="utf-8")


if __name__ == "__main__":
    import traceback

    _this = sys.modules[__name__]
    fns = [
        v for k, v in sorted(globals().items())
        if k.startswith("test_") and callable(v)
        and getattr(v, "__module__", None) == _this.__name__
    ]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
