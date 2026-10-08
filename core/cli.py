"""命令列介面。

用法:
    python -m core.cli analyze trades.csv
    python -m core.cli analyze trades.csv --market tw_stock --framework vectorbt
    python -m core.cli analyze trades.csv --json out.json --strategy strat.py
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from .analyzer import analyze_file, sanitize_json
from .models import Market


class _ContractArgumentParser(argparse.ArgumentParser):
    """在 argparse 階段拒絕會造成來源優先序不明的 CLI 組合。"""

    def parse_args(self, args=None, namespace=None):
        parsed = super().parse_args(args, namespace)
        if getattr(parsed, "command", None) == "scan-screenshot":
            supplied = (
                bool(parsed.image),
                bool(parsed.text_file),
                parsed.text is not None,
            )
            if sum(supplied) != 1:
                self.error(
                    "scan-screenshot 必須且只能提供一個來源："
                    "IMAGE、--text-file PATH 或 --text TEXT"
                )
        if (
            getattr(parsed, "command", None) == "scan-text"
            and parsed.text is not None
            and parsed.file is not None
        ):
            self.error(
                "scan-text 的文字參數與 --file 不可同時使用；"
                "請擇一，或兩者都省略以讀取 stdin"
            )
        return parsed


def _force_utf8_stdout() -> None:
    """確保中文在各平台終端機正確進出(尤其 Windows cp950)。

    stdin 也必須處理:Windows 上管線餵入的 UTF-8 中文會被 cp950 解碼成亂碼,
    導致 `cat 對話.txt | scan-text` 的詐騙關鍵字**靜默漏抓**(判「低風險」)——
    對一個反詐工具,因編碼漏抓詐騙是最諷刺的失敗。只對非 tty 的 stdin 重設,
    不動互動輸入;errors='replace' 避免真的餵入非 UTF-8 位元組時直接崩潰。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass
    try:
        if sys.stdin is not None and not sys.stdin.isatty():
            reconfigure = getattr(sys.stdin, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


def _paths_alias(left: str | Path, right: str | Path) -> bool:
    """Compare lexical aliases and existing hardlinks without touching content."""

    a = Path(left)
    b = Path(right)
    if a.resolve(strict=False) == b.resolve(strict=False):
        return True
    if a.exists() and b.exists():
        try:
            return a.samefile(b)
        except OSError:
            return False
    return False


def _preflight_file_outputs(
    *,
    inputs: list[str | Path],
    outputs: dict[str, str | Path | None],
) -> None:
    """Reject input/output aliases, duplicate outputs and existing destinations."""

    requested = [
        (label, Path(value))
        for label, value in outputs.items()
        if value not in (None, "")
    ]
    for index, (label, path) in enumerate(requested):
        for other_label, other_path in requested[index + 1:]:
            if _paths_alias(path, other_path):
                raise ValueError(
                    f"輸出路徑衝突: {label} 與 {other_label} 指向同一檔案 {path}"
                )
        for source in inputs:
            if source not in (None, "") and _paths_alias(path, source):
                raise ValueError(
                    f"拒絕覆蓋輸入資料: {label} 的目的地 {path} 與來源 {source} 相同"
                )
        if path.exists() or path.is_symlink():
            raise ValueError(
                f"輸出檔已存在，為避免覆蓋資料已拒絕: {label}={path}"
            )


def _write_new_text(path: str | Path, content: str, *, encoding: str = "utf-8") -> None:
    """Create a new text file atomically with respect to overwrite races."""

    with Path(path).open("x", encoding=encoding, newline="") as handle:
        handle.write(content)


def _broker_choices() -> list[str]:
    """scaffold --broker 的可選值:paper + 註冊表所有券商(動態取得)。"""
    from .broker import BROKER_TEMPLATES
    return ["paper"] + list(BROKER_TEMPLATES.keys())


def _examples_dir() -> Path:
    """用 __file__ 定位 examples/,不依賴使用者的 cwd。"""
    # examples 放在 core/ 套件內並透過 package-data 打包 ——
    # 否則 pip 安裝後 site-packages 上一層沒有 examples/,demo 與 --example 全壞。
    return Path(__file__).resolve().parent / "examples"


# 範例對照:--example 值 → (檔案, 市場)
_EXAMPLES = {
    "tw": ("tw_stock_gambling.csv", Market.TW_STOCK),
    "us": ("us_stock_edge.csv", Market.US_STOCK),
    "crypto": ("crypto_luck.json", Market.CRYPTO),
}


def _example_path(key: str) -> tuple[str, Market]:
    fname, market = _EXAMPLES[key]
    return str(_examples_dir() / fname), market


def _parse_field_overrides(fields: list[str] | None) -> dict[str, str] | None:
    """解析 --field 標準名=欄位名。格式錯誤回傳 None(已印錯誤)。"""
    if not fields:
        return {}
    out: dict[str, str] = {}
    for item in fields:
        if "=" not in item:
            print(
                f"錯誤:--field 格式應為『標準名=你的欄位名』,但收到 {item!r}。\n"
                "  例:--field symbol=代號 --field entry_price=買價",
                file=sys.stderr,
            )
            return None
        std, col = item.split("=", 1)
        out[std.strip()] = col.strip()
    return out


def _analyze_with_overrides(target, market_hint, args, field_overrides):
    """依是否有 field_overrides 選擇載入路徑,回傳 AnalysisResult。"""
    from .analyzer import analyze_log
    from .ingest.loader import load_trades

    if field_overrides:
        log = load_trades(
            target,
            market_hint=market_hint,
            auto_estimate_costs=not args.no_cost_estimate,
            field_overrides=field_overrides,
        )
        return analyze_log(log, framework=args.framework, n_bootstrap=args.bootstrap)
    return analyze_file(
        target,
        market_hint=market_hint,
        framework=args.framework,
        auto_estimate_costs=not args.no_cost_estimate,
        n_bootstrap=args.bootstrap,
    )


def _compute_full_extras(result, equity):
    """--full 健檢:算出趨勢報告與風險情境。

    回傳 (trend_report, scenario, skip_reason):
    scenario 為 None 時 skip_reason 必有值,說明「為什麼略過」——
    這個原因會同步進終端、HTML 與 JSON,三個通道的誠實度必須一致,
    不能終端有解釋、分享出去的 HTML 卻靜默消失。
    """
    from .montecarlo import simulate_ruin_scenario
    from .trend import analyze_trend

    trend_report = analyze_trend(result.log)
    if not getattr(result.metrics, "currency_reliable", False):
        return (
            trend_report,
            None,
            "損益幣別未經確認，無法把 pnl 與起始權益放進同一情境模擬",
        )
    pnls = [t.pnl or 0.0 for t in result.log]
    try:
        scenario = simulate_ruin_scenario(pnls, start_equity=equity)
    except ValueError as exc:
        # 第二層防護(--equity 已在入口驗證):模擬失敗不該炸掉整份報告,
        # 主報告照常輸出,風險區塊誠實標示無法執行的原因。
        return trend_report, None, str(exc)
    if scenario is None:
        return trend_report, None, "交易不足 10 筆 —— 樣本太少,模擬只會給假精準"
    return trend_report, scenario, None


def _render_trend(report) -> str:
    from .trend import render_trend_text
    return render_trend_text(report)


def _render_scenario(s) -> str:
    from .montecarlo import render_scenario
    return render_scenario(s)


def _print_next_steps(result, args, target) -> None:
    """依裁決結果,動態提示使用者接下來能做什麼。"""
    # 提示裡的指令要能直接複製執行:路徑含空白就加引號,否則會被 shell 拆開
    t = str(target)
    quoted = f'"{t}"' if any(ch.isspace() for ch in t) else t
    print("\n" + "─" * 70)
    print("下一步:")
    v = result.verdict
    if v.should_discourage:
        print("  • 先別加碼、別借錢、別重押 — 你的策略還沒通過驗證。")
        if result.follow_guru is not None and result.follow_guru.expectancy < 0:
            print("  • 你有跟單 / 聽明牌的虧損 → 執行 anti-gambling-trader scam-check 檢測是否遇到詐騙。")
    else:
        print(f"  • 想把這套邏輯變成可回測程式?執行 "
              f"anti-gambling-trader scaffold --from-analysis {quoted}")
    # 可發現性:analyze 只是體檢的第一步,主動導流到趨勢與風險情境
    if not getattr(args, "full", False):
        print(f"  • 想看時間趨勢、優勢是否在衰退?執行 anti-gambling-trader trend {quoted}")
        print(f"  • 想模擬「這樣玩下去會不會爆倉」?執行 "
              f"anti-gambling-trader risk-sim {quoted} --equity 你的本金")
        print("  • 或一次看完:analyze 加上 --full(主報告 + 趨勢 + 風險情境)")
    extras = []
    if not args.strategy:
        extras.append("--strategy out.py(產生可回測策略骨架)")
    if not args.json:
        extras.append("--json out.json(存結構化結果)")
    if not args.html and not args.card:
        extras.append("--html 報告.html / --card 圖卡.html(可傳給家人的成品)")
    if extras:
        print(f"  • 本次只顯示報告。可加:{' / '.join(extras)}")
    print("─" * 70)


def _cmd_demo(args) -> int:
    """零參數,跑內建範例立刻看效果。"""
    if getattr(args, "edge", False):
        path, market = _example_path("us")
        intro = "▶ 這是一個『具統計優勢』的範例(美股季線突破策略):"
    else:
        path, market = _example_path("tw")
        intro = "▶ 這是一個『賭博型』的範例(台股當沖追高 + 聽明牌):"
    print(intro)
    print(f"  資料:{path}\n")
    try:
        result = analyze_file(path, market_hint=market)
    except (ValueError, FileNotFoundError) as exc:
        print(f"錯誤:找不到內建範例({exc})。", file=sys.stderr)
        return 1
    print(result.text_report)
    print("\n" + "─" * 70)
    if getattr(args, "edge", False):
        print("想看『賭博型』範例的對照?執行  anti-gambling-trader demo")
    else:
        print("想看『具優勢』範例的對照?執行  anti-gambling-trader demo --edge")
    print("準備好分析自己的資料了?執行  anti-gambling-trader init-template  產生空白範本。")
    print("─" * 70)
    return 0


# 空白 CSV 範本:標準中文欄名 + 兩行範例 + 說明註解
_TEMPLATE_CSV = """\
代號,方向,進場時間,出場時間,進場價,出場價,數量,手續費,損益,損益幣別,策略
# 說明:把下面兩行範例換成你自己的交易。一列 = 一筆「已平倉」交易。
# 最簡單填法:填代號、出場時間、已扣全部成本的淨損益、損益幣別、策略；方向未知可留白。
# 完整填法:填進場價、出場價、數量，損益可留白由工具計算。
# 用價差推算時，方向必須填「買/做多」或「賣/做空」;時間可用 2025-01-03 或 2025/01/03。
# 數量:台股可填股數;若你的欄位是「張」請改欄名為「張數」(會自動 ×1000)。
# 只有用價量推算 pnl 時，手續費留空才會自動估算；直接 pnl 不重複扣費。
2330,買,2025-01-03,2025-02-10,1000,1080,1000,,,TWD,季線突破
2317,買,2025-01-15,2025-01-15,210,205,2000,,,TWD,聽明牌當沖
"""


def _cmd_init_template(args) -> int:
    """產生空白交易紀錄範本。"""
    out = Path(args.out)
    if out.exists():
        print(f"錯誤:{out} 已存在,為避免覆蓋你的資料,請改用 --out 指定別的路徑。",
              file=sys.stderr)
        return 1
    try:
        _write_new_text(out, _TEMPLATE_CSV, encoding="utf-8-sig")
    except OSError as exc:
        print(f"錯誤:無法寫入範本 {out}({exc})", file=sys.stderr)
        return 1
    print(f"✅ 已產生空白交易紀錄範本:{out}\n")
    print("下一步:")
    print(f"  1. 用 Excel 或記事本打開 {out},把範例換成你自己的交易。")
    print(f"  2. 存檔後執行:anti-gambling-trader analyze {out}")
    print("\n提示:以 # 開頭的是說明列,分析時會自動略過,可保留或刪除。")
    return 0


def _cmd_start(_args) -> int:
    """只呈現四條最短路徑，避免新手先讀完整參數表。"""

    print(
        """\
==============================================================
                   反詐投資王｜從這裡開始
==============================================================
你現在手上有什麼？依情況複製下面一條命令：

  0. 想用圖形介面（瀏覽器版或桌面視窗版）
     anti-gambling-trader ui

  1. 有完整交易紀錄
     anti-gambling-trader fit-check 你的交易.csv

  2. 沒有表格，想從今天開始逐筆記
     anti-gambling-trader record

  3. 有券商或圖表截圖
     anti-gambling-trader scan-screenshot 截圖.png

  4. 有 LINE 群組對話或可疑投資訊息
     anti-gambling-trader scan-text --file LINE對話.txt

安全底線：沒有至少 30 筆完整、連續、未挑選的已平倉紀錄前，
本工具只會建議紙上模擬，不會認證你「適合投入真錢」。
"""
    )
    return 0


def _cmd_ui(args) -> int:
    """Lazy-load the optional UI path without changing existing CLI imports."""

    from .ui.launcher import run_ui

    return run_ui(
        mode=args.mode,
        port=args.port,
        no_open=args.no_open,
        ready_file=args.ready_file,
    )


def _cmd_record(args) -> int:
    """逐筆新增交易；沒有帶參數時進入五個短問題的互動輸入。"""

    from .onboarding import (
        append_beginner_row,
        build_beginner_row,
        prompt_beginner_row,
    )

    try:
        if args.symbol:
            row = build_beginner_row(
                symbol=args.symbol,
                pnl=args.pnl,
                side=args.side,
                entry_time=args.entry_time,
                exit_time=args.exit_time,
                entry_price=args.entry_price,
                exit_price=args.exit_price,
                quantity=args.quantity,
                fees=args.fees,
                currency=args.currency,
                strategy=args.strategy,
            )
        else:
            row = prompt_beginner_row()
        out, count = append_beginner_row(args.out, row)
    except (ValueError, OSError, EOFError, KeyboardInterrupt) as exc:
        print(f"錯誤: {exc}", file=sys.stderr)
        return 1

    print(f"✅ 已記錄第 {count} 筆已平倉交易: {out}")
    out_arg = f'"{out}"' if any(ch.isspace() for ch in str(out)) else str(out)
    if not row.get("策略"):
        print("  提醒:這筆沒有進場理由；工具之後無法辨識是哪套交易方法。")
    if count < 30:
        print(f"  還差 {30 - count} 筆才到最低判讀門檻；現在不要用勝率判斷自己有沒有本事。")
        print("  繼續記錄: anti-gambling-trader record --out " + out_arg)
    else:
        print("  已達最低筆數，可執行: anti-gambling-trader fit-check " + out_arg)
    return 0


def _cmd_fit_check(args) -> int:
    """用實際紀錄做快速階段分流，不用自我感覺問卷。"""

    from .onboarding import render_stage, stage_from_analysis

    target = args.file
    market_hint = Market(args.market) if args.market else None
    if args.example:
        target, market_hint = _example_path(args.example)
    if not target:
        print(
            "請提供交易紀錄，例如: anti-gambling-trader fit-check my_trades.csv\n"
            "還沒有紀錄?先執行 anti-gambling-trader record。",
            file=sys.stderr,
        )
        return 1
    try:
        result = analyze_file(
            target,
            market_hint=market_hint,
            n_bootstrap=args.bootstrap,
        )
    except (ValueError, FileNotFoundError, ImportError) as exc:
        print(f"錯誤: {exc}", file=sys.stderr)
        return 1
    assessment = stage_from_analysis(result)
    print(render_stage(assessment))
    print(
        f"\n核心依據: {result.metrics.total_trades} 筆 / "
        f"每筆期望值 {result.metrics.expectancy:,.2f} / "
        f"裁決 {result.verdict.level.value}"
    )
    target_arg = f'"{target}"' if any(ch.isspace() for ch in str(target)) else str(target)
    print(f"完整體檢: anti-gambling-trader analyze {target_arg} --full --equity 你的本金")
    return assessment.exit_code


def _cmd_scan_screenshot(args) -> int:
    """辨識截圖中的交易欄位與策略文字線索；低信心值絕不自動採用。"""

    from .ingest.beginner import build_beginner_form, build_review_questions
    from .ingest.screenshot import (
        OCRUnavailableError,
        STRATEGY_NOTICE,
        parse_ocr_text,
        parse_screenshot_image,
    )

    supplied = [bool(args.image), bool(args.text_file), args.text is not None]
    if sum(supplied) != 1:
        print(
            "錯誤:請擇一提供圖片、--text-file OCR文字檔，或 --text OCR文字。",
            file=sys.stderr,
        )
        return 1
    input_paths = [path for path in (args.image, args.text_file) if path]
    try:
        _preflight_file_outputs(
            inputs=input_paths,
            outputs={"--json": args.json},
        )
    except (ValueError, OSError) as exc:
        print(f"錯誤: {exc}", file=sys.stderr)
        return 1
    try:
        if args.image:
            result = parse_screenshot_image(
                args.image,
                language=args.language,
                acceptance_threshold=args.threshold,
            )
        else:
            text_input = args.text
            if args.text_file:
                text_input = _read_file_text(args.text_file)
                if text_input is None:
                    return 1
            result = parse_ocr_text(
                text_input or "",
                acceptance_threshold=args.threshold,
                source=(f"text:{Path(args.text_file).name}" if args.text_file else "inline_text"),
            )
    except (ValueError, FileNotFoundError, OCRUnavailableError) as exc:
        print(f"錯誤: {exc}", file=sys.stderr)
        if args.image and isinstance(exc, OCRUnavailableError):
            print(
                "替代方式:先用手機/系統 OCR 複製文字，再執行 "
                "anti-gambling-trader scan-screenshot --text \"貼上的文字\"。",
                file=sys.stderr,
            )
        return 1

    form = build_beginner_form(result)
    print("=" * 66)
    print("                 交易截圖辨識｜逐欄覆核")
    print("=" * 66)
    print("【可先帶入的候選】（仍要對照原圖，不會自動存成交易）")
    safe_fields = [field for field in form if field.status == "auto_filled"]
    if safe_fields:
        unit_labels = {
            "share": "股", "lot": "張", "contract": "口", "asset": "幣/單位",
            "quantity": "單位", "currency": "元/帳戶幣別", "percent": "%",
            "price": "", "datetime": "",
        }
        for field in safe_fields:
            shown_value = {"long": "做多", "short": "做空"}.get(
                str(field.value), field.value
            )
            localized_unit = unit_labels.get(field.unit or "", field.unit or "")
            unit = f" {localized_unit}" if localized_unit else ""
            print(f"  • {field.label}: {shown_value}{unit}")
            print(f"      證據: {field.evidence}")
    else:
        print("  沒有足夠可靠的欄位可先帶入。")

    review = build_review_questions(result)
    if review:
        print("\n【需要你確認】")
        for index, question in enumerate(review, 1):
            required = "必填" if question.required else "建議"
            print(f"  {index}. [{required}] {question.prompt}")
            print(f"      為什麼要問: {question.reason}")

    print("\n【交易技術文字線索】")
    if result.strategy_clues:
        for clue in result.strategy_clues:
            print(f"  • {clue.label}:「{clue.evidence}」")
    else:
        print("  沒有辨識到可解釋的技術文字；不能只看圖形替你猜策略。")
    print(f"  注意: {STRATEGY_NOTICE}")

    if result.warnings:
        print("\n【辨識警告】")
        for warning in result.warnings:
            print(f"  • {warning}")
    print("\n單張截圖不能證明你適合交易，也不能驗證績效；請保留完整連續紀錄。")
    print(
        "確認原圖後，可用這些已核對值執行 anti-gambling-trader record；"
        "不要直接把 OCR JSON 當成已確認交易。"
    )

    if args.json:
        payload = {
            "source": result.source,
            "needs_manual_review": result.needs_manual_review,
            "requires_human_review": result.requires_human_review,
            "missing_required_fields": list(result.missing_required_fields),
            "safe_fields": {
                name: {
                    "value": candidate.candidate_value,
                    "unit": candidate.unit,
                    "confidence": candidate.confidence,
                    "evidence": candidate.evidence,
                    "derived": candidate.derived,
                }
                for name, candidate in result.selected_fields.items()
            },
            "review_questions": [
                {
                    "field": question.field,
                    "prompt": question.prompt,
                    "reason": question.reason,
                    "required": question.required,
                    "choices": list(question.choices),
                }
                for question in review
            ],
            "strategy_clues": [
                {
                    "code": clue.code,
                    "label": clue.label,
                    "evidence": clue.evidence,
                    "confidence": clue.confidence,
                }
                for clue in result.strategy_clues
            ],
            "warnings": list(result.warnings),
            "strategy_notice": STRATEGY_NOTICE,
            "confidence_notice": (
                "confidence 是文字抽取規則分數，不是 OCR 正確率、"
                "策略勝率或詐騙機率。"
            ),
        }
        try:
            _write_new_text(
                args.json,
                json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
            )
        except OSError as exc:
            print(f"錯誤:無法寫入 --json {args.json}({exc})", file=sys.stderr)
            return 1
        print(f"[已輸出待覆核 JSON] {args.json}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = _ContractArgumentParser(
        prog="anti-gambling-trader",
        description="反詐投資王 — 用統計學判斷你的交易是優勢還是賭博",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # ── start:新手唯一入口 ──
    sub.add_parser("start", help="第一次使用?依你手上的資料選最短路徑")

    ui = sub.add_parser("ui", help="啟動本機新手圖形工作台（瀏覽器或桌面視窗）")
    ui.add_argument(
        "--mode",
        choices=["choose", "browser", "desktop"],
        default="choose",
        help="預設 choose；也可直接指定 browser 或 desktop",
    )
    ui.add_argument("--port", type=int, default=0, help="本機連接埠；0 為自動選擇")
    ui.add_argument(
        "--no-open",
        action="store_true",
        help="瀏覽器模式只印本機網址，不自動開頁",
    )
    ui.add_argument(
        "--ready-file",
        metavar="PATH",
        help="就緒後 exclusive-create 非機密 JSON（供自動驗收）",
    )

    a = sub.add_parser("analyze", help="分析一份交易紀錄")
    a.add_argument("file", nargs="?", help="交易紀錄檔(.csv / .json / .xlsx)")
    a.add_argument(
        "--example",
        choices=["tw", "us", "crypto"],
        help="不用自己的資料,直接分析內建範例(tw 台股 / us 美股 / crypto 加密貨幣)",
    )
    a.add_argument(
        "--market",
        choices=[m.value for m in Market],
        default=None,
        help="若所有交易同屬一個市場,可指定以提升準確度",
    )
    a.add_argument(
        "--field",
        action="append",
        metavar="標準名=你的欄位名",
        help="手動指定欄位對應(自動辨識失敗時),可重複。"
             "例:--field symbol=代號 --field entry_price=買價",
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
    a.add_argument("--html", metavar="PATH", help="輸出自包含的 HTML 報告(可存檔分享)")
    a.add_argument(
        "--card", metavar="PATH", help="輸出分享圖卡 HTML(截圖傳給家人的數據卡)"
    )
    a.add_argument(
        "--full", action="store_true",
        help="一鍵全身健檢:主報告後附上時間趨勢(trend)與風險情境模擬(risk-sim)",
    )
    a.add_argument(
        "--equity", type=float, default=None,
        help="起始權益（必須與損益同幣別）,供 --full 風險情境模擬;未給則粗估",
    )
    a.add_argument(
        "--bootstrap", type=int, default=5000, help="bootstrap 重抽次數(預設 5000)"
    )

    # ── scaffold:產生個人交易程式專案 ──
    s = sub.add_parser("scaffold", help="產生一套屬於你的交易程式專案")
    s.add_argument("--name", default="my_trading_bot", help="專案名稱")
    s.add_argument(
        "--broker",
        choices=_broker_choices(),
        default="paper",
        help="券商(預設 paper 紙上模擬,不碰真錢;用 brokers 指令看完整清單)",
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
        default=None,
        help="主要市場(省略時會從標的代號自動推斷)",
    )
    s.add_argument(
        # default=None 才能區分「使用者沒給」與「使用者明確給了 AAPL」——
        # 用預設值當哨兵會把明確輸入 AAPL 的人誤判成沒指定,被分析檔覆蓋
        "--symbols", default=None, help="標的代號,逗號分隔(如 AAPL,MSFT;預設 AAPL)"
    )
    s.add_argument(
        "--from-analysis",
        metavar="PATH",
        help=(
            "分析完整交易紀錄並嵌入安全階段；只有 tiny_live_validation 且"
            "幣別/報酬/回撤基準可靠時，設定才可能允許極小額 live 驗證"
        ),
    )
    s.add_argument("--out", default=".", help="專案輸出目錄(預設目前目錄)")

    # ── chart-preview:產生圖表樣式預覽 ──
    cp = sub.add_parser("chart-preview", help="產生四種圖表庫的樣式預覽 HTML")
    cp.add_argument("--out", default="chart_preview.html", help="輸出 HTML 路徑")

    # ── brokers / charts:列出可用選項 ──
    sub.add_parser("brokers", help="列出可接入的券商範例框架")
    sub.add_parser("charts", help="列出可用的開源圖表庫")

    # ── scam-check:投資詐騙風險自我檢測 ──
    sub.add_parser(
        "scam-check",
        help="互動式檢測你是否遇到投資詐騙(假飆股群/假名師/保證獲利/詐騙幣)",
    )

    # ── demo:零參數,一行看效果 ──
    d = sub.add_parser("demo", help="不用準備資料,一行指令立刻看分析效果")
    d.add_argument(
        "--edge",
        action="store_true",
        help="改看『具優勢』的範例(預設看『賭博』範例)",
    )

    # ── init-template:產生空白 CSV 範本 ──
    it = sub.add_parser(
        "init-template", help="產生一份空白交易紀錄範本(照填即可分析)"
    )
    it.add_argument(
        "--out", default="trades_template.csv", help="範本輸出路徑"
    )

    # ── record:不開試算表也能逐筆記錄 ──
    rec = sub.add_parser("record", help="五個短問題新增一筆已平倉交易(新手推薦)")
    rec.add_argument("--out", default="my_trades.csv", help="要新增到哪個 CSV")
    rec.add_argument("--symbol", help="標的代號；省略時進入互動輸入")
    rec.add_argument(
        "--pnl",
        help=(
            "已扣手續費/稅/滑價的已實現淨損益金額；必須在數值內帶幣別"
            "（如 25 USD、-1250 TWD），或另外提供 --currency"
        ),
    )
    rec.add_argument(
        "--side",
        default=None,
        help="買/做多 或 賣/做空；direct net pnl 可留白，價差推算必填",
    )
    rec.add_argument("--entry-time", help="進場時間")
    rec.add_argument("--exit-time", help="出場/平倉時間")
    rec.add_argument("--entry-price", help="進場價")
    rec.add_argument("--exit-price", help="出場價")
    rec.add_argument("--quantity", help="數量(台股請填股數)")
    rec.add_argument("--fees", help="此筆總手續費與稅")
    rec.add_argument("--currency", help="損益/帳戶幣別，例如 TWD、USD、JPY")
    rec.add_argument("--strategy", default="", help="當時的進場理由，不要事後美化")

    # ── fit-check:用紀錄做 30 秒階段分流 ──
    fit = sub.add_parser(
        "fit-check", help="快速判斷目前只適合停手、紙上模擬或極小額驗證"
    )
    fit.add_argument("file", nargs="?", help="交易紀錄檔(.csv/.json/.xlsx)")
    fit.add_argument("--example", choices=["tw", "us", "crypto"], help="使用內建範例")
    fit.add_argument(
        "--market", choices=[m.value for m in Market], default=None,
        help="若整份紀錄屬同一市場才指定",
    )
    fit.add_argument("--bootstrap", type=int, default=5000, help="bootstrap 重抽次數")

    # ── scan-screenshot:圖片 OCR / OCR 文字的保守式欄位辨識 ──
    shot = sub.add_parser(
        "scan-screenshot",
        help="辨識交易截圖的點位、數量、損益與技術文字線索",
        description=(
            "辨識交易截圖的點位、數量、損益與技術文字線索。"
            "輸入來源三選一，且必須只提供一個：IMAGE、--text-file 或 --text。"
        ),
    )
    shot.add_argument(
        "image", nargs="?", metavar="IMAGE", help="三選一：券商/圖表截圖路徑"
    )
    shot.add_argument("--text-file", help="三選一：已由手機或系統 OCR 匯出的文字檔")
    shot.add_argument("--text", help="三選一：直接貼入 OCR 文字")
    shot.add_argument("--language", default="chi_tra+eng", help="Tesseract 語言")
    shot.add_argument(
        "--threshold", type=float, default=0.80,
        help="自動帶入候選的規則門檻 0.80~1（只能調高；仍需人工覆核）",
    )
    shot.add_argument("--json", metavar="PATH", help="輸出供介面使用的待覆核 JSON")

    # ── scan-text:詐騙話術文字偵測 ──
    st = sub.add_parser(
        "scan-text",
        help="貼上群組對話 / 廣告文案,掃描詐騙話術特徵",
        description=(
            "掃描群組對話或廣告文案。直接文字與 --file 不可同時使用；"
            "兩者都省略時從 stdin 讀取。"
        ),
    )
    st.add_argument(
        "text", nargs="?",
        help="直接提供文字；不可與 --file 同時使用（省略則從 stdin 讀取）",
    )
    st.add_argument(
        "--file", metavar="PATH",
        help="從檔案讀取文字；不可與直接文字同時使用",
    )

    # ── guru-check:假老師績效驗證器 ──
    gc = sub.add_parser(
        "guru-check", help="檢驗老師宣稱的績效:純靠運氣出現的機率有多高?"
    )
    gc.add_argument("--win-rate", type=float, help="宣稱的勝率(0.9 = 90%%)")
    gc.add_argument("--trades", type=int, help="宣稱基於幾筆交易(沒有就無法檢驗)")
    gc.add_argument("--winning-months", type=int, help="宣稱連續獲利幾個月")
    gc.add_argument("--total-months", type=int, help="完整紀錄總共幾個月")
    gc.add_argument("--monthly-return", type=float, help="宣稱的月報酬(0.2 = 20%%)")
    gc.add_argument("--payoff-ratio", type=float, help="宣稱的盈虧比")
    gc.add_argument("--gurus", type=int, default=1000, help="市面上有多少人自稱老師")
    gc.add_argument(
        "--null-win-prob", type=float, default=0.5,
        help="虛無假設下的單次勝率(預設 0.5;若標的長期上漲可調高至 0.55~0.62)",
    )

    # ── survivorship:倖存者偏差模擬器 ──
    sv = sub.add_parser(
        "survivorship", help="用數學算出「連贏 N 次的神人」有多容易靠運氣出現"
    )
    sv.add_argument("--traders", type=int, default=1000, help="群組人數")
    sv.add_argument("--trials", type=int, default=20, help="每人預測幾次")
    sv.add_argument("--streak", type=int, default=10, help="宣稱的連勝次數")

    # ── forensics:假績效統計鑑識 ──
    fo = sub.add_parser(
        "forensics", help="鑑識老師/平台宣稱的報酬序列是否有可疑徵兆"
    )
    fo.add_argument(
        "returns", nargs="?",
        help="報酬序列,逗號分隔(如 0.02,0.03,-0.01)。0.02 = 2%%",
    )
    fo.add_argument("--file", metavar="PATH", help="從檔案讀取(每行一個報酬)")
    fo.add_argument(
        "--periods-per-year", type=int, default=12, help="一年幾期(月=12,日=252)"
    )

    # ── risk-sim:風險情境模擬 ──
    rs = sub.add_parser(
        "risk-sim", help="用你的損益分布模擬未來情境(爆倉比例、最壞回撤)"
    )
    rs.add_argument("file", nargs="?", help="交易紀錄檔")
    rs.add_argument("--example", choices=["tw", "us", "crypto"], help="用內建範例")
    rs.add_argument(
        "--equity", type=float,
        help="起始權益（必須與交易損益同幣別；未給則粗估）",
    )
    rs.add_argument("--future-trades", type=int, default=200, help="模擬未來幾筆")
    rs.add_argument("--paths", type=int, default=5000, help="模擬幾條路徑")

    # ── trend:時間趨勢分析 ──
    tr = sub.add_parser("trend", help="月報趨勢與優勢衰減偵測")
    tr.add_argument("file", nargs="?", help="交易紀錄檔")
    tr.add_argument("--example", choices=["tw", "us", "crypto"], help="用內建範例")

    return p


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    args = _build_parser().parse_args(argv)

    if args.command == "start":
        return _cmd_start(args)

    if args.command == "ui":
        if isinstance(args.port, bool) or not 0 <= args.port <= 65535:
            print("錯誤: --port 必須介於 0 與 65535", file=sys.stderr)
            return 2
        return _cmd_ui(args)

    if args.command == "analyze":
        # 決定要分析哪個檔案:--example 範例 > 指定檔案 > 無檔案導流
        target = args.file
        market_hint = Market(args.market) if args.market else None
        if args.example:
            target, market_hint = _example_path(args.example)
        elif not target:
            print(
                "你還沒提供交易資料檔。\n"
                "  • 想先看效果?     執行  anti-gambling-trader demo\n"
                "  • 用內建範例分析?  加  --example tw|us|crypto\n"
                "  • 不知道格式?     執行  anti-gambling-trader init-template "
                "產生空白範本照填",
                file=sys.stderr,
            )
            return 1

        try:
            _preflight_file_outputs(
                inputs=[target],
                outputs={
                    "--json": args.json,
                    "--strategy": args.strategy,
                    "--html": args.html,
                    "--card": args.card,
                },
            )
        except (ValueError, OSError) as exc:
            print(f"錯誤: {exc}", file=sys.stderr)
            return 1

        # 解析 --field 欄位覆寫
        field_overrides = _parse_field_overrides(args.field)
        if field_overrides is None:
            return 1

        # --equity 先驗證再開始分析:0/負數/NaN/inf 都會讓風險模擬
        # 產生垃圾或崩潰,與其印完主報告才炸,不如一開始就講清楚。
        if args.equity is not None:
            if not (math.isfinite(args.equity) and args.equity > 0):
                print(
                    f"錯誤: --equity 必須是 > 0 的有限數,收到 {args.equity}",
                    file=sys.stderr,
                )
                return 1
            if not args.full:
                print(
                    "提醒: --equity 只在 --full 的風險情境模擬中使用;"
                    "本次未帶 --full,此參數不會生效。",
                    file=sys.stderr,
                )

        try:
            result = _analyze_with_overrides(
                target, market_hint, args, field_overrides
            )
        except (ValueError, FileNotFoundError, ImportError) as exc:
            print(f"錯誤: {exc}", file=sys.stderr)
            return 1

        # 終端機輸出完整報告
        print(result.text_report)

        # --full:一鍵全身健檢,附上時間趨勢與風險情境
        full_extras = None
        if args.full:
            full_extras = _compute_full_extras(result, args.equity)
            trend_report, scenario, skip_reason = full_extras
            print()
            print(_render_trend(trend_report))
            print()
            if scenario is not None:
                print(
                    f"  情境模擬幣別: {result.metrics.pnl_currency};"
                    " --equity 必須使用同一幣別。"
                )
                print(_render_scenario(scenario))
                if scenario.start_equity_inferred:
                    print(
                        "  💡 上面的本金是工具粗估的 —— 下次帶 --equity 你的真實本金,"
                        "爆倉比例才有意義。"
                    )
            else:
                print(f"(已略過風險情境模擬:{skip_reason}。)")

        if args.json:
            payload = result.as_dict()
            if full_extras is not None:
                trend_report, scenario, skip_reason = full_extras
                # --full 的附加結果一併進 JSON,避免機器可讀輸出與
                # 「一鍵全身健檢」的語意不一致(終端有、JSON 卻沒有)。
                payload["full_extras"] = {
                    "trend": trend_report.as_dict(),
                    "risk_scenario": scenario.as_dict() if scenario else None,
                    "risk_scenario_skipped_reason": skip_reason,
                }
            try:
                _write_new_text(
                    args.json,
                    # sanitize:full_extras 等巢狀結構的 inf/NaN 也要轉 null;
                    # allow_nan=False 當最後防線 —— 再漏就直接炸,不輸出壞 JSON
                    json.dumps(sanitize_json(payload), ensure_ascii=False,
                               indent=2, allow_nan=False),
                )
            except OSError as exc:
                print(f"錯誤:無法寫入 --json {args.json}({exc})", file=sys.stderr)
                return 1
            print(f"\n[已輸出 JSON 結果] {args.json}")

        if args.strategy:
            try:
                _write_new_text(args.strategy, result.strategy_code)
            except OSError as exc:
                print(
                    f"錯誤:無法寫入 --strategy {args.strategy}({exc})",
                    file=sys.stderr,
                )
                return 1
            print(f"[已輸出策略骨架] {args.strategy}")
            if result.verdict.should_discourage:
                print(
                    "  注意:由於裁決為『應勸退』,骨架中的安全閘門預設為啟用 — "
                    "執行時會中止,逼你先把策略驗證好。"
                )

        if args.html:
            from .report_html import render_html_report

            # --full 時把趨勢與風險情境一併帶進 HTML(警語隨區塊自動進入);
            # 模擬被略過時,略過原因也要進 HTML —— 分享出去的報告
            # 不能看起來像「完整健檢已做完」。
            trend_r, scen, skip = full_extras if full_extras else (None, None, None)
            try:
                _write_new_text(
                    args.html,
                    render_html_report(
                        result, trend=trend_r, scenario=scen, scenario_note=skip
                    ),
                )
            except OSError as exc:
                print(f"錯誤:無法寫入 --html {args.html}({exc})", file=sys.stderr)
                return 1
            print(f"[已輸出 HTML 報告] {args.html}(用瀏覽器打開)")

        if args.card:
            from .report_html import render_share_card

            try:
                _write_new_text(args.card, render_share_card(result))
            except OSError as exc:
                print(f"錯誤:無法寫入 --card {args.card}({exc})", file=sys.stderr)
                return 1
            print(f"[已輸出分享圖卡] {args.card}(用瀏覽器打開後截圖)")

        # ── 下一步引導(讓使用者知道接下來能做什麼)──
        _print_next_steps(result, args, target)

        # 以裁決結果決定 exit code:勸退時回傳非 0,方便腳本判斷
        return 2 if result.verdict.should_discourage else 0

    if args.command == "demo":
        return _cmd_demo(args)

    if args.command == "init-template":
        return _cmd_init_template(args)

    if args.command == "record":
        return _cmd_record(args)

    if args.command == "fit-check":
        return _cmd_fit_check(args)

    if args.command == "scan-screenshot":
        return _cmd_scan_screenshot(args)

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

    if args.command == "scam-check":
        from .antiscam import run_scam_check

        result = run_scam_check()
        if result is None:
            return 1  # 問卷被中斷:沒有結論,以錯誤碼收場
        # 高風險時回傳非 0,方便腳本判斷
        return 2 if result.risk_level in ("極高", "高") else 0

    if args.command == "scan-text":
        return _cmd_scan_text(args)

    if args.command == "guru-check":
        return _cmd_guru_check(args)

    if args.command == "survivorship":
        from .survivorship import guru_illusion, render_guru_illusion

        g = guru_illusion(
            n_traders=args.traders, n_trials=args.trials, streak=args.streak
        )
        print(render_guru_illusion(g))
        return 0

    if args.command == "forensics":
        return _cmd_forensics(args)

    if args.command == "risk-sim":
        return _cmd_risk_sim(args)

    if args.command == "trend":
        return _cmd_trend(args)

    return 0


def _read_file_text(path_str: str) -> str | None:
    """讀文字檔,對散戶友善:自動處理 BOM 與 Big5/cp950,錯誤給人話而非 traceback。

    回傳 None 表示讀取失敗(錯誤訊息已印到 stderr,呼叫端直接 return 1)。
    """
    p = Path(path_str)
    try:
        # utf-8-sig 順便吃掉 Windows 記事本常見的 UTF-8 BOM
        return p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            return p.read_text(encoding="cp950")   # 台灣常見的 Big5 舊檔
        except (UnicodeDecodeError, OSError):
            print(
                f"錯誤:無法解讀 {path_str} 的文字編碼。"
                "請用記事本或 Excel 將檔案另存為 UTF-8 後再試。",
                file=sys.stderr,
            )
            return None
    except FileNotFoundError:
        print(f"錯誤:找不到檔案 {path_str}", file=sys.stderr)
        return None
    except OSError as exc:
        print(f"錯誤:無法讀取 {path_str}({exc})", file=sys.stderr)
        return None


def _read_text_input(args) -> str | None:
    """從參數 / 檔案 / 標準輸入取得文字。"""
    if getattr(args, "file", None):
        return _read_file_text(args.file)
    if getattr(args, "text", None):
        return args.text
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return None


def _cmd_scan_text(args) -> int:
    from .antiscam.text_scanner import render_scan, scan_text

    text = _read_text_input(args)
    if not text:
        print(
            "請提供要掃描的文字。用法:\n"
            '  anti-gambling-trader scan-text "老師帶單保證獲利,快加VIP"\n'
            "  anti-gambling-trader scan-text --file 對話紀錄.txt\n"
            "  cat 對話.txt | anti-gambling-trader scan-text",
            file=sys.stderr,
        )
        return 1
    r = scan_text(text)
    print(render_scan(r))
    return 2 if r.risk_level in ("極高", "高") else 0


def _cmd_guru_check(args) -> int:
    from .antiscam.guru_claim import analyze_guru_claim, render_guru_claim

    a = analyze_guru_claim(
        claimed_win_rate=args.win_rate,
        claimed_trades=args.trades,
        claimed_winning_months=args.winning_months,
        total_months=args.total_months,
        claimed_monthly_return=args.monthly_return,
        payoff_ratio=args.payoff_ratio,
        n_gurus_in_market=args.gurus,
        null_win_prob=args.null_win_prob,
    )
    print(render_guru_claim(a))
    bad = a.verdict in (
        "宣稱自相矛盾", "宣稱在數學上不可能持續", "可由倖存者偏差解釋",
        "與純運氣無法區分",
    )
    return 2 if bad else 0


def _cmd_forensics(args) -> int:
    from .forensics import analyze_returns, render_forensics

    raw: str | None = None
    if args.file:
        raw = _read_file_text(args.file)
        if raw is None:
            return 1
    elif args.returns:
        raw = args.returns
    if not raw:
        print(
            "請提供報酬序列。用法:\n"
            "  anti-gambling-trader forensics 0.02,0.031,-0.005,0.028,...\n"
            "  anti-gambling-trader forensics --file 老師的月報酬.txt",
            file=sys.stderr,
        )
        return 1
    try:
        vals = [
            float(x.strip())
            for x in raw.replace("\n", ",").split(",")
            if x.strip() and not x.strip().startswith("#")
        ]
    except ValueError as exc:
        print(f"錯誤:報酬序列格式無法解析({exc})", file=sys.stderr)
        return 1

    f = analyze_returns(vals, periods_per_year=args.periods_per_year)
    print(render_forensics(f))
    return 2 if f.suspicion_level in ("高度可疑", "可疑") else 0


def _load_for_tool(args):
    """risk-sim / trend 共用的資料載入。"""
    from .ingest.loader import load_trades

    if getattr(args, "example", None):
        path, market = _example_path(args.example)
        return load_trades(path, market_hint=market)
    if not getattr(args, "file", None):
        return None
    return load_trades(args.file)


def _cmd_risk_sim(args) -> int:
    from .montecarlo import render_scenario, simulate_ruin_scenario

    try:
        log = _load_for_tool(args)
    except (ValueError, FileNotFoundError, ImportError) as exc:
        print(f"錯誤: {exc}", file=sys.stderr)
        return 1
    if log is None:
        print("請提供交易紀錄檔,或用 --example tw|us|crypto", file=sys.stderr)
        return 1

    from .metrics.performance import compute_metrics

    try:
        metrics = compute_metrics(log)
    except ValueError as exc:
        print(f"錯誤: {exc}", file=sys.stderr)
        return 1
    if not metrics.currency_reliable:
        print(
            "錯誤:損益幣別未經確認，無法把 pnl 與 --equity 放進同一情境模擬。",
            file=sys.stderr,
        )
        return 1

    pnls = [t.pnl or 0.0 for t in log]
    try:
        s = simulate_ruin_scenario(
            pnls,
            start_equity=args.equity,
            n_future_trades=args.future_trades,
            n_paths=args.paths,
        )
    except ValueError as exc:
        # simulate 的參數驗證訊息已是清楚的繁中,直接轉述,不給 traceback
        print(f"錯誤: {exc}", file=sys.stderr)
        return 1
    if s is None:
        print("交易筆數太少(< 10),無法做有意義的情境模擬。", file=sys.stderr)
        return 1
    print(f"情境模擬幣別: {metrics.pnl_currency}; --equity 必須使用同一幣別。")
    print(render_scenario(s))
    return 2 if s.ruin_fraction > 0.1 else 0


def _cmd_trend(args) -> int:
    from .trend import analyze_trend, render_trend_text

    try:
        log = _load_for_tool(args)
    except (ValueError, FileNotFoundError, ImportError) as exc:
        print(f"錯誤: {exc}", file=sys.stderr)
        return 1
    if log is None:
        print("請提供交易紀錄檔,或用 --example tw|us|crypto", file=sys.stderr)
        return 1
    print(render_trend_text(analyze_trend(log)))
    return 0


def _cmd_scaffold(args) -> int:
    """處理 scaffold 指令:產生個人交易程式專案。"""
    from .scaffold import ScaffoldOptions
    from .scaffold.generator import write_project

    from .ingest.loader import infer_market

    verdict = None
    stage = None
    inferred_market = None
    inferred_symbols = None
    if args.from_analysis:
        try:
            result = analyze_file(args.from_analysis)
            verdict = result.verdict
            from .onboarding import stage_from_analysis
            stage = stage_from_analysis(result)
            print(f"[已分析交易紀錄] 裁決:{verdict.headline}")
            print(f"  → 交易階段:{stage.title}")
            if stage.code != "tiny_live_validation":
                print("  → 專案將禁用真實下單，先完成該階段要求。\n")
            # 從分析結果繼承市場與標的,使用者連 --market --symbols 都能省
            markets = [m for m in result.log.markets if m != Market.UNKNOWN]
            if markets:
                inferred_market = markets[0].value
            syms = sorted({t.symbol for t in result.log})[:5]
            if syms:
                inferred_symbols = syms
        except (ValueError, FileNotFoundError, ImportError) as exc:
            print(f"警告:分析交易紀錄失敗({exc}),改以無裁決方式產生。", file=sys.stderr)

    user_set_symbols = args.symbols is not None
    symbols = [s.strip() for s in (args.symbols or "").split(",") if s.strip()]
    if not symbols:
        symbols = inferred_symbols or ["AAPL"]   # 沒自訂就用分析來的,再不然預設

    # market 推斷優先序:--market 指定 > --from-analysis 繼承 > 從標的代號推斷。
    # 但使用者「明確自訂了 --symbols」時,繼承會與標的矛盾
    # (分析檔是台股、標的卻是 AAPL,產出 market: tw_stock 配美股)——
    # 此時改依標的推斷,繼承只在 symbols 也來自分析檔時使用。
    market = args.market
    if market is None and inferred_market and not user_set_symbols:
        market = inferred_market
    if market is None and symbols:
        market = infer_market(symbols[0]).value
        if market != "unknown":
            print(f"[自動推斷市場] 依標的 {symbols[0]} 判斷為:{market}\n")
    market = market or "us_stock"

    opts = ScaffoldOptions(
        project_name=args.name,
        broker=args.broker,
        chart=args.chart,
        market=market,
        symbols=symbols or ["AAPL"],
        verdict=verdict,
        stage=stage,
    )
    try:
        root = write_project(opts, args.out)
    except (ValueError, OSError) as exc:
        # ScaffoldOptions.validate() 的訊息已是清楚的繁中(非法專案名/標的),
        # 直接轉述而非丟 traceback 給散戶。
        print(f"錯誤: {exc}", file=sys.stderr)
        return 1
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
