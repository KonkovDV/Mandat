"""M-04: удостоверение привязано к запросу операции — сумма, представитель, получатель, одноразовый nonce."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandat.audit import attestation
from mandat.audit.attestation import OperationRequest
from mandat.audit.status_list import StatusRegistry

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
PSEUDONYM_KEY = b"k" * 32
REP_INN = "123456789012"
AUDIENCE = "bank-dbo-msb"
NONCE = "n-0123456789abcdef"


def _report(**operation):
    return {
        "verdict": "confirmed",
        "profile": "strict",
        "organization": {"inn": "7707083893"},
        "requester": {"inn": REP_INN},
        "operation": {"code": "payment_sign", "date": NOW.date().isoformat(), "amount_rub": 500_000, **operation},
        "chain": {"poa_numbers": ["poc"]},
        "rule_pack": {"sha256": "a" * 64},
        "reproducibility_hash": "b" * 64,
    }


def _issued(tmp_path: Path, **kwargs):
    key = Ed25519PrivateKey.generate()
    sd = tmp_path / "status"
    registry = StatusRegistry.open(sd)
    idx = registry.allocate({"org_inn": "7707083893", "verdict": "confirmed", "poa_numbers": ["poc"]})
    options = {"pseudonym_key": PSEUDONYM_KEY, "audience": AUDIENCE, "nonce": NONCE, **kwargs}
    cred = attestation.issue(_report(), key, now=NOW, status=registry.credential_status(idx), **options)
    registry.save(key, now=NOW)
    return key, cred, sd


def _request(**changes) -> OperationRequest:
    base = {
        "operation": "payment_sign",
        "amount_rub": 500_000,
        "representative_inn": REP_INN,
        "audience": AUDIENCE,
        "nonce": NONCE,
        "organization_inn": "7707083893",
        "operation_date": NOW.date(),
    }
    return OperationRequest(**{**base, **changes})


class Nonces:
    def __init__(self) -> None:
        self.used: set[str] = set()

    def __call__(self, nonce: str) -> bool:
        if nonce in self.used:
            return False
        self.used.add(nonce)
        return True


def _authorize(key, cred, sd, *, request=None, nonces=None, pseudonym_key=PSEUDONYM_KEY):
    return attestation.authorize_operation(
        cred,
        [key.public_key()],
        now=NOW + timedelta(minutes=1),
        status_dir=sd,
        seen={},
        request=request,
        pseudonym_key=pseudonym_key,
        consume_nonce=nonces,
    )


@pytest.fixture(autouse=True)
def _prod(monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")


def test_bound_request_is_authorized_once(tmp_path):
    key, cred, sd = _issued(tmp_path)
    nonces = Nonces()
    assert _authorize(key, cred, sd, request=_request(), nonces=nonces) == []
    again = _authorize(key, cred, sd, request=_request(), nonces=nonces)
    assert any("уже использован" in p for p in again)


def test_smaller_amount_is_covered(tmp_path):
    key, cred, sd = _issued(tmp_path)
    assert _authorize(key, cred, sd, request=_request(amount_rub=100), nonces=Nonces()) == []


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"operation": "account_dispose"}, "операция"),
        ({"amount_rub": 500_001}, "больше проверенной"),
        ({"amount_rub": -1}, "отрицательная"),
        ({"representative_inn": "500100732259"}, "представитель"),
        ({"audience": "other-system"}, "не для"),
        ({"nonce": "n-ffffffffffffffff"}, "nonce"),
    ],
)
def test_each_mismatch_is_refused_and_nonce_is_kept(tmp_path, changes, message):
    key, cred, sd = _issued(tmp_path)
    nonces = Nonces()
    problems = _authorize(key, cred, sd, request=_request(**changes), nonces=nonces)
    assert any(message in p for p in problems), problems
    assert nonces.used == set()  # отказ не расходует nonce
    assert _authorize(key, cred, sd, request=_request(), nonces=nonces) == []


def test_protected_mode_requires_request(tmp_path):
    key, cred, sd = _issued(tmp_path)
    assert any("нет запроса операции" in p for p in _authorize(key, cred, sd))


def test_protected_mode_requires_nonce_store(tmp_path):
    key, cred, sd = _issued(tmp_path)
    assert any("хранилища nonce" in p for p in _authorize(key, cred, sd, request=_request()))


@pytest.mark.parametrize(
    ("issue_kwargs", "message"),
    [
        ({"audience": None}, "не для"),
        ({"nonce": None}, "nonce"),
        ({"pseudonym_key": None}, "представителя не сверить"),
    ],
)
def test_unbound_credential_is_refused(tmp_path, issue_kwargs, message):
    key, cred, sd = _issued(tmp_path, **issue_kwargs)
    assert any(message in p for p in _authorize(key, cred, sd, request=_request(), nonces=Nonces()))


def test_missing_pseudonym_key_at_check_is_refused(tmp_path):
    key, cred, sd = _issued(tmp_path)
    problems = _authorize(key, cred, sd, request=_request(), nonces=Nonces(), pseudonym_key=None)
    assert any("представителя не сверить" in p for p in problems)


def test_amount_without_bound_amount_is_refused():
    subject = {
        "operation": "payment_sign",
        "amountRub": None,
        "organizationInn": "7707083893",
        "operationDate": NOW.date().isoformat(),
    }
    problems = attestation.check_request(subject, _request(), pseudonym_key=None, consume_nonce=None)
    assert any("нет суммы" in p for p in problems)


def test_tampered_binding_breaks_signature(tmp_path):
    key, cred, sd = _issued(tmp_path)
    cred["credentialSubject"]["amountRub"] = 10**9
    problems = _authorize(key, cred, sd, request=_request(amount_rub=10**9), nonces=Nonces())
    assert any("подпис" in p for p in problems)


def test_refused_credential_does_not_burn_nonce(tmp_path):
    key, cred, sd = _issued(tmp_path)
    cred["credentialSubject"]["verdict"] = "needs_review"
    nonces = Nonces()
    _authorize(key, cred, sd, request=_request(), nonces=nonces)
    assert nonces.used == set()


def test_short_nonce_is_rejected_at_issue():
    with pytest.raises(ValueError, match="nonce"):
        attestation.issue(_report(), Ed25519PrivateKey.generate(), now=NOW, nonce="short")


def test_stand_without_request_keeps_old_behaviour(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "dev")
    key, cred, sd = _issued(tmp_path)
    assert _authorize(key, cred, sd) == []


def test_request_without_amount_is_refused_when_credential_has_amount(tmp_path):
    key, cred, sd = _issued(tmp_path)
    problems = _authorize(key, cred, sd, request=_request(amount_rub=None), nonces=Nonces())
    assert any("в запросе нет суммы" in p for p in problems)


def test_request_without_amount_passes_when_credential_has_none():
    subject = {
        "operation": "payment_sign",
        "amountRub": None,
        "organizationInn": "7707083893",
        "operationDate": NOW.date().isoformat(),
    }
    problems = attestation.check_request(subject, _request(amount_rub=None), pseudonym_key=None, consume_nonce=None)
    assert not any("сумм" in p for p in problems)


@pytest.mark.parametrize(("amount", "message"), [(True, "целым"), (-5, "отрицательная"), (1.5, "целым")])
def test_bad_request_amount_is_checked_first(amount, message):
    subject = {
        "operation": "payment_sign",
        "amountRub": None,
        "organizationInn": "7707083893",
        "operationDate": NOW.date().isoformat(),
    }
    problems = attestation.check_request(subject, _request(amount_rub=amount), pseudonym_key=None, consume_nonce=None)
    assert any(message in p for p in problems)


@pytest.mark.parametrize(
    ("changes", "message"),
    [({"nonce": "short"}, "короче"), ({"audience": ""}, "не указан получатель"), ({"audience": None}, "получатель")],
)
def test_weak_request_fields_are_refused(tmp_path, changes, message):
    key, cred, sd = _issued(tmp_path)
    nonces = Nonces()
    problems = _authorize(key, cred, sd, request=_request(**changes), nonces=nonces)
    assert any(message in p for p in problems)
    assert nonces.used == set()


def test_other_day_and_other_company_are_refused(tmp_path):
    key, cred, sd = _issued(tmp_path)
    nonces = Nonces()
    other_day = _authorize(
        key, cred, sd, request=_request(operation_date=NOW.date() + timedelta(days=1)), nonces=nonces
    )
    assert any("дата операции" in p for p in other_day)
    other_company = _authorize(key, cred, sd, request=_request(organization_inn="7729985006"), nonces=nonces)
    assert any("организация" in p for p in other_company)
    assert nonces.used == set()
    assert _authorize(key, cred, sd, request=_request(), nonces=nonces) == []


def test_check_after_moscow_midnight_is_the_next_day(tmp_path):
    key, cred, sd = _issued(tmp_path)
    # 22:00 UTC — уже следующие сутки в Москве.
    late = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    assert late.astimezone(ZoneInfo("Europe/Moscow")).date() == NOW.date() + timedelta(days=1)
    problems = attestation.authorize_operation(
        cred,
        [key.public_key()],
        now=late,
        status_dir=sd,
        seen={},
        operation_risk="default",
        request=_request(),
        pseudonym_key=PSEUDONYM_KEY,
        consume_nonce=Nonces(),
    )
    assert any("день операции" in p for p in problems)


def test_credential_ends_with_the_power_of_attorney(tmp_path):
    key = Ed25519PrivateKey.generate()
    report = _report()
    report["chain"]["documents"] = [{"number": "poc", "effective_until": "2026-10-06"}]
    cred = attestation.issue(report, key, now=NOW, pseudonym_key=PSEUDONYM_KEY, audience=AUDIENCE, nonce=NONCE)
    until = datetime.fromisoformat(cred["validUntil"])
    assert until == datetime(2026, 10, 7, 0, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    assert until < NOW + attestation.VALID_FOR


def test_expired_power_makes_a_zero_window(tmp_path):
    report = _report()
    report["chain"]["documents"] = [{"effective_until": "2026-09-30"}]
    cred = attestation.issue(report, Ed25519PrivateKey.generate(), now=NOW)
    assert cred["validFrom"] == cred["validUntil"]


def test_failing_nonce_store_means_refusal(tmp_path):
    key, cred, sd = _issued(tmp_path)

    def down(nonce: str) -> bool:
        raise ConnectionError("redis down")

    problems = _authorize(key, cred, sd, request=_request(), nonces=down)
    assert any("недоступно" in p for p in problems)
