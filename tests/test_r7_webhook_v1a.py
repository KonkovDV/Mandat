"""R7: асимметричная подпись Standard Webhooks v1a (Ed25519) и read-back с ключом получателя."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from test_r5_tlog import Receiver, _HeaderResponse

from mandat import cli
from mandat.audit import anchor
from mandat.services import webhooks

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def test_pair_signs_v1a_and_verifies_with_public_key_only():
    signing, verifying = webhooks.new_signing_key()
    assert signing.startswith("whsk_") and verifying.startswith("whpk_")
    headers = webhooks.sign_headers(signing, "msg_1", b'{"a":1}', NOW)
    assert headers["webhook-signature"].startswith("v1a,")
    assert webhooks.signature_ok(verifying, headers, b'{"a":1}', NOW)
    assert webhooks.signature_ok(signing, headers, b'{"a":1}', NOW)  # из whsk_ выводится открытый ключ
    assert not webhooks.signature_ok(verifying, headers, b'{"a":2}', NOW)
    assert not webhooks.signature_ok(webhooks.new_signing_key()[1], headers, b'{"a":1}', NOW)
    assert not webhooks.signature_ok(verifying, headers, b'{"a":1}', NOW + timedelta(minutes=10))


def test_mixed_keys_rotation_and_cross_scheme():
    hmac_secret = webhooks.new_secret()
    signing, verifying = webhooks.new_signing_key()
    headers = webhooks.sign_headers(f"{signing} {hmac_secret}", "msg_2", b"{}", NOW)
    sigs = headers["webhook-signature"].split(" ")
    assert [s.split(",")[0] for s in sigs] == ["v1a", "v1"]
    assert webhooks.signature_ok(verifying, headers, b"{}", NOW)
    assert webhooks.signature_ok(hmac_secret, headers, b"{}", NOW)
    only_v1a = {**headers, "webhook-signature": sigs[0]}
    assert not webhooks.signature_ok(hmac_secret, only_v1a, b"{}", NOW)  # v1a не сверяется HMAC-секретом
    only_v1 = {**headers, "webhook-signature": sigs[1]}
    assert not webhooks.signature_ok(verifying, only_v1, b"{}", NOW)  # и наоборот
    garbage = {**headers, "webhook-signature": "v1a,!!! v1a," + base64.b64encode(b"x" * 64).decode()}
    assert not webhooks.signature_ok(verifying, garbage, b"{}", NOW)


def test_key_formats():
    signing, verifying = webhooks.new_signing_key()
    raw = base64.b64decode(signing[5:])
    seed_only = "whsk_" + base64.b64encode(raw[:32]).decode().rstrip("=")  # без выравнивания тоже принимается
    assert webhooks.parse_keys(seed_only)[0] == webhooks.parse_keys(signing)[0]
    with pytest.raises(ValueError, match="whpk_ нельзя подписать"):
        webhooks.sign_headers(verifying, "msg_3", b"{}", NOW)
    with pytest.raises(ValueError, match="32 байта Ed25519"):
        webhooks.parse_keys("whpk_" + base64.b64encode(b"x" * 31).decode())
    with pytest.raises(ValueError, match="seed"):
        webhooks.parse_keys("whsk_" + base64.b64encode(b"x" * 40).decode())
    with pytest.raises(ValueError, match="открытая часть"):
        webhooks.parse_keys("whsk_" + base64.b64encode(raw[:32] + b"\x00" * 32).decode())
    with pytest.raises(ValueError, match="base64"):
        webhooks.parse_keys("whpk_!!!")
    with pytest.raises(ValueError, match="ни один"):
        webhooks.parse_keys([])


def test_delivery_with_asymmetric_key():
    signing, verifying = webhooks.new_signing_key()
    out = webhooks.delivery({"alerts": [1]}, signing, now=NOW)
    assert webhooks.signature_ok(verifying, out["headers"], out["body"].encode(), NOW)


def test_cli_prints_pair(capsys):
    assert cli.main(["webhook-secret", "--asymmetric"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("whsk_") and lines[1].startswith("whpk_")
    signing, verifying = lines[0].split()[0], lines[1].split()[0]
    headers = webhooks.sign_headers(signing, "msg_4", b"{}", NOW)
    assert webhooks.signature_ok(verifying, headers, b"{}", NOW)
    assert cli.main(["webhook-secret"]) == 0
    assert capsys.readouterr().out.startswith("whsec_")


class AsymmetricReceiver(Receiver):
    """Получатель подписывает ответы read-back своим whsk_; «Мандат» знает только его whpk_."""

    def __init__(self, secret: str):
        super().__init__(secret)
        self.signing, self.verifying = webhooks.new_signing_key()
        self.sign_with_shared = False

    def __call__(self, request):
        response = super().__call__(request)
        if request.get_method() == "POST" or not isinstance(response, _HeaderResponse):
            return response
        body = response.getvalue()
        rid = response.headers.get("webhook-id") or {k.lower(): v for k, v in request.header_items()}["webhook-id"]
        key = self.secret if self.sign_with_shared else self.signing
        return _HeaderResponse(webhooks.sign_headers(key, rid, body, datetime.now(UTC)), body)


def test_readback_with_receiver_public_key():
    receiver = AsymmetricReceiver(webhooks.new_secret())
    sink = anchor.WebhookSink(
        "https://h.example/anchor",
        receiver.secret,
        receiver,
        readback="https://h.example/anchor/{sha256}",
        readback_key=receiver.verifying,
    )
    stmt = {"type": anchor.STATEMENT_TYPE, "segment": 1}
    receipt = sink.publish(stmt)
    assert sink.check(stmt, receipt)
    # Тот, у кого есть общий секрет (root на хосте), подписал ответ им: при ключе получателя не принимается.
    receiver.sign_with_shared = True
    assert "подпись" in sink.remote_problem(stmt, receipt)
    with pytest.raises(ValueError, match="whpk_"):
        anchor.WebhookSink(
            "https://h.example", receiver.secret, readback="https://h.example/{sha256}", readback_key="whsec_x"
        )


def test_readback_key_from_env(monkeypatch):
    _, verifying = webhooks.new_signing_key()
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_SECRET", webhooks.new_secret())
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_WEBHOOK_READBACK", "https://h.example/a/{sha256}")
    monkeypatch.setenv("MANDAT_JOURNAL_ANCHOR_WEBHOOK_READBACK_KEY", verifying)
    (sink,) = anchor.configured_sinks("webhook:https://h.example/a")
    assert sink.readback_key == verifying
