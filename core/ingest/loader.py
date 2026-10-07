"""通用交易資料載入器。

支援 CSV / JSON / Excel,並且能「自動辨識欄位」 — 因為每家券商、
每個交易所匯出的欄位名稱都不一樣(中文、英文、大小寫、底線……)。
這裡用同義詞對照表盡量自動對應,對應不到的才要求使用者明確指定。
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ..markets import contract_multiplier as _contract_multiplier
from ..markets import infer_market as _infer_market
from ..markets import infer_pnl_currency as _infer_pnl_currency
from ..models import Market, Side, Trade, TradeLog
from .costs import estimate_round_trip_cost

# ── 欄位同義詞對照(全部轉小寫、去空白後比對)──────────────────
# 涵蓋台股券商、美股券商、加密交易所的常見匯出欄位。
FIELD_SYNONYMS: dict[str, list[str]] = {
    "symbol": [
        "symbol", "ticker", "代號", "股票代號", "標的", "商品", "幣別", "pair",
        "instrument", "code", "stockcode", "證券代號",
        # 台股券商 / 幣安 / IBKR
        "證券名稱", "股票名稱", "商品名稱", "market", "underlyingsymbol", "contract",
    ],
    "side": [
        "side", "方向", "買賣", "買賣別", "交易別", "buysell", "direction", "type",
        "action", "long_short", "多空",
    ],
    "entry_time": [
        "entry_time", "entrytime", "進場時間", "買進時間", "open_time", "opentime",
        "成交日期", "進場日", "date", "buy_date", "開倉時間",
        # 台股 / 幣安 / IBKR
        "成交日", "交易日期", "委託日期", "date(utc)", "tradedate", "datetime",
    ],
    "exit_time": [
        "exit_time", "exittime", "出場時間", "賣出時間", "close_time", "closetime",
        "平倉時間", "sell_date", "exit_date", "平倉日",
    ],
    "entry_price": [
        "entry_price", "entryprice", "進場價", "買進價", "open_price", "成交價",
        "買價", "cost", "成本", "開倉價", "buy_price",
        # 台股 / 幣安 / IBKR(注意:成交價金/價金是「總額」非單價,不納入)
        "成交均價", "price", "tradeprice", "成交單價",
    ],
    "exit_price": [
        "exit_price", "exitprice", "出場價", "賣出價", "close_price", "平倉價",
        "賣價", "sell_price",
    ],
    "quantity": [
        "quantity", "qty", "數量", "股數", "張數", "張", "size", "amount", "volume",
        "口數", "手數", "手", "lot", "lots", "成交數量", "成交股數", "shares", "成交量",
        # 外匯只有這些欄名能明確表示「基礎貨幣單位」，而不是標準/迷你/微型手。
        "units", "unit", "baseunits", "basequantity", "基礎貨幣單位", "基礎貨幣數量",
    ],
    "fees": [
        "fees", "fee", "手續費", "費用", "成本費用", "commission", "手續費及稅",
        "交易成本", "total_fee",
        # 台股稅費分項 / 幣安 / IBKR
        "證交稅", "交易稅", "手續費及交易稅", "ibcommission",
    ],
    "pnl": [
        # 優先採用標準或明示「淨」的欄位；同檔另有 gross/模糊欄時不可搶先配對。
        "pnl", "net_pnl", "netpnl", "損益", "已實現淨損益", "淨損益",
        "profit", "盈虧", "已實現損益", "realized_pnl", "獲利", "賺賠",
        # 注意:不收 "return" —— 它常指「報酬率(%)」而非損益金額,會被誤當金額。
        # 台股 / 幣安 / IBKR
        "損益金額", "realized profit", "fifopnlrealized",
    ],
    "pnl_currency": [
        "pnl_currency", "pnlcurrency", "損益幣別", "帳戶幣別", "結算幣別",
        "account_currency", "accountcurrency", "settlement_currency",
    ],
    "tag": [
        "tag", "策略", "strategy", "標籤", "備註", "note", "remark", "策略名稱",
        "setup", "進場理由",
    ],
}

# side 欄位的值對照
LONG_TOKENS = {"long", "buy", "b", "做多", "多", "買", "買進", "1"}
SHORT_TOKENS = {"short", "sell", "s", "做空", "空", "賣", "賣出", "放空", "-1"}

# market 推斷:代號特徵
_TW_PATTERN = re.compile(r"^\d{4,6}[A-Z]?$")        # 台股多為 4~6 碼數字(如 2330、00878)
_CRYPTO_PATTERN = re.compile(r"(USDT|USDC|BTC|ETH|USD)$", re.IGNORECASE)
_US_PATTERN = re.compile(r"^[A-Z]{1,5}$")            # 美股多為 1~5 個大寫字母


def _norm(name: str) -> str:
    return re.sub(r"\s+", "", str(name)).strip().lower()


_FINANCIAL_ALIAS_FIELDS = frozenset({
    "entry_price",
    "exit_price",
    "quantity",
    "pnl",
    "pnl_currency",
})


def _build_field_map(
    columns: Iterable[str],
    explicit_overrides: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """把實際欄位名對應到標準欄位名。回傳 {標準名: 實際欄位名}。"""
    normalized = {_norm(c): c for c in columns}
    explicit = explicit_overrides or {}
    field_map: dict[str, str] = {}
    for std, synonyms in FIELD_SYNONYMS.items():
        matches = list(dict.fromkeys(
            normalized[_norm(syn)]
            for syn in synonyms
            if _norm(syn) in normalized
        ))
        # fees deliberately stays outside this guard: commission and tax are
        # separate components and are summed below, while total-fee aliases
        # have their own existing precedence rules. Other financial fields
        # cannot safely switch aliases from one row/source to another.
        if (
            std in _FINANCIAL_ALIAS_FIELDS
            and len(matches) > 1
            and std not in explicit
        ):
            shown = "、".join(str(match) for match in matches)
            detail = (
                "pnl 可能同時包含 gross/net 或多個淨損益來源，工具不會替你合併"
                if std == "pnl" else
                "不同列若改用另一欄，會讓金額或部位被靜默改讀"
            )
            raise ValueError(
                f"自動辨識到財務欄位「{std}」有多個不同別名：{shown}；{detail}。"
                "請先統一欄名，或用 field_overrides 明確指定，"
                f"例如 field_overrides={{'{std}': '欄名'}} / --field {std}=欄名。"
            )
        if matches:
            field_map[std] = matches[0]
    return field_map


def infer_market(symbol: str, hint: Market | None = None) -> Market:
    """從標的代號推斷市場別。使用者有指定 hint 時優先採用。

    實作已移至 core/markets.py(修正了「EURUSD 被誤判為加密貨幣」的 bug,
    並新增期貨 / 選擇權 / 外匯 / 台股 ETF 的辨識)。此處保留為相容入口。
    """
    return _infer_market(symbol, hint)


def _parse_side(value: Any) -> Side | None:
    v = _norm(value)
    if v in SHORT_TOKENS:
        return Side.SHORT
    if v in LONG_TOKENS:
        return Side.LONG
    return None


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _naive(value)
    s = str(value).strip()
    # 嘗試多種常見格式
    formats = [
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
        "%Y%m%d", "%m/%d/%Y", "%d/%m/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # 最後嘗試 ISO 格式(含時區)
    try:
        return _naive(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except ValueError as exc:
        raise ValueError(f"無法解析時間格式: {value!r}") from exc


def _has_time_value(value: Any) -> bool:
    """原始列是否真的提供時間；空白、破折號不算。"""
    return value is not None and str(value).strip() not in ("", "-", "—")


def _time_has_explicit_zone(value: Any) -> bool:
    """判斷原值是否明示 UTC/offset；不替 naive 時間猜使用者時區。"""

    if isinstance(value, datetime):
        return value.tzinfo is not None and value.utcoffset() is not None
    return bool(re.search(r"(?:Z|[+\-]\d{2}:?\d{2})$", str(value).strip(), re.I))


def _local_calendar_date(value: Any):
    """取得原始掛鐘日期；UTC 正規化只用於排序，不能改寫交易日。"""

    if isinstance(value, datetime):
        return value.date()
    s = str(value).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return _parse_time(value).date()


def _naive(dt: datetime) -> datetime:
    """統一成可比較的 naive datetime；aware 時間先換成 UTC。

    同一份檔案若混有帶時區(ISO 格式)與不帶時區的時間,
    aware 與 naive datetime **不能互相比較**；直接拔掉 offset 又會改變真實
    先後順序。帶時區值先轉成同一 UTC 絕對時間，再移除 tzinfo；不帶時區
    值維持原掛鐘時間，視為同一個使用者當地時區。
    """
    if dt.tzinfo is not None and dt.utcoffset() is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _to_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else default

    # 幣別只能出現在數字頭尾；不要用全域 replace 把中間髒文字修成數字。
    # 長碼必須排在短碼前，且英文字碼要有邊界，避免 USDT 被 USD 部分吞掉。
    s = str(value).strip()
    code_token = (
        r"(?:USDT|USDC|BUSD|TWD|NTD|USD|JPY|EUR|GBP|CHF|CAD|AUD|NZD|"
        r"SGD|KRW|CNY|CNH|RMB|HKD)(?![A-Z])"
    )
    symbol_token = r"(?:NT\$|US\$|\$|￥|¥|＄)"

    def strip_currency(text: str) -> tuple[str, int]:
        count = 0
        prefix = re.match(
            rf"(?i)^\s*(?:{code_token}|{symbol_token})\s*", text
        )
        if prefix:
            text = text[prefix.end():]
            count += 1
        suffix = re.search(
            rf"(?i)\s*(?:(?<![A-Z])(?:USDT|USDC|BUSD|TWD|NTD|USD|JPY|"
            rf"EUR|GBP|CHF|CAD|AUD|NZD|SGD|KRW|CNY|CNH|RMB|HKD)|"
            rf"{symbol_token})\s*$",
            text,
        )
        if suffix:
            text = text[:suffix.start()]
            count += 1
        return text.strip(), count

    s, currency_count = strip_currency(s)
    accounting_negative = False
    if "(" in s or ")" in s:
        # 會計負數只接受一組完整包住金額的括號；不修補缺半邊或巢狀括號。
        if not (s.startswith("(") and s.endswith(")")):
            return default
        inner = s[1:-1].strip()
        if not inner or "(" in inner or ")" in inner:
            return default
        accounting_negative = True
        s, inner_currency_count = strip_currency(inner)
        currency_count += inner_currency_count

    # 同一金額同時寫兩個幣別符號並不明確；拒絕猜測。
    if currency_count > 1:
        return default
    s = re.sub(r"[,，\s]", "", s)
    if s in ("", "-", "—"):
        return default
    if accounting_negative and s[:1] in ("+", "-"):
        # (-100) / USD (-100) 是雙重負號；(+100) 也不是標準會計表示。
        return default
    try:
        f = float(s)
    except ValueError:
        return default
    # "nan"/"inf" 能被 float() 接受,但會汙染整條統計管線
    # (NaN 的比較恆為 False,inf 讓權益/回撤全爛掉)——視同無法解析。
    if not math.isfinite(f):
        return default
    return -f if accounting_negative else f


def _normalize_currency(value: Any) -> str | None:
    """正規化損益/帳戶幣別；不接受單獨 `$` 這種模稜兩可記號。"""

    if value is None or str(value).strip() in ("", "-", "—"):
        return None
    raw = re.sub(r"\s+", "", str(value)).upper()
    aliases = {"NT$": "TWD", "NTD": "TWD", "US$": "USD", "RMB": "CNY"}
    raw = aliases.get(raw, raw)
    if re.fullmatch(r"[A-Z]{3}", raw):
        return raw
    return raw if raw in {"USDT", "USDC", "BUSD"} else None


def _currency_from_amount(value: Any) -> str | None:
    """讀取損益值旁的幣別；不從商品代號猜帳戶結算幣別。"""

    text = str(value or "").upper().strip()
    if "NT$" in text or re.search(r"\b(?:TWD|NTD)\b", text):
        return "TWD"
    if "US$" in text or re.search(r"\bUSD\b", text):
        return "USD"
    if re.search(r"\bJPY\b", text):
        return "JPY"
    match = re.search(
        r"\b(EUR|GBP|CHF|CAD|AUD|NZD|SGD|KRW|CNY|CNH|RMB|HKD|"
        r"USDT|USDC|BUSD)\b",
        text,
    )
    if not match:
        return None
    return {"RMB": "CNY"}.get(match.group(1), match.group(1))


def sniff_format(path: str | Path) -> str:
    """依副檔名判斷格式: csv / json / excel。"""
    ext = Path(path).suffix.lower()
    if ext in (".csv", ".txt", ".tsv"):
        return "csv"
    if ext == ".json":
        return "json"
    if ext in (".xlsx", ".xls"):
        return "excel"
    raise ValueError(f"不支援的副檔名: {ext}(支援 .csv / .json / .xlsx)")


def _rows_from_csv(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    import csv

    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    # 編碼回退:繁中版 Excel「另存 CSV」預設是 ANSI(cp950/Big5),
    # 只認 UTF-8 會讓照著文件操作的新手直接吃 UnicodeDecodeError。
    # 先試 UTF-8(含 BOM),失敗再試 cp950 —— 都失敗才報錯。
    for enc in ("utf-8-sig", "cp950"):
        try:
            with path.open("r", encoding=enc, newline="") as f:
                reader = csv.DictReader(f, delimiter=delimiter)
                rows = list(reader)
                columns = reader.fieldnames or []
            # 記下實際使用的編碼:cp950 回退「有可能」把損壞的 UTF-8 讀成
            # 貌似合法的中文而不報錯 —— 編碼判定結果必須到報告開頭,
            # 使用者看到亂碼時才有線索,而不是工具靜默宣稱辨識成功。
            _rows_from_csv.last_encoding = enc
            return list(columns), rows
        except UnicodeDecodeError:
            continue
    raise ValueError(
        f"無法解讀 {path.name} 的文字編碼(試過 UTF-8 與 Big5)。"
        "請用 Excel 另存為『CSV UTF-8』格式後重試。"
    )


def _rows_from_json(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    # 支援 [{...}, ...] 或 {"trades": [...]}
    if isinstance(data, dict):
        for key in ("trades", "data", "records", "orders"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            raise ValueError("JSON 物件中找不到交易陣列(預期鍵: trades/data/records)")
    if not isinstance(data, list) or not data:
        raise ValueError("JSON 必須是非空的交易陣列")
    columns: list[str] = []
    seen: set[str] = set()
    for index, row in enumerate(data, 1):
        if not isinstance(row, dict):
            raise ValueError(
                f"JSON 第 {index} 筆交易必須是物件，收到 {type(row).__name__}"
            )
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(key)
    return columns, data


def _rows_from_excel(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ImportError(
            "讀取 Excel 需要 openpyxl。請執行: pip install openpyxl"
        ) from exc
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = [str(c) if c is not None else "" for c in next(rows_iter)]
    rows: list[dict[str, Any]] = []
    for raw in rows_iter:
        if all(c is None for c in raw):
            continue
        rows.append(dict(zip(header, raw)))
    wb.close()
    return header, rows


def load_trades(
    path: str | Path,
    *,
    market_hint: Market | None = None,
    auto_estimate_costs: bool = True,
    field_overrides: Optional[dict[str, str]] = None,
) -> TradeLog:
    """從檔案載入交易紀錄,回傳正規化的 TradeLog。

    Args:
        path:               資料檔路徑(.csv / .json / .xlsx)
        market_hint:        若所有交易都屬同一市場,可在此指定以提升準確度
        auto_estimate_costs: 當該筆交易未提供 fees 時,是否用成本模型自動估算。
                             強烈建議開啟 — 忽略成本是賭徒最常見的自我欺騙。
        field_overrides:    手動指定欄位對應 {標準名: 實際欄位名},覆寫自動辨識

    Raises:
        ValueError: 缺少必要欄位,或資料無法解析
    """
    path = Path(path)
    fmt = sniff_format(path)

    used_encoding = "utf-8"
    if fmt == "csv":
        columns, rows = _rows_from_csv(path)
        used_encoding = getattr(_rows_from_csv, "last_encoding", "utf-8-sig")
    elif fmt == "json":
        columns, rows = _rows_from_json(path)
    else:
        columns, rows = _rows_from_excel(path)

    if not rows:
        raise ValueError(f"檔案中沒有任何資料列: {path}")

    field_map = _build_field_map(columns, field_overrides)
    if field_overrides:
        field_map.update(field_overrides)

    dangerous_cashflow_columns = {
        column for column in columns if _norm(column) in {"淨收付", "淨收付金額"}
    }
    if dangerous_cashflow_columns and "pnl" not in field_map:
        shown = "、".join(sorted(dangerous_cashflow_columns))
        raise ValueError(
            f"欄位「{shown}」通常是單邊交割收付款（含本金），不是已平倉實現損益；"
            "拒絕把它當 pnl。請改匯出已實現損益/realized P&L 報表。"
        )

    pnl_column = field_map.get("pnl", "")
    pnl_column_norm = _norm(pnl_column)
    beginner_layout = {
        "代號", "方向", "進場時間", "出場時間", "進場價", "出場價",
        "數量", "手續費", "損益", "損益幣別", "策略",
    }.issubset(set(columns))
    net_pnl_headers = {
        "pnl", "net_pnl", "netpnl", "損益", "已實現淨損益", "淨損益",
    }
    gross_pnl_headers = {"profit", "獲利", "realizedprofit", "fifopnlrealized"}
    if field_overrides and "pnl" in field_overrides:
        # --field pnl=自訂淨利欄 是使用者對標準欄 contract 的明確確認。
        pnl_semantics = "net"
    elif pnl_column_norm in {_norm(name) for name in net_pnl_headers}:
        pnl_semantics = "net"
    elif pnl_column_norm == "損益" and beginner_layout:
        pnl_semantics = "net"
    elif pnl_column_norm in {_norm(name) for name in gross_pnl_headers}:
        pnl_semantics = "gross"
    elif pnl_column:
        pnl_semantics = "ambiguous"
    else:
        pnl_semantics = "missing"

    # 檢查必要欄位。pnl 可由價格推算,故 pnl 與(進出場價)二擇一即可。
    required_core = ["symbol", "entry_price", "exit_price", "quantity"]
    has_prices = all(k in field_map for k in ("entry_price", "exit_price", "quantity"))
    has_pnl = "pnl" in field_map and "symbol" in field_map

    if not (has_prices or has_pnl):
        missing = [k for k in required_core if k not in field_map]
        # 列出偵測到的欄位(帶編號),並指向 CLI 真正可用的兩條出路。
        numbered = "\n".join(f"    [{i}] {c}" for i, c in enumerate(columns, 1))
        raise ValueError(
            f"無法自動辨識必要欄位:{missing}\n"
            f"  你的檔案有這些欄位:\n{numbered}\n\n"
            f"  兩種解法:\n"
            f"  (1) 用 --field 手動對應,例如:\n"
            f"      --field symbol=你的代號欄 --field entry_price=你的買價欄 "
            f"--field exit_price=你的賣價欄 --field quantity=你的數量欄\n"
            f"  (2) 執行 `init-template` 產生標準格式範本,照填後再分析。"
        )

    def get(row: dict, std: str, default: Any = None) -> Any:
        col = field_map.get(std)
        return row.get(col, default) if col else default

    normalized_columns = {_norm(column): column for column in columns}

    def matching_columns(names: set[str]) -> list[str]:
        return [
            actual for normalized, actual in normalized_columns.items()
            if normalized in {_norm(name) for name in names}
        ]

    fee_override = field_map.get("fees") if field_overrides and "fees" in field_overrides else None
    total_fee_columns = matching_columns({
        "fees", "fee", "費用", "成本費用", "手續費及稅", "手續費及交易稅",
        "交易成本", "total_fee",
    })
    commission_columns = matching_columns({"手續費", "commission", "ibcommission"})
    tax_columns = matching_columns({"證交稅", "交易稅"})
    if fee_override:
        fee_columns = [fee_override]
        fee_mode = "override_total"
    elif total_fee_columns:
        fee_columns = [total_fee_columns[0]]
        fee_mode = "explicit_total"
    else:
        fee_columns = list(dict.fromkeys(commission_columns + tax_columns))
        fee_mode = "components" if fee_columns else "missing"
    # 只讓「可解析的時間」參與整檔時區一致性判斷。格式錯誤的 direct net
    # pnl 時間會在逐列階段降級為 unknown；若在這裡把髒字串當成無時區，
    # 它會讓同檔內原本合法的 aware 時間也整批被拒絕。
    time_awareness: set[bool] = set()
    for row in rows:
        for std in ("entry_time", "exit_time"):
            value = get(row, std)
            if not _has_time_value(value):
                continue
            try:
                _parse_time(value)
            except ValueError:
                continue
            time_awareness.add(_time_has_explicit_zone(value))
    if len(time_awareness) > 1:
        raise ValueError(
            "同一份紀錄混用了有時區與無時區的時間，無法安全判定真實先後順序。"
            "請先全部補成相同 UTC offset（例如 +08:00），或全部改成同一當地時區。"
        )
    all_times_are_aware = time_awareness == {True}

    buy_sell_mapped_fields = {
        "entry_price": {"買進價", "買價", "buy_price"},
        "exit_price": {"賣出價", "賣價", "sell_price"},
        "entry_time": {"買進時間", "買進日期", "buy_date"},
        "exit_time": {"賣出時間", "賣出日期", "sell_date"},
    }
    short_uses_buy_sell_mapping = any(
        _norm(field_map.get(field, "")) in {_norm(name) for name in names}
        for field, names in buy_sell_mapped_fields.items()
    )
    generic_entry_price_columns = {
        "成交價", "成交均價", "price", "tradeprice", "成交單價"
    }
    direct_entry_basis_ambiguous = (
        _norm(field_map.get("entry_price", ""))
        in {_norm(name) for name in generic_entry_price_columns}
    )

    # 台股「張」單位偵測:若數量欄名含「張」(如 張數、張),代表單位是「張」
    # 而非「股」,1 張 = 1000 股。不換算會讓損益與成本差 1000 倍且不報錯,
    # 對台股使用者是最危險的靜默錯誤。
    qty_col = field_map.get("quantity", "")
    qty_col_norm = _norm(qty_col)
    qty_in_lots = (
        "張" in str(qty_col)
        or qty_col_norm in {"lot", "lots", "boardlot", "boardlots"}
    )
    qty_in_ambiguous_hands = "手" in str(qty_col)
    lot_multiplier = 1000 if qty_in_lots else 1
    forex_base_unit_columns = {
        "units", "unit", "baseunits", "basequantity", "基礎貨幣單位", "基礎貨幣數量",
    }
    qty_is_explicit_forex_units = _norm(qty_col) in forex_base_unit_columns

    trades: list[Trade] = []
    skipped = 0
    skip_reasons: list[str] = []   # 收集略過原因,回報給使用者(不再靜默吞錯)
    unknown_multiplier_symbols: set[str] = set()   # 槓桿商品但查不到契約乘數
    ambiguous_forex_symbols: set[str] = set()
    invalid_lot_unit_symbols: set[str] = set()
    ambiguous_entry_basis_symbols: set[str] = set()
    tw_lot_converted = 0
    direct_pnl_count = 0
    gross_pnl_adjusted_count = 0
    unknown_side_count = 0
    retained_bad_entry_time_count = 0
    retained_bad_exit_time_count = 0
    ignored_net_fee_count = 0
    invalid_direct_notional_symbols: set[str] = set()
    for row in rows:
        row_no = len(trades) + skipped + 1
        symbol = str(get(row, "symbol", "")).strip()
        # 註解列(以 # 開頭,如範本中的說明)直接略過,不計入錯誤
        if symbol.startswith("#"):
            continue
        if not symbol:
            skipped += 1
            if len(skip_reasons) < 10:
                skip_reasons.append(f"第 {row_no} 列:缺少標的代號")
            continue

        # 先判斷是否真的有可用的 direct net pnl。只有這種已結算金額，才可在
        # optional 時間或費用欄髒掉時保留；gross pnl 與價差推算仍需完整可信資料。
        pnl_raw = get(row, "pnl")
        pnl = _to_float(pnl_raw, None)
        pnl_was_provided = (
            pnl_raw is not None and str(pnl_raw).strip() not in ("", "-", "—")
        )
        if pnl_was_provided and pnl is None:
            skipped += 1
            if len(skip_reasons) < 10:
                skip_reasons.append(
                    f"第 {row_no} 列({symbol}):pnl「{pnl_raw}」無法解析；"
                    "拒絕忽略它後改用價差推算"
                )
            continue
        if pnl is not None and pnl_semantics == "ambiguous":
            skipped += 1
            if len(skip_reasons) < 10:
                skip_reasons.append(
                    f"第 {row_no} 列({symbol}):欄位「{pnl_column}」無法確認是 gross 還是"
                    "已扣全部成本的 net pnl；確認後請改名 net_pnl/已實現淨損益，"
                    "或用 --field pnl=原欄名 明確確認"
                )
            continue
        is_direct_net_pnl = pnl is not None and pnl_semantics == "net"

        # 時間欄缺少時仍保留 datetime 佔位值以相容舊 API，但 known 旗標
        # 必須為 False。後續當沖/持倉/趨勢邏輯只能使用 known 時間。
        entry_time_raw = get(row, "entry_time")
        exit_time_raw = get(row, "exit_time")
        entry_time_known = "entry_time" in field_map and _has_time_value(entry_time_raw)
        exit_time_known = "exit_time" in field_map and _has_time_value(exit_time_raw)
        bad_entry_time_retained = False
        bad_exit_time_retained = False
        entry_time = datetime(1970, 1, 1)
        entry_local_date = None
        if entry_time_known:
            try:
                entry_time = _parse_time(entry_time_raw)
                entry_local_date = _local_calendar_date(entry_time_raw)
            except ValueError as exc:
                if not is_direct_net_pnl:
                    skipped += 1
                    if len(skip_reasons) < 10:
                        skip_reasons.append(
                            f"第 {row_no} 列({symbol}):進場時間格式無法解析 — {exc}"
                        )
                    continue
                entry_time_known = False
                bad_entry_time_retained = True

        # 出場時間獨立解析，避免一個壞時間拖累同列另一個可用時間。
        exit_time = entry_time
        exit_local_date = None
        if exit_time_known:
            try:
                exit_time = _parse_time(exit_time_raw)
                exit_local_date = _local_calendar_date(exit_time_raw)
            except ValueError as exc:
                if not is_direct_net_pnl:
                    skipped += 1
                    if len(skip_reasons) < 10:
                        skip_reasons.append(
                            f"第 {row_no} 列({symbol}):出場時間格式無法解析 — {exc}"
                        )
                    continue
                exit_time_known = False
                exit_time = entry_time
                bad_exit_time_retained = True

        market = infer_market(symbol, market_hint)
        parsed_side = _parse_side(get(row, "side"))

        entry_price_raw = _to_float(get(row, "entry_price"), None)
        exit_price_raw = _to_float(get(row, "exit_price"), None)
        quantity_raw = _to_float(get(row, "quantity"), None)

        # 契約乘數只屬於台灣期貨/選擇權。使用者若明示其他市場，代號即使
        # 剛好長得像 TXF/TXO 也不可套用台期乘數，否則損益會被放大 50~200 倍。
        if market in (Market.TW_FUTURES, Market.TW_OPTIONS):
            mult, mult_known = _contract_multiplier(symbol)
        else:
            mult, mult_known = 1.0, True

        fee_values: list[float] = []
        provided_fee_columns: set[str] = set()
        fee_parse_failed = False
        ignored_net_fee = False
        for fee_column in fee_columns:
            fees_raw = row.get(fee_column)
            if fees_raw is None or str(fees_raw).strip() in ("", "-", "—"):
                continue
            provided_fee_columns.add(fee_column)
            parsed_fee = _to_float(fees_raw, None)
            if parsed_fee is None:
                if is_direct_net_pnl:
                    # net pnl 已含成本；髒掉的 optional fee 只能放棄揭露，不可把
                    # 整筆已結算損益丟掉，否則會形成可被盈虧相關缺值扭曲的樣本。
                    ignored_net_fee = True
                    fee_values = []
                    provided_fee_columns.clear()
                    break
                fee_parse_failed = True
                if len(skip_reasons) < 10:
                    skip_reasons.append(
                        f"第 {row_no} 列({symbol}):成本欄 {fee_column}="
                        f"「{fees_raw}」無法解析；拒絕靜默改用估計成本"
                    )
                break
            fee_values.append(parsed_fee)
        if fee_parse_failed:
            skipped += 1
            continue
        if any(value < 0 for value in fee_values):
            if is_direct_net_pnl:
                ignored_net_fee = True
                fee_values = []
                provided_fee_columns.clear()
            else:
                skipped += 1
                if len(skip_reasons) < 10:
                    skip_reasons.append(
                        f"第 {row_no} 列({symbol}):交易成本不可為負數"
                    )
                continue
        fees = sum(fee_values) if fee_values else None
        if pnl is not None and pnl_semantics == "gross":
            if fees is None or fee_mode not in {"explicit_total", "override_total"}:
                skipped += 1
                if len(skip_reasons) < 10:
                    skip_reasons.append(
                        f"第 {row_no} 列({symbol}):欄位「{pnl_column}」視為 gross pnl，"
                        "但缺少明確總成本欄（total_fee/交易成本），無法換成淨損益"
                    )
                continue
            pnl -= fees
            gross_pnl_adjusted_count += 1
        raw_currency = get(row, "pnl_currency")
        field_currency = _normalize_currency(raw_currency)
        amount_currency = _currency_from_amount(pnl_raw)
        if (
            raw_currency is not None
            and str(raw_currency).strip() not in ("", "-", "—")
            and field_currency is None
        ):
            skipped += 1
            if len(skip_reasons) < 10:
                skip_reasons.append(
                    f"第 {row_no} 列({symbol}):損益幣別「{raw_currency}」無法辨識；"
                    "請填三碼幣別（如 TWD/USD/JPY）或 USDT/USDC"
            )
            continue
        if field_currency and amount_currency and field_currency != amount_currency:
            skipped += 1
            if len(skip_reasons) < 10:
                skip_reasons.append(
                    f"第 {row_no} 列({symbol}):pnl 金額寫 {amount_currency}，"
                    f"但 pnl_currency 欄寫 {field_currency}；幣別衝突"
                )
            continue
        explicit_currency = field_currency or amount_currency
        if pnl is not None and explicit_currency is None:
            skipped += 1
            if len(skip_reasons) < 10:
                skip_reasons.append(
                    f"第 {row_no} 列({symbol}):direct pnl 缺少帳戶結算幣別；"
                    "請填 pnl_currency/損益幣別，或在金額旁寫 TWD、USD、USDT 等"
                )
            continue

        # direct pnl 是券商結算後金額，不能從 AAPL/EURJPY 猜帳戶幣別。
        # 價差推算則一定是商品原生報價幣別；若另填不同幣別但沒有匯率，也拒絕。
        native_quote_currency = _infer_pnl_currency(symbol, market)
        if (
            pnl is None
            and explicit_currency
            and native_quote_currency
            and explicit_currency != native_quote_currency
        ):
            skipped += 1
            if len(skip_reasons) < 10:
                skip_reasons.append(
                    f"第 {row_no} 列({symbol}):價差推算的原生幣別為 {native_quote_currency}，"
                    f"但損益幣別填 {explicit_currency}；缺少匯率，拒絕換算"
                )
            continue
        pnl_currency = (
            explicit_currency if pnl is not None
            else (native_quote_currency or explicit_currency)
        )
        if pnl is not None:
            direct_pnl_count += 1

        # 外匯券商的 quantity 可能是「1 手」也可能是「100000 units」。標準手、
        # 迷你手、微型手又各差 10 倍，不能看到數字 1 就偷偷乘 100000。
        # 沒有直接 pnl 時，只有欄名明示 base units 才允許由價差推算。
        forex_quantity_ambiguous = (
            market == Market.FOREX
            and quantity_raw is not None
            and not qty_is_explicit_forex_units
        )
        if pnl is None and market == Market.FOREX:
            skipped += 1
            if len(skip_reasons) < 10:
                skip_reasons.append(
                    f"第 {row_no} 列({symbol}):外匯價差損益的報價幣別與帳戶換匯口徑未知，"
                    "拒絕直接用價差×數量推算；請提供券商已換算、已扣成本的 pnl"
                )
            continue
        if pnl is None and market == Market.UNKNOWN:
            skipped += 1
            if len(skip_reasons) < 10:
                skip_reasons.append(
                    f"第 {row_no} 列({symbol}):市場與數量/成本規格無法辨識，"
                    "拒絕用通用乘數 1 推算損益；請指定 --market 或提供 direct net pnl"
                )
            continue
        incomplete_component_costs = (
            fee_mode == "components"
            and bool(fee_values)
            and (
                not any(column in provided_fee_columns for column in commission_columns)
                or (
                    market in (
                        Market.TW_STOCK,
                        Market.TW_ETF,
                        Market.TW_FUTURES,
                        Market.TW_OPTIONS,
                    )
                    and not any(column in provided_fee_columns for column in tax_columns)
                )
                or (
                    market in (
                        Market.TW_STOCK,
                        Market.TW_ETF,
                        Market.TW_FUTURES,
                        Market.TW_OPTIONS,
                    )
                    and not (commission_columns and tax_columns)
                )
            )
        )
        if pnl is None and incomplete_component_costs:
            skipped += 1
            if len(skip_reasons) < 10:
                skip_reasons.append(
                    f"第 {row_no} 列({symbol}):成本只有部分分項，缺少完整手續費/佣金與交易稅；"
                    "拒絕把部分成本冒充完整成本，請補總 costs 或 direct net pnl"
                )
            continue

        non_tw_lot_unit = (
            qty_in_lots
            and quantity_raw is not None
            and market not in (Market.TW_STOCK, Market.TW_ETF)
        )
        ambiguous_hand_unit = qty_in_ambiguous_hands and quantity_raw is not None
        if pnl is None and (non_tw_lot_unit or ambiguous_hand_unit):
            skipped += 1
            if len(skip_reasons) < 10:
                unit_label = "手" if ambiguous_hand_unit else "張/lot"
                skip_reasons.append(
                    f"第 {row_no} 列({symbol}):數量欄以『{unit_label}』表示，"
                    f"無法對 {market.value} 安全換算；台股請改填股/shares 或張/lots，"
                    "其他市場請改填 shares/口/contracts/units"
                )
            continue

        # 有效性守門:這列的損益必須「算得出來」——
        # 要嘛直接給了 pnl,要嘛(進場價 > 0、出場價 ≥ 0、數量 > 0)齊全。
        # 缺料就略過並記錄,絕不用 0 補洞:0 元進場價會把出場價整段
        # 誤算成獲利、空白 pnl 會變成假打平交易,都足以翻轉統計裁決。
        has_prices = (
            entry_price_raw is not None and entry_price_raw > 0
            and exit_price_raw is not None and exit_price_raw >= 0
            and quantity_raw is not None and quantity_raw > 0
        )
        if pnl is None and not has_prices:
            skipped += 1
            if len(skip_reasons) < 10:
                skip_reasons.append(
                    f"第 {row_no} 列({symbol}):算不出損益"
                    "(缺 pnl,且進場價/出場價/數量不完整或無法解析)"
                )
            continue

        # 價差損益的正負取決於做多/做空。市場/欄位完整性先檢查，讓新手先
        # 看到最根本的錯誤；資料齊全後仍缺方向則絕不默認 long。
        if pnl is None and parsed_side is None:
            skipped += 1
            if len(skip_reasons) < 10:
                skip_reasons.append(
                    f"第 {row_no} 列({symbol}):缺少或無法辨識做多/做空方向，"
                    "拒絕由價差推算損益；請提供 side 或直接淨 pnl"
                )
            continue
        if pnl is None and parsed_side == Side.SHORT and short_uses_buy_sell_mapping:
            skipped += 1
            if len(skip_reasons) < 10:
                skip_reasons.append(
                    f"第 {row_no} 列({symbol}):空單不可把 buy/sell 價格或時間固定當 entry/exit；"
                    "請把欄名改成 entry_price/exit_price、entry_time/exit_time 後再分析"
                )
            continue
        if (
            pnl is None
            and parsed_side == Side.SHORT
            and market in (Market.TW_STOCK, Market.TW_ETF, Market.US_STOCK)
        ):
            skipped += 1
            if len(skip_reasons) < 10:
                skip_reasons.append(
                    f"第 {row_no} 列({symbol}):股票/商品放空的借券費、融券利息與召回成本"
                    "無法由價差可靠估算；請提供券商已扣全部成本的 direct net pnl"
                )
            continue
        side_known = parsed_side is not None
        side = parsed_side or Side.LONG  # direct pnl 的相容佔位值，不得用來反推方向
        if not side_known:
            unknown_side_count += 1

        # 台期/選擇權且乘數未知:pnl 沒直接給的話,用乘數 1.0 推算會少算
        # 數十到數千倍 —— 那不是「估計」,是錯的數字。誠實略過並要求 pnl。
        if (
            pnl is None
            and market in (Market.TW_FUTURES, Market.TW_OPTIONS)
            and not mult_known
        ):
            skipped += 1
            if len(skip_reasons) < 10:
                skip_reasons.append(
                    f"第 {row_no} 列({symbol}):契約乘數未知,"
                    "期貨/選擇權損益無法可信推算 —— 請直接提供 pnl 欄位"
                )
            continue

        entry_price = entry_price_raw or 0.0
        exit_price = exit_price_raw or 0.0
        row_lot_multiplier = (
            lot_multiplier if market in (Market.TW_STOCK, Market.TW_ETF) else 1
        )
        quantity = (quantity_raw or 0.0) * row_lot_multiplier
        if (
            qty_in_lots
            and quantity_raw is not None
            and quantity_raw > 0
            and row_lot_multiplier == 1000
        ):
            tw_lot_converted += 1
        notional_reliable = True
        invalid_direct_notional = False
        if pnl is not None and (
            entry_price_raw is None
            or entry_price_raw <= 0
            or quantity_raw is None
            or quantity_raw <= 0
        ):
            # direct pnl 金額不依賴價量欄，因此保留；但沒有正的實際進場價與
            # 數量，就沒有可驗證的本金母體，絕不能產生看似精確的 return。
            entry_price = (
                entry_price_raw
                if entry_price_raw is not None and entry_price_raw > 0
                else 0.0
            )
            quantity = (
                quantity_raw * row_lot_multiplier
                if quantity_raw is not None and quantity_raw > 0
                else 0.0
            )
            notional_reliable = False
            invalid_direct_notional = True
        if pnl is not None and (
            direct_entry_basis_ambiguous
            or (parsed_side == Side.SHORT and short_uses_buy_sell_mapping)
        ):
            notional_reliable = False
            ambiguous_entry_basis_symbols.add(symbol)
        if pnl is not None and (non_tw_lot_unit or ambiguous_hand_unit):
            # direct pnl 金額仍可用；欄名「張」對非台股沒有安全換算，故部位歸零。
            entry_price = 0.0
            exit_price = 0.0
            quantity = 0.0
            notional_reliable = False
            invalid_lot_unit_symbols.add(symbol)
        if (
            pnl is not None
            and (
                not pnl_currency
                or not native_quote_currency
                or pnl_currency != native_quote_currency
            )
        ):
            # 例：AAPL 名目本金是 USD，但 direct pnl 已由券商換成 TWD；
            # 沒有匯率就不能拿 TWD / USD 算 32 倍假報酬。
            notional_reliable = False
        if market in (Market.TW_FUTURES, Market.TW_OPTIONS) and not mult_known:
            notional_reliable = False
            unknown_multiplier_symbols.add(symbol)
        if pnl is not None and forex_quantity_ambiguous:
            # 直接損益仍可用來做金額統計，但名目部位不可信；歸零可讓報告明示
            # 回撤百分比/報酬率沒有可靠資本基準，也避免估出錯 10 萬倍的成本。
            entry_price = 0.0
            exit_price = 0.0
            quantity = 0.0
            ambiguous_forex_symbols.add(symbol)
            notional_reliable = False
        tag = get(row, "tag")
        tag = str(tag).strip() if tag not in (None, "") else None

        # 成本處理:使用者沒給 fees 且開啟自動估算時,補上估計成本。
        # 「乘數未知就拒絕估算」只適用於乘數真正必要的台期/選擇權。
        # 外匯若是明示 base units 可用乘數 1；手數不明時已在上方拒絕或降級。
        if pnl is None and fees is None and auto_estimate_costs and entry_price and quantity:
            if market in (Market.TW_FUTURES, Market.TW_OPTIONS) and not mult_known:
                unknown_multiplier_symbols.add(symbol)
                fees = 0.0
            else:
                # 當沖成本優惠只能在進/出場時間都由原始資料提供時套用。
                # pnl-only 的 1970 佔位值不是真實交易日，不得誤用當沖稅率。
                is_day_trade = (
                    entry_time_known
                    and exit_time_known
                    and entry_local_date == exit_local_date
                )
                fees = estimate_round_trip_cost(
                    market, side, entry_price, exit_price, quantity,
                    is_day_trade=is_day_trade,
                    contract_multiplier=mult,
                )
        fees = fees or 0.0

        try:
            trade = Trade(
                symbol=symbol,
                market=market,
                side=side,
                entry_time=entry_time,
                exit_time=exit_time,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
                fees=fees,
                pnl=pnl,  # 若為 None,Trade.__post_init__ 會用價格推算(已含 fees)
                tag=tag,
                contract_multiplier=mult,
                entry_time_known=entry_time_known,
                exit_time_known=exit_time_known,
                contract_multiplier_known=mult_known,
                notional_reliable=notional_reliable,
                pnl_currency=pnl_currency,
                side_known=side_known,
                entry_local_date=entry_local_date,
                exit_local_date=exit_local_date,
            )
        except ValueError as exc:
            # Trade 的不變量驗證(如出場早於進場)→ 記錄原因後略過該列
            skipped += 1
            if len(skip_reasons) < 10:
                skip_reasons.append(f"第 {row_no} 列:{exc}")
            continue
        trades.append(trade)
        if bad_entry_time_retained:
            retained_bad_entry_time_count += 1
        if bad_exit_time_retained:
            retained_bad_exit_time_count += 1
        if ignored_net_fee:
            ignored_net_fee_count += 1
        if invalid_direct_notional:
            invalid_direct_notional_symbols.add(symbol)

    if not trades:
        detail = "\n  ".join(skip_reasons) if skip_reasons else "請檢查欄位對應與資料格式。"
        raise ValueError(
            f"沒有任何有效交易可解析(略過 {skipped} 列)。\n  {detail}"
        )

    # 略過比例過高,通常代表欄位對應整個錯了 — 主動警告而非靜默。
    skip_ratio = skipped / (len(trades) + skipped) if (len(trades) + skipped) else 0
    warn = ""
    if skip_ratio > 0.2:
        warn = f", ⚠️略過比例 {skip_ratio:.0%} 偏高(可能欄位對應有誤)"
    # 有列被略過就要說「為什麼」—— 只給筆數不給原因,使用者無從修資料。
    # source 是報告開頭就會看到的字串,揭露前 2 筆原因 + 導引。
    if skipped and skip_reasons:
        preview = ";".join(skip_reasons[:2])
        more = f"(其餘 {skipped - 2} 列原因略)" if skipped > 2 else ""
        warn += f", 略過原因:{preview}{more}"
    lot_note = (
        f", {tw_lot_converted} 筆台股/台股 ETF 數量以『張』×1000 換算為股"
        if tw_lot_converted else ""
    )
    if unknown_multiplier_symbols:
        syms = ", ".join(sorted(unknown_multiplier_symbols)[:3])
        warn += (
            f", ⚠️{syms} 為槓桿商品但查不到契約乘數 —— 已停用名目部位、"
            "報酬率與自動成本；請提供直接淨 pnl 與實際 fees"
        )
    if ambiguous_forex_symbols:
        syms = ", ".join(sorted(ambiguous_forex_symbols)[:3])
        warn += (
            f", ⚠️{syms} 的外匯數量單位不明 —— 已採用檔案提供的 pnl，"
            "但不計名目部位、報酬率與自動成本；若要計算請提供 units 欄"
        )
    if invalid_lot_unit_symbols:
        syms = ", ".join(sorted(invalid_lot_unit_symbols)[:3])
        warn += (
            f", ⚠️{syms} 的『張/lot/手』不是可安全套用的數量單位——已保留 direct pnl，"
            "但停用名目部位與報酬率；請改填 shares/口/units"
        )
    if ambiguous_entry_basis_symbols:
        syms = ", ".join(sorted(ambiguous_entry_basis_symbols)[:3])
        warn += (
            f", ⚠️{syms} 的成交價/buy-sell 欄無法證明是實際進場成本——"
            "已保留 direct pnl，但停用名目部位、報酬率與回撤百分比"
        )
    if invalid_direct_notional_symbols:
        syms = ", ".join(sorted(invalid_direct_notional_symbols)[:3])
        warn += (
            f", ⚠️{syms} 的 direct pnl 缺少有效進場價或數量——已保留損益金額，"
            "但停用名目部位、報酬率與回撤百分比"
        )
    if ignored_net_fee_count:
        warn += (
            f", ⚠️{ignored_net_fee_count} 筆 direct net pnl 的 optional fee 無法解析或為負數；"
            "淨損益已保留，fees 改為 0/未知且未重複扣除"
        )

    currencies = {t.pnl_currency for t in trades if t.pnl_currency}
    missing_currency = sum(1 for t in trades if not t.pnl_currency)
    if (
        len(currencies) > 1
        or (currencies and missing_currency)
        or (missing_currency and len(trades) > 1)
    ):
        shown = "、".join(sorted(currencies)) or "未知"
        raise ValueError(
            "同一份紀錄含不同或不明的損益幣別（"
            f"{shown}；不明 {missing_currency} 筆），不可直接相加。"
            "請依同一帳戶/結算幣別分檔，或提供 pnl_currency/損益幣別欄。"
        )
    currency_note = (
        f", 損益幣別 {next(iter(currencies))}（未做匯率換算）"
        if len(currencies) == 1 else
        ", ⚠️損益幣別不明（請勿與其他帳戶或幣別合併）"
    )
    direct_pnl_note = (
        f", {direct_pnl_count} 筆採用檔案直接 pnl；必須是已扣手續費/稅/滑價的淨損益，"
        "fees 僅揭露、不重複扣除"
        if direct_pnl_count else ""
    )
    if gross_pnl_adjusted_count:
        direct_pnl_note += (
            f", 其中 {gross_pnl_adjusted_count} 筆 gross pnl 已扣檔案明示總成本後轉為 net"
        )
    fee_note = (
        ", 成本由手續費/佣金與證交稅分項加總"
        if fee_mode == "components" and commission_columns and tax_columns else ""
    )
    side_note = (
        f", ⚠️{unknown_side_count} 筆 direct pnl 缺少可靠方向——不納入方向偏好或策略反推"
        if unknown_side_count else ""
    )

    unknown_entry_times = sum(1 for t in trades if not t.entry_time_known)
    unknown_exit_times = sum(1 for t in trades if not t.exit_time_known)
    time_note = ""
    if unknown_entry_times or unknown_exit_times:
        time_note = (
            f", ⚠️時間資料不完整(缺進場 {unknown_entry_times} 筆、缺出場 {unknown_exit_times} 筆)"
            "—— 這些交易不納入當沖/持倉指標；缺出場時間時不做時間趨勢"
        )
    if retained_bad_entry_time_count or retained_bad_exit_time_count:
        time_note += (
            f", ⚠️時間格式錯誤但因 direct net pnl 保留"
            f"（進場 {retained_bad_entry_time_count} 筆、出場 {retained_bad_exit_time_count} 筆）；"
            "錯誤時間已標記 unknown，不納入時間型指標"
        )
    timezone_note = (
        ", 帶時區時間已統一轉為 UTC 後比較"
        if all_times_are_aware else
        ", 無時區時間視為同一使用者當地時區"
        if time_awareness == {False} else ""
    )

    return TradeLog(
        trades=trades,
        source=(
            f"{path.name} ({fmt}"
            + (", 以 Big5/cp950 編碼讀取 — 若見亂碼請改存 CSV UTF-8"
               if used_encoding == "cp950" else "")
            + f", 載入 {len(trades)} 筆, 略過 {skipped} 筆"
            f"{warn}{lot_note}{time_note}{timezone_note}{side_note}{currency_note}"
            f"{fee_note}{direct_pnl_note})"
        ),
        account_label=path.stem,
        rejected_row_count=skipped,
        rejected_row_reasons=tuple(skip_reasons),
    )
