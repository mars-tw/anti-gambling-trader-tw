from __future__ import annotations

import pytest

from core.data_api.credentials import (
    CredentialPair,
    InMemoryCredentialVault,
    SystemCredentialVault,
)


def test_credential_pair_repr_redacts_both_values():
    pair = CredentialPair("fake-key-canary", "fake-secret-canary")

    rendered = repr(pair)
    assert "fake-key-canary" not in rendered
    assert "fake-secret-canary" not in rendered
    assert rendered.count("<redacted>") == 2


@pytest.mark.parametrize(
    ("key", "secret"),
    [
        ("", "secret"),
        ("key", ""),
        ("key\nleak", "secret"),
        ("key", "secret\x00leak"),
        ("k" * 513, "secret"),
        ("key", "s" * 513),
    ],
)
def test_credential_pair_rejects_empty_control_and_oversized_values(key, secret):
    with pytest.raises(ValueError) as caught:
        CredentialPair(key, secret)

    assert key not in str(caught.value) if key else True
    assert secret not in str(caught.value) if secret else True


def test_in_memory_vault_crud_is_scoped_by_provider_and_profile():
    vault = InMemoryCredentialVault()
    pair = CredentialPair("fake-key", "fake-secret")

    assert vault.exists("binance", "primary") is False
    vault.store("binance", "primary", pair)
    assert vault.exists("binance", "primary") is True
    assert vault.read("binance", "primary") == pair
    assert vault.exists("pionex", "primary") is False
    vault.delete("binance", "primary")
    vault.delete("binance", "primary")
    assert vault.exists("binance", "primary") is False
    with pytest.raises(KeyError, match="credential_not_found"):
        vault.read("binance", "primary")


@pytest.mark.parametrize("provider", ["", "BINANCE", "other", "../binance"])
def test_vault_rejects_non_allowlisted_provider(provider):
    vault = InMemoryCredentialVault()
    with pytest.raises(ValueError, match="invalid_provider"):
        vault.store(provider, "primary", CredentialPair("fake", "fake-secret"))


@pytest.mark.parametrize(
    "profile",
    ["", "Primary", "-primary", "../primary", "a/b", "a b", "a" * 33],
)
def test_vault_rejects_profile_outside_fixed_slug_grammar(profile):
    vault = InMemoryCredentialVault()
    with pytest.raises(ValueError, match="invalid_profile"):
        vault.store("shioaji", profile, CredentialPair("fake", "fake-secret"))


def test_encoded_blob_cap_applies_even_when_each_field_character_count_is_valid():
    pair = CredentialPair("🧪" * 512, "🔒" * 512)
    vault = InMemoryCredentialVault()

    with pytest.raises(ValueError, match="credential_blob_too_large") as caught:
        vault.store("pionex", "primary", pair)

    assert "🧪" not in str(caught.value)
    assert "🔒" not in str(caught.value)


class _FakeBackend:
    def __init__(self) -> None:
        self.target = None
        self.payload = None

    def store(self, target, payload):
        self.target = target
        self.payload = payload

    def read(self, target):
        assert target == self.target
        return self.payload

    def exists(self, target):
        return target == self.target

    def delete(self, target):
        assert target == self.target
        self.target = None
        self.payload = None


def test_system_vault_uses_only_fixed_app_namespace_without_secret_in_target():
    backend = _FakeBackend()
    vault = SystemCredentialVault()
    vault._backend = backend
    pair = CredentialPair("fake-key-canary", "fake-secret-canary")

    vault.store("shioaji", "paper_1", pair)

    assert backend.target == "AntiGamblingTrader/readonly/shioaji/paper_1"
    assert "fake-key-canary" not in backend.target
    assert "fake-secret-canary" not in backend.target
    assert vault.exists("shioaji", "paper_1") is True
    assert vault.read("shioaji", "paper_1") == pair
    vault.delete("shioaji", "paper_1")


def test_vault_repr_never_contains_stored_credentials():
    vault = InMemoryCredentialVault()
    vault.store(
        "binance",
        "primary",
        CredentialPair("fake-key-canary", "fake-secret-canary"),
    )

    rendered = repr(vault)
    assert "fake-key-canary" not in rendered
    assert "fake-secret-canary" not in rendered
