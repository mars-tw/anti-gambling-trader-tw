"""Credential vaults for read-only provider API key pairs.

Persistent storage is limited to an application-owned Windows Generic
Credential namespace.  The in-memory implementation is the only fallback on
non-Windows systems and is suitable for tests and one UI session.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import threading
import unicodedata
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

from core.data_api.models import PROVIDERS

_PROFILE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$", re.ASCII)
_TARGET_PREFIX = "AntiGamblingTrader/readonly"
_MAX_SECRET_CHARS = 512
_MAX_BLOB_BYTES = 2560

_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168


def _has_control(value: str) -> bool:
    return any(unicodedata.category(ch) == "Cc" for ch in value)


def _validate_secret(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not (1 <= len(value) <= _MAX_SECRET_CHARS)
        or _has_control(value)
    ):
        raise ValueError(f"invalid_{field}")
    return value


def _validate_location(provider: Any, profile: Any) -> tuple[str, str]:
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("invalid_provider")
    if not isinstance(profile, str) or _PROFILE.fullmatch(profile) is None:
        raise ValueError("invalid_profile")
    return provider, profile


def _target_name(provider: Any, profile: Any) -> str:
    safe_provider, safe_profile = _validate_location(provider, profile)
    return f"{_TARGET_PREFIX}/{safe_provider}/{safe_profile}"


@dataclass(frozen=True, repr=False)
class CredentialPair:
    api_key: str
    api_secret: str

    def __post_init__(self) -> None:
        _validate_secret(self.api_key, "api_key")
        _validate_secret(self.api_secret, "api_secret")

    def __repr__(self) -> str:
        return "CredentialPair(api_key=<redacted>, api_secret=<redacted>)"


def _encode_pair(pair: Any) -> bytes:
    if not isinstance(pair, CredentialPair):
        raise ValueError("invalid_credential_pair")
    payload = json.dumps(
        {"api_key": pair.api_key, "api_secret": pair.api_secret},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > _MAX_BLOB_BYTES:
        raise ValueError("credential_blob_too_large")
    return payload


def _decode_pair(payload: bytes) -> CredentialPair:
    if not payload or len(payload) > _MAX_BLOB_BYTES:
        raise RuntimeError("credential_blob_invalid")
    try:
        decoded = json.loads(payload.decode("utf-8"))
        if not isinstance(decoded, dict) or set(decoded) != {"api_key", "api_secret"}:
            raise ValueError
        return CredentialPair(decoded["api_key"], decoded["api_secret"])
    except Exception:
        raise RuntimeError("credential_blob_invalid") from None


class InMemoryCredentialVault:
    """Process-local credential storage; never persists to disk or env."""

    def __init__(self) -> None:
        self._pairs: dict[tuple[str, str], CredentialPair] = {}
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        return "InMemoryCredentialVault(<redacted>)"

    def store(self, provider: str, profile: str, pair: CredentialPair) -> None:
        safe_provider, safe_profile = _validate_location(provider, profile)
        _encode_pair(pair)
        with self._lock:
            self._pairs[(safe_provider, safe_profile)] = pair

    def read(self, provider: str, profile: str) -> CredentialPair:
        key = _validate_location(provider, profile)
        with self._lock:
            try:
                return self._pairs[key]
            except KeyError:
                raise KeyError("credential_not_found") from None

    def delete(self, provider: str, profile: str) -> None:
        key = _validate_location(provider, profile)
        with self._lock:
            self._pairs.pop(key, None)

    def exists(self, provider: str, profile: str) -> bool:
        key = _validate_location(provider, profile)
        with self._lock:
            return key in self._pairs


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _CREDENTIALW(ctypes.Structure):
    """Forward-declared Win32 CREDENTIALW structure."""


_PCREDENTIALW = ctypes.POINTER(_CREDENTIALW)

_CREDENTIALW._fields_ = [
    ("Flags", wintypes.DWORD),
    ("Type", wintypes.DWORD),
    ("TargetName", wintypes.LPWSTR),
    ("Comment", wintypes.LPWSTR),
    ("LastWritten", _FILETIME),
    ("CredentialBlobSize", wintypes.DWORD),
    ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
    ("Persist", wintypes.DWORD),
    ("AttributeCount", wintypes.DWORD),
    ("Attributes", ctypes.c_void_p),
    ("TargetAlias", wintypes.LPWSTR),
    ("UserName", wintypes.LPWSTR),
]


class _WindowsCredentialBackend:
    def __init__(self) -> None:
        if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
            raise RuntimeError("persistent_credential_vault_unavailable")
        try:
            library = ctypes.WinDLL("advapi32", use_last_error=True)
            library.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
            library.CredWriteW.restype = wintypes.BOOL
            library.CredReadW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(_PCREDENTIALW),
            ]
            library.CredReadW.restype = wintypes.BOOL
            library.CredFree.argtypes = [ctypes.c_void_p]
            library.CredFree.restype = None
            library.CredDeleteW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            library.CredDeleteW.restype = wintypes.BOOL
        except Exception:
            raise RuntimeError("persistent_credential_vault_unavailable") from None
        self._library = library

    @staticmethod
    def _last_error() -> int:
        getter = getattr(ctypes, "get_last_error", None)
        return int(getter()) if callable(getter) else 0

    def store(self, target: str, payload: bytes) -> None:
        blob = ctypes.create_string_buffer(payload, len(payload))
        credential = _CREDENTIALW()
        credential.Flags = 0
        credential.Type = _CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.Comment = "AntiGamblingTrader read-only API credential"
        credential.CredentialBlobSize = len(payload)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(wintypes.BYTE))
        credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
        credential.AttributeCount = 0
        credential.Attributes = None
        credential.TargetAlias = None
        credential.UserName = "readonly-api"
        if not self._library.CredWriteW(ctypes.byref(credential), 0):
            raise RuntimeError("credential_store_failed")

    def _read_pointer(self, target: str) -> _PCREDENTIALW:
        pointer = _PCREDENTIALW()
        if not self._library.CredReadW(
            target,
            _CRED_TYPE_GENERIC,
            0,
            ctypes.byref(pointer),
        ):
            if self._last_error() == _ERROR_NOT_FOUND:
                raise KeyError("credential_not_found")
            raise RuntimeError("credential_read_failed")
        if not pointer:
            raise RuntimeError("credential_read_failed")
        return pointer

    def read(self, target: str) -> bytes:
        pointer = self._read_pointer(target)
        try:
            size = int(pointer.contents.CredentialBlobSize)
            if size < 1 or size > _MAX_BLOB_BYTES or not pointer.contents.CredentialBlob:
                raise RuntimeError("credential_blob_invalid")
            return ctypes.string_at(pointer.contents.CredentialBlob, size)
        finally:
            self._library.CredFree(pointer)

    def exists(self, target: str) -> bool:
        try:
            pointer = self._read_pointer(target)
        except KeyError:
            return False
        self._library.CredFree(pointer)
        return True

    def delete(self, target: str) -> None:
        if not self._library.CredDeleteW(target, _CRED_TYPE_GENERIC, 0):
            if self._last_error() == _ERROR_NOT_FOUND:
                return
            raise RuntimeError("credential_delete_failed")


class SystemCredentialVault:
    """Windows Credential Manager vault constrained to the app namespace."""

    def __init__(self) -> None:
        self._backend: _WindowsCredentialBackend | None = None

    def __repr__(self) -> str:
        return "SystemCredentialVault(namespace=AntiGamblingTrader/readonly)"

    def _get_backend(self) -> _WindowsCredentialBackend:
        if self._backend is None:
            self._backend = _WindowsCredentialBackend()
        return self._backend

    def store(self, provider: str, profile: str, pair: CredentialPair) -> None:
        target = _target_name(provider, profile)
        payload = _encode_pair(pair)
        self._get_backend().store(target, payload)

    def read(self, provider: str, profile: str) -> CredentialPair:
        target = _target_name(provider, profile)
        return _decode_pair(self._get_backend().read(target))

    def delete(self, provider: str, profile: str) -> None:
        target = _target_name(provider, profile)
        self._get_backend().delete(target)

    def exists(self, provider: str, profile: str) -> bool:
        target = _target_name(provider, profile)
        return self._get_backend().exists(target)
