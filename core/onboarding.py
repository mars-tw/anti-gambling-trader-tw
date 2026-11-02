"""新手交易紀錄與「目前適合哪一步」的保守分流。

這裡刻意不做人格測驗，也不因為使用者自認有紀律就說「適合交易」。
能否進入下一階段只看實際、已平倉的連續紀錄與樣本外證據；沒有資料時，
最多只能建議紙上模擬。
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .markets import infer_market, infer_pnl_currency
from .models import Market


BEGINNER_COLUMNS = (
    "代號",
    "方向",
    "進場時間",
    "出場時間",
    "進場價",
    "出場價",
    "數量",
    "手續費",
    "損益",
    "損益幣別",
    "策略",
)


@dataclass(frozen=True)
class StageAssessment:
    """交易階段分流；不是投資建議或能力認證。"""

    code: str
    title: str
    reason: str
    next_actions: tuple[str, ...]
    exit_code: int


def stage_from_analysis(result: Any) -> StageAssessment:
    """把完整分析濃縮成保守、可行動的下一階段。

    `statistical_edge` 只代表樣本內訊號。除非樣本外也延續，否則不會把
    使用者推進真錢階段；即使延續也只建議極小額驗證，不認證「適合重押」。
    """

    return stage_from_components(
        verdict=result.verdict,
        metrics=result.metrics,
        oos=result.out_of_sample,
    )


def stage_from_components(*, verdict: Any, metrics: Any, oos: Any) -> StageAssessment:
    """供文字/HTML 報告重用同一套階段分流，避免不同畫面說法互相矛盾。"""

    if metrics.expectancy <= 0:
        return StageAssessment(
            code="stop_real_money",
            title="目前不適合投入真錢，先停手檢查方法",
            reason=(
                f"這份樣本的每筆期望值為 {metrics.expectancy:,.2f}；"
                "目前沒有把真錢繼續投進去的正向證據。"
            ),
            next_actions=(
                "停止加碼、借錢、重押與跟單，先回到紙上模擬。",
                "先修正讓平均虧損大於平均獲利的結構，不要把問題只歸因於筆數。",
            ),
            exit_code=2,
        )

    if metrics.total_trades < 30:
        return StageAssessment(
            code="paper_only",
            title="目前不適合投入真錢，只適合紙上模擬",
            reason=f"只有 {metrics.total_trades} 筆已平倉交易，樣本不足以分辨能力與運氣。",
            next_actions=(
                "先用固定規則累積至少 30 筆連續紀錄，不挑單、不刪虧損。",
                "不要放大部位、借錢或用生活費交易。",
            ),
            exit_code=2,
        )

    if verdict.should_discourage:
        return StageAssessment(
            code="stop_real_money",
            title="目前不適合投入真錢，先停手檢查方法",
            reason=verdict.headline,
            next_actions=(
                "停止加碼、借錢、重押與跟單，先回到紙上模擬。",
                "修正規則後另留一段未參與調整的新資料，再做樣本外驗證。",
            ),
            exit_code=2,
        )

    if oos is None or not oos.edge_persisted:
        return StageAssessment(
            code="paper_until_oos",
            title="樣本內有訊號，但目前仍只適合紙上模擬",
            reason="樣本外資料尚未確認優勢延續；樣本內通過不等於未來可重複。",
            next_actions=(
                "凍結交易規則，不再用同一批資料調參。",
                "用後續、未看過的交易驗證，確認扣除成本後仍為正期望。",
            ),
            exit_code=2,
        )

    if (
        not getattr(metrics, "return_metrics_reliable", False)
        or not getattr(metrics, "drawdown_pct_reliable", False)
        or not getattr(metrics, "currency_reliable", False)
    ):
        return StageAssessment(
            code="paper_until_risk_data",
            title="優勢訊號尚缺可靠風險基準，目前仍只適合紙上模擬",
            reason=(
                "損益金額可能有訊號，但名目本金、契約乘數、結算幣別或時間序列不足，"
                "無法可信計算報酬率與回撤百分比；因此不能安全設定真錢部位。"
            ),
            next_actions=(
                "補齊進場價、數量、正確契約乘數、帳戶結算幣別與完整出場時間。",
                "確認報酬率與回撤百分比可用後，再用未看過的新交易重新驗證。",
            ),
            exit_code=2,
        )

    return StageAssessment(
        code="tiny_live_validation",
        title="可考慮極小額驗證；仍不代表適合重押或全職交易",
        reason=(
            "樣本內統計訊號與樣本外延續都有證據；前提是交易規則在看到後段結果前"
            "就已凍結，若看完整段才調規則，這不算真正的樣本外驗證。"
        ),
        next_actions=(
            "只用即使全部損失也不影響生活的資金，單筆風險先壓在帳戶 1% 以內。",
            "持續記錄所有交易；優勢衰退、規則漂移或超出風險上限時退回紙上模擬。",
        ),
        exit_code=0,
    )


def render_stage(assessment: StageAssessment) -> str:
    lines = [
        "=" * 62,
        "                 目前適合哪一個交易階段",
        "=" * 62,
        f"【結論】{assessment.title}",
        f"【原因】{assessment.reason}",
        "【下一步】",
    ]
    lines.extend(f"  • {item}" for item in assessment.next_actions)
    lines.extend(
        [
            "",
            "這是依目前紀錄做的風險分流，不是獲利保證或投資建議。",
            "過去績效不代表未來；沒有完整連續紀錄時，不應投入真錢。",
        ]
    )
    return "\n".join(lines)


def _finite_number(value: Any, label: str, *, allow_blank: bool = True) -> float | None:
    if value is None or str(value).strip() == "":
        if allow_blank:
            return None
        raise ValueError(f"{label}不可留白")
    # 新手入口和檔案載入器必須採用同一個金額語法。先在仍保有原始標註時
    # 檢查，不能先把「USD100TWD」或「USD100USD」剝成裸數字再接受。
    from .ingest.loader import _money_units, _to_float

    annotations = _money_units(value)
    if len(annotations) > 1:
        raise ValueError(f"{label}只能標示一種幣別，收到 {value!r}")
    if annotations and annotations[0] is None:
        raise ValueError(f"{label}不可使用無法判定幣別的裸 $／¥，請填明確代碼")
    parsed = _to_float(value, None)
    if parsed is None:
        raise ValueError(f"{label}必須是數字，收到 {value!r}")
    return parsed


def _safe_spreadsheet_text(value: Any) -> str:
    """避免備註被試算表當成公式執行。"""

    text = str(value or "").strip()
    if text.startswith(("=", "+", "@", "\t", "\r")):
        return "'" + text
    if text.startswith("-") and not re.fullmatch(r"-[0-9.,]+", text):
        return "'" + text
    return text


def _normalize_currency(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    raw = re.sub(r"\s+", "", str(value)).upper()
    raw = {"NT$": "TWD", "NTD": "TWD", "US$": "USD", "RMB": "CNY"}.get(raw, raw)
    if not re.fullmatch(r"[A-Z]{3}", raw) and raw not in {"USDT", "USDC", "BUSD"}:
        raise ValueError("損益幣別請填 TWD、USD、JPY、USDT 等代碼")
    return raw


def _currency_from_pnl_text(value: Any) -> str | None:
    # `_currency_from_amount` accepts a code adjacent to a numeric value, so
    # USDT100 / USDC100 remain valid while USD100TWD stays ambiguous.
    from .ingest.loader import _currency_from_amount

    return _currency_from_amount(value)


def build_beginner_row(
    *,
    symbol: str,
    pnl: Any = None,
    side: str | None = None,
    entry_time: str | None = None,
    exit_time: str | None = None,
    entry_price: Any = None,
    exit_price: Any = None,
    quantity: Any = None,
    fees: Any = None,
    currency: str | None = None,
    strategy: str = "",
    today: date | None = None,
) -> dict[str, str]:
    """建立一列新手紀錄；損益或完整價量資料至少要有一組。"""

    from .ingest.loader import _parse_time

    clean_symbol = _safe_spreadsheet_text(symbol)
    if not clean_symbol:
        raise ValueError("代號不可留白，例如 2330、AAPL、BTCUSDT")
    if len(clean_symbol) > 64:
        raise ValueError("代號過長，請確認沒有把整段文字貼進代號欄")

    side_text = str(side or "").strip().lower()
    if not side_text:
        clean_side = ""
    elif side_text in {"買", "買進", "做多", "多", "long", "buy", "b"}:
        clean_side = "買"
    elif side_text in {"賣", "賣出", "放空", "做空", "空", "short", "sell", "s"}:
        clean_side = "賣"
    else:
        raise ValueError("方向請填『買/做多』或『賣/做空』")

    from .ingest.loader import _currency_from_amount, _money_units

    def amount_unit(value: Any, label: str, *, is_quantity: bool = False) -> str | None:
        annotations = _money_units(value)
        if len(annotations) > 1:
            raise ValueError(f"{label}只能標示一種幣別，收到 {value!r}")
        if not annotations:
            return None
        unit = annotations[0]
        if unit is None:
            raise ValueError(f"{label}不可使用無法判定幣別的裸 $／¥，請填明確代碼")
        if is_quantity:
            raise ValueError("數量不可附帶幣別；請只填股數、口數或幣數")
        # Keep this shared helper as the canonical single-annotation parser.
        return _currency_from_amount(value)

    field_currency = _normalize_currency(currency)
    raw_units = {
        label: amount_unit(value, label, is_quantity=(label == "數量"))
        for label, value in (
            ("已實現損益", pnl),
            ("進場價", entry_price),
            ("出場價", exit_price),
            ("數量", quantity),
            ("手續費", fees),
        )
    }
    pnl_n = _finite_number(pnl, "已實現損益")
    entry_n = _finite_number(entry_price, "進場價")
    exit_n = _finite_number(exit_price, "出場價")
    qty_n = _finite_number(quantity, "數量")
    fees_n = _finite_number(fees, "手續費")

    if fees_n is not None and fees_n < 0:
        raise ValueError("手續費不可小於 0")
    price_set = entry_n is not None or exit_n is not None or qty_n is not None
    complete_prices = (
        entry_n is not None
        and entry_n > 0
        and exit_n is not None
        and exit_n >= 0
        and qty_n is not None
        and qty_n > 0
    )
    market = infer_market(clean_symbol)
    native_quote_currency = infer_pnl_currency(clean_symbol, market)
    known_units = {unit for unit in raw_units.values() if unit}
    if field_currency:
        known_units.add(field_currency)
    # A computed pnl inherits the venue's known quote unit even when its two
    # price cells are bare. This closes the last path that could subtract a
    # foreign labelled fee or save foreign labelled prices as native currency.
    if pnl_n is None and native_quote_currency:
        known_units.add(native_quote_currency)
    if len(known_units) > 1:
        shown = "、".join(sorted(known_units))
        raise ValueError(f"價格、損益、成本與幣別欄不可混用 {shown}；沒有匯率時不可換算")
    amount_currency = raw_units["已實現損益"]
    explicit_currency = field_currency or amount_currency
    if pnl_n is None and market == Market.FOREX:
        raise ValueError(
            "外匯不可只用價差與數量推算，因報價幣與帳戶換匯口徑可能不同；"
            "請填券商已換算、已扣手續費/點差/隔夜利息的淨損益"
        )
    if pnl_n is None and not complete_prices:
        if price_set:
            raise ValueError("若不填已實現損益，進場價、出場價、數量三項都要完整")
        raise ValueError("請填已實現損益；若不知道，改填完整的進場價、出場價與數量")
    if pnl_n is None and not clean_side:
        raise ValueError(
            "用進出場價推算損益時必須明示方向：請填『買/做多』或『賣/做空』；"
            "只有券商 direct net pnl 可在方向未知時留白"
        )

    # 新手四步流程只詢問「何時平倉」。不能因為 entry_time 留白就把
    # exit_time 複製成進場時間，否則 loader 會把未知持倉誤判成當沖。
    default_day = (today or date.today()).isoformat()
    entry_text = str(entry_time or "").strip()
    exit_text = str(exit_time or default_day).strip()
    entry_dt = _parse_time(entry_text) if entry_text else None
    exit_dt = _parse_time(exit_text)
    if entry_dt is not None and exit_dt < entry_dt:
        raise ValueError("出場時間不可早於進場時間")

    def fmt_number(value: float | None) -> str:
        """寫檔時保留浮點往返精度；整數仍用舊版無小數點寫法。

        舊版 f"{value:g}" 只有 6 位有效數字：進場 70200.01 與出場 70200.02
        都會被寫成 70200，載入後 0.01 USDT 的損益直接變成 0。這裡不做任何
        四捨五入、epsilon 容忍或幣別小數位猜測，只保證 repr 可原值讀回。
        """

        if value is None:
            return ""
        number = float(value)
        if number.is_integer() and abs(number) < 1e16:
            # 1e16 以內的整數值用十進位整數字串仍可完整往返（數量 1、價格 70200）。
            return str(int(number))
        return repr(number)

    if pnl_n is not None and not explicit_currency:
        raise ValueError(
            "已實現淨損益請同時填帳戶結算幣別，例如『-120 TWD』或 --currency USD；"
            "不能從商品代號猜券商最後用哪個幣別結算"
        )
    currency_text = explicit_currency or infer_pnl_currency(clean_symbol, market) or ""

    return {
        "代號": clean_symbol,
        "方向": clean_side,
        "進場時間": entry_text,
        "出場時間": exit_text,
        "進場價": fmt_number(entry_n),
        "出場價": fmt_number(exit_n),
        "數量": fmt_number(qty_n),
        "手續費": fmt_number(fees_n),
        "損益": fmt_number(pnl_n),
        "損益幣別": currency_text,
        "策略": _safe_spreadsheet_text(strategy),
    }


def _file_tail_bytes(path: Path, size: int = 8192) -> bytes:
    """只讀檔尾位元組，用來判斷既有換行慣例；不重寫也不轉碼既有檔案。"""

    total = path.stat().st_size
    with path.open("rb") as handle:
        if total > size:
            handle.seek(total - size)
        return handle.read()


def _newline_convention(tail: bytes) -> str:
    """沿用舊檔的換行慣例；看不出來時用 csv 原本的 CRLF 預設。"""

    if b"\r\n" in tail:
        return "\r\n"
    if b"\n" in tail:
        return "\n"
    if b"\r" in tail:
        return "\r"
    return "\r\n"


def append_beginner_row(path: str | Path, row: dict[str, str]) -> tuple[Path, int]:
    """安全新增一筆；只接手本工具的標準欄位，不猜任意既有表格。"""

    out = Path(path)
    if out.suffix.lower() not in {".csv", ".txt"}:
        raise ValueError("逐筆記錄目前請使用 .csv 檔，例如 my_trades.csv")
    if not out.parent.exists():
        raise ValueError(f"資料夾不存在: {out.parent}")

    existing_rows = 0
    if out.exists():
        encoding = "utf-8-sig"
        try:
            with out.open("r", encoding=encoding, newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader, [])
                existing_rows = sum(
                    1 for values in reader
                    if values and not str(values[0]).strip().startswith("#")
                )
        except UnicodeDecodeError:
            encoding = "cp950"
            with out.open("r", encoding=encoding, newline="") as handle:
                reader = csv.reader(handle)
                header = next(reader, [])
                existing_rows = sum(
                    1 for values in reader
                    if values and not str(values[0]).strip().startswith("#")
                )

        # 同一個欄名出現兩次時，無法確定這筆的值該寫進哪一欄；寧可拒絕，
        # 也不要照 header 順序亂填而覆蓋使用者原本的資料。
        duplicated = [
            column
            for column in BEGINNER_COLUMNS
            if list(header).count(column) > 1
        ]
        if duplicated:
            raise ValueError(
                "現有檔案有重複的欄位名稱: "
                + "、".join(duplicated)
                + "。無法確定這筆要寫進哪一欄，請改用新的 --out 檔名，"
                "避免寫壞原始資料。"
            )

        missing = [column for column in BEGINNER_COLUMNS if column not in header]
        # 舊版範本沒有「損益」欄；只有在本列可由完整價量計算時才相容。
        required_missing = [column for column in missing if column != "損益幣別"]
        if required_missing == ["損益"] and not row.get("損益"):
            required_missing = []
        if required_missing:
            raise ValueError(
                "現有檔案不是本工具的新手格式，缺少欄位: "
                + "、".join(required_missing)
                + "。請改用新的 --out 檔名，避免寫壞原始資料。"
            )
        if "損益幣別" in missing and row.get("損益幣別"):
            raise ValueError(
                "現有檔案是舊版格式，缺少『損益幣別』欄，不能安全丟棄這筆的幣別。"
                "請改用新的 --out 檔名，或先在舊檔新增『損益幣別』欄。"
            )
        # 舊檔最後一列若沒有換行（很多試算表／手動編輯會這樣存），直接 append
        # 會把舊的最後一欄和新列的第一欄黏成同一列：載入端少一筆（通常正好是
        # 虧損那筆），但這裡卻回報新增成功。所以先補一個符合舊檔慣例的換行。
        tail = _file_tail_bytes(out)
        terminator = _newline_convention(tail)
        needs_separator = bool(tail) and not tail.endswith((b"\n", b"\r"))

        buffer = io.StringIO()
        csv.writer(buffer, lineterminator=terminator).writerow(
            [row.get(column, "") for column in header]
        )
        # 先試編碼再動檔案：cp950 舊檔存不下這筆字元時，不可以已經落地換行或
        # 半列內容，否則會留下一列空交易或壞列。
        payload_encoding = "utf-8" if encoding == "utf-8-sig" else encoding
        try:
            payload = buffer.getvalue().encode(payload_encoding)
        except UnicodeEncodeError as exc:
            label = "Big5" if payload_encoding == "cp950" else payload_encoding.upper()
            raise ValueError(
                f"現有檔案是 {label} 編碼，這筆內容含無法儲存的字元。"
                "請先另存為 CSV UTF-8。"
            ) from exc
        separator = terminator.encode(payload_encoding) if needs_separator else b""
        # 以二進位 append 既有位元組：不重寫、不轉碼舊資料，也不會在檔案
        # 中間插入第二個 UTF-8 BOM。
        with out.open("ab") as handle:
            handle.write(separator + payload)
    else:
        encoding = "utf-8-sig"
        with out.open("w", encoding=encoding, newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=BEGINNER_COLUMNS)
            writer.writeheader()
            writer.writerow(row)

    return out, existing_rows + 1


def prompt_beginner_row(
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> dict[str, str]:
    """五個短問題的新手輸入；不知道淨損益時才展開價量欄位。"""

    output_fn("一筆 = 一筆已平倉交易。未平倉部位先不要填。")
    symbol = input_fn("1/5 標的代號（例 2330、AAPL、BTCUSDT）: ").strip()
    side = input_fn(
        "2/5 方向 [買/做多 或 賣/做空；有券商淨損益時可留白，無預設]: "
    ).strip()
    pnl = input_fn(
        "3/5 已實現淨損益（扣完手續費/稅/滑價；例 -1250 TWD；不知道可留白）: "
    ).strip()
    kwargs: dict[str, Any] = {}
    if not pnl:
        output_fn("不知道已實現損益，改用進出場價與數量計算：")
        kwargs["entry_price"] = input_fn("  進場價: ").strip()
        kwargs["exit_price"] = input_fn("  出場價: ").strip()
        kwargs["quantity"] = input_fn("  數量（台股請填股數）: ").strip()
    exit_time = input_fn(
        f"4/5 平倉日期／時間 [預設 {date.today().isoformat()}]: "
    ).strip()
    strategy = input_fn(
        "5/5 進場理由（例 突破月線；不知道可留白，不要事後美化）: "
    ).strip()
    return build_beginner_row(
        symbol=symbol,
        pnl=pnl,
        side=side,
        exit_time=exit_time or None,
        strategy=strategy,
        **kwargs,
    )
