from __future__ import annotations

import os
import stat
import sys
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from tandem.vault import Cipher, TokenVault, VaultError, credentials, load_server_key


def test_server_key_is_created_once_and_private(tmp_path):
    key = load_server_key(tmp_path, env={})
    assert load_server_key(tmp_path, env={}) == key
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(tmp_path / "secret.key").st_mode) == 0o600
    env_key = Fernet.generate_key().decode()
    assert load_server_key(tmp_path, env={"TANDEM_SECRET_KEY": env_key}) == env_key.encode()
    with pytest.raises(ValueError):
        load_server_key(tmp_path, env={"TANDEM_SECRET_KEY": "not-a-key"})


async def test_tokens_are_encrypted_refreshed_and_redacted(rt):
    vault: TokenVault = rt.vault
    vault.save(
        "gmail",
        "google",
        {"access_token": "ya29.first-access-token", "refresh_token": "1//refresh-secret", "expires_in": 3600},
        account="sam@example.com",
    )
    row = rt.store._one(select(credentials).where(credentials.c.name == "gmail"))
    assert "ya29" not in row["token"] and "refresh-secret" not in row["token"]
    pub = vault.public("gmail")
    assert pub["account"] == "sam@example.com" and "token" not in pub

    calls: list[dict[str, Any]] = []

    async def refresh(tok: dict[str, Any]) -> dict[str, Any]:
        calls.append(tok)
        return {"access_token": "ya29.second-access-token", "expires_in": 3600}

    assert await vault.access_token("gmail", refresh) == "ya29.first-access-token"
    assert not calls
    rt.clock.advance(minutes=59)
    assert await vault.access_token("gmail", refresh) == "ya29.second-access-token"
    assert calls[0]["refresh_token"] == "1//refresh-secret"
    # a refresh that omits the refresh token keeps the old one
    assert vault.token("gmail")["refresh_token"] == "1//refresh-secret"
    # both tokens are masked anywhere text leaves the tool layer
    leaked = rt.redactor.text("echo ya29.second-access-token and 1//refresh-secret")
    assert "ya29" not in leaked and "refresh-secret" not in leaked


async def test_failed_refresh_marks_the_connection(rt):
    rt.vault.save("slack", "slack", {"access_token": "xoxe.old", "refresh_token": "r", "expires_in": 60})

    async def boom(tok: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("invalid_grant")

    with pytest.raises(VaultError, match="invalid_grant"):
        await rt.vault.access_token("slack", boom)
    assert rt.vault.public("slack")["status"] == "refresh_failed"
    assert rt.vault.delete("slack") and not rt.vault.has("slack")
    with pytest.raises(VaultError):
        await rt.vault.access_token("slack")


def test_wrong_key_cannot_decrypt():
    a, b = Cipher(Fernet.generate_key()), Cipher(Fernet.generate_key())
    with pytest.raises(VaultError):
        b.decrypt(a.encrypt({"x": 1}))
