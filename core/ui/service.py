"""Shared stdlib service behind beginner browser/desktop GUI.

No HTTP or UI framework code. Core stays zero-dependency; optional
Excel support is imported only on the .xlsx path.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import posixpath
import re
import tempfile
import threading
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional
from uuid import uuid4

import core as _core_pkg
from core.analyzer import analyze_log, sanitize_json
from core.antiscam.text_scanner import render_scan, scan_text
from core.data_api.manager import DataManager
from core.data_api.transport import DataAPIError
from core.ingest.loader import load_trades
from core.montecarlo import (
    SimulationCancelled,
    simulate_capital_risk,
    threshold_amount,
)
from core.onboarding import BEGINNER_COLUMNS, build_beginner_row
from core.report_html import render_html_report, render_share_card
from core.scaffold import ScaffoldOptions
from core.scaffold.generator import generate_project
from core.ui.chart_report import render_evidence_report, render_share_summary
from core.ui.visuals import build_visuals

# ---------------------------------------------------------------------------
# Limits / constants
# ---------------------------------------------------------------------------

_MAX_UPLOAD_BYTES = 2 * 1024 * 1024
_MAX_TRADES = 2000
_MAX_MANUAL = 2000
_MAX_SCAN_CHARS = 20_000
_MAX_FILENAME = 120
_ARTIFACT_MAX_ENTRIES = 20
_ARTIFACT_MAX_BYTES = 32 * 1024 * 1024
_XLSX_MAX_MEMBERS = 128
_XLSX_MAX_TOTAL = 20 * 1024 * 1024
_XLSX_MAX_MEMBER = 8 * 1024 * 1024
_XLSX_MAX_ROWS = _MAX_TRADES + 1  # header + accepted trades
_XLSX_MAX_COLUMNS = 128
_XLSX_MAX_CELLS = _XLSX_MAX_ROWS * _XLSX_MAX_COLUMNS
_XLSX_CELL_REF = re.compile(r"^\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6})$")
_RISK_SEED = 20261008
_RISK_DEADLINE_SECONDS = 15.0
_RISK_JOIN_TIMEOUT_SECONDS = 3.0
_RISK_JOB_ID = re.compile(r"^[0-9a-f]{32}$")

_ALLOWED_EXT = {".csv", ".json", ".xlsx"}
_SAMPLE_NAMES = {
    "tw": "tw_stock_gambling.csv",
    "us": "us_stock_edge.csv",
    "crypto": "crypto_luck.json",
}
_EXPORT_SOURCE_LABELS = {
    "demo": "內建示範資料，非本人績效",
    "import": "來源：使用者匯入資料",
    "manual": "來源：使用者手動輸入資料",
    "api": "來源：使用者主動讀取的唯讀 API 資料",
}
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_ADD_RECORD_FIELDS = frozenset(
    {
        "symbol",
        "pnl",
        "side",
        "entry_time",
        "exit_time",
        "entry_price",
        "exit_price",
        "quantity",
        "fees",
        "currency",
        "strategy",
    }
)
_DANGEROUS_KEYS = frozenset(
    {"path", "dest_dir", "ready", "stage", "allow_live_trading", "filesystem"}
)


class UIError(ValueError):
    """User-facing service error with HTTP-like status code."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = int(status)


def _format_research_number(value: Any, *, currency: str = "") -> str:
    """Format a core numeric fact for a beginner-facing research gate."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return "無法計算"
    if not math.isfinite(number):
        return "無法計算"
    suffix = f" {currency}" if currency else ""
    return f"{number:,.2f}{suffix}"


def _xml_local_name(tag: Any) -> str:
    """Return an XML element's local name without trusting its namespace."""

    text = str(tag)
    return text.rsplit("}", 1)[-1]


def _xlsx_xml_attr(element: Any, name: str) -> str | None:
    """Read an XML attribute by local name, including namespaced attributes."""

    wanted = name.lower()
    for key, value in element.attrib.items():
        if _xml_local_name(key).lower() == wanted:
            return str(value)
    return None


def _xlsx_part_name(value: Any, *, base: str = "") -> str:
    """Resolve one internal OOXML part reference without accepting escapes."""

    text = str(value or "").replace("\\", "/")
    if (
        not text
        or "\x00" in text
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in text)
        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text)
        or text.startswith("//")
        or any(part == ".." for part in text.split("/"))
    ):
        raise UIError("XLSX 工作表參照路徑不安全或無法辨識", status=400)
    candidate = text.lstrip("/") if text.startswith("/") else posixpath.join(
        posixpath.dirname(base), text
    )
    normalized = posixpath.normpath(candidate)
    if (
        normalized in {"", ".", ".."}
        or normalized.startswith("../")
        or "/../" in normalized
    ):
        raise UIError("XLSX 工作表參照路徑不安全或無法辨識", status=400)
    return normalized


def _xlsx_relationship_source(name: str) -> str | None:
    """Map ``*_rels/*.rels`` to the package part it describes."""

    normalized = name.replace("\\", "/")
    parent, filename = posixpath.split(normalized)
    if posixpath.basename(parent).lower() != "_rels" or not filename.lower().endswith(
        ".rels"
    ):
        return None
    if parent.lower() == "_rels" and filename.lower() == ".rels":
        return ""
    source_name = filename[:-5]
    source_dir = posixpath.dirname(parent)
    return posixpath.join(source_dir, source_name) if source_dir else source_name


def _xlsx_cell_position(reference: Any) -> tuple[int, int]:
    """Parse one finite Excel A1 reference as (column, row)."""

    match = _XLSX_CELL_REF.fullmatch(str(reference or ""))
    if match is None:
        raise UIError("Excel 儲存格位置格式不正確", status=400)
    letters, row_text = match.groups()
    column = 0
    for char in letters.upper():
        column = column * 26 + (ord(char) - ord("A") + 1)
    try:
        row = int(row_text)
    except ValueError as exc:  # regex already limits this; keep the error user-safe.
        raise UIError("Excel 列號格式不正確", status=400) from exc
    return column, row


def _xlsx_dimension_bounds(reference: Any) -> tuple[int, int, int, int]:
    """Parse an OOXML worksheet dimension without accepting named ranges."""

    parts = str(reference or "").split(":")
    if len(parts) not in {1, 2}:
        raise UIError("Excel 使用範圍格式不正確", status=400)
    start_column, start_row = _xlsx_cell_position(parts[0])
    end_column, end_row = _xlsx_cell_position(parts[-1])
    if end_column < start_column or end_row < start_row:
        raise UIError("Excel 使用範圍格式不正確", status=400)
    return start_column, start_row, end_column, end_row


@dataclass(frozen=True)
class Artifact:
    id: str
    filename: str
    media_type: str
    content: bytes


def _examples_dir() -> Path:
    return Path(_core_pkg.__file__).resolve().parent / "examples"


def _new_id() -> str:
    return uuid4().hex


def _safe_display_filename(filename: str) -> str:
    name = str(filename or "").replace("\\", "/").split("/")[-1].strip()
    if not name:
        name = "upload.bin"
    return name


def _ext_of(filename: str) -> str:
    return Path(filename).suffix.lower()


def _excel_available() -> bool:
    try:
        import openpyxl  # noqa: F401
        import defusedxml  # noqa: F401

        return True
    except Exception:
        return False


def _reject_dangerous(data: dict) -> None:
    unknown = sorted(str(k) for k in data if not isinstance(k, str) or k not in _ADD_RECORD_FIELDS)
    if unknown:
        raise UIError(f"不接受未知或危險欄位：{', '.join(unknown)}", status=400)


def _finite(obj: Any) -> Any:
    return sanitize_json(obj)


def _readiness(result: Any) -> list[dict[str, Any]]:
    """Build research readiness checklist from AnalysisResult (no auto-unlock)."""
    d = result.as_dict()
    integrity = d.get("integrity") or {}
    verdict = d.get("verdict") or {}
    metrics = verdict.get("metrics") or d.get("metrics") or {}
    # Prefer result.metrics when available.
    try:
        m = result.metrics.as_dict()
    except Exception:
        m = metrics if isinstance(metrics, dict) else {}
    oos = d.get("out_of_sample") or {}

    def item(key: str, label: str, passed: bool, detail: str) -> dict[str, Any]:
        return {"key": key, "label": label, "passed": bool(passed), "detail": detail}

    total_trades = m.get("total_trades")
    if total_trades is None:
        total_trades = m.get("n_trades")
    try:
        total_trades_n = int(total_trades or 0)
    except Exception:
        total_trades_n = 0

    expectancy = m.get("expectancy")
    try:
        expectancy_ok = expectancy is not None and float(expectancy) > 0
    except Exception:
        expectancy_ok = False

    currency_value = m.get("pnl_currency") or m.get("currency") or ""
    currency = str(currency_value).strip().upper() if currency_value else ""
    if not re.fullmatch(r"[A-Z0-9]{2,12}", currency):
        currency = ""
    if expectancy is None:
        expectancy_detail = "目前資料不足，無法計算每筆期望值。"
    else:
        expectancy_detail = f"每筆平均結果：{_format_research_number(expectancy, currency=currency)}"

    should_discourage = bool(verdict.get("should_discourage"))
    oos_available = bool(oos.get("available"))
    edge_persisted = bool(oos.get("edge_persisted"))
    if not oos_available:
        oos_detail = "目前資料不足，尚未完成樣本外檢查。"
    elif edge_persisted:
        oos_detail = "樣本外資料仍呈現正向證據；這仍不等於可用真錢交易。"
    else:
        oos_detail = "樣本外資料未能延續原本的優勢。"

    def reliability_detail(note: Any, *, passed: bool, passed_text: str, failed_text: str) -> str:
        text = str(note or "").strip()
        return text or (passed_text if passed else failed_text)

    return [
        item(
            "integrity.complete",
            "資料完整性",
            bool(integrity.get("complete")),
            str(
                integrity.get("summary")
                or integrity.get("reason")
                or (
                    f"拒絕 {integrity.get('rejected_row_count', 0)} 列，"
                    f"疑似重複 {integrity.get('suspected_duplicate_count', 0)} 列"
                )
            ),
        ),
        item(
            "total_trades>=30",
            "交易筆數 ≥ 30",
            total_trades_n >= 30,
            f"目前 {total_trades_n} 筆",
        ),
        item(
            "expectancy>0",
            "期望值 > 0",
            expectancy_ok,
            expectancy_detail,
        ),
        item(
            "not_should_discourage",
            "未觸發勸退",
            not should_discourage,
            str(verdict.get("headline") or verdict.get("message") or verdict.get("label") or ""),
        ),
        item(
            "oos_edge_persisted",
            "樣本外優勢仍在",
            oos_available and edge_persisted,
            oos_detail,
        ),
        item(
            "return_metrics_reliable",
            "報酬指標可靠",
            bool(m.get("return_metrics_reliable", m.get("returns_reliable"))),
            reliability_detail(
                m.get("return_note"),
                passed=bool(m.get("return_metrics_reliable", m.get("returns_reliable"))),
                passed_text="報酬資料可用於目前指標。",
                failed_text="資料基準不足，報酬指標暫不採用。",
            ),
        ),
        item(
            "drawdown_pct_reliable",
            "回撤百分比可靠",
            bool(m.get("drawdown_pct_reliable", m.get("drawdown_reliable"))),
            reliability_detail(
                m.get("drawdown_note"),
                passed=bool(m.get("drawdown_pct_reliable", m.get("drawdown_reliable"))),
                passed_text="回撤比例資料可用於目前指標。",
                failed_text="資料基準不足，回撤比例暫不採用。",
            ),
        ),
        item(
            "currency_reliable",
            "幣別可靠",
            bool(m.get("currency_reliable", integrity.get("currency_reliable"))),
            reliability_detail(
                m.get("currency_note"),
                passed=bool(m.get("currency_reliable", integrity.get("currency_reliable"))),
                passed_text="幣別資料可用於目前比較。",
                failed_text="幣別資料不足或混用，金額比較需保守解讀。",
            ),
        ),
    ]


class UIService:
    """Session-scoped analysis / scan / export / scaffold service."""

    def __init__(
        self,
        mode: str = "browser",
        data_manager: DataManager | None = None,
    ) -> None:
        self._mode = str(mode or "browser")
        self._state_lock = threading.RLock()
        # Heavy analysis and every mutation share one non-blocking gate.  The
        # state lock is intentionally held only for snapshots/commits, so a
        # browser can still poll state() while an analysis is running.
        self._operation_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._busy = False
        self._closed = False
        self._manual_rows: list[dict[str, Any]] = []
        self._manual_revision = 0
        self._analysis: Optional[dict[str, Any]] = None
        self._analysis_result: Any = None  # private AnalysisResult for exports
        self._analysis_revision = 0
        self._risk_job: Optional[dict[str, Any]] = None
        self._risk_cancel: Optional[threading.Event] = None
        self._risk_thread: Optional[threading.Thread] = None
        self._artifacts: dict[str, Artifact] = {}
        self._artifact_bytes = 0
        self._tmpdir = tempfile.TemporaryDirectory(prefix="ui_svc_")
        self._data_manager = data_manager or DataManager()
        self._sample_digests: dict[str, str] = {}
        self._load_sample_digests()

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        with self._close_lock:
            with self._state_lock:
                if self._closed:
                    return
                cancel = self._risk_cancel
                worker = self._risk_thread
            # The API manager owns its worker (and, for Shioaji, the provider
            # owns its child process).  Cancel and join that lane before
            # waiting on the shared operation gate.
            self._data_manager.close()
            # Cooperative cancellation is signalled before waiting for either
            # the worker or the single-operation gate.
            if cancel is not None:
                cancel.set()
            if worker is threading.current_thread():
                raise RuntimeError("risk worker cannot close its owning service")
            if worker is not None and worker.is_alive():
                worker.join(_RISK_JOIN_TIMEOUT_SECONDS)
                if worker.is_alive():
                    raise RuntimeError("risk simulation worker did not stop")
            if not self._operation_lock.acquire(timeout=_RISK_JOIN_TIMEOUT_SECONDS):
                raise RuntimeError("UI operation did not stop before close deadline")
            try:
                with self._state_lock:
                    if self._risk_thread is not None and self._risk_thread.is_alive():
                        raise RuntimeError("risk simulation worker is still active")
                    self._closed = True
                    self._manual_rows.clear()
                    self._manual_revision = 0
                    self._analysis = None
                    self._analysis_result = None
                    self._risk_job = None
                    self._risk_cancel = None
                    self._risk_thread = None
                    self._artifacts.clear()
                    self._artifact_bytes = 0
                    self._busy = False
                try:
                    self._tmpdir.cleanup()
                except Exception:
                    pass
            finally:
                self._operation_lock.release()

    def __enter__(self) -> "UIService":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- state -------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        # Never hold the service state lock while taking the manager lock.
        # The manager's completion callback takes these locks in the opposite
        # temporal order (manager commit, then service state), so this snapshot
        # order prevents a lock inversion.
        data_api = self._data_manager.snapshot()
        with self._state_lock:
            return _finite(
                {
                    "mode": self._mode,
                    "capabilities": {
                        "excel": _excel_available(),
                        "live_trading": False,
                    },
                    "manual_rows": list(self._manual_rows),
                    "manual_revision": self._manual_revision,
                    "analysis": self._analysis,
                    "risk_simulation": self._public_risk_job_locked(
                        include_result=False
                    ),
                    "data_api": data_api,
                    "busy": self._busy,
                }
            )

    # -- concurrency helper ------------------------------------------------

    @contextmanager
    def _operation(self, *, invalidate_analysis: bool = False) -> Iterator[None]:
        """Enter the one mutation/statistics lane without ever queueing."""

        if not self._operation_lock.acquire(blocking=False):
            raise UIError("服務忙碌中，請稍後再試", status=409)
        entered = False
        try:
            with self._state_lock:
                if self._closed:
                    raise UIError("本次使用已結束", status=409)
                self._busy = True
                if invalidate_analysis:
                    self._invalidate_analysis_locked()
                entered = True
            yield
        finally:
            if entered:
                with self._state_lock:
                    self._busy = False
            self._operation_lock.release()

    def _invalidate_analysis(self) -> None:
        with self._state_lock:
            self._invalidate_analysis_locked()

    def _invalidate_analysis_locked(self) -> None:
        if self._risk_cancel is not None:
            self._risk_cancel.set()
        self._analysis = None
        self._analysis_result = None
        self._risk_job = None
        self._risk_cancel = None
        if self._risk_thread is not None and not self._risk_thread.is_alive():
            self._risk_thread = None

    def _clear_artifacts_locked(self) -> None:
        self._artifacts.clear()
        self._artifact_bytes = 0

    # -- read-only API data -----------------------------------------------

    @staticmethod
    def _data_ui_error(exc: BaseException, *, fallback_status: int = 422) -> UIError:
        code = getattr(exc, "code", None)
        messages = {
            "auth": "憑證缺少、無效，或唯讀資料權限尚未通過",
            "source_restricted": "公開行情查詢受到資料來源限制（HTTP 403），不需要輸入私有憑證。",
            "rate_limit": "資料來源目前限制請求頻率，請稍後再試",
            "timeout": "唯讀資料請求超過 45 秒期限",
            "network": "無法連線到固定的資料來源",
            "invalid_response": "資料來源回應或輸入格式無法安全採用",
            "oversize": "資料超過本機工作階段的 4MiB 上限",
            "cancelled": "唯讀資料工作已取消",
        }
        status = getattr(exc, "status", None)
        if isinstance(status, bool) or not isinstance(status, int):
            status = fallback_status
        if status not in {400, 403, 404, 409, 413, 422, 500}:
            status = fallback_status
        return UIError(messages.get(code, messages["invalid_response"]), status=status)

    def data_credentials(
        self,
        *,
        provider: Any,
        profile: Any,
        api_key: Any,
        api_secret: Any,
        remember: Any,
        use_saved: Any,
    ) -> dict[str, Any]:
        with self._operation(invalidate_analysis=True):
            try:
                with self._state_lock:
                    self._clear_artifacts_locked()
                return _finite(
                    self._data_manager.configure_credentials(
                        provider=provider,
                        profile=profile,
                        api_key=api_key,
                        api_secret=api_secret,
                        remember=remember,
                        use_saved=use_saved,
                    )
                )
            except UIError:
                raise
            except Exception as exc:
                raise self._data_ui_error(exc) from None

    def _finish_data_job(self) -> None:
        # DataManager invokes this only after its terminal state is committed
        # and after releasing its own lock.
        with self._state_lock:
            self._busy = False
            self._operation_lock.release()

    def data_start(
        self,
        *,
        provider: Any,
        kind: Any,
        query: Any,
        profile: Any = "default",
    ) -> dict[str, Any]:
        if not self._operation_lock.acquire(blocking=False):
            raise UIError("服務忙碌中，請稍後再試", status=409)
        try:
            with self._state_lock:
                if self._closed:
                    raise UIError("本次使用已結束", status=409)
                self._busy = True
                self._invalidate_analysis_locked()
                self._clear_artifacts_locked()
            # Manager validation and its own lock are intentionally outside
            # the state lock.  Once start succeeds, the completion callback
            # owns release of the shared operation gate.
            return _finite(
                self._data_manager.start(
                    provider=provider,
                    kind=kind,
                    query=query,
                    profile=profile,
                    on_finish=self._finish_data_job,
                )
            )
        except UIError:
            with self._state_lock:
                self._busy = False
            self._operation_lock.release()
            raise
        except Exception as exc:
            with self._state_lock:
                self._busy = False
            self._operation_lock.release()
            raise self._data_ui_error(exc, fallback_status=400) from None

    def data_status(self, job_id: Any) -> dict[str, Any]:
        try:
            return _finite(self._data_manager.status(job_id))
        except Exception as exc:
            raise self._data_ui_error(exc, fallback_status=404) from None

    def data_cancel(self, job_id: Any) -> dict[str, Any]:
        try:
            return _finite(self._data_manager.cancel(job_id))
        except Exception as exc:
            raise self._data_ui_error(exc, fallback_status=404) from None

    @staticmethod
    def _public_normalization(value: dict[str, Any]) -> dict[str, Any]:
        public = {key: item for key, item in value.items() if key != "log"}
        public["log_available"] = value.get("log") is not None
        return _finite(public)

    def data_analyze(
        self,
        *,
        job_id: Any,
        opening_zero_confirmed: Any,
        transfers_reconciled: Any,
        pnl_basis: Any,
        total_costs_confirmed: Any,
    ) -> dict[str, Any]:
        with self._operation(invalidate_analysis=True):
            try:
                normalized = self._data_manager.analyze(
                    job_id,
                    opening_zero_confirmed=opening_zero_confirmed,
                    transfers_reconciled=transfers_reconciled,
                    pnl_basis=pnl_basis,
                    total_costs_confirmed=total_costs_confirmed,
                )
                if not isinstance(normalized, dict):
                    raise DataAPIError("invalid_response", status=422)
                public_normalization = self._public_normalization(normalized)
                log = normalized.get("log")
                if normalized.get("available") is not True or log is None:
                    return {
                        "available": False,
                        "normalization": public_normalization,
                    }

                status = self._data_manager.status(job_id)
                dataset = status.get("result")
                if not isinstance(dataset, dict):
                    raise DataAPIError("invalid_response", status=409)
                requested = dataset.get("requested")
                requested = requested if isinstance(requested, dict) else {}
                provider = str(dataset.get("provider") or "")
                kind = str(dataset.get("kind") or "")
                symbol = str(dataset.get("symbol") or "")
                log.source = f"{provider} {kind} {symbol}".strip()
                api_provenance = {
                    "source_bound_dataset_id": f"{status['job_id']}:{status['revision']}",
                    "provider": provider,
                    "kind": kind,
                    "period": {
                        "start_ms": requested.get("start_ms"),
                        "end_ms": requested.get("end_ms"),
                    },
                    "fees_basis": (
                        str(pnl_basis)
                        if provider == "shioaji"
                        else "API 成交費用欄位；第三資產費用或未釐清週期不推算"
                    ),
                }
                result = analyze_log(log, framework="generic", n_bootstrap=5000)
                analysis = self._store_analysis(
                    result,
                    origin="api",
                    api_provenance=api_provenance,
                )
                return {
                    "available": True,
                    "normalization": public_normalization,
                    "analysis": analysis,
                }
            except UIError:
                raise
            except Exception as exc:
                raise self._data_ui_error(exc) from None

    def data_export(self, job_id: Any, format: Any) -> Artifact:
        with self._operation():
            try:
                filename, media_type, content = self._data_manager.export(
                    job_id, format=format
                )
                return self._put_artifact(filename, media_type, content)
            except UIError:
                raise
            except Exception as exc:
                raise self._data_ui_error(exc) from None

    # -- samples / upload --------------------------------------------------

    def _load_sample_digests(self) -> None:
        root = _examples_dir()
        for key, fname in _SAMPLE_NAMES.items():
            path = root / fname
            if path.is_file():
                self._sample_digests[key] = hashlib.sha256(path.read_bytes()).hexdigest()

    def analyze_sample(self, name: str) -> dict[str, Any]:
        with self._operation(invalidate_analysis=True):
            try:
                if not isinstance(name, str):
                    raise UIError("sample 必須是 tw、us 或 crypto", status=400)
                key = name.strip().lower()
                if key not in _SAMPLE_NAMES:
                    raise UIError(
                        f"未知示範樣本：{name!r}（可用 tw / us / crypto）",
                        status=400,
                    )
                path = _examples_dir() / _SAMPLE_NAMES[key]
                if not path.is_file():
                    raise UIError(f"示範樣本不存在：{_SAMPLE_NAMES[key]}", status=404)
                data = path.read_bytes()
                return self._analyze_bytes(
                    filename=_SAMPLE_NAMES[key],
                    content=data,
                    origin="demo",
                )
            except UIError:
                raise
            except Exception as exc:
                raise UIError("分析示範樣本失敗，請確認安裝內容完整", status=500) from exc

    def analyze_upload(self, filename: str, content: bytes) -> dict[str, Any]:
        # Validation lives inside the invalidating operation.  A failed new
        # import must never leave an older favorable result exportable.
        with self._operation(invalidate_analysis=True):
            try:
                if not isinstance(filename, str):
                    raise UIError("檔名必須是文字", status=400)
                display = _safe_display_filename(filename)
                if len(display) > _MAX_FILENAME:
                    raise UIError(f"檔名不可超過 {_MAX_FILENAME} 個字元", status=400)
                if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in display):
                    raise UIError("檔名含不可顯示的控制字元", status=400)
                if not isinstance(content, (bytes, bytearray)):
                    raise UIError("上傳內容必須是位元組資料", status=400)
                raw = bytes(content)
                if len(raw) > _MAX_UPLOAD_BYTES:
                    raise UIError("檔案超過 2MiB 上限", status=413)
                if len(raw) == 0:
                    raise UIError("檔案是空的", status=400)
                ext = _ext_of(display)
                if ext not in _ALLOWED_EXT:
                    raise UIError("僅支援 CSV / JSON（或可用時的 XLSX）", status=400)
                if ext == ".xlsx" and not _excel_available():
                    raise UIError(
                        "目前環境未安裝 openpyxl 與 defusedxml，無法讀取 Excel",
                        status=422,
                    )
                origin = "import"
                # Renamed upload of exact builtin digest still counts as demo.
                digest = hashlib.sha256(raw).hexdigest()
                if digest in self._sample_digests.values():
                    origin = "demo"
                return self._analyze_bytes(
                    filename=display,
                    content=raw,
                    origin=origin,
                )
            except UIError:
                raise
            except Exception as exc:
                raise UIError(f"分析上傳檔案失敗：{self._clean_err(exc)}", status=400) from exc

    def _analyze_bytes(
        self,
        *,
        filename: str,
        content: bytes,
        origin: str,
    ) -> dict[str, Any]:
        ext = _ext_of(filename)
        if ext == ".xlsx":
            self._validate_xlsx_bytes(content)

        owned_name = f"{_new_id()}{ext if ext in _ALLOWED_EXT else '.bin'}"
        tmp_path = Path(self._tmpdir.name) / owned_name
        try:
            if ext == ".xlsx":
                # Re-validate then write owned temp for openpyxl.
                self._validate_xlsx_bytes(content)
                tmp_path.write_bytes(content)
                self._preflight_xlsx(tmp_path)
            else:
                tmp_path.write_bytes(content)

            # Loading is bounded before the expensive bootstrap/statistical
            # path.  In particular, a 2,001-row file never reaches
            # analyze_log().
            log = load_trades(tmp_path)
            if len(log.trades) > _MAX_TRADES:
                raise UIError(f"交易筆數超過 {_MAX_TRADES} 上限", status=413)
            # A random service temp path is never part of the public result.
            log.source = filename
            result = analyze_log(log, framework="generic", n_bootstrap=5000)
            return self._store_analysis(result, origin=origin)
        except UIError:
            raise
        except Exception as exc:
            raise UIError(self._clean_err(exc), status=400) from exc
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass

    def _clean_err(self, exc: BaseException) -> str:
        msg = str(exc)
        # Never leak owned temp paths to clients.
        msg = re.sub(re.escape(self._tmpdir.name), "<temp>", msg, flags=re.IGNORECASE)
        msg = re.sub(r"[A-Za-z]:\\[^\s\"']+", "<path>", msg)
        return f"輸入無法解析：{msg}"

    def _validate_xlsx_bytes(self, content: bytes) -> None:
        bio = io.BytesIO(content)
        try:
            if not zipfile.is_zipfile(bio):
                raise UIError("XLSX 不是有效的 ZIP 容器", status=400)
            bio.seek(0)
            with zipfile.ZipFile(bio) as zf:
                infos = zf.infolist()
                if len(infos) > _XLSX_MAX_MEMBERS:
                    raise UIError("XLSX 成員數過多", status=400)
                total = 0
                for info in infos:
                    name = info.filename.replace("\\", "/")
                    if (
                        name.startswith("/")
                        or re.match(r"^[A-Za-z]:", name)
                        or ".." in name.split("/")
                        or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in name)
                    ):
                        raise UIError("XLSX 含非法路徑成員", status=400)
                    if info.flag_bits & 0x1:
                        raise UIError("不支援加密的 XLSX", status=400)
                    if info.file_size > _XLSX_MAX_MEMBER:
                        raise UIError("XLSX 單一成員過大", status=400)
                    total += info.file_size
                    if total > _XLSX_MAX_TOTAL:
                        raise UIError("XLSX 解壓後總量過大", status=400)
                    # Bound read to validate CRC without extracting to disk.
                    data = zf.read(info)
                    if len(data) > _XLSX_MAX_MEMBER:
                        raise UIError("XLSX 單一成員過大", status=400)
        except UIError:
            raise
        except zipfile.BadZipFile as exc:
            raise UIError("XLSX 損壞或無法讀取", status=400) from exc
        except Exception as exc:
            raise UIError(f"XLSX 驗證失敗：{exc}", status=400) from exc

    def _preflight_xlsx(self, path: Path) -> None:
        """Validate XML and finite worksheet bounds before handing to openpyxl.

        ``openpyxl`` iterates the declared worksheet dimension in read-only
        mode.  A tiny archive can therefore claim the entire Excel grid and
        turn loading into billions of empty-cell visits.  Check both that
        declaration and every stored row/cell reference while the XML is
        still bounded by the ZIP limits above.  Worksheet parts are resolved
        through OOXML relationships/content types rather than a directory
        name, because valid packages may store them under arbitrary names.
        """
        import defusedxml.ElementTree as det

        with zipfile.ZipFile(path, "r") as zf:
            xml_parts: dict[str, Any] = {}
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if not name.lower().endswith((".xml", ".rels")):
                    continue
                raw = zf.read(info)
                try:
                    root = det.fromstring(raw)
                except Exception as exc:
                    raise UIError(f"XLSX XML 不安全或損壞：{exc}", status=400) from exc
                xml_parts[name] = root

            content_types = next(
                (
                    root
                    for name, root in xml_parts.items()
                    if name.lower() == "[content_types].xml"
                ),
                None,
            )
            if content_types is None:
                raise UIError("XLSX 缺少內容類型定義", status=400)

            worksheet_parts: set[str] = set()
            workbook_parts: set[str] = set()
            content_type_by_part: dict[str, str] = {}
            for element in content_types.iter():
                if _xml_local_name(element.tag) != "Override":
                    continue
                part_name = _xlsx_xml_attr(element, "PartName")
                content_type = _xlsx_xml_attr(element, "ContentType")
                if part_name is None or content_type is None:
                    continue
                resolved = _xlsx_part_name(part_name)
                content_type_by_part[resolved] = content_type
                kind = content_type.lower()
                if kind.endswith("worksheet+xml"):
                    worksheet_parts.add(resolved)
                elif kind.endswith("sheet.main+xml"):
                    workbook_parts.add(resolved)

            relationships: dict[str, dict[str, tuple[str, str | None]]] = {}
            for name, root in xml_parts.items():
                source = _xlsx_relationship_source(name)
                if source is None:
                    continue
                rels: dict[str, tuple[str, str | None]] = {}
                for element in root.iter():
                    if _xml_local_name(element.tag) != "Relationship":
                        continue
                    relation_id = _xlsx_xml_attr(element, "Id")
                    relation_type = _xlsx_xml_attr(element, "Type")
                    target = _xlsx_xml_attr(element, "Target")
                    if relation_id is None or relation_type is None:
                        continue
                    kind = relation_type.rsplit("/", 1)[-1].lower()
                    target_name: str | None = None
                    if target is not None and _xlsx_xml_attr(element, "TargetMode") != "External":
                        if kind in {"officedocument", "worksheet"}:
                            target_name = _xlsx_part_name(target, base=source)
                    elif kind in {"officedocument", "worksheet"}:
                        raise UIError("XLSX 工作表參照路徑不安全或無法辨識", status=400)
                    if relation_id in rels:
                        raise UIError("XLSX 關聯識別碼重複", status=400)
                    rels[relation_id] = (kind, target_name)
                    if kind == "worksheet":
                        if target_name is None:
                            raise UIError("XLSX 工作表參照路徑不安全或無法辨識", status=400)
                        worksheet_parts.add(target_name)
                    elif kind == "officedocument" and source == "":
                        if target_name is None:
                            raise UIError("XLSX 活頁簿參照路徑不安全或無法辨識", status=400)
                        workbook_parts.add(target_name)
                relationships[source] = rels

            if not workbook_parts and "xl/workbook.xml" in xml_parts:
                workbook_parts.add("xl/workbook.xml")
            if not workbook_parts:
                raise UIError("XLSX 缺少可辨識的活頁簿參照", status=400)

            for workbook_part in workbook_parts:
                workbook_root = xml_parts.get(workbook_part)
                if workbook_root is None:
                    raise UIError("XLSX 活頁簿參照不存在", status=400)
                workbook_rels = relationships.get(workbook_part, {})
                for element in workbook_root.iter():
                    if _xml_local_name(element.tag) != "sheet":
                        continue
                    relation_id = _xlsx_xml_attr(element, "id")
                    relation = workbook_rels.get(relation_id or "")
                    if relation is None or relation[0] != "worksheet" or relation[1] is None:
                        raise UIError("XLSX 工作表參照不安全或無法辨識", status=400)
                    worksheet_parts.add(relation[1])

            if not worksheet_parts:
                raise UIError("XLSX 缺少可辨識的工作表", status=400)
            for worksheet_part in worksheet_parts:
                worksheet_root = xml_parts.get(worksheet_part)
                if worksheet_root is None or worksheet_part.lower().endswith(".rels"):
                    raise UIError("XLSX 工作表參照不存在或格式不正確", status=400)
                declared_type = content_type_by_part.get(worksheet_part)
                if declared_type is not None and not declared_type.lower().endswith("worksheet+xml"):
                    raise UIError("XLSX 工作表內容類型不正確", status=400)
                # The scanner intentionally keys off local element names, not
                # the root tag or pathname, to match openpyxl's permissive XML
                # handling while still bounding every worksheet it can load.
                self._validate_xlsx_worksheet_bounds(worksheet_root)

    @staticmethod
    def _xlsx_oversize_error() -> UIError:
        return UIError(
            "Excel 使用範圍過大（最多 2,001 列、128 欄）；"
            "請修剪未使用的列／欄後另存，或改匯出 CSV。",
            status=413,
        )

    def _validate_xlsx_worksheet_bounds(self, root: Any) -> None:
        """Reject hostile, inflated, or inconsistent worksheet coordinates."""

        dimensions = [
            element
            for element in root.iter()
            if _xml_local_name(element.tag) == "dimension"
        ]
        # Some valid write-only producers omit ``dimension``.  Their stored
        # row/cell references are still checked below; do not silently repair
        # a supplied malformed or oversized declaration.
        declared: tuple[int, int, int, int] | None = None
        if len(dimensions) > 1:
            raise UIError("Excel 工作表使用範圍重複或不一致", status=400)
        if dimensions:
            declared = _xlsx_dimension_bounds(dimensions[0].attrib.get("ref"))
            _, _, end_column, end_row = declared
            if end_row > _XLSX_MAX_ROWS or end_column > _XLSX_MAX_COLUMNS:
                raise self._xlsx_oversize_error()

        seen_rows: set[int] = set()
        row_count = 0
        cell_count = 0
        for element in root.iter():
            name = _xml_local_name(element.tag)
            if name == "row":
                row_ref = element.attrib.get("r")
                try:
                    row_number = int(str(row_ref))
                except (TypeError, ValueError) as exc:
                    raise UIError("Excel 列號格式不正確", status=400) from exc
                if row_number < 1:
                    raise UIError("Excel 列號格式不正確", status=400)
                row_count += 1
                if row_count > _XLSX_MAX_ROWS or row_number > _XLSX_MAX_ROWS:
                    raise self._xlsx_oversize_error()
                if row_number in seen_rows:
                    raise UIError("Excel 含重複列號，請重新另存檔案", status=400)
                seen_rows.add(row_number)
                if declared is not None:
                    _, start_row, _, end_row = declared
                    if not start_row <= row_number <= end_row:
                        raise UIError("Excel 使用範圍與實際資料不一致，請重新另存檔案", status=400)
            elif name == "c":
                cell_count += 1
                if cell_count > _XLSX_MAX_CELLS:
                    raise self._xlsx_oversize_error()
                column, row = _xlsx_cell_position(element.attrib.get("r"))
                if row > _XLSX_MAX_ROWS or column > _XLSX_MAX_COLUMNS:
                    raise self._xlsx_oversize_error()
                if declared is not None:
                    start_column, start_row, end_column, end_row = declared
                    if not (
                        start_column <= column <= end_column
                        and start_row <= row <= end_row
                    ):
                        raise UIError("Excel 使用範圍與實際資料不一致，請重新另存檔案", status=400)

    # -- manual records ----------------------------------------------------

    def add_record(self, data: dict) -> dict[str, Any]:
        with self._operation():
            try:
                if not isinstance(data, dict):
                    raise UIError("紀錄必須是物件", status=400)
                _reject_dangerous(data)

                exit_time = data.get("exit_time")
                if exit_time is None or str(exit_time).strip() == "":
                    raise UIError("必須提供實際出場日期（exit_time），不可留空", status=400)

                currency = data.get("currency")
                if currency is None or str(currency).strip() == "":
                    raise UIError("必須明確提供損益幣別（currency）", status=400)

                side = data.get("side")
                if side is None or str(side).strip().lower() == "unknown":
                    side = ""

                with self._state_lock:
                    if len(self._manual_rows) >= _MAX_MANUAL:
                        raise UIError(f"手動紀錄超過 {_MAX_MANUAL} 上限", status=413)
                row = build_beginner_row(
                    symbol=data.get("symbol"),
                    pnl=data.get("pnl"),
                    side=side,
                    entry_time=data.get("entry_time"),
                    exit_time=exit_time,
                    entry_price=data.get("entry_price"),
                    exit_price=data.get("exit_price"),
                    quantity=data.get("quantity"),
                    fees=data.get("fees"),
                    currency=currency,
                    strategy=data.get("strategy"),
                )
                with self._state_lock:
                    self._manual_rows.append(row)
                    self._manual_revision += 1
                    self._invalidate_analysis_locked()
                return self.state()
            except UIError:
                raise
            except Exception as exc:
                raise UIError(f"新增紀錄失敗：{exc}", status=400) from exc

    def import_records(self, filename: str, content: bytes) -> dict[str, Any]:
        """Atomically restore only a CSV previously exported by this UI.

        General broker files remain analysis-only.  This path accepts the
        exact beginner ledger header, validates every row with the same
        normalizer used for manual entry, and changes session state only after
        the whole replacement is known to be safe.
        """

        with self._operation(invalidate_analysis=True):
            try:
                if not isinstance(filename, str):
                    raise UIError("檔名必須是文字", status=400)
                display = _safe_display_filename(filename)
                if len(display) > _MAX_FILENAME:
                    raise UIError(f"檔名不可超過 {_MAX_FILENAME} 個字元", status=400)
                if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in display):
                    raise UIError("檔名含不可顯示的控制字元", status=400)
                if _ext_of(display) != ".csv":
                    raise UIError("逐筆紀錄只接受本工具下載的 CSV", status=400)
                rows = self._parse_beginner_records_csv(content)
                with self._state_lock:
                    self._manual_rows = rows
                    self._manual_revision += 1
                    self._invalidate_analysis_locked()
                return self.state()
            except UIError:
                raise
            except Exception as exc:
                raise UIError(f"載入逐筆紀錄失敗：{exc}", status=400) from exc

    @staticmethod
    def _parse_beginner_records_csv(content: bytes) -> list[dict[str, str]]:
        if not isinstance(content, (bytes, bytearray)):
            raise UIError("逐筆紀錄內容必須是位元組資料", status=400)
        raw = bytes(content)
        if not raw:
            raise UIError("逐筆紀錄檔案是空的", status=400)
        if len(raw) > _MAX_UPLOAD_BYTES:
            raise UIError("逐筆紀錄檔案超過 2MiB 上限", status=413)
        try:
            text = raw.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise UIError("逐筆紀錄必須是 UTF-8 CSV", status=400) from exc
        if "\x00" in text:
            raise UIError("逐筆紀錄含不支援的控制字元", status=400)

        try:
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            header = next(reader, None)
        except csv.Error as exc:
            raise UIError("逐筆紀錄 CSV 格式不正確", status=400) from exc
        if header != list(BEGINNER_COLUMNS):
            raise UIError(
                "這不是本工具下載的逐筆紀錄 CSV；"
                "一般券商檔案請用「匯入現有檔案」分析。",
                status=400,
            )

        restored: list[dict[str, str]] = []
        try:
            for values in reader:
                # Tolerate accidental empty lines without turning them into a
                # fake transaction; every nonempty line must be complete.
                if not values or all(not value.strip() for value in values):
                    continue
                source_line = reader.line_num
                if len(values) != len(BEGINNER_COLUMNS):
                    raise UIError(f"第 {source_line} 行欄位數不正確", status=400)
                if len(restored) >= _MAX_MANUAL:
                    raise UIError(f"手動紀錄超過 {_MAX_MANUAL} 上限", status=413)
                source = dict(zip(BEGINNER_COLUMNS, values))
                exit_time = source["出場時間"]
                currency = source["損益幣別"]
                if not exit_time.strip():
                    raise UIError(f"第 {source_line} 行必須提供實際出場日期", status=400)
                if not currency.strip():
                    raise UIError(f"第 {source_line} 行必須提供損益幣別", status=400)
                try:
                    row = build_beginner_row(
                        symbol=source["代號"],
                        pnl=source["損益"],
                        side=source["方向"],
                        entry_time=source["進場時間"],
                        exit_time=exit_time,
                        entry_price=source["進場價"],
                        exit_price=source["出場價"],
                        quantity=source["數量"],
                        fees=source["手續費"],
                        currency=currency,
                        strategy=source["策略"],
                    )
                except Exception as exc:
                    raise UIError(f"第 {source_line} 行無法載入：{exc}", status=400) from exc
                restored.append(row)
        except csv.Error as exc:
            raise UIError("逐筆紀錄 CSV 格式不正確", status=400) from exc
        return restored

    def remove_record(self, index: int, revision: int) -> dict[str, Any]:
        """Remove exactly one session row after optimistic revision checking."""

        with self._operation():
            if isinstance(index, bool) or not isinstance(index, int):
                raise UIError("index 必須是非負整數", status=400)
            if isinstance(revision, bool) or not isinstance(revision, int):
                raise UIError("revision 必須是非負整數", status=400)
            if index < 0 or revision < 0:
                raise UIError("index 與 revision 必須是非負整數", status=400)
            with self._state_lock:
                if revision != self._manual_revision:
                    raise UIError("紀錄版本已更新，請重新載入後再移除", status=409)
                if index >= len(self._manual_rows):
                    raise UIError("找不到要移除的紀錄", status=404)
                del self._manual_rows[index]
                self._manual_revision += 1
                self._invalidate_analysis_locked()
            return self.state()

    def analyze_records(self) -> dict[str, Any]:
        with self._operation(invalidate_analysis=True):
            try:
                with self._state_lock:
                    rows = [dict(row) for row in self._manual_rows]
                if not rows:
                    raise UIError("尚無手動紀錄可分析", status=400)
                if len(rows) > _MAX_TRADES:
                    raise UIError(f"交易筆數超過 {_MAX_TRADES} 上限", status=413)
                buf = io.StringIO()
                writer = csv.DictWriter(buf, fieldnames=list(BEGINNER_COLUMNS))
                writer.writeheader()
                for row in rows:
                    writer.writerow({k: row.get(k, "") for k in BEGINNER_COLUMNS})
                raw = ("\ufeff" + buf.getvalue()).encode("utf-8")
                return self._analyze_bytes(
                    filename="manual_records.csv",
                    content=raw,
                    origin="manual",
                )
            except UIError:
                raise
            except Exception as exc:
                raise UIError(f"分析手動紀錄失敗：{exc}", status=400) from exc

    # -- scan --------------------------------------------------------------

    def scan(self, text: str) -> dict[str, Any]:
        with self._operation():
            try:
                if not isinstance(text, str):
                    raise UIError("掃描內容必須是文字", status=400)
                if len(text) > _MAX_SCAN_CHARS:
                    raise UIError(f"文字超過 {_MAX_SCAN_CHARS} 字元上限", status=413)
                obj = scan_text(text)
                return _finite({"result": obj.as_dict(), "text_report": render_scan(obj)})
            except UIError:
                raise
            except Exception as exc:
                raise UIError(f"掃描失敗：{exc}", status=400) from exc

    # -- analysis store ----------------------------------------------------

    def _store_analysis(
        self,
        result: Any,
        *,
        origin: str,
        api_provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._state_lock:
            self._analysis_revision += 1
            analysis_id = _new_id()
            revision = self._analysis_revision
            visuals = build_visuals(result)
            visuals["provenance"] = {
                "analysis_id": analysis_id,
                "revision": revision,
                "origin": origin,
                "demo": origin == "demo",
                "source": str(getattr(result.log, "source", "") or ""),
            }
            if origin == "api":
                if not isinstance(api_provenance, dict):
                    raise UIError("API 分析缺少資料集來源綁定", status=500)
                visuals["provenance"]["api"] = dict(api_provenance)
            payload = {
                "id": analysis_id,
                "revision": revision,
                "origin": origin,
                "result": result.as_dict(),
                "metrics": result.metrics.as_dict(),
                "text_report": result.text_report,
                "readiness": _readiness(result),
                "visuals": visuals,
                "can_live": False,
            }
            if origin == "api":
                payload["api_provenance"] = dict(api_provenance or {})
            payload = _finite(payload)
            self._analysis = payload
            self._analysis_result = result
            self._risk_job = None
            self._risk_cancel = None
            if self._risk_thread is not None and not self._risk_thread.is_alive():
                self._risk_thread = None
        return payload

    def _require_analysis(self, analysis_id: Optional[str]) -> Any:
        if self._analysis is None or self._analysis_result is None:
            raise UIError("目前沒有可匯出的分析結果", status=409)
        if not analysis_id:
            raise UIError("此匯出需要提供 analysis_id", status=400)
        if analysis_id != self._analysis.get("id"):
            raise UIError("analysis_id 已過期或不相符", status=409)
        return self._analysis_result

    def _export_provenance_locked(self) -> dict[str, Any]:
        """Return only server-owned provenance for the current analysis."""

        analysis = self._analysis
        if analysis is None:
            raise UIError("目前沒有可匯出的分析結果", status=409)
        origin = analysis.get("origin")
        if origin not in _EXPORT_SOURCE_LABELS:
            raise UIError("分析來源無法辨識", status=500)
        analysis_id = analysis.get("id")
        revision = analysis.get("revision")
        if not isinstance(analysis_id, str) or not analysis_id:
            raise UIError("分析識別資料無效", status=500)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise UIError("分析版本資料無效", status=500)
        provenance = {
            "origin": origin,
            "demo": origin == "demo",
            "analysis_id": analysis_id,
            "revision": revision,
        }
        if origin == "api":
            api = analysis.get("api_provenance")
            if not isinstance(api, dict):
                raise UIError("API 分析來源綁定無效", status=500)
            provenance["api"] = dict(api)
        return provenance

    # -- asynchronous capital-risk scenario -------------------------------

    def _public_risk_job_locked(self, *, include_result: bool) -> dict[str, Any] | None:
        job = self._risk_job
        if job is None:
            return None
        public = {
            "job_id": job["job_id"],
            "analysis_id": job["analysis_id"],
            "revision": job["revision"],
            "status": job["status"],
            "progress": dict(job["progress"]),
            "cancel_requested": bool(job.get("cancel_requested")),
        }
        if job.get("error"):
            public["error"] = str(job["error"])
        if include_result and job.get("status") == "completed":
            public["result"] = job.get("result")
        return public

    @staticmethod
    def _risk_number(value: Any, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise UIError(f"{label} 必須是有限數字", status=400)
        try:
            number = float(value)
        except (OverflowError, ValueError) as exc:
            raise UIError(f"{label} 超出可計算範圍", status=400) from exc
        if not math.isfinite(number):
            raise UIError(f"{label} 必須是有限數字", status=400)
        return number

    @staticmethod
    def _risk_int(value: Any, label: str, minimum: int, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise UIError(f"{label} 必須是整數", status=400)
        if not minimum <= value <= maximum:
            raise UIError(f"{label} 必須介於 {minimum} 與 {maximum}", status=400)
        return value

    def start_risk_simulation(
        self,
        *,
        analysis_id: Any,
        revision: Any,
        start_equity: Any,
        currency: Any,
        threshold_kind: Any,
        threshold_value: Any,
        future_trades: Any,
        paths: Any,
    ) -> dict[str, Any]:
        """Validate, bind, and dispatch one non-daemon owned worker."""

        if not self._operation_lock.acquire(blocking=False):
            raise UIError("服務忙碌中，請稍後再試", status=409)
        release_gate = True
        try:
            with self._state_lock:
                if self._closed:
                    raise UIError("本次使用已結束", status=409)
                if not isinstance(analysis_id, str) or not analysis_id:
                    raise UIError("analysis_id 必須是目前分析識別碼", status=400)
                if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
                    raise UIError("revision 必須是正整數", status=400)
                result = self._require_analysis(analysis_id)
                analysis = self._analysis
                assert analysis is not None
                if revision != analysis.get("revision"):
                    raise UIError("analysis revision 已過期或不相符", status=409)
                equity = self._risk_number(start_equity, "start_equity")
                if equity <= 0.0:
                    raise UIError("start_equity 必須大於 0", status=400)
                if not isinstance(currency, str):
                    raise UIError("currency 必須是目前分析的結算幣別", status=400)
                requested_currency = currency.strip().upper()
                if not re.fullmatch(r"[A-Z0-9]{2,12}", requested_currency):
                    raise UIError("currency 格式不正確", status=400)
                if not isinstance(threshold_kind, str):
                    raise UIError("threshold_kind 必須是文字", status=400)
                threshold_value_number = self._risk_number(
                    threshold_value, "threshold_value"
                )
                try:
                    threshold_amount(
                        equity, threshold_kind.strip(), threshold_value_number
                    )
                except ValueError as exc:
                    raise UIError(str(exc), status=400) from exc
                horizon = self._risk_int(future_trades, "future_trades", 1, 1000)
                path_count = self._risk_int(paths, "paths", 100, 5000)
                if horizon * path_count > 1_000_000:
                    raise UIError(
                        "future_trades × paths 不可超過 1,000,000", status=400
                    )

                integrity = result.log.integrity_as_dict()
                if not integrity.get("complete"):
                    raise UIError(
                        "交易紀錄完整性未通過，不提供推論式資金情境", status=422
                    )
                metrics = result.metrics
                actual_currency = str(metrics.pnl_currency or "").strip().upper()
                if not metrics.currency_reliable or not actual_currency:
                    raise UIError(
                        metrics.currency_note or "損益幣別未可靠確認，無法執行資金情境",
                        status=422,
                    )
                if requested_currency != actual_currency:
                    raise UIError(
                        "currency 必須和目前分析的帳戶結算幣別完全相同", status=400
                    )
                pnl_values: list[float] = []
                for trade in result.log:
                    pnl_values.append(self._risk_number(trade.pnl, "trade pnl"))
                if len(pnl_values) < 10:
                    raise UIError("至少需要 10 筆完整的已平倉損益", status=422)
                if len(pnl_values) > _MAX_TRADES:
                    raise UIError(f"交易筆數超過 {_MAX_TRADES} 上限", status=413)

                job_id = _new_id()
                cancel = threading.Event()
                job = {
                    "job_id": job_id,
                    "analysis_id": analysis_id,
                    "revision": revision,
                    "status": "running",
                    "progress": {"completed_paths": 0, "paths": path_count},
                    "cancel_requested": False,
                    "result": None,
                    "error": None,
                }
                self._risk_job = job
                self._risk_cancel = cancel
                self._busy = True
                provenance = {
                    "analysis_id": analysis_id,
                    "revision": revision,
                    "origin": analysis.get("origin"),
                    "demo": analysis.get("origin") == "demo",
                    "source": str(getattr(result.log, "source", "") or ""),
                }
                worker = threading.Thread(
                    target=self._run_risk_worker,
                    kwargs={
                        "job_id": job_id,
                        "pnls": tuple(pnl_values),
                        "start_equity": equity,
                        "currency": actual_currency,
                        "threshold_kind": threshold_kind.strip(),
                        "threshold_value": threshold_value_number,
                        "future_trades": horizon,
                        "paths": path_count,
                        "cancel": cancel,
                        "provenance": provenance,
                    },
                    name=f"ui-risk-{job_id[:8]}",
                    daemon=False,
                )
                self._risk_thread = worker
            try:
                worker.start()
            except BaseException:
                with self._state_lock:
                    if self._risk_job is job:
                        self._risk_job = None
                        self._risk_cancel = None
                        self._risk_thread = None
                        self._busy = False
                raise
            release_gate = False
            with self._state_lock:
                public = self._public_risk_job_locked(include_result=False)
                assert public is not None
                return public
        except UIError:
            raise
        except Exception as exc:
            raise UIError(f"無法啟動資金情境：{exc}", status=500) from exc
        finally:
            if release_gate:
                self._operation_lock.release()

    def _run_risk_worker(
        self,
        *,
        job_id: str,
        pnls: tuple[float, ...],
        start_equity: float,
        currency: str,
        threshold_kind: str,
        threshold_value: float,
        future_trades: int,
        paths: int,
        cancel: threading.Event,
        provenance: dict[str, Any],
    ) -> None:
        def progress(completed: int, total: int) -> None:
            with self._state_lock:
                if self._risk_job is not None and self._risk_job.get("job_id") == job_id:
                    self._risk_job["progress"] = {
                        "completed_paths": int(completed),
                        "paths": int(total),
                    }

        try:
            scenario = simulate_capital_risk(
                pnls,
                start_equity=start_equity,
                currency=currency,
                threshold_kind=threshold_kind,
                threshold_value=threshold_value,
                future_trades=future_trades,
                paths=paths,
                seed=_RISK_SEED,
                cancel_event=cancel,
                progress_callback=progress,
                deadline_seconds=_RISK_DEADLINE_SECONDS,
            )
            scenario["simulation_id"] = job_id
            scenario["analysis_id"] = provenance["analysis_id"]
            scenario["revision"] = provenance["revision"]
            scenario["provenance"] = dict(provenance)
            with self._state_lock:
                job = self._risk_job
                analysis = self._analysis
                if job is None or job.get("job_id") != job_id:
                    return
                if (
                    analysis is None
                    or analysis.get("id") != provenance["analysis_id"]
                    or analysis.get("revision") != provenance["revision"]
                ):
                    job["status"] = "failed"
                    job["error"] = "分析版本已變更，情境結果未採用"
                    job["result"] = None
                elif cancel.is_set():
                    job["status"] = "cancelled"
                    job["result"] = None
                else:
                    job["status"] = "completed"
                    job["progress"] = {
                        "completed_paths": paths,
                        "paths": paths,
                    }
                    job["result"] = _finite(scenario)
        except SimulationCancelled:
            with self._state_lock:
                if self._risk_job is not None and self._risk_job.get("job_id") == job_id:
                    self._risk_job["status"] = "cancelled"
                    self._risk_job["result"] = None
        except TimeoutError:
            with self._state_lock:
                if self._risk_job is not None and self._risk_job.get("job_id") == job_id:
                    self._risk_job["status"] = "failed"
                    self._risk_job["error"] = "資金情境超過 15 秒安全計算時間"
                    self._risk_job["result"] = None
        except Exception:
            with self._state_lock:
                if self._risk_job is not None and self._risk_job.get("job_id") == job_id:
                    self._risk_job["status"] = "failed"
                    self._risk_job["error"] = "資金情境計算失敗；輸入未被改寫"
                    self._risk_job["result"] = None
        finally:
            with self._state_lock:
                if self._risk_job is not None and self._risk_job.get("job_id") == job_id:
                    self._busy = False
                # Release while still holding the state lock. A caller that
                # observes a terminal status can acquire the operation gate,
                # then waits until this fully committed state is visible.
                self._operation_lock.release()

    def risk_simulation_status(self, job_id: Any) -> dict[str, Any]:
        if not isinstance(job_id, str) or not _RISK_JOB_ID.fullmatch(job_id):
            raise UIError("找不到指定資金情境", status=404)
        with self._state_lock:
            job = self._risk_job
            if job is None or job.get("job_id") != job_id:
                raise UIError("找不到指定資金情境", status=404)
            analysis = self._analysis
            if (
                analysis is None
                or analysis.get("id") != job.get("analysis_id")
                or analysis.get("revision") != job.get("revision")
            ):
                raise UIError("資金情境所屬分析已過期", status=409)
            public = self._public_risk_job_locked(include_result=True)
            assert public is not None
            return _finite(public)

    def cancel_risk_simulation(self, job_id: Any) -> dict[str, Any]:
        if not isinstance(job_id, str) or not _RISK_JOB_ID.fullmatch(job_id):
            raise UIError("找不到指定資金情境", status=404)
        with self._state_lock:
            job = self._risk_job
            if job is None or job.get("job_id") != job_id:
                raise UIError("找不到指定資金情境", status=404)
            analysis = self._analysis
            if (
                analysis is None
                or analysis.get("id") != job.get("analysis_id")
                or analysis.get("revision") != job.get("revision")
            ):
                raise UIError("資金情境所屬分析已過期", status=409)
            if job.get("status") == "running" and self._risk_cancel is not None:
                job["cancel_requested"] = True
                self._risk_cancel.set()
            public = self._public_risk_job_locked(include_result=True)
            assert public is not None
            return _finite(public)

    def _require_simulation_locked(self, simulation_id: str | None) -> dict[str, Any] | None:
        if simulation_id is None:
            return None
        if not isinstance(simulation_id, str) or not _RISK_JOB_ID.fullmatch(simulation_id):
            raise UIError("simulation_id 格式不正確", status=400)
        job = self._risk_job
        if job is None or job.get("job_id") != simulation_id:
            raise UIError("simulation_id 已過期或不存在", status=404)
        analysis = self._analysis
        if (
            analysis is None
            or analysis.get("id") != job.get("analysis_id")
            or analysis.get("revision") != job.get("revision")
        ):
            raise UIError("simulation_id 所屬分析已過期", status=409)
        if job.get("status") != "completed" or job.get("result") is None:
            raise UIError("資金情境尚未完成，不能匯出", status=409)
        public = self._public_risk_job_locked(include_result=True)
        assert public is not None
        return public


    def _add_export_source_banner(
        self, document: str, *, origin: str, marker: str
    ) -> str:
        """Insert a visible source label using only server-known constant text."""

        label = _EXPORT_SOURCE_LABELS.get(origin)
        if label is None:
            raise UIError("分析來源無法辨識", status=500)
        banner = (
            '<div role="note" style="margin:0 0 16px;padding:12px 14px;'
            'background:#7f1d1d;border:2px solid #fecaca;border-radius:8px;'
            'color:#fff1f2;font-weight:800;font-size:18px;line-height:1.5">'
            f"{label}</div>"
        )
        if marker not in document:
            raise UIError("匯出格式無法加入資料來源標示", status=500)
        return document.replace(marker, f"{marker}\n{banner}", 1)

    # -- export / artifacts ------------------------------------------------

    def export(
        self,
        kind: str,
        analysis_id: str | None = None,
        simulation_id: str | None = None,
    ) -> Artifact:
        with self._operation():
            try:
                if not isinstance(kind, str):
                    raise UIError("匯出種類必須是文字", status=400)
                k = kind.strip().lower()
                if k in {"json", "html", "card"}:
                    with self._state_lock:
                        result = self._require_analysis(analysis_id)
                        provenance = self._export_provenance_locked()
                        assert self._analysis is not None
                        visuals = self._analysis.get("visuals") or {}
                        simulation = self._require_simulation_locked(simulation_id)
                    if k == "json":
                        payload = _finite(result.as_dict())
                        payload["ui_provenance"] = provenance
                        payload["visuals"] = _finite(visuals)
                        if simulation is not None:
                            payload["risk_simulation"] = _finite(simulation)
                        body = json.dumps(
                            payload,
                            ensure_ascii=False,
                            indent=2,
                        ).encode("utf-8")
                        return self._put_artifact(
                            "analysis.json", "application/json", body
                        )
                    if k == "html":
                        html = render_html_report(result)
                        marker = '<div class="disclaimer">'
                        if marker not in html:
                            raise UIError("HTML 匯出格式無法加入證據圖表", status=500)
                        html = html.replace(
                            marker,
                            render_evidence_report(visuals, simulation) + "\n" + marker,
                            1,
                        )
                        html = self._add_export_source_banner(
                            html,
                            origin=provenance["origin"],
                            marker='<div class="wrap">',
                        )
                        return self._put_artifact(
                            "report.html", "text/html; charset=utf-8", html.encode("utf-8")
                        )
                    card_document = render_share_card(result)
                    card_marker = '<div class="foot">'
                    if card_marker not in card_document:
                        raise UIError("分享卡格式無法加入分位摘要", status=500)
                    card_document = card_document.replace(
                        card_marker,
                        render_share_summary(visuals, simulation) + "\n" + card_marker,
                        1,
                    )
                    card = self._add_export_source_banner(
                        card_document,
                        origin=provenance["origin"],
                        marker='<div class="card">',
                    )
                    return self._put_artifact(
                        "share_card.html",
                        "text/html; charset=utf-8",
                        card.encode("utf-8"),
                    )
                if k == "records":
                    return self._export_records_csv()
                if k == "template":
                    return self._export_template_csv()
                raise UIError(f"未知匯出種類：{kind!r}", status=400)
            except UIError:
                raise
            except Exception as exc:
                raise UIError(f"匯出失敗：{exc}", status=400) from exc

    def _export_records_csv(self) -> Artifact:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(BEGINNER_COLUMNS))
        writer.writeheader()
        for row in self._manual_rows:
            writer.writerow({c: row.get(c, "") for c in BEGINNER_COLUMNS})
        raw = ("\ufeff" + buf.getvalue()).encode("utf-8")
        return self._put_artifact("records.csv", "text/csv; charset=utf-8", raw)

    def _export_template_csv(self) -> Artifact:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(BEGINNER_COLUMNS))
        writer.writeheader()
        raw = ("\ufeff" + buf.getvalue()).encode("utf-8")
        return self._put_artifact("template.csv", "text/csv; charset=utf-8", raw)

    def scaffold(
        self,
        project_name: str,
        symbols: list[str],
        market: str = "us_stock",
    ) -> Artifact:
        with self._operation():
            try:
                if not isinstance(project_name, str):
                    raise UIError("專案名稱必須是文字", status=400)
                if not isinstance(market, str):
                    raise UIError("market 必須是文字", status=400)
                name = project_name.strip()
                self._validate_project_name(name)
                if not isinstance(symbols, list) or not symbols:
                    raise UIError("symbols 必須是非空清單", status=400)
                if len(symbols) > 100:
                    raise UIError("symbols 最多 100 個", status=413)
                if any(not isinstance(symbol, str) for symbol in symbols):
                    raise UIError("symbols 每一項都必須是文字", status=400)
                syms = [symbol.strip() for symbol in symbols if symbol.strip()]
                if not syms:
                    raise UIError("symbols 必須是非空清單", status=400)
                options = ScaffoldOptions(
                    project_name=name,
                    broker="paper",
                    chart="lightweight",
                    market=market or "us_stock",
                    symbols=syms,
                    verdict=None,
                    stage=None,  # ALWAYS None — keeps live flags false
                )
                files = generate_project(options)
                zbytes = self._zip_scaffold(name, files)
                return self._put_artifact(
                    f"{name}.zip",
                    "application/zip",
                    zbytes,
                )
            except UIError:
                raise
            except Exception as exc:
                raise UIError(f"產生專案鷹架失敗：{exc}", status=400) from exc

    def _validate_project_name(self, name: str) -> None:
        if not name:
            raise UIError("專案名稱不可空白", status=400)
        if name.endswith(".") or name.endswith(" "):
            raise UIError("專案名稱不可以點或空白結尾", status=400)
        stem = name.split(".")[0].upper()
        if stem in _WINDOWS_RESERVED:
            raise UIError("專案名稱不可使用 Windows 保留字", status=400)
        if re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
            raise UIError("專案名稱含非法字元", status=400)

    def _zip_scaffold(self, project_name: str, files: list[Any]) -> bytes:
        buf = io.BytesIO()
        prefix = project_name.strip("/\\")
        readme = (
            "# 產生的紙上交易鷹架（非實盤）\n\n"
            "此 ZIP 由 UI 服務自動產生，僅供學習與紙上模擬。\n"
            "- broker 固定為 paper；不會啟用實盤交易旗標。\n"
            "- 不代表任何真實券商資料或績效保證。\n"
            "- 請勿直接對真實資金執行產生的程式。\n"
        )
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{prefix}/README_SCAFFOLD.md", readme.encode("utf-8"))
            for gf in files:
                rel = str(getattr(gf, "relpath", "") or "").replace("\\", "/")
                if (
                    not rel
                    or rel.startswith("/")
                    or re.match(r"^[A-Za-z]:", rel)
                    or ".." in rel.split("/")
                    or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in rel)
                ):
                    raise UIError(f"鷹架路徑不安全：{rel!r}", status=400)
                content = getattr(gf, "content", "")
                if isinstance(content, bytes):
                    data = content
                else:
                    data = str(content).encode("utf-8")
                # Ensure Chinese filenames encode cleanly.
                arcname = f"{prefix}/{rel}"
                info = zipfile.ZipInfo(arcname)
                info.flag_bits |= 0x800  # UTF-8
                zf.writestr(info, data)
        return buf.getvalue()

    def artifact(self, artifact_id: str) -> Artifact:
        with self._state_lock:
            art = self._artifacts.get(str(artifact_id))
            if art is None:
                raise UIError("找不到指定產物", status=404)
            return art

    def _put_artifact(self, filename: str, media_type: str, content: bytes) -> Artifact:
        safe_name = _safe_display_filename(filename)
        if len(safe_name) > _MAX_FILENAME:
            safe_name = safe_name[:_MAX_FILENAME]
        art = Artifact(
            id=_new_id(),
            filename=safe_name,
            media_type=media_type,
            content=content,
        )
        if len(content) > _ARTIFACT_MAX_BYTES:
            raise UIError("產物超過快取容量上限", status=413)
        with self._state_lock:
            # Evict oldest until under caps.
            while (
                len(self._artifacts) >= _ARTIFACT_MAX_ENTRIES
                or self._artifact_bytes + len(content) > _ARTIFACT_MAX_BYTES
            ) and self._artifacts:
                oldest_id = next(iter(self._artifacts))
                old = self._artifacts.pop(oldest_id)
                self._artifact_bytes -= len(old.content)
            self._artifacts[art.id] = art
            self._artifact_bytes += len(content)
        return art
