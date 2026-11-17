"""Регрессии red-team 14.10.2026 (docs/audit/REDTEAM-2026-10-14.md)."""

from __future__ import annotations

import json

import pytest

from mandat.api.principals import access_problem
from mandat.intake import egrul
from mandat.privacy import pii

STRONG = "k" * 32


@pytest.mark.parametrize("env", ["pilot", "prod"])
def test_rt01_short_shared_admin_token_refused_in_protected_mode(monkeypatch, env):
    monkeypatch.setenv("MANDAT_ENV", env)
    monkeypatch.delenv("MANDAT_API_USERS", raising=False)
    monkeypatch.setenv("MANDAT_API_TOKEN", "secret")
    assert "MANDAT_API_TOKEN" in (access_problem() or "")
    monkeypatch.setenv("MANDAT_API_TOKEN", STRONG)
    assert access_problem() is None


def test_rt01_short_token_still_allowed_in_demo(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    monkeypatch.delenv("MANDAT_API_USERS", raising=False)
    monkeypatch.setenv("MANDAT_API_TOKEN", "secret")
    assert access_problem() is None


@pytest.mark.parametrize("env", ["pilot", "prod"])
def test_rt02_short_pii_key_refused_in_protected_mode(monkeypatch, env):
    monkeypatch.setenv("MANDAT_ENV", env)
    monkeypatch.setenv("MANDAT_PII_KEY", "short")
    with pytest.raises(pii.PiiKeyMissing):
        pii._key()
    monkeypatch.setenv("MANDAT_PII_KEY", STRONG)
    assert pii._key() == STRONG.encode()


def _egrul(padding: int = 0) -> str:
    payload = {"status": "active", "inn": "7707083893", "executives": [], "note": "x" * padding}
    return json.dumps(payload)


def test_rt06_oversized_egrul_rejected():
    text = _egrul(egrul.MAX_EGRUL_BYTES)
    assert len(text.encode()) > egrul.MAX_EGRUL_BYTES
    with pytest.raises(egrul.EgrulError, match="больше"):
        egrul.parse_egrul(text, doc_id="egrul")
    with pytest.raises(egrul.EgrulError, match="больше"):
        egrul.parse_egrul(text.encode(), doc_id="egrul")


def test_rt06_egrul_at_limit_is_parsed():
    base = len(_egrul().encode())
    text = _egrul(egrul.MAX_EGRUL_BYTES - base)
    assert len(text.encode()) == egrul.MAX_EGRUL_BYTES
    snapshot = egrul.parse_egrul(text, doc_id="egrul")
    assert snapshot.status == "active"


def test_rt06_limit_matches_xml_limit():
    from mandat.intake.xml_safety import MAX_XML_BYTES

    assert egrul.MAX_EGRUL_BYTES == MAX_XML_BYTES
