"""Ключи из окружения в pilot и prod — случайные байты, а не пароли.

HMAC журнала и псевдонимов и HKDF ключа хранилища перебор не замедляют, поэтому ключ-пароль подбирается по
словарю; для ключа ПДн это путь к ИНН по псевдониму. Проверка ловит пароли, повторы и короткие ключи.
"""

from __future__ import annotations

import base64
import secrets

import pytest
from conftest import random_key

from mandat.audit.keymaterial import decode_random, weak_key


@pytest.mark.parametrize(
    "value",
    [
        secrets.token_hex(32),
        secrets.token_hex(48).upper(),
        secrets.token_urlsafe(48),
        base64.b64encode(secrets.token_bytes(32)).decode(),
        base64.b64encode(secrets.token_bytes(33)).decode().rstrip("="),
        random_key("any"),
    ],
)
def test_generator_output_is_accepted(value):
    assert weak_key("K", value) is None


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("k" * 32, "не пароль"),
        ("correct horse battery staple 2026!", "не пароль"),
        ("mandat-store-key-for-the-pilot-2026", "не пароль"),
        ("correcthorsebatterystaplecorrecthorsebatterystaple", "похоже на слово"),
        ("CORRECTHORSEBATTERYSTAPLECORRECTHORSEBATTERYSTAPLE", "похоже на слово"),
        ("00" * 32, "мало разных байтов"),
        ("ab" * 40, "мало разных байтов"),
        (secrets.token_hex(16), "не пароль"),  # 16 байт: в hex 32 знака — не наш формат
        (base64.b64encode(b"\x01\x02" * 16).decode(), "мало разных байтов"),
    ],
)
def test_passwords_repeats_and_short_keys_are_refused(value, why):
    problem = weak_key("MANDAT_PII_KEY", value)
    assert problem and problem.startswith("MANDAT_PII_KEY:") and why in problem


def test_hex_and_base64_decode_to_the_same_bytes():
    raw = secrets.token_bytes(32)
    assert decode_random(raw.hex()) == raw
    assert decode_random(base64.b64encode(raw).decode()) == raw
    assert decode_random(base64.urlsafe_b64encode(raw).decode().rstrip("=")) == raw
    assert decode_random("не ключ") is None


@pytest.mark.parametrize(
    ("name", "load"),
    [("MANDAT_JOURNAL_KEY", "journal"), ("MANDAT_PII_KEY", "pii"), ("MANDAT_STORE_KEY", "store")],
)
def test_protected_mode_refuses_a_password_and_demo_accepts_it(monkeypatch, name, load):
    from mandat.services.env_policy import key_problem

    for other, tag in (("MANDAT_JOURNAL_KEY", "j"), ("MANDAT_PII_KEY", "p"), ("MANDAT_STORE_KEY", "s")):
        monkeypatch.setenv(other, random_key(tag))
    monkeypatch.setenv(name, "mandat-pilot-key-2026-please-change-me!")
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    problem = key_problem() or ""
    assert name in problem and "случайный ключ" in problem, problem
    monkeypatch.setenv("MANDAT_ENV", "demo")
    assert key_problem() is None


def test_shared_admin_token_must_be_random_in_protected_mode(monkeypatch):
    from mandat.api.principals import access_problem

    monkeypatch.delenv("MANDAT_API_USERS", raising=False)
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    monkeypatch.setenv("MANDAT_API_TOKEN", "admin-token-for-the-pilot-2026-ok")
    assert "MANDAT_API_TOKEN" in (access_problem() or "")
    monkeypatch.setenv("MANDAT_API_TOKEN", secrets.token_urlsafe(48))
    assert access_problem() is None
