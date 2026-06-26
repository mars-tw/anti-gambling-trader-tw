"""專案腳架產生器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..broker.registry import BROKER_TEMPLATES
from ..charts.registry import get_chart_lib
from ..verdict.judge import Verdict
from . import templates as T


@dataclass
class ScaffoldOptions:
    """產生個人交易程式專案的選項。"""

    project_name: str = "my_trading_bot"
    broker: str = "paper"               # paper | binance | ibkr | alpaca | shioaji
    chart: str = "lightweight"          # lightweight | plotly | mplfinance | echarts
    market: str = "us_stock"            # tw_stock | us_stock | crypto
    symbols: list[str] = field(default_factory=lambda: ["AAPL"])
    verdict: Optional[Verdict] = None   # 若有分析過交易紀錄,帶入裁決以嵌入安全閘門


@dataclass
class GeneratedFile:
    relpath: str
    content: str


def generate_project(opts: ScaffoldOptions) -> list[GeneratedFile]:
    """產生整個專案的檔案清單(尚未寫入磁碟)。"""
    files: list[GeneratedFile] = []

    chart_lib = get_chart_lib(opts.chart)
    broker_tmpl = BROKER_TEMPLATES.get(opts.broker)  # paper 不在註冊表中,為 None

    # 裁決:決定真實下單預設是否禁用
    discouraged = bool(opts.verdict and opts.verdict.should_discourage)
    verdict_level = opts.verdict.level.value if opts.verdict else "unknown"
    verdict_headline = opts.verdict.headline if opts.verdict else "(尚未分析交易紀錄)"

    # ── README ──
    files.append(GeneratedFile("README.md", T.readme(opts, chart_lib, broker_tmpl,
                                                     discouraged, verdict_headline)))

    # ── 設定檔 ──
    files.append(GeneratedFile("config.example.yaml",
                               T.config_yaml(opts, discouraged, verdict_level)))

    # ── 依賴 ──
    files.append(GeneratedFile("requirements.txt",
                               T.requirements(chart_lib, broker_tmpl)))

    # ── 策略檔(進出場規則待填,含安全閘門)──
    files.append(GeneratedFile("strategy.py",
                               T.strategy_py(opts, discouraged, verdict_level,
                                             verdict_headline)))

    # ── 券商接入 ──
    files.append(GeneratedFile("broker_setup.py",
                               T.broker_setup_py(opts, broker_tmpl)))
    if broker_tmpl is not None:
        # 把選定券商的範例框架也寫進專案,方便交易者填寫
        files.append(GeneratedFile(f"brokers/{broker_tmpl.key}_broker.py",
                                   broker_tmpl.code))
        files.append(GeneratedFile("brokers/__init__.py", ""))

    # ── 圖表模組 ──
    files.append(GeneratedFile("charting.py", chart_lib.module_code))

    # ── 主程式(預設紙上模擬)──
    files.append(GeneratedFile("main.py",
                               T.main_py(opts, chart_lib, broker_tmpl, discouraged)))

    # ── 資料餵送(回測 / 即時的統一介面)──
    files.append(GeneratedFile("data_feed.py", T.data_feed_py(opts)))

    # ── .gitignore(避免把金鑰、個人資料上傳)──
    files.append(GeneratedFile(".gitignore", T.gitignore()))

    return files


def write_project(opts: ScaffoldOptions, dest_dir: str | Path) -> Path:
    """產生專案並寫入指定目錄。回傳專案根目錄路徑。"""
    root = Path(dest_dir) / opts.project_name
    files = generate_project(opts)
    for f in files:
        target = root / f.relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.content, encoding="utf-8")
    return root
