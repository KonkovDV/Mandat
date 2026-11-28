"""Допуск операции через HTTP — контракт для ДБО.

POST /v1/operations/authorize передаёт удостоверение и запрос в ``authorize_operation`` и ничего не ослабляет.
Повтор ``nonce``, в том числе одновременный, даёт ровно один допуск; вердикт не confirmed, чужая сумма или
получатель допуска не дают; отказ не расходует ``nonce``; решение пишется в журнал без ИНН представителя.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from mandat.api.app import create_app
from mandat.api.principals import hash_token
from mandat.api.storage import MemoryStore, RedisStore, SQLiteStore
from mandat.audit import attestation, journal
from mandat.audit.attestation import BANK_TZ
from mandat.audit.signing import Keyring
from mandat.audit.status_list import StatusRegistry
from mandat.services import admission

TOKEN = "t" * 40
PSEUDONYM_KEY = b"k" * 32
REP_INN = "123456789012"
ORG_INN = "7707083893"
AUDIENCE = "bank-dbo-msb"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def _today() -> str:
    return datetime.now(UTC).astimezone(BANK_TZ).date().isoformat()


def _report(verdict: str = "confirmed") -> dict:
    return {
        "verdict": verdict,
        "profile": "strict",
        "organization": {"inn": ORG_INN},
        "requester": {"inn": REP_INN},
        "operation": {"code": "PAYMENT_SIGN", "date": _today(), "amount_rub": 500_000},
        "chain": {"poa_numbers": ["poc"]},
        "rule_pack": {"sha256": "a" * 64},
        "reproducibility_hash": "b" * 64,
    }


class Issuer:
    """Издатель стенда: ключ, реестр статусов, удостоверения на запрос."""

    def __init__(self, tmp_path) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.status_dir = tmp_path / "status"
        self.registry = StatusRegistry.open(self.status_dir)
        self.count = 0

    def issue(self, nonce: str, verdict: str = "confirmed") -> dict:
        index = self.registry.allocate({"org_inn": ORG_INN, "verdict": verdict, "poa_numbers": ["poc"]})
        cred = attestation.issue(
            _report(verdict),
            self.key,
            status=self.registry.credential_status(index),
            pseudonym_key=PSEUDONYM_KEY,
            audience=AUDIENCE,
            nonce=nonce,
        )
        self.registry.save(self.key)
        self.count += 1
        return cred

    def config(self) -> admission.AdmissionConfig:
        return admission.AdmissionConfig(Keyring.of([self.key.public_key()]), self.status_dir, PSEUDONYM_KEY)


def _body(cred: dict, nonce: str, **changes) -> dict:
    request = {
        "organization_inn": ORG_INN,
        "operation": "PAYMENT_SIGN",
        "operation_date": _today(),
        "amount_rub": 500_000,
        "representative_inn": REP_INN,
        "audience": AUDIENCE,
        "nonce": nonce,
        **changes,
    }
    return {"credential": cred, "request": request, "operation_risk": "payment"}


@pytest.fixture
def issuer(tmp_path) -> Issuer:
    return Issuer(tmp_path)


def _client(issuer: Issuer, tmp_path, store=None, journal_path=None) -> TestClient:
    app = create_app(
        token=TOKEN,
        store=store or SQLiteStore(tmp_path / "store.db"),
        journal_path=journal_path or tmp_path / "journal.jsonl",
        admission_config=issuer.config(),
    )
    return TestClient(app)


def test_confirmed_credential_and_matching_request_are_admitted_once(issuer, tmp_path):
    nonce = "n-" + "1" * 20
    client = _client(issuer, tmp_path)
    body = _body(issuer.issue(nonce), nonce)
    first = client.post("/v1/operations/authorize", json=body, headers=HEADERS)
    assert first.status_code == 200, first.text
    assert first.json()["allowed"] is True, first.json()
    assert first.json()["problems"] == []
    again = client.post("/v1/operations/authorize", json=body, headers=HEADERS)
    assert again.status_code == 200
    assert again.json()["allowed"] is False
    assert any("nonce уже использован" in p for p in again.json()["problems"])


@pytest.mark.parametrize("verdict", ["refuted", "needs_review", "insufficient_data"])
def test_verdict_other_than_confirmed_is_not_admitted(issuer, tmp_path, verdict):
    nonce = "n-" + verdict.ljust(20, "x")
    answer = _client(issuer, tmp_path).post(
        "/v1/operations/authorize", json=_body(issuer.issue(nonce, verdict), nonce), headers=HEADERS
    )
    assert answer.status_code == 200
    assert answer.json()["allowed"] is False
    assert any("нужен confirmed" in p for p in answer.json()["problems"])


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"amount_rub": 500_001}, "больше проверенной"),
        ({"audience": "other-system"}, "выпущено не для"),
        ({"organization_inn": "7728168971"}, "организация"),
        ({"representative_inn": "500100732259"}, "представитель в запросе не тот"),
        ({"operation": "USER_ADMIN"}, "операция"),
    ],
)
def test_mismatched_request_is_refused_and_does_not_burn_the_nonce(issuer, tmp_path, change, reason):
    nonce = "n-" + "2" * 20
    client = _client(issuer, tmp_path)
    cred = issuer.issue(nonce)
    wrong = client.post("/v1/operations/authorize", json=_body(cred, nonce, **change), headers=HEADERS).json()
    assert wrong["allowed"] is False
    assert any(reason in p for p in wrong["problems"]), wrong["problems"]
    right = client.post("/v1/operations/authorize", json=_body(cred, nonce), headers=HEADERS).json()
    assert right["allowed"] is True, right


def _race(client: TestClient, body: dict, n: int = 8) -> list[bool]:
    results: list[bool] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def worker() -> None:
        barrier.wait()
        answer = client.post("/v1/operations/authorize", json=body, headers=HEADERS).json()
        with lock:
            results.append(answer["allowed"])

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


@pytest.mark.parametrize("kind", ["memory", "sqlite", "redis"])
def test_concurrent_requests_with_one_nonce_give_exactly_one_admission(issuer, tmp_path, kind):
    store = {
        "memory": lambda: MemoryStore(),
        "sqlite": lambda: SQLiteStore(tmp_path / "race.db"),
        "redis": lambda: RedisStore(fakeredis.FakeStrictRedis()),
    }[kind]()
    nonce = "n-" + "3" * 20
    client = _client(issuer, tmp_path, store=store)
    results = _race(client, _body(issuer.issue(nonce), nonce))
    assert results.count(True) == 1, results


def test_decision_is_journaled_without_the_representative_inn(issuer, tmp_path):
    nonce = "n-" + "4" * 20
    log = tmp_path / "journal.jsonl"
    client = _client(issuer, tmp_path, journal_path=log)
    client.post("/v1/operations/authorize", json=_body(issuer.issue(nonce), nonce), headers=HEADERS)
    records = journal.read_journal(log)
    assert [r["action"] for r in records] == ["authorize_operation"]
    assert records[0]["details"]["allowed"] is True
    assert REP_INN not in log.read_text(encoding="utf-8")
    assert journal.verify_journal(log).ok


def test_revoked_credential_is_refused(issuer, tmp_path):
    nonce = "n-" + "5" * 20
    cred = issuer.issue(nonce)
    index = int(cred["credentialStatus"][0]["statusListIndex"])
    issuer.registry.mark(index, "revocation", "МЧД отменена")
    issuer.registry.save(issuer.key)
    answer = _client(issuer, tmp_path).post("/v1/operations/authorize", json=_body(cred, nonce), headers=HEADERS)
    assert answer.json()["allowed"] is False
    assert any("отозвано" in p for p in answer.json()["problems"]), answer.json()


def test_forged_signature_and_garbage_credential_are_refusals_not_errors(issuer, tmp_path):
    nonce = "n-" + "6" * 20
    client = _client(issuer, tmp_path)
    cred = issuer.issue(nonce)
    cred["credentialSubject"]["amountRub"] = 900_000_000
    forged = client.post("/v1/operations/authorize", json=_body(cred, nonce, amount_rub=900_000_000), headers=HEADERS)
    assert forged.status_code == 200 and forged.json()["allowed"] is False
    assert any("подпись не верна" in p for p in forged.json()["problems"]), forged.json()
    garbage = client.post("/v1/operations/authorize", json=_body({"credentialStatus": [{}]}, nonce), headers=HEADERS)
    assert garbage.status_code == 200 and garbage.json()["allowed"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"amount_rub": True},
        {"amount_rub": 10.5},
        {"operation_date": "17.11.2026"},
        {"nonce": "short"},
        {"representative_inn": "12345"},
        {"extra": "field"},
    ],
)
def test_malformed_request_is_422(issuer, tmp_path, change):
    nonce = "n-" + "7" * 20
    body = _body(issuer.issue(nonce), nonce)
    body["request"].update(change)
    answer = _client(issuer, tmp_path).post("/v1/operations/authorize", json=body, headers=HEADERS)
    assert answer.status_code == 422, answer.text


def _operation(nonce: str) -> attestation.OperationRequest:
    return attestation.OperationRequest(
        operation="PAYMENT_SIGN",
        amount_rub=500_000,
        representative_inn=REP_INN,
        audience=AUDIENCE,
        nonce=nonce,
        organization_inn=ORG_INN,
        operation_date=datetime.now(UTC).astimezone(BANK_TZ).date(),
    )


@pytest.mark.parametrize("env", ["demo", "pilot"])
@pytest.mark.parametrize("risk", ["low", "archive", "default", "LOW ", None])
def test_caller_cannot_widen_the_status_window_of_a_payment(issuer, tmp_path, monkeypatch, env, risk):
    """Окно свежести списка задаёт подписанная операция: платёж по списку трёхдневной давности не допускается."""
    monkeypatch.setenv("MANDAT_ENV", env)
    nonce = "n-" + "w" * 20
    cred = issuer.issue(nonce)
    issuer.registry.save(issuer.key, now=datetime.now(UTC) - timedelta(days=3))
    decision = admission.decide_detailed(
        admission.AdmissionRequest(cred, _operation(nonce), risk),
        issuer.config(),
        MemoryStore(),
        now=datetime.now(UTC),
        protected=env != "demo",
    )
    assert any("список устарел" in p for p in decision.problems), decision.problems


def test_stale_list_refuses_a_payment_over_http_whatever_risk_is_named(issuer, tmp_path):
    nonce = "n-" + "v" * 20
    cred = issuer.issue(nonce)
    issuer.registry.save(issuer.key, now=datetime.now(UTC) - timedelta(days=3))
    body = {**_body(cred, nonce), "operation_risk": "archive"}
    answer = _client(issuer, tmp_path).post("/v1/operations/authorize", json=body, headers=HEADERS).json()
    assert answer["allowed"] is False
    assert any("список устарел" in p for p in answer["problems"]), answer


@pytest.mark.parametrize("risk", ["lowest", "архив", "pay ment"])
def test_unknown_operation_risk_is_422_and_does_not_burn_the_nonce(issuer, tmp_path, risk):
    nonce = "n-" + "u" * 20
    client = _client(issuer, tmp_path)
    cred = issuer.issue(nonce)
    body = {**_body(cred, nonce), "operation_risk": risk}
    wrong = client.post("/v1/operations/authorize", json=body, headers=HEADERS)
    assert wrong.status_code == 422, wrong.text
    assert risk not in wrong.text
    right = client.post("/v1/operations/authorize", json=_body(cred, nonce), headers=HEADERS)
    assert right.json()["allowed"] is True, right.json()


def test_route_answers_503_when_admission_is_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("MANDAT_ATTESTATION_KEYRING", raising=False)
    app = create_app(token=TOKEN, store=MemoryStore(), journal_path=tmp_path / "j.jsonl")
    answer = TestClient(app).post("/v1/operations/authorize", json=_body({}, "n-" + "8" * 20), headers=HEADERS)
    assert answer.status_code == 503


def test_protected_mode_needs_a_shared_store_and_a_journal(issuer, tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    monkeypatch.setenv("MANDAT_JOURNAL_KEY", "j" * 40)
    monkeypatch.setenv("MANDAT_PII_KEY", "p" * 40)
    nonce = "n-" + "9" * 20
    body = _body(issuer.issue(nonce), nonce)
    # В pilot и prod допуск получает только учётная запись системы банка.
    expires = (datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds")
    account = {"name": "dbo", "role": "dbo", "token_sha256": hash_token(TOKEN), "expires_at": expires}
    dbo = json.dumps([{**account, "audiences": [AUDIENCE]}])
    memory = TestClient(
        create_app(users=dbo, store=MemoryStore(), journal_path=tmp_path / "j.jsonl", admission_config=issuer.config())
    )
    assert memory.post("/v1/operations/authorize", json=body, headers=HEADERS).status_code == 503
    no_journal = TestClient(
        create_app(users=dbo, store=SQLiteStore(tmp_path / "s.db"), admission_config=issuer.config())
    )
    assert no_journal.post("/v1/operations/authorize", json=body, headers=HEADERS).status_code == 503


def test_config_from_environment(issuer, tmp_path, monkeypatch):
    keyring = tmp_path / "keyring.json"
    keyring.write_text(json.dumps({"keys": [{"kid": attestation.public_key_multibase(issuer.key)}]}))
    key_file = tmp_path / "representative_hmac.key"
    key_file.write_bytes(PSEUDONYM_KEY)
    key_file.chmod(0o600)
    monkeypatch.setenv("MANDAT_ATTESTATION_KEYRING", str(keyring))
    monkeypatch.setenv("MANDAT_STATUS_DIR", str(issuer.status_dir))
    monkeypatch.setenv("MANDAT_REPRESENTATIVE_KEY_FILE", str(key_file))
    config = admission.AdmissionConfig.from_env(protected=True)
    assert config is not None and config.pseudonym_key == PSEUDONYM_KEY
    monkeypatch.delenv("MANDAT_STATUS_DIR")
    with pytest.raises(admission.AdmissionConfigError, match="MANDAT_STATUS_DIR"):
        admission.AdmissionConfig.from_env(protected=False)
    monkeypatch.setenv("MANDAT_STATUS_DIR", str(issuer.status_dir))
    monkeypatch.delenv("MANDAT_REPRESENTATIVE_KEY_FILE")
    with pytest.raises(admission.AdmissionConfigError, match="HMAC"):
        admission.AdmissionConfig.from_env(protected=True)
    assert admission.AdmissionConfig.from_env(protected=False).pseudonym_key is None


@pytest.mark.parametrize("kind", ["memory", "sqlite", "redis"])
def test_store_watermark_only_grows(tmp_path, kind):
    store = {
        "memory": lambda: MemoryStore(),
        "sqlite": lambda: SQLiteStore(tmp_path / "wm.db"),
        "redis": lambda: RedisStore(fakeredis.FakeStrictRedis()),
    }[kind]()
    assert store.watermark_get("list#1") is None
    assert store.watermark_raise("list#1", 5) == 5
    assert store.watermark_raise("list#1", 3) == 5
    assert store.watermark_raise("list#1", 7) == 7
    assert store.watermark_get("list#1") == 7


def test_status_list_rollback_is_refused_through_the_store(issuer, tmp_path):
    """Сервис видел новый список, ему подложили старый, правильно подписанный: допуска нет."""
    store = SQLiteStore(tmp_path / "rb.db")
    client = _client(issuer, tmp_path, store=store)
    old_nonce, new_nonce = "n-" + "a" * 20, "n-" + "b" * 20
    old_cred = issuer.issue(old_nonce)
    snapshot = {p.name: p.read_bytes() for p in issuer.status_dir.rglob("*.json")}
    new_cred = issuer.issue(new_nonce)
    assert client.post("/v1/operations/authorize", json=_body(new_cred, new_nonce), headers=HEADERS).json()["allowed"]
    for path in issuer.status_dir.rglob("*.json"):
        if path.name in snapshot:
            path.write_bytes(snapshot[path.name])
    answer = client.post("/v1/operations/authorize", json=_body(old_cred, old_nonce), headers=HEADERS).json()
    assert answer["allowed"] is False, answer
    assert any("старше" in p for p in answer["problems"]), answer["problems"]


def test_moment_is_the_server_clock_not_a_request_field(issuer, tmp_path):
    """Дата в запросе вчерашняя: удостоверение выпущено на сегодня, допуска нет, хотя поле подделать легко."""
    nonce = "n-" + "c" * 20
    yesterday = (datetime.now(UTC).astimezone(BANK_TZ).date() - timedelta(days=1)).isoformat()
    answer = (
        _client(issuer, tmp_path)
        .post(
            "/v1/operations/authorize",
            json=_body(issuer.issue(nonce), nonce, operation_date=yesterday),
            headers=HEADERS,
        )
        .json()
    )
    assert answer["allowed"] is False
    assert any("дата операции" in p for p in answer["problems"]), answer


# --- Повтор после потерянного ответа --------------------------------------------------------------

URL = "/v1/operations/authorize"


def test_same_request_after_a_lost_answer_is_refused_as_already_admitted(issuer, tmp_path):
    """Ответ на допуск потерялся, ДБО повторил тот же запрос: отказ с признаком и временем первого допуска."""
    nonce = "n-" + "a" * 20
    client = _client(issuer, tmp_path)
    body = _body(issuer.issue(nonce), nonce)
    first = client.post(URL, json=body, headers=HEADERS).json()
    assert first["allowed"] is True and first["already_admitted"] is False and first["admitted_at"] is None
    again = client.post(URL, json=body, headers=HEADERS).json()
    assert again["allowed"] is False
    assert again["already_admitted"] is True
    assert again["admitted_at"] == first["checked_at"]
    assert again["problems"] == [attestation.NONCE_USED]


def test_other_request_with_the_same_nonce_is_refused_without_the_flag(issuer, tmp_path):
    nonce = "n-" + "b" * 20
    client = _client(issuer, tmp_path)
    cred = issuer.issue(nonce)
    assert client.post(URL, json=_body(cred, nonce), headers=HEADERS).json()["allowed"] is True
    other = client.post(URL, json=_body(cred, nonce, amount_rub=400_000), headers=HEADERS).json()
    assert other["allowed"] is False and other["already_admitted"] is False
    assert other["problems"] == [admission.NONCE_OTHER_REQUEST]


@pytest.mark.parametrize("kind", ["memory", "sqlite", "redis"])
def test_concurrent_identical_requests_give_one_admission_and_flagged_replays(issuer, tmp_path, kind):
    store = {
        "memory": lambda: MemoryStore(),
        "sqlite": lambda: SQLiteStore(tmp_path / "replay.db"),
        "redis": lambda: RedisStore(fakeredis.FakeStrictRedis()),
    }[kind]()
    nonce = "n-" + "c" * 20
    client = _client(issuer, tmp_path, store=store)
    body = _body(issuer.issue(nonce), nonce)
    answers: list[dict] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        answer = client.post(URL, json=body, headers=HEADERS).json()
        with lock:
            answers.append(answer)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [a["allowed"] for a in answers].count(True) == 1, answers
    assert all(a["already_admitted"] for a in answers if not a["allowed"]), answers


def test_replay_is_journaled_with_the_flag_and_without_the_inn(issuer, tmp_path):
    nonce = "n-" + "d" * 20
    log = tmp_path / "journal.jsonl"
    client = _client(issuer, tmp_path, journal_path=log)
    body = _body(issuer.issue(nonce), nonce)
    client.post(URL, json=body, headers=HEADERS)
    client.post(URL, json=body, headers=HEADERS)
    records = journal.read_journal(log)
    assert [r["details"]["already_admitted"] for r in records] == [False, True]
    assert [r["details"]["allowed"] for r in records] == [True, False]
    assert REP_INN not in log.read_text(encoding="utf-8")


def test_nonce_spent_before_the_upgrade_stays_spent(issuer, tmp_path):
    """Ключ, занятый first_seen в старом формате, — повтор без признака, а не новый допуск."""
    nonce = "n-" + "e" * 20
    store = SQLiteStore(tmp_path / "legacy.db")
    assert store.first_seen(admission.nonce_key(AUDIENCE, nonce), 3600)
    answer = _client(issuer, tmp_path, store=store).post(URL, json=_body(issuer.issue(nonce), nonce), headers=HEADERS)
    assert answer.json()["allowed"] is False and answer.json()["already_admitted"] is False
    assert answer.json()["problems"] == [attestation.NONCE_USED]


def test_replay_after_revocation_is_refused_by_status_without_the_flag(issuer, tmp_path):
    """После отзыва удостоверения повтор не узнаёт о допуске: отказ по статусу, проводить нечего."""
    nonce = "n-" + "f" * 20
    client = _client(issuer, tmp_path)
    cred = issuer.issue(nonce)
    assert client.post(URL, json=_body(cred, nonce), headers=HEADERS).json()["allowed"] is True
    issuer.registry.mark(int(cred["credentialStatus"][0]["statusListIndex"]), "revocation", "МЧД отменена")
    issuer.registry.save(issuer.key)
    again = client.post(URL, json=_body(cred, nonce), headers=HEADERS).json()
    assert again["allowed"] is False and again["already_admitted"] is False
    assert any("отозвано" in p for p in again["problems"]), again


def test_fingerprint_hides_the_representative_inn(issuer):
    """С ключом представителя отпечаток — HMAC: ИНН по нему перебором не подобрать без ключа."""
    nonce = "n-" + "g" * 20
    body = _body(issuer.issue(nonce), nonce)
    request = admission.AdmissionRequest(
        body["credential"],
        attestation.OperationRequest(
            operation="PAYMENT_SIGN",
            amount_rub=500_000,
            representative_inn=REP_INN,
            audience=AUDIENCE,
            nonce=nonce,
            organization_inn=ORG_INN,
            operation_date=admission.parse_day(_today()),
        ),
        "payment",
    )
    keyed = admission.request_fingerprint(request, PSEUDONYM_KEY)
    assert keyed == admission.request_fingerprint(request, PSEUDONYM_KEY)
    assert keyed != admission.request_fingerprint(request, b"x" * 32)
    assert keyed != admission.request_fingerprint(request, None)


def test_admission_without_a_journal_releases_the_nonce(issuer, tmp_path, monkeypatch):
    """503 из-за журнала: допуск не выдан, и повтор того же запроса проверяется заново, а не «уже допущен»."""
    nonce = "n-" + "h" * 20
    client = _client(issuer, tmp_path)
    body = _body(issuer.issue(nonce), nonce)
    real = journal.append

    def broken(*args, **kwargs):
        raise OSError("журнал недоступен")

    monkeypatch.setattr(journal, "append", broken)
    assert client.post(URL, json=body, headers=HEADERS).status_code == 503
    monkeypatch.setattr(journal, "append", real)
    again = client.post(URL, json=body, headers=HEADERS).json()
    assert again["allowed"] is True and again["already_admitted"] is False
