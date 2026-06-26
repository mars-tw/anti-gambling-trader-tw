"""命令列介面。

用法:
    python -m core.cli analyze trades.csv
    python -m core.cli analyze trades.csv --market tw_stock --framework vectorbt
    python -m core.cli analyze trades.csv --json out.json --strategy strat.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyzer import analyze_file
from .models import Market


def _force_utf8_stdout() -> None:
    """確保中文與 emoji 在各平台終端機都能正確輸出(尤其 Windows cp950)。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="anti-gambling-trader",
        description="反詐投資王 — 用統計學判斷你的交易是優勢還是賭博",
    )
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("analyze", help="分析一份交易紀錄")
    a.add_argument("file", help="交易紀錄檔(.csv / .json / .xlsx)")
    a.add_argument(
        "--market",
        choices=[m.value for m in Market],
        default=None,
        help="若所有交易同屬一個市場,可指定以提升準確度",
    )
    a.add_argument(
        "--framework",
        choices=["backtrader", "vectorbt", "generic"],
        default="backtrader",
        help="產生策略骨架的框架(預設 backtrader)",
    )
    a.add_argument(
        "--no-cost-estimate",
        action="store_true",
        help="不要自動估算手續費/稅(不建議:忽略成本會高估獲利)",
    )
    a.add_argument("--json", metavar="PATH", help="把結構化結果寫成 JSON 檔")
    a.add_argument("--strategy", metavar="PATH", help="把策略骨架寫成 .py 檔")
    a.add_argument(
        "--bootstrap", type=int, default=5000, help="bootstrap 重抽次數(預設 5000)"
    )

    # ── scaffold:產生個人交易程式專案 ──
    s = sub.add_parser("scaffold", help="產生一套屬於你的交易程式專案")
    s.add_argument("--name", default="my_trading_bot", help="專案名稱")
    s.add_argument(
        "--broker",
        choices=["paper", "binance", "ibkr", "alpaca", "shioaji"],
        default="paper",
        help="券商(預設 paper 紙上模擬,不碰真錢)",
    )
    s.add_argument(
        "--chart",
        choices=["lightweight", "plotly", "mplfinance", "echarts"],
        default="lightweight",
        help="圖表庫(用 chart-preview 先看樣式)",
    )
    s.add_argument(
        "--market",
        choices=[m.value for m in Market],
        default="us_stock",
        help="主要市場",
    )
    s.add_argument(
        "--symbols", default="AAPL", help="標的代號,逗號分隔(如 AAPL,MSFT)"
    )
    s.add_argument(
        "--from-analysis",
        metavar="PATH",
        help="先分析這份交易紀錄,把裁決嵌入專案(勸退時預設禁用真實下單)",
    )
    s.add_argument("--out", default=".", help="專案輸出目錄(預設目前目錄)")

    # ── chart-preview:產生圖表樣式預覽 ──
    cp = sub.add_parser("chart-preview", help="產生四種圖表庫的樣式預覽 HTML")
    cp.add_argument("--out", default="chart_preview.html", help="輸出 HTML 路徑")

    # ── brokers / charts:列出可用選項 ──
    sub.add_parser("brokers", help="列出可接入的券商範例框架")
    sub.add_parser("charts", help="列出可用的開源圖表庫")

    return p


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    args = _build_parser().parse_args(argv)

    if args.command == "analyze":
        market_hint = Market(args.market) if args.market else None
        try:
            result = analyze_file(
                args.file,
                market_hint=market_hint,
                framework=args.framework,
                auto_estimate_costs=not args.no_cost_estimate,
                n_bootstrap=args.bootstrap,
            )
        except (ValueError, FileNotFoundError, ImportError) as exc:
            print(f"錯誤: {exc}", file=sys.stderr)
            return 1

        # 終端機輸出完整報告
        print(result.text_report)

        if args.json:
            Path(args.json).write_text(
                json.dumps(result.as_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"\n[已輸出 JSON 結果] {args.json}")

        if args.strategy:
            Path(args.strategy).write_text(result.strategy_code, encoding="utf-8")
            print(f"[已輸出策略骨架] {args.strategy}")
            if result.verdict.should_discourage:
                print(
                    "  注意:由於裁決為『應勸退』,骨架中的安全閘門預設為啟用 — "
                    "執行時會中止,逼你先把策略驗證好。"
                )

        # 以裁決結果決定 exit code:勸退時回傳非 0,方便腳本判斷
        return 2 if result.verdict.should_discourage else 0

    if args.command == "scaffold":
        return _cmd_scaffold(args)

    if args.command == "chart-preview":
        from .charts import build_preview_page

        out = build_preview_page(args.out)
        print(f"✅ 圖表樣式預覽已產生:{out}")
        print("   用瀏覽器打開,挑一個你喜歡的樣式,再用 scaffold --chart <key> 產生專案。")
        return 0

    if args.command == "brokers":
        from .broker import list_brokers

        print("可接入的券商範例框架(scaffold --broker <key>):\n")
        print(f"  {'paper':10s} 紙上模擬 PaperBroker(預設,不碰真錢,完整可用)")
        for t in list_brokers():
            print(f"  {t.key:10s} {t.name}  [{t.market}]")
            print(f"  {'':10s}   安裝: {t.sdk_install}")
        return 0

    if args.command == "charts":
        from .charts import list_chart_libs

        print("可用的開源圖表庫(scaffold --chart <key>):\n")
        for lib in list_chart_libs():
            print(f"  {lib.key:12s} {lib.name}  [{lib.license}, {lib.kind}]")
            print(f"  {'':12s}   {lib.blurb}")
        return 0

    return 0


def _cmd_scaffold(args) -> int:
    """處理 scaffold 指令:產生個人交易程式專案。"""
    from .scaffold import ScaffoldOptions
    from .scaffold.generator import write_project

    verdict = None
    if args.from_analysis:
        try:
            result = analyze_file(args.from_analysis)
            verdict = result.verdict
            print(f"[已分析交易紀錄] 裁決:{verdict.headline}")
            if verdict.should_discourage:
                print("  → 專案將預設禁用真實下單,逼你先把策略驗證好。\n")
        except (ValueError, FileNotFoundError, ImportError) as exc:
            print(f"警告:分析交易紀錄失敗({exc}),改以無裁決方式產生。", file=sys.stderr)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    opts = ScaffoldOptions(
        project_name=args.name,
        broker=args.broker,
        chart=args.chart,
        market=args.market,
        symbols=symbols or ["AAPL"],
        verdict=verdict,
    )
    root = write_project(opts, args.out)
    print(f"✅ 個人交易程式專案已產生:{root}\n")
    print("下一步:")
    print(f"  cd {root}")
    print("  pip install -r requirements.txt")
    print("  python main.py            # 先用紙上模擬跑一遍(不碰真錢)")
    print("\n提醒:")
    print("  - 在 strategy.py 填入你的進出場規則(寫不出明確規則,本身就是警訊)。")
    print("  - 要接真實券商,填 brokers/ 下的範例框架,並承擔風險自負。")
    print("  - 真實下單預設被安全閘門封鎖,需明確解除。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
