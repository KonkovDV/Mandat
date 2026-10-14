"""mandat serve не стартует без доступа: ни токена, ни пользователей."""

from __future__ import annotations

import json

import pytest

from mandat import cli
from mandat.api.principals import access_problem, hash_token
from mandat.services.env_policy import key_problem


def test_serve_refuses_without_token_and_users(monkeypatch, capsys):
    monkeypatch.delenv("MANDAT_API_TOKEN", raising=False)
    monkeypatch.delenv("MANDAT_API_USERS", raising=False)
    assert cli.main(["serve"]) == 2
    assert "MANDAT_API_USERS" in capsys.readouterr().err


def test_serve_refuses_broken_users(monkeypatch):
    monkeypatch.delenv("MANDAT_API_TOKEN", raising=False)
    monkeypatch.setenv("MANDAT_API_USERS", '[{"name": "a"}]')
    assert "не читается" in (access_problem() or "")


def test_either_token_or_users_is_enough(monkeypatch):
    monkeypatch.delenv("MANDAT_API_USERS", raising=False)
    monkeypatch.setenv("MANDAT_API_TOKEN", "secret")
    assert access_problem() is None
    monkeypatch.delenv("MANDAT_API_TOKEN")
    users = [{"name": "a", "role": "admin", "token_sha256": hash_token("t")}]
    monkeypatch.setenv("MANDAT_API_USERS", json.dumps(users))
    assert access_problem() is None


def test_compose_does_not_demand_only_the_token(root):
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "MANDAT_API_TOKEN:?" not in compose
    assert "MANDAT_API_USERS" in compose


STRONG = "k" * 32


def _access(monkeypatch):
    monkeypatch.delenv("MANDAT_API_USERS", raising=False)
    monkeypatch.setenv("MANDAT_API_TOKEN", "secret")


@pytest.mark.parametrize("env", ["pilot", "prod", "unknown"])
@pytest.mark.parametrize(
    ("journal", "pii", "missing"),
    [
        (None, None, ("MANDAT_JOURNAL_KEY", "MANDAT_PII_KEY")),
        (STRONG, None, ("MANDAT_PII_KEY",)),
        (None, STRONG, ("MANDAT_JOURNAL_KEY",)),
        ("short", STRONG, ("MANDAT_JOURNAL_KEY",)),
    ],
)
def test_serve_refuses_protected_mode_without_keys(monkeypatch, capsys, env, journal, pii, missing):
    _access(monkeypatch)
    monkeypatch.setenv("MANDAT_ENV", env)
    for name, value in (("MANDAT_JOURNAL_KEY", journal), ("MANDAT_PII_KEY", pii)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert cli.main(["serve"]) == 2
    err = capsys.readouterr().err
    for name in missing:
        assert name in err


@pytest.mark.parametrize("env", ["pilot", "prod"])
def test_key_check_passes_with_keys(monkeypatch, env):
    monkeypatch.setenv("MANDAT_ENV", env)
    monkeypatch.setenv("MANDAT_JOURNAL_KEY", STRONG)
    monkeypatch.setenv("MANDAT_PII_KEY", STRONG)
    assert key_problem() is None


@pytest.mark.parametrize("env", ["demo", "dev", "test"])
def test_key_check_skipped_in_keyless_modes(monkeypatch, env):
    monkeypatch.setenv("MANDAT_ENV", env)
    monkeypatch.delenv("MANDAT_JOURNAL_KEY", raising=False)
    monkeypatch.delenv("MANDAT_PII_KEY", raising=False)
    assert key_problem() is None


def test_compose_requires_explicit_env(root):
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "MANDAT_ENV:?" in compose
    assert "MANDAT_ENV:-demo" not in compose
