"""Bounded, session-scoped orchestration for read-only API data jobs.

The manager owns exactly one non-daemon worker.  It never persists credentials,
never exposes them through public job objects, and only publishes a dataset
after the worker has finished and the complete payload has passed the size and
secret-key checks below.
"""

from __future__ import annotations

import csv
import io
import json
import re
import threading
from importlib import metadata as importlib_metadata
from importlib.util import find_spec
from typing import Any, Callable
from uuid import uuid4

from core.data_api.context import SyncContext
from core.data_api.credentials import (
    CredentialPair,
    SystemCredentialVault,
)
from core.data_api.models import validate_query
from core.data_api.normalization import normalize_closed_dataset
from core.data_api.providers import create_client
from core.data_api.transport import DataAPIError


_PROVIDERS = frozenset({"pionex", "binance", "shioaji"})
_PRIVATE_KINDS = frozenset({"fills", "account"})
_DATASET_KINDS = frozenset({"candles", "fills", "account", "realizations"})
_PROFILE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_MAX_DATASET_BYTES = 4 * 1024 * 1024
_JOIN_SECONDS = 5.0
_SAFE_ERROR_CODES = frozenset(
    {
        "auth",
        "source_restricted",
        "rate_limit",
        "timeout",
        "network",
        "invalid_response",
        "oversize",
        "cancelled",
    }
)
_ERROR_MESSAGES = {
    "auth": "憑證缺少、無效，或唯讀資料權限尚未通過",
    "source_restricted": "公開行情查詢受到資料來源限制（HTTP 403），不需要輸入私有憑證。",
    "rate_limit": "資料來源目前限制請求頻率",
    "timeout": "唯讀資料請求超過 45 秒期限",
    "network": "無法連線到固定的資料來源",
    "invalid_response": "資料來源回應格式無法安全採用",
    "oversize": "資料超過本機工作階段的 4MiB 上限",
    "cancelled": "唯讀資料工作已取消",
}

# Short placeholders are common in tests and ordinary dataset values.  They
# must still be rejected when echoed exactly, while real API credentials are
# also rejected when embedded in a longer diagnostic string.
_SECRET_SUBSTRING_MIN_LENGTH = 16
_FORBIDDEN_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "api_secret",
        "secret",
        "secret_key",
        "password",
        "passphrase",
        "authorization",
        "signature",
        "credential",
        "credentials",
        "access_token",
        "refresh_token",
        "account_number",
        "account_no",
        "person_id",
    }
)


def _provider(value: Any) -> str:
    if not isinstance(value, str) or value.strip().lower() not in _PROVIDERS:
        raise DataAPIError("invalid_response", status=400)
    return value.strip().lower()


def _profile(value: Any) -> str:
    if not isinstance(value, str) or _PROFILE.fullmatch(value.strip().lower()) is None:
        raise DataAPIError("invalid_response", status=400)
    return value.strip().lower()


def _credential_text(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        raise DataAPIError("auth", status=400)
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise DataAPIError("auth", status=400)
    return value


def _error_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in _SAFE_ERROR_CODES:
        return code
    return "invalid_response"


def _known_credential_values(credentials: Any) -> tuple[str, ...]:
    """Return only in-memory values that must never cross the public boundary."""

    if credentials is None:
        return ()
    values: list[str] = []
    for attribute in ("api_key", "api_secret"):
        candidate = getattr(credentials, attribute, None)
        if isinstance(candidate, str) and candidate:
            values.append(candidate)
    return tuple(values)


def _assert_no_secret_keys(
    value: Any,
    credential_values: tuple[str, ...] = (),
    _seen: set[int] | None = None,
) -> None:
    """Reject public payloads that contain credential field names or values.

    The error deliberately carries no field name or matched text.  Provider
    diagnostics are untrusted, and a chained exception could otherwise turn a
    server-side serialization failure into a credential disclosure in logs.
    """

    if isinstance(value, str):
        for credential in credential_values:
            if len(credential) >= _SECRET_SUBSTRING_MIN_LENGTH:
                matched = credential in value
            else:
                matched = credential == value
            if matched:
                raise DataAPIError("invalid_response")
        return

    if isinstance(value, dict):
        seen = _seen if _seen is not None else set()
        object_id = id(value)
        if object_id in seen:
            raise DataAPIError("invalid_response")
        seen.add(object_id)
        try:
            for key, child in value.items():
                if not isinstance(key, str):
                    raise DataAPIError("invalid_response")
                normalized = key.strip().lower().replace("-", "_")
                if normalized in _FORBIDDEN_KEYS:
                    raise DataAPIError("invalid_response")
                _assert_no_secret_keys(key, credential_values, seen)
                _assert_no_secret_keys(child, credential_values, seen)
        finally:
            seen.discard(object_id)
        return

    if isinstance(value, (list, tuple)):
        seen = _seen if _seen is not None else set()
        object_id = id(value)
        if object_id in seen:
            raise DataAPIError("invalid_response")
        seen.add(object_id)
        try:
            for child in value:
                _assert_no_secret_keys(child, credential_values, seen)
        finally:
            seen.discard(object_id)


def _shioaji_sdk_capability() -> dict[str, Any]:
    """Report package availability without importing or logging in to the SDK."""

    try:
        available = find_spec("shioaji") is not None
    except (AttributeError, ImportError, ValueError):
        available = False
    version: str | None = None
    if available:
        try:
            version = importlib_metadata.version("shioaji")
        except importlib_metadata.PackageNotFoundError:
            pass
        except Exception:
            # Metadata is advisory UI capability only.  Do not import an SDK
            # or expose an environment-specific exception to the browser.
            pass
    return {"available": available, "version": version}


def _bounded_dataset(
    value: Any,
    provider: str,
    credentials: CredentialPair | None = None,
    credential_values: tuple[str, ...] = (),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DataAPIError("invalid_response")
    if value.get("schema_version") != "api-data-v1":
        raise DataAPIError("invalid_response")
    if value.get("provider") != provider or value.get("kind") not in _DATASET_KINDS:
        raise DataAPIError("invalid_response")
    known_values = tuple(dict.fromkeys((*_known_credential_values(credentials), *credential_values)))
    _assert_no_secret_keys(value, known_values)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise DataAPIError("invalid_response") from None
    if len(encoded) > _MAX_DATASET_BYTES:
        raise DataAPIError("oversize")
    # A JSON round trip detaches caller-owned mutable containers and rejects
    # provider-specific Python objects before the result becomes public.
    return json.loads(encoded.decode("utf-8"))


def _csv_cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if value is None:
        return ""
    text = str(value)
    # Preserve decimal strings exactly while preventing non-numeric spreadsheet
    # formula prefixes in user-requested CSV downloads.
    if text.startswith(("=", "+", "@")):
        return "'" + text
    if text.startswith("-") and re.fullmatch(r"-[0-9]+(?:\.[0-9]+)?", text) is None:
        return "'" + text
    return text


class DataManager:
    """Own credentials and one latest-only read-only data job."""

    def __init__(self, vault: Any = None, client_factory: Any = None) -> None:
        self._lock = threading.RLock()
        self._closed = False
        self._closing = False
        self._revision = 0
        self._job: dict[str, Any] | None = None
        self._worker: threading.Thread | None = None
        self._session_credentials: dict[tuple[str, str], CredentialPair] = {}
        self._credential_values: set[str] = set()
        self._configured: dict[tuple[str, str], dict[str, Any]] = {}
        self._client_factory = client_factory or create_client
        if vault is not None:
            self._vault = vault
        else:
            try:
                self._vault = SystemCredentialVault()
            except Exception:
                # Session-only credentials remain available on systems without
                # a persistent OS credential vault.
                self._vault = None

    # -- credentials -------------------------------------------------

    def configure_credentials(
        self,
        provider: str,
        profile: str = "default",
        api_key: str = "",
        api_secret: str = "",
        remember: bool = False,
        use_saved: bool = False,
    ) -> dict[str, Any]:
        provider_name = _provider(provider)
        profile_name = _profile(profile)
        if type(remember) is not bool or type(use_saved) is not bool:
            raise DataAPIError("auth", status=400)
        with self._lock:
            if self._closed or self._closing:
                raise DataAPIError("cancelled", status=409)
            if self._worker is not None and self._worker.is_alive():
                raise DataAPIError("invalid_response", status=409)

        if use_saved:
            if api_key or api_secret or remember:
                raise DataAPIError("auth", status=400)
            if self._vault is None:
                raise DataAPIError("auth", status=422)
            try:
                pair = self._vault.read(provider_name, profile_name)
            except Exception:
                raise DataAPIError("auth", status=422) from None
            if pair is None:
                raise DataAPIError("auth", status=404)
            source = "windows_vault"
            remembered = True
        else:
            key = _credential_text(api_key)
            secret = _credential_text(api_secret)
            pair = CredentialPair(key, secret)
            source = "session"
            remembered = False
            if remember:
                if self._vault is None:
                    raise DataAPIError("auth", status=422)
                try:
                    self._vault.store(provider_name, profile_name, pair)
                except Exception:
                    raise DataAPIError("auth", status=422) from None
                source = "windows_vault"
                remembered = True

        key = (provider_name, profile_name)
        with self._lock:
            if self._closed or self._closing:
                raise DataAPIError("cancelled", status=409)
            self._session_credentials[key] = pair
            self._credential_values.update(_known_credential_values(pair))
            self._configured[key] = {
                "provider": provider_name,
                "profile": profile_name,
                "configured": True,
                "authentication_verified": False,
                "remembered": remembered,
                "source": source,
            }
            # A changed credential reference invalidates any older terminal
            # dataset; an active job was rejected above.
            self._job = None
            return dict(self._configured[key])

    # -- public job state --------------------------------------------

    def _public_job_locked(
        self, *, include_result: bool = False
    ) -> dict[str, Any] | None:
        job = self._job
        if job is None:
            return None
        public = {
            "job_id": job["job_id"],
            "revision": job["revision"],
            "provider": job["provider"],
            "kind": job["kind"],
            "profile": job["profile"],
            "status": job["status"],
            "progress": dict(job["progress"]),
            "cancel_requested": bool(job.get("cancel_requested")),
            "authentication_verified": bool(job.get("authentication_verified")),
        }
        if job.get("error"):
            code = str(job["error"])
            public["error"] = {
                "code": code,
                "message": _ERROR_MESSAGES.get(code, _ERROR_MESSAGES["invalid_response"]),
            }
        if include_result and job.get("status") == "completed":
            public["result"] = job.get("result")
        return public

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "providers": ["pionex", "binance", "shioaji"],
                "persistent_vault_available": self._vault is not None,
                "capabilities": {"shioaji_sdk": _shioaji_sdk_capability()},
                "configured": [
                    dict(value)
                    for _, value in sorted(self._configured.items(), key=lambda item: item[0])
                ],
                "job": self._public_job_locked(include_result=False),
            }

    def _credentials_for(
        self, provider: str, kind: str, profile: str
    ) -> CredentialPair | None:
        if provider not in {"shioaji"} and kind not in _PRIVATE_KINDS:
            return None
        pair = self._session_credentials.get((provider, profile))
        if pair is None:
            raise DataAPIError("auth", status=422)
        return pair

    def start(
        self,
        provider: str,
        kind: str,
        query: Any,
        profile: str = "default",
        on_finish: Callable[[], Any] | None = None,
    ) -> dict[str, Any]:
        provider_name = _provider(provider)
        profile_name = _profile(profile)
        if not isinstance(kind, str):
            raise DataAPIError("invalid_response", status=400)
        kind_name = kind.strip().lower()
        if not isinstance(query, dict):
            raise DataAPIError("invalid_response", status=400)
        try:
            validated_query = validate_query(provider_name, kind_name, dict(query))
        except DataAPIError:
            raise
        except Exception:
            raise DataAPIError("invalid_response", status=400) from None
        if not isinstance(validated_query, dict):
            raise DataAPIError("invalid_response", status=400)

        with self._lock:
            if self._closed or self._closing:
                raise DataAPIError("cancelled", status=409)
            if self._worker is not None and self._worker.is_alive():
                raise DataAPIError("invalid_response", status=409)
            credentials = self._credentials_for(provider_name, kind_name, profile_name)
            self._revision += 1
            job_id = uuid4().hex
            cancel = threading.Event()
            job = {
                "job_id": job_id,
                "revision": self._revision,
                "provider": provider_name,
                "kind": kind_name,
                "profile": profile_name,
                "status": "running",
                "progress": {"pages": 0, "records": 0},
                "cancel_requested": False,
                "authentication_verified": False,
                "result": None,
                "error": None,
                "cancel": cancel,
            }
            self._job = job
            worker = threading.Thread(
                target=self._run,
                kwargs={
                    "job_id": job_id,
                    "revision": self._revision,
                    "provider": provider_name,
                    "kind": kind_name,
                    "query": validated_query,
                    "credentials": credentials,
                    "cancel": cancel,
                    "on_finish": on_finish,
                },
                name=f"data-api-{job_id[:8]}",
                daemon=False,
            )
            self._worker = worker
        try:
            worker.start()
        except BaseException:
            with self._lock:
                if self._job is job:
                    self._job = None
                    self._worker = None
            raise
        with self._lock:
            public = self._public_job_locked(include_result=False)
            assert public is not None
            return public

    def _run(
        self,
        *,
        job_id: str,
        revision: int,
        provider: str,
        kind: str,
        query: dict[str, Any],
        credentials: CredentialPair | None,
        cancel: threading.Event,
        on_finish: Callable[[], Any] | None,
    ) -> None:
        def progress(pages: int, records: int) -> None:
            with self._lock:
                job = self._job
                if (
                    job is not None
                    and job.get("job_id") == job_id
                    and job.get("revision") == revision
                ):
                    job["progress"] = {
                        "pages": max(0, int(pages)),
                        "records": max(0, int(records)),
                    }

        try:
            context = SyncContext(
                cancel_event=cancel,
                deadline_seconds=45,
                progress=progress,
            )
            client = self._client_factory(
                provider,
                transport=None,
                credentials=credentials,
            )
            with self._lock:
                credential_values = tuple(self._credential_values)
            dataset = _bounded_dataset(
                client.fetch(kind, query, context),
                provider,
                credentials,
                credential_values,
            )
            provenance = dataset.get("provenance")
            authenticated = bool(
                isinstance(provenance, dict)
                and provenance.get("authentication_verified") is True
            )
            with self._lock:
                job = self._job
                if (
                    job is None
                    or job.get("job_id") != job_id
                    or job.get("revision") != revision
                ):
                    return
                if cancel.is_set():
                    job["status"] = "cancelled"
                    job["result"] = None
                    job["error"] = "cancelled"
                else:
                    job["status"] = "completed"
                    job["result"] = dataset
                    job["authentication_verified"] = authenticated
                    rows = dataset.get("rows")
                    if isinstance(rows, list):
                        job["progress"]["records"] = len(rows)
        except BaseException as exc:
            code = _error_code(exc)
            if (
                code == "auth"
                and getattr(exc, "status", None) == 403
                and provider in {"pionex", "binance"}
                and kind == "candles"
            ):
                # These are public, fixed-host market endpoints.  A 403 here
                # is a source restriction, not a prompt to supply a private
                # credential or try another host.
                code = "source_restricted"
            if cancel.is_set():
                code = "cancelled"
            with self._lock:
                job = self._job
                if (
                    job is not None
                    and job.get("job_id") == job_id
                    and job.get("revision") == revision
                ):
                    job["status"] = "cancelled" if code == "cancelled" else "failed"
                    job["result"] = None
                    job["error"] = code
        finally:
            # DataManager's commit is complete before the UI callback obtains
            # its own state lock and releases the shared operation gate.
            if on_finish is not None:
                try:
                    on_finish()
                except Exception:
                    pass

    def status(self, job_id: Any) -> dict[str, Any]:
        if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None:
            raise DataAPIError("invalid_response", status=404)
        with self._lock:
            if self._job is None or self._job.get("job_id") != job_id:
                raise DataAPIError("invalid_response", status=404)
            public = self._public_job_locked(include_result=True)
            assert public is not None
            return public

    def cancel(self, job_id: Any) -> dict[str, Any]:
        if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None:
            raise DataAPIError("invalid_response", status=404)
        with self._lock:
            job = self._job
            if job is None or job.get("job_id") != job_id:
                raise DataAPIError("invalid_response", status=404)
            if job.get("status") == "running":
                job["cancel_requested"] = True
                job["cancel"].set()
            public = self._public_job_locked(include_result=True)
            assert public is not None
            return public

    def _completed_dataset(self, job_id: Any) -> dict[str, Any]:
        status = self.status(job_id)
        if status.get("status") != "completed" or not isinstance(status.get("result"), dict):
            raise DataAPIError("invalid_response", status=409)
        return status["result"]

    def analyze(self, job_id: Any, **normalization_flags: Any) -> dict[str, Any]:
        allowed = {
            "opening_zero_confirmed",
            "transfers_reconciled",
            "pnl_basis",
            "total_costs_confirmed",
        }
        if set(normalization_flags) - allowed:
            raise DataAPIError("invalid_response", status=400)
        flags = {
            "opening_zero_confirmed": False,
            "transfers_reconciled": False,
            "pnl_basis": "unknown",
            "total_costs_confirmed": False,
        }
        flags.update(normalization_flags)
        if type(flags["opening_zero_confirmed"]) is not bool:
            raise DataAPIError("invalid_response", status=400)
        if type(flags["transfers_reconciled"]) is not bool:
            raise DataAPIError("invalid_response", status=400)
        if type(flags["total_costs_confirmed"]) is not bool:
            raise DataAPIError("invalid_response", status=400)
        if not isinstance(flags["pnl_basis"], str):
            raise DataAPIError("invalid_response", status=400)
        try:
            return normalize_closed_dataset(self._completed_dataset(job_id), **flags)
        except DataAPIError:
            raise
        except Exception:
            raise DataAPIError("invalid_response", status=422) from None

    def export(
        self, job_id: Any, format: str = "json"
    ) -> tuple[str, str, bytes]:
        dataset = self._completed_dataset(job_id)
        if not isinstance(format, str) or format.strip().lower() not in {"json", "csv"}:
            raise DataAPIError("invalid_response", status=400)
        export_format = format.strip().lower()
        provider = str(dataset.get("provider") or "api")
        kind = str(dataset.get("kind") or "data")
        with self._lock:
            revision = self._job.get("revision") if self._job is not None else 0
        stem = f"{provider}-{kind}-r{revision}"
        if export_format == "json":
            body = json.dumps(
                dataset,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
            ).encode("utf-8")
            return f"{stem}.json", "application/json", body

        source_rows = dataset.get("rows")
        rows = [dict(row) for row in source_rows if isinstance(row, dict)] if isinstance(source_rows, list) else []
        if not rows:
            summary = dataset.get("summary")
            if isinstance(summary, dict):
                for section in ("balances", "positions", "accounts"):
                    values = summary.get(section)
                    if isinstance(values, list):
                        for value in values:
                            if isinstance(value, dict):
                                rows.append({"section": section, **value})
        fieldnames: list[str] = []
        for row in rows:
            for key in row:
                name = str(key)
                if name not in fieldnames:
                    fieldnames.append(name)
        if not fieldnames:
            fieldnames = ["provider", "kind", "message"]
            rows = [
                {
                    "provider": provider,
                    "kind": kind,
                    "message": "no records returned",
                }
            ]
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_cell(row.get(key)) for key in fieldnames})
        body = ("\ufeff" + output.getvalue()).encode("utf-8")
        if len(body) > _MAX_DATASET_BYTES:
            raise DataAPIError("oversize")
        return f"{stem}.csv", "text/csv; charset=utf-8", body

    # -- lifecycle ---------------------------------------------------

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            # Closing is an admission barrier, not a cleanup claim.  If a
            # bounded join times out, a later close() retries the join and
            # clears sensitive session state only after the owned worker exits.
            self._closing = True
            job = self._job
            worker = self._worker
            if job is not None and job.get("status") == "running":
                job["cancel_requested"] = True
                job["cancel"].set()
        if worker is threading.current_thread():
            raise RuntimeError("data API worker cannot close its manager")
        if worker is not None and worker.is_alive():
            worker.join(_JOIN_SECONDS)
            if worker.is_alive():
                raise RuntimeError("data API worker did not stop")
        with self._lock:
            current_worker = self._worker
            if current_worker is not None and current_worker.is_alive():
                raise RuntimeError("data API worker did not stop")
            self._session_credentials.clear()
            self._credential_values.clear()
            self._configured.clear()
            self._job = None
            self._worker = None
            self._closed = True
            self._closing = False

    def __enter__(self) -> "DataManager":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


__all__ = ["DataManager"]
