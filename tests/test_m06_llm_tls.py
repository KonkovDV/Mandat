"""M-06: в pilot/prod LLM base_url — https (loopback http только с явным флагом)."""

from __future__ import annotations

import pytest

from mandat.advisory.llm import OpenAICompatClient


@pytest.fixture(autouse=True)
def _allow_hosts(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_LLM_ALLOWED_HOSTS", "127.0.0.1,localhost,::1,model.internal")


def test_prod_https_ok(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    client = OpenAICompatClient("https://model.internal/v1", "m")
    assert client.model == "m"


def test_prod_http_reject(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    monkeypatch.delenv("MANDAT_LLM_ALLOW_INSECURE_LOOPBACK", raising=False)
    with pytest.raises(ValueError, match="https"):
        OpenAICompatClient("http://model.internal/v1", "m")


def test_prod_loopback_no_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    monkeypatch.delenv("MANDAT_LLM_ALLOW_INSECURE_LOOPBACK", raising=False)
    with pytest.raises(ValueError, match="ALLOW_INSECURE_LOOPBACK"):
        OpenAICompatClient("http://127.0.0.1:8000/v1", "local")


def test_prod_loopback_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    monkeypatch.setenv("MANDAT_LLM_ALLOW_INSECURE_LOOPBACK", "1")
    assert OpenAICompatClient("http://127.0.0.1:8000/v1", "local").base_url.startswith("http://")
    assert OpenAICompatClient("http://localhost:8000/v1", "local")
    assert OpenAICompatClient("http://[::1]:8000/v1", "local")


def test_prod_flag_nonloopback_still_rejects(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    monkeypatch.setenv("MANDAT_LLM_ALLOW_INSECURE_LOOPBACK", "1")
    with pytest.raises(ValueError, match="https"):
        OpenAICompatClient("http://model.internal/v1", "m")


def test_dev_http_ok(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "dev")
    monkeypatch.delenv("MANDAT_LLM_ALLOW_INSECURE_LOOPBACK", raising=False)
    assert OpenAICompatClient("http://model.internal/v1", "m")


def test_not_allowlisted_https(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    with pytest.raises(ValueError, match="MANDAT_LLM_ALLOWED_HOSTS"):
        OpenAICompatClient("https://evil.example/v1", "m")
