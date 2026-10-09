"""Read-only API data foundation (api-data-v1 / v0.4.0).

Public exports are limited to foundation types. Provider clients and SDKs are
never imported eagerly from this package root.
"""

from __future__ import annotations

from core.data_api.context import SyncContext
from core.data_api.credentials import (
    PROVIDERS,
    CredentialPair,
    InMemoryCredentialVault,
    SystemCredentialVault,
)
from core.data_api.models import normalize_decimal, validate_query
from core.data_api.normalization import normalize_closed_dataset
from core.data_api.transport import DataAPIError, ReadOnlyHTTPTransport

__all__ = [
    "PROVIDERS",
    "CredentialPair",
    "DataAPIError",
    "InMemoryCredentialVault",
    "ReadOnlyHTTPTransport",
    "SyncContext",
    "SystemCredentialVault",
    "normalize_closed_dataset",
    "normalize_decimal",
    "validate_query",
]
