"""M-06: в pilot/prod LLM base_url — https (loopback http только с явным флагом)."""

from __future__ import annotations

import pytest

from mandat.advisory.llm import OpenAICompatClient


@pytest.fixture(autouse=True)
def _allow_internal_host(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_LLM_ALLOWED_HOSTS", "127.0.0.1,localhost,model.internal")


def test_prod_http_internal_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    monkeypatch.delenv("MANDAT_LLM_ALLOW_INSECURE_LOOPBACK", raising=False)
    with pytest.raises(ValueError, match="https"):
        OpenAICompatClient("http://model.internal/v1", "m")


def test_prod_https_allowlisted_ok(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    client = OpenAICompatClient("https://model.internal/v1", "m")
    assert client.model == "m"


def test_demo_http_localhost_ok(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    monkeypatch.delenv("MANDAT_LLM_ALLOW_INSECURE_LOOPBACK", raising=False)
    client = OpenAICompatClient("http://127.0.0.1:8000/v1", "local")
    assert "127.0.0.1" in client.base_url


def test_prod_loopback_http_without_flag_fails(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    monkeypatch.delenv("MANDAT_LLM_ALLOW_INSECURE_LOOPBACK", raising=False)
    with pytest.raises(ValueError, match="ALLOW_INSECURE_LOOPBACK"):
        OpenAICompatClient("http://localhost:8000/v1", "local")


def test_prod_loopback_http_with_flag_ok(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    monkeypatch.setenv("MANDAT_LLM_ALLOW_INSECURE_LOOPBACK", "1")
    client = OpenAICompatClient("http://127.0.0.1:8000/v1", "local")
    assert client.base_url.startswith("http://")
