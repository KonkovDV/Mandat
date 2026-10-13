"""Standard Webhooks 1.0: whsec_, несколько подписей, смена ключа, окно времени, повтор."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest

from mandat.api.storage import MemoryStore
from mandat.services.webhooks import (
    canonical_body,
    decode_secret,
    delivery,
    new_secret,
    sign_headers,
    signature_ok,
)

NOW = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)


def test_spec_vector():
    # Пример из официальной библиотеки standard-webhooks (тест test_sign).
    key = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
    headers = sign_headers(
        key, "msg_p5jXN8AQM9LWM0D4loKWxJek", b'{"test": 2432232314}', datetime.fromtimestamp(1614265330, UTC)
    )
    assert headers["webhook-signature"] == "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="


def test_new_secret_has_spec_shape():
    s = new_secret()
    assert s.startswith("whsec_") and len(decode_secret(s)) == 32


@pytest.mark.parametrize(
    "bad",
    [
        "whsec_!!!",
        "whsec_" + base64.b64encode(b"short").decode(),
        "whsec_" + base64.b64encode(b"x" * 65).decode(),
        "  ",
    ],
)
def test_bad_secrets_are_rejected(bad):
    with pytest.raises(ValueError):
        decode_secret(bad)


def test_legacy_plain_secret_still_works():
    packed = delivery({"a": 1}, "secret", now=NOW)
    assert signature_ok("secret", packed["headers"], packed["body"].encode(), NOW)
    assert not signature_ok("other", packed["headers"], packed["body"].encode(), NOW)


def test_rotation_signs_with_every_key_and_accepts_any():
    old, new = new_secret(), new_secret()
    packed = delivery({"a": 1}, f"{new} {old}", now=NOW)
    body = packed["body"].encode()
    sigs = packed["headers"]["webhook-signature"].split(" ")
    assert len(sigs) == 2 and all(s.startswith("v1,") for s in sigs)
    assert signature_ok(old, packed["headers"], body, NOW)  # получатель ещё со старым ключом
    assert signature_ok(new, packed["headers"], body, NOW)  # получатель уже с новым
    assert not signature_ok(new_secret(), packed["headers"], body, NOW)


def test_tampered_body_and_headers_fail():
    key = new_secret()
    packed = delivery({"a": 1}, key, now=NOW)
    body = packed["body"].encode()
    assert not signature_ok(key, packed["headers"], body + b" ", NOW)
    assert not signature_ok(key, {**packed["headers"], "webhook-id": "msg_other"}, body, NOW)
    assert not signature_ok(key, {**packed["headers"], "webhook-timestamp": str(int(NOW.timestamp()) + 1)}, body, NOW)
    assert not signature_ok(key, {**packed["headers"], "webhook-signature": "v1a,abc"}, body, NOW)


def test_header_names_are_case_insensitive():
    key = new_secret()
    packed = delivery({"a": 1}, key, now=NOW)
    upper = {k.upper(): v for k, v in packed["headers"].items()}
    assert signature_ok(key, upper, packed["body"].encode(), NOW)


@pytest.mark.parametrize("shift", [-301, 301])
def test_timestamp_outside_window_fails(shift):
    key = new_secret()
    packed = delivery({"a": 1}, key, now=NOW)
    assert not signature_ok(key, packed["headers"], packed["body"].encode(), NOW + timedelta(seconds=shift))


@pytest.mark.parametrize("stamp", ["1.5", "-1", "١٧٦٠٣٣٩٢٠٠", ""])
def test_malformed_timestamp_fails(stamp):
    key = new_secret()
    packed = delivery({"a": 1}, key, now=NOW)
    assert not signature_ok(key, {**packed["headers"], "webhook-timestamp": stamp}, packed["body"].encode(), NOW)


def test_id_with_dot_is_refused():
    with pytest.raises(ValueError):
        sign_headers(new_secret(), "msg.1", b"{}", NOW)


def test_replay_is_rejected_with_store():
    key, seen = new_secret(), MemoryStore()
    packed = delivery({"a": 1}, key, now=NOW)
    body = packed["body"].encode()
    assert signature_ok(key, packed["headers"], body, NOW, seen=seen)
    assert not signature_ok(key, packed["headers"], body, NOW + timedelta(seconds=10), seen=seen)


def test_delivery_id_depends_only_on_body():
    a = delivery({"a": 1}, "s", now=NOW)["headers"]["webhook-id"]
    b = delivery({"a": 1}, "s", now=NOW + timedelta(hours=1))["headers"]["webhook-id"]
    assert a == b and canonical_body({"a": 1}) == b'{"a":1}'


def test_missing_replay_store_in_protected_mode_is_a_refusal(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    secret = new_secret()
    body = b"{}"
    headers = sign_headers(secret, "evt_1", body, NOW)
    assert signature_ok(secret, headers, body, NOW) is False
