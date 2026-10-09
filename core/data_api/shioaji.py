"""Parent-side Shioaji client using one owned, bounded worker process."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from typing import Any, Callable

from core.data_api.context import SyncContext
from core.data_api.credentials import CredentialPair
from core.data_api.models import validate_query
from core.data_api.providers import MAX_DATASET_BYTES, _context_now_ms, _credential_values
from core.data_api.transport import DataAPIError

_PROTOCOL = "agt-data-worker-v1"
_MAX_REQUEST_BYTES = 128 * 1024
_MAX_FRAME_BYTES = MAX_DATASET_BYTES + 4096
_WORKER_TIMEOUT_SECONDS = 45.0
_POLL_SECONDS = 0.1

_SAFE_ENVIRONMENT_KEYS = frozenset(
    {
        "APPDATA",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "COMSPEC",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "TZ",
        "WINDIR",
    }
)


def _worker_command() -> list[str]:
    if bool(getattr(sys, "frozen", False)):
        return [sys.executable, "--data-api-worker"]
    return [sys.executable, "-m", "core.data_api.shioaji_worker"]


def _worker_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    values = os.environ if source is None else source
    environment = {
        key: value
        for key, value in values.items()
        if key.upper() in _SAFE_ENVIRONMENT_KEYS and isinstance(value, str)
    }
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    if os.name != "nt":
        return False
    try:
        import ctypes

        attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        return attributes != 0xFFFFFFFF and bool(attributes & 0x400)
    except (AttributeError, OSError, TypeError, ValueError):
        return True


def _owned_tree_safe(root: Path) -> bool:
    try:
        root = root.resolve(strict=True)
        if _is_reparse_point(root):
            return False
        pending = [root]
        while pending:
            current = pending.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    child = Path(entry.path)
                    if _is_reparse_point(child):
                        return False
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(child)
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _secure_owned_temp(path: Path) -> bool:
    if os.name != "nt":
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            return stat.S_IMODE(path.stat().st_mode) == 0o700
        except OSError:
            return False
    try:
        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.argtypes = []
        get_current_process.restype = wintypes.HANDLE
        open_process_token = advapi32.OpenProcessToken
        open_process_token.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        open_process_token.restype = wintypes.BOOL
        get_token_information = advapi32.GetTokenInformation
        get_token_information.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        get_token_information.restype = wintypes.BOOL
        convert_sid = advapi32.ConvertSidToStringSidW
        convert_sid.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
        convert_sid.restype = wintypes.BOOL
        convert_sddl = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert_sddl.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD)]
        convert_sddl.restype = wintypes.BOOL
        set_file_security = advapi32.SetFileSecurityW
        set_file_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
        set_file_security.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        local_free = kernel32.LocalFree
        local_free.argtypes = [wintypes.HLOCAL]
        local_free.restype = wintypes.HLOCAL

        token = wintypes.HANDLE()
        if not open_process_token(get_current_process(), 0x0008, ctypes.byref(token)):
            return False
        sid_text = wintypes.LPWSTR()
        descriptor = wintypes.LPVOID()
        try:
            needed = wintypes.DWORD(0)
            get_token_information(token, 1, None, 0, ctypes.byref(needed))
            if not needed.value:
                return False
            token_user = ctypes.create_string_buffer(needed.value)
            if not get_token_information(token, 1, token_user, needed, ctypes.byref(needed)):
                return False
            sid_ptr = ctypes.cast(token_user, ctypes.POINTER(ctypes.c_void_p)).contents.value
            if not sid_ptr or not convert_sid(sid_ptr, ctypes.byref(sid_text)):
                return False
            sddl = f"D:P(A;OICI;FA;;;{sid_text.value})"
            descriptor_size = wintypes.DWORD(0)
            if not convert_sddl(sddl, 1, ctypes.byref(descriptor), ctypes.byref(descriptor_size)):
                return False
            if not set_file_security(str(path), 0x80000004, descriptor):
                return False
            return _verify_owned_acl(path, sid_ptr, advapi32, kernel32)
        finally:
            if descriptor:
                local_free(descriptor)
            if sid_text:
                local_free(sid_text)
            close_handle(token)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _verify_owned_acl(path: Path, expected_sid: Any, advapi32: Any, kernel32: Any) -> bool:
    """Read back a protected DACL and require one full-rights ACE for our SID."""
    import ctypes
    from ctypes import wintypes

    get_file_security = advapi32.GetFileSecurityW
    get_file_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    get_file_security.restype = wintypes.BOOL
    get_control = advapi32.GetSecurityDescriptorControl
    get_control.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    get_control.restype = wintypes.BOOL
    get_dacl = advapi32.GetSecurityDescriptorDacl
    get_dacl.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL)]
    get_dacl.restype = wintypes.BOOL
    get_acl_info = advapi32.GetAclInformation
    get_acl_info.argtypes = [wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD]
    get_acl_info.restype = wintypes.BOOL
    get_ace = advapi32.GetAce
    get_ace.argtypes = [wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID)]
    get_ace.restype = wintypes.BOOL
    equal_sid = advapi32.EqualSid
    equal_sid.argtypes = [wintypes.LPVOID, wintypes.LPVOID]
    equal_sid.restype = wintypes.BOOL
    needed = wintypes.DWORD(0)
    get_file_security(str(path), 0x00000004, None, 0, ctypes.byref(needed))
    if not needed.value:
        return False
    descriptor = ctypes.create_string_buffer(needed.value)
    if not get_file_security(str(path), 0x00000004, descriptor, needed, ctypes.byref(needed)):
        return False
    control = wintypes.WORD(0)
    control_revision = wintypes.DWORD(0)
    if not get_control(descriptor, ctypes.byref(control), ctypes.byref(control_revision)):
        return False
    if not (int(control.value) & 0x1000):  # SE_DACL_PROTECTED
        return False
    dacl_present = wintypes.BOOL(False)
    dacl = wintypes.LPVOID()
    dacl_defaulted = wintypes.BOOL(False)
    if not get_dacl(descriptor, ctypes.byref(dacl_present), ctypes.byref(dacl), ctypes.byref(dacl_defaulted)):
        return False
    if not dacl_present.value or not dacl.value:
        return False

    class _AclSizeInformation(ctypes.Structure):
        _fields_ = (("ace_count", wintypes.DWORD), ("acl_bytes_in_use", wintypes.DWORD), ("acl_bytes_free", wintypes.DWORD))

    acl_info = _AclSizeInformation()
    if not get_acl_info(dacl, ctypes.byref(acl_info), ctypes.sizeof(acl_info), 2):  # AclSizeInformation
        return False
    if int(acl_info.ace_count) != 1:
        return False
    ace = wintypes.LPVOID()
    if not get_ace(dacl, 0, ctypes.byref(ace)) or not ace.value:
        return False

    class _AceHeader(ctypes.Structure):
        _fields_ = (("ace_type", ctypes.c_ubyte), ("ace_flags", ctypes.c_ubyte), ("ace_size", wintypes.WORD))

    header = ctypes.cast(ace, ctypes.POINTER(_AceHeader)).contents
    if int(header.ace_type) != 0 or (int(header.ace_flags) & 0x1F) != 0x03:
        return False
    mask = ctypes.cast(int(ace.value) + ctypes.sizeof(_AceHeader), ctypes.POINTER(wintypes.DWORD)).contents.value
    if int(mask) != 0x1F01FF:
        return False
    ace_sid = ctypes.c_void_p(int(ace.value) + ctypes.sizeof(_AceHeader) + ctypes.sizeof(wintypes.DWORD))
    return bool(equal_sid(expected_sid, ace_sid))


def _new_owned_temp() -> Path:
    try:
        temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
        path = Path(tempfile.mkdtemp(prefix="agt-shioaji-", dir=str(temp_root))).resolve(strict=True)
        if path.parent != temp_root or _is_reparse_point(temp_root):
            _cleanup_owned_temp(path)
            raise OSError("unsafe temp root")
        if not _secure_owned_temp(path):
            _cleanup_owned_temp(path)
            raise OSError("unsafe temp permissions")
        return path
    except (OSError, RuntimeError, ValueError):
        raise DataAPIError("network") from None


def _cleanup_owned_temp(path: Path) -> bool:
    try:
        temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
        resolved = path.resolve(strict=True)
        if resolved.parent != temp_root or _is_reparse_point(resolved) or not _owned_tree_safe(resolved):
            return False
        if os.name != "nt":
            shutil.rmtree(resolved)
            return True
        import ctypes
        from ctypes import wintypes

        class _SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = (
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", wintypes.WORD),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", wintypes.LPVOID),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            )

        operation = _SHFILEOPSTRUCTW(
            None,
            0x0003,
            f"{resolved}\0\0",
            None,
            0x0040 | 0x0010 | 0x0004 | 0x0400,
            False,
            None,
            None,
        )
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        operation_fn = shell32.SHFileOperationW
        operation_fn.argtypes = [ctypes.POINTER(_SHFILEOPSTRUCTW)]
        operation_fn.restype = ctypes.c_int
        result = operation_fn(ctypes.byref(operation))
        return result == 0 and not operation.fAnyOperationsAborted and not resolved.exists()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False


def _remaining_seconds(context: SyncContext) -> float:
    value = context.remaining_seconds()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataAPIError("timeout")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise DataAPIError("timeout")
    return result


def _json_no_duplicates(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise DataAPIError("invalid_response") from None

    def pairs_hook(pairs: list[tuple[Any, Any]]) -> dict[Any, Any]:
        result: dict[Any, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    def reject_constant(_value: str) -> Any:
        raise ValueError("nonfinite")

    try:
        return json.loads(text, object_pairs_hook=pairs_hook, parse_constant=reject_constant)
    except (json.JSONDecodeError, TypeError, ValueError):
        raise DataAPIError("invalid_response") from None


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in {
                "api_key",
                "api_secret",
                "credentials",
                "account_id",
                "person_id",
                "broker_id",
            }:
                return True
            if _contains_forbidden_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def _contains_secret_value(value: Any, secrets: tuple[str, ...]) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_secret_value(item, secrets) for item in value.values())
    if isinstance(value, list):
        return any(_contains_secret_value(item, secrets) for item in value)
    if isinstance(value, str):
        return any(
            value == secret or (len(secret) >= 8 and secret in value)
            for secret in secrets
        )
    return False


class ShioajiDataClient:
    """Read-only Shioaji client with a killable login/SDK isolation boundary."""

    provider = "shioaji"

    def __init__(
        self,
        *,
        credentials: CredentialPair | None = None,
        popen_factory: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._credentials = credentials
        self._popen_factory = popen_factory if popen_factory is not None else subprocess.Popen
        if not callable(clock):
            raise ValueError("clock")
        self._clock = clock
        self._environment = _worker_environment(environment)

    def fetch(self, kind: str, query: Mapping[str, Any], context: SyncContext) -> dict[str, Any]:
        context.check()
        validation_kind = "fills" if kind == "realizations" else kind
        if validation_kind not in {"candles", "fills", "account"}:
            raise ValueError("kind")
        validated = validate_query(self.provider, validation_kind, query)
        api_key, api_secret = _credential_values(self._credentials)
        request = {
            "protocol": _PROTOCOL,
            "action": "fetch",
            "kind": kind,
            "query": validated,
            "captured_at_ms": _context_now_ms(context),
            "request_capabilities": {
                "read_only": True,
                "allow_orders": False,
                "allow_ca": False,
                "credentials_transport": "anonymous-stdin-memory-only",
            },
            "credentials": {"api_key": api_key, "api_secret": api_secret},
        }
        expected_kind = "realizations" if kind in {"fills", "realizations"} else kind
        result = self._invoke(request, context)
        self._validate_dataset(result, expected_kind, validated, api_key, api_secret)
        coverage = result.get("coverage")
        records = 0
        if isinstance(coverage, Mapping):
            raw_count = coverage.get("raw_count")
            if isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count >= 0:
                records = raw_count
        context.advance(pages=1, records=records)
        return result

    def fetch_candles(self, query: Mapping[str, Any], context: SyncContext) -> dict[str, Any]:
        return self.fetch("candles", query, context)

    def fetch_fills(self, query: Mapping[str, Any], context: SyncContext) -> dict[str, Any]:
        return self.fetch("fills", query, context)

    def fetch_account(self, query: Mapping[str, Any], context: SyncContext) -> dict[str, Any]:
        return self.fetch("account", query, context)

    def sdk_info(self, context: SyncContext) -> dict[str, Any]:
        """Probe packaged SDK metadata without sending credentials or logging in."""

        context.check()
        result = self._invoke(None, context, sdk_info=True)
        if not isinstance(result, Mapping):
            raise DataAPIError("invalid_response")
        if result.get("sdk") != "shioaji" or result.get("login_performed") is not False:
            raise DataAPIError("invalid_response")
        return dict(result)

    def _invoke(
        self,
        request: Mapping[str, Any] | None,
        context: SyncContext,
        *,
        sdk_info: bool = False,
    ) -> Any:
        if sdk_info:
            command = _worker_command() + ["--sdk-info"]
            payload = None
        else:
            command = _worker_command()
            try:
                payload = json.dumps(
                    request,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError, OverflowError):
                raise DataAPIError("invalid_response") from None
            if len(payload) > _MAX_REQUEST_BYTES:
                raise DataAPIError("oversize")
        runtime_dir = _new_owned_temp()
        environment = dict(self._environment)
        environment["SJ_HOME_PATH"] = str(runtime_dir)
        environment["SJ_CONTRACTS_PATH"] = str(runtime_dir)
        environment["SJ_LOG_PATH"] = os.devnull
        environment["SJ_DATASTORE_ENABLED"] = "false"
        if not bool(getattr(sys, "frozen", False)):
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        else:
            environment.pop("PYTHONPATH", None)
        creationflags = 0
        if os.name == "nt":
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            process = self._popen_factory(
                command,
                stdin=subprocess.DEVNULL if sdk_info else subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                env=environment,
                creationflags=creationflags,
                cwd=str(runtime_dir),
            )
        except (OSError, ValueError):
            _cleanup_owned_temp(runtime_dir)
            raise DataAPIError("network") from None

        pending_input = payload
        stdout = b""
        stderr = b""
        try:
            started = self._clock()
            while True:
                context.check()
                local_remaining = _WORKER_TIMEOUT_SECONDS - (self._clock() - started)
                remaining = min(local_remaining, _remaining_seconds(context))
                if not math.isfinite(remaining) or remaining <= 0:
                    raise DataAPIError("timeout")
                poll_timeout = min(_POLL_SECONDS, remaining)
                try:
                    if pending_input is not None:
                        current_input = pending_input
                        pending_input = None
                        stdout, stderr = process.communicate(
                            input=current_input,
                            timeout=poll_timeout,
                        )
                    else:
                        stdout, stderr = process.communicate(timeout=poll_timeout)
                    break
                except subprocess.TimeoutExpired:
                    continue
            return self._parse_frame(stdout, stderr)
        except DataAPIError:
            self._kill_owned(process)
            raise
        except (OSError, ValueError):
            self._kill_owned(process)
            raise DataAPIError("network") from None
        finally:
            _cleanup_owned_temp(runtime_dir)

    @staticmethod
    def _kill_owned(process: Any) -> None:
        try:
            running = process.poll() is None
        except Exception:
            running = True
        if running:
            try:
                process.kill()
            except Exception:
                running = False
        for _attempt in range(2):
            try:
                process.communicate(timeout=5.0)
                return
            except subprocess.TimeoutExpired:
                continue
            except Exception:
                break
        try:
            process.wait(timeout=5.0)
        except Exception:
            return

    @staticmethod
    def _parse_frame(stdout: Any, stderr: Any) -> Any:
        if not isinstance(stdout, (bytes, bytearray)) or not isinstance(
            stderr, (bytes, bytearray)
        ):
            raise DataAPIError("invalid_response")
        if len(stdout) > _MAX_FRAME_BYTES or len(stderr) > _MAX_FRAME_BYTES:
            raise DataAPIError("oversize")
        stripped = bytes(stdout).strip()
        if not stripped or b"\n" in stripped or b"\r" in stripped:
            raise DataAPIError("invalid_response")
        frame = _json_no_duplicates(stripped)
        if not isinstance(frame, Mapping) or frame.get("protocol") != _PROTOCOL:
            raise DataAPIError("invalid_response")
        ok = frame.get("ok")
        if ok is True:
            if "result" not in frame:
                raise DataAPIError("invalid_response")
            return frame["result"]
        if ok is False:
            error = frame.get("error")
            if not isinstance(error, Mapping):
                raise DataAPIError("invalid_response")
            code = error.get("code")
            if not isinstance(code, str):
                raise DataAPIError("invalid_response")
            raise DataAPIError(code)
        raise DataAPIError("invalid_response")

    @staticmethod
    def _validate_dataset(
        result: Any,
        expected_kind: str,
        query: Mapping[str, Any],
        api_key: str,
        api_secret: str,
    ) -> None:
        if not isinstance(result, Mapping):
            raise DataAPIError("invalid_response")
        if result.get("schema_version") != "api-data-v1":
            raise DataAPIError("invalid_response")
        if result.get("provider") != "shioaji" or result.get("kind") != expected_kind:
            raise DataAPIError("invalid_response")
        if result.get("market") != query.get("market"):
            raise DataAPIError("invalid_response")
        expected_symbol = query.get("symbol", "")
        if result.get("symbol") != expected_symbol:
            raise DataAPIError("invalid_response")
        if _contains_forbidden_key(result):
            raise DataAPIError("invalid_response")
        try:
            serialized = json.dumps(
                result,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError, OverflowError):
            raise DataAPIError("invalid_response") from None
        if len(serialized.encode("utf-8")) > MAX_DATASET_BYTES:
            raise DataAPIError("oversize")
        if _contains_secret_value(result, (api_key, api_secret)):
            raise DataAPIError("invalid_response")
        provenance = result.get("provenance")
        if not isinstance(provenance, Mapping):
            raise DataAPIError("invalid_response")
        if provenance.get("authentication_verified") is not True:
            raise DataAPIError("invalid_response")


__all__ = ("ShioajiDataClient",)
