"""mandat serve не стартует без доступа: ни токена, ни пользователей."""

from __future__ import annotations

import json

from mandat import cli
from mandat.api.principals import access_problem, hash_token


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
