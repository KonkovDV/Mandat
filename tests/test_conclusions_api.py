"""Реестр заключений юриста через HTTP API: черновик, утверждение вторым юристом, применение в строгой проверке.

Те же доступ, строгий JSON, лимиты и стиль ошибок, что у разбора: 403 — роль и независимость, 409 — состояние и
версия, 422 — поля, 503 — журнал и хранилище. Применение, перепроверка и каждый шаг — в журнале аудита.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from conftest import random_key
from fastapi.testclient import TestClient

from mandat.advisory import llm
from mandat.api import app as app_module
from mandat.api import conclusions as conclusion_api
from mandat.api.app import create_app
from mandat.api.principals import hash_token
from mandat.api.storage import MemoryStore, SQLiteStore, Store
from mandat.audit import journal
from mandat.eval import synth

USERS = {
    "op": ("operator", None),
    "rv": ("reviewer", "lawyer"),
    "rv2": ("reviewer", "lawyer"),
    "cmp": ("reviewer", "compliance"),
    "aud": ("auditor", None),
    "adm": ("admin", None),
    "dbo": ("dbo", None),
}
BASIS = "П. 9.2 устава: директор действует без доверенности, ограничений на платежи нет"


def _token(name: str) -> str:
    return (name * 40)[:40]


def _h(name: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + _token(name)}


def _users() -> str:
    out = []
    for name, (role, spec) in USERS.items():
        entry = {"name": name, "role": role, "token_sha256": hash_token(_token(name))}
        if spec:
            entry["specialization"] = spec
        if role == "dbo":
            entry["audiences"] = ["bank-dbo-msb"]
        out.append(entry)
    return json.dumps(out)


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch):
    for var in ("MANDAT_ENV", "MANDAT_JOURNAL_KEY", "MANDAT_API_JOURNAL", "MANDAT_STORE_KEY", "MANDAT_API_STORE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(params=["memory", "sqlite", "redis"])
def store(request, tmp_path) -> Store:
    if request.param == "memory":
        return MemoryStore()
    if request.param == "sqlite":
        return SQLiteStore(tmp_path / "api.db")
    return request.getfixturevalue("redis_store")


def _client(store: Store, tmp_path: Path) -> TestClient:
    return TestClient(create_app(users=_users(), rate_per_min=0, store=store, journal_path=tmp_path / "j.jsonl"))


def _manifest(defect: str = "ok", seed: int = 1, charter_extra: str = "") -> dict:
    manifest = synth.build(defect, seed).inline_manifest()
    if charter_extra:
        for doc in manifest["documents"]:
            if doc["kind"] == "charter_text":
                doc["content"] += charter_extra
    return manifest


def _scope(manifest: dict) -> tuple[str, str]:
    import hashlib

    charter = next(d["content"] for d in manifest["documents"] if d["kind"] == "charter_text")
    return manifest["organization"]["ogrn"], hashlib.sha256(charter.encode("utf-8")).hexdigest()


def _draft(client: TestClient, manifest: dict, who: str = "op", **over) -> dict:
    ogrn, sha = _scope(manifest)
    body = {
        "ogrn": ogrn,
        "charter_sha256": sha,
        "outcome": "no_restriction",
        "basis": BASIS,
        "operations": ["PAYMENT_SIGN"],
        "max_amount_rub": 1_000_000,
        "valid_until": conclusion_api.today().replace(year=conclusion_api.today().year + 1).isoformat(),
    }
    body.update(over)
    return client.post("/v1/conclusions", json=body, headers=_h(who))


def _approved(client: TestClient, manifest: dict) -> dict:
    created = _draft(client, manifest)
    assert created.status_code == 200, created.text
    approved = client.post(
        f"/v1/conclusions/{created.json()['id']}/approve", json={"expected_version": 1}, headers=_h("rv")
    )
    assert approved.status_code == 200, approved.text
    return approved.json()


def _check(client: TestClient, manifest: dict, profile: str = "strict", who: str = "op") -> dict:
    response = client.post(f"/v1/cases?profile={profile}", json=manifest, headers=_h(who))
    assert response.status_code == 200, response.text
    return response.json()


def _charter_messages(report: dict) -> list[str]:
    return [f["message"] for f in report["findings"] if f["rule_id"] == "R-CHR-002"]


def test_draft_approve_apply_and_journal(store, tmp_path):
    client = _client(store, tmp_path)
    manifest = _manifest()
    created = _draft(client, manifest)
    assert created.status_code == 200, created.text
    draft = created.json()
    assert (draft["status"], draft["version"], draft["author"], draft["origin"]) == ("draft", 1, "op", "manual")
    approved = client.post(
        f"/v1/conclusions/{draft['id']}/approve", json={"expected_version": 1, "reason": "сверено"}, headers=_h("rv")
    ).json()
    assert (approved["status"], approved["approver"], approved["version"]) == ("approved", "rv", 2)
    assert approved["content_sha256"] == draft["content_sha256"]
    report = _check(client, manifest)
    section = report["charter_conclusions"]
    assert [row["id"] for row in section["applied"]] == [draft["id"]]
    assert any(f"№ {draft['id']}" in m and "Утвердил rv" in m for m in _charter_messages(report))
    records = journal.read_journal(tmp_path / "j.jsonl")
    actions = [r["action"] for r in records]
    assert actions == ["conclusion_create", "conclusion_approve", "conclusion_apply", "verify"]
    apply, verify = records[2], records[3]
    assert apply["case_id"] == verify["case_id"] == report["case_id"]
    assert apply["details"]["conclusion"] == draft["id"] and apply["details"]["attempt"] == verify["details"]["attempt"]
    assert apply["details"]["conclusion_sha256"] == draft["content_sha256"]
    assert verify["details"]["conclusions"] == [draft["id"]]
    assert BASIS not in (tmp_path / "j.jsonl").read_text(encoding="utf-8")
    _check(client, manifest)
    listing = client.get(f"/v1/conclusions?ogrn={manifest['organization']['ogrn']}", headers=_h("op")).json()
    assert [(i["id"], i["applications"]["applied"]) for i in listing["items"]] == [(draft["id"], 2)]
    one = client.get(f"/v1/conclusions/{draft['id']}", headers=_h("aud")).json()
    assert one["applications"] == {"applied": 2, "cleared": 2} and one["status_label"] == "утверждено"


def test_maker_checker_over_http(store, tmp_path):
    client = _client(store, tmp_path)
    manifest = _manifest()
    own = _draft(client, manifest, who="rv").json()
    selfish = client.post(f"/v1/conclusions/{own['id']}/approve", json={"expected_version": 1}, headers=_h("rv"))
    assert selfish.status_code == 403 and "автор не утверждает" in selfish.json()["detail"]
    for who in ("cmp", "op", "aud", "adm", "dbo"):
        refused = client.post(f"/v1/conclusions/{own['id']}/approve", json={"expected_version": 1}, headers=_h(who))
        assert refused.status_code == 403, who
    stale = client.post(f"/v1/conclusions/{own['id']}/approve", json={"expected_version": 0}, headers=_h("rv2"))
    assert stale.status_code == 409
    assert (
        client.post(f"/v1/conclusions/{own['id']}/approve", json={"expected_version": 1}, headers=_h("rv2")).json()[
            "approver"
        ]
        == "rv2"
    )
    again = client.post(f"/v1/conclusions/{own['id']}/approve", json={"expected_version": 2}, headers=_h("rv2"))
    assert again.status_code == 409
    for who in ("adm", "aud", "dbo"):
        assert _draft(client, manifest, who=who).status_code == 403, who


def test_revoked_and_expired_conclusions_are_not_applied(store, tmp_path):
    client = _client(store, tmp_path)
    manifest = _manifest()
    approved = _approved(client, manifest)
    path = f"/v1/conclusions/{approved['id']}/revoke"
    assert client.post(path, json={"expected_version": 2}, headers=_h("op")).status_code == 403
    assert client.post(path, json={"expected_version": 2}, headers=_h("rv2")).status_code == 422
    revoked = client.post(path, json={"expected_version": 2, "reason": "решение собрания"}, headers=_h("rv2"))
    assert revoked.json()["status"] == "revoked"
    report = _check(client, manifest)
    assert report["charter_conclusions"]["applied"] == []
    assert any("не применено: заключение отозвано" in m for m in _charter_messages(report))
    second = _approved(client, manifest)
    body, version = store.conclusion_get(second["id"])
    store.conclusion_put(second["id"], {**body, "valid_until": "2026-10-01"}, version)
    report = _check(client, manifest)
    assert any("срок заключения истёк 01.10.2026" in m for m in _charter_messages(report))
    listed = client.get(f"/v1/conclusions/{second['id']}", headers=_h("op")).json()
    assert listed["status"] == "expired" and listed["status_label"] == "срок истёк"


def test_new_charter_version_puts_the_old_conclusion_on_recheck_and_tells_the_lawyer(store, tmp_path):
    client = _client(store, tmp_path)
    old = _manifest()
    approved = _approved(client, old)
    new = _manifest(charter_extra=" Устав в новой редакции, утверждённой 01.10.2026.")
    report = _check(client, new)
    assert report["verdict"] != "confirmed" and report["charter_conclusions"]["applied"] == []
    (previous,) = report["charter_conclusions"]["previous"]
    assert (previous["id"], previous["outcome"], previous["approver"]) == (approved["id"], "no_restriction", "rv")
    after = client.get(f"/v1/conclusions/{approved['id']}", headers=_h("op")).json()
    assert after["status"] == "recheck" and after["recheck_case"] == report["case_id"]
    assert after["status_label"] == "на перепроверке"
    record = next(r for r in journal.read_journal(tmp_path / "j.jsonl") if r["action"] == "conclusion_recheck")
    assert record["case_id"] == approved["id"] and record["details"]["case"] == report["case_id"]
    review = client.get(f"/v1/cases/{report['case_id']}/review", headers=_h("rv")).json()
    assert [p["id"] for p in review["previous_conclusions"]] == [approved["id"]]
    queue = client.get("/v1/review/queue", headers=_h("rv")).json()["items"]
    assert next(i for i in queue if i["case_id"] == report["case_id"])["previous_conclusions"][0]["approved_at"]
    # Старая редакция после перепроверки тоже не закрывается старым заключением.
    back = _check(client, old)
    assert any("на перепроверке" in m for m in _charter_messages(back))
    # Новый проект по кейсу ссылается на прежнее заключение, а список показывает, кто пришёл на смену.
    draft = client.post(f"/v1/cases/{report['case_id']}/conclusion-draft", json={}, headers=_h("rv")).json()
    assert draft["supersedes"] == [approved["id"]] and draft["previous"][0]["id"] == approved["id"]
    old_view = client.get(f"/v1/conclusions/{approved['id']}", headers=_h("op")).json()
    assert old_view["superseded_by"] == draft["id"]


def test_demo_profile_does_not_read_the_registry(store, tmp_path):
    client = _client(store, tmp_path)
    manifest = _manifest()
    _approved(client, manifest)
    report = _check(client, manifest, profile="demo")
    assert "charter_conclusions" not in report and report["verdict"] == "needs_review"
    assert "conclusion_apply" not in [r["action"] for r in journal.read_journal(tmp_path / "j.jsonl")]


def test_access_rules_ids_and_strict_json(store, tmp_path):
    client = _client(store, tmp_path)
    manifest = _manifest()
    created = _draft(client, manifest).json()
    ogrn = manifest["organization"]["ogrn"]
    for who, code in (("op", 200), ("rv", 200), ("aud", 200), ("adm", 200), ("dbo", 403)):
        assert client.get(f"/v1/conclusions?ogrn={ogrn}", headers=_h(who)).status_code == code, who
        assert client.get(f"/v1/conclusions/{created['id']}", headers=_h(who)).status_code == code, who
    assert client.get(f"/v1/conclusions?ogrn={ogrn}").status_code == 401
    assert client.get("/v1/conclusions?ogrn=123", headers=_h("op")).status_code == 422
    assert client.get("/v1/conclusions/cnc-000000000000", headers=_h("op")).status_code == 404
    assert client.get("/v1/conclusions/../../etc", headers=_h("op")).status_code == 404
    assert client.post(
        "/v1/conclusions/cnc-zz/approve", json={"expected_version": 1}, headers=_h("rv")
    ).status_code == (404)
    raw = '{"expected_version": 1, "reason": "a", "reason": "b"}'
    dup = client.post(
        f"/v1/conclusions/{created['id']}/approve",
        content=raw,
        headers={**_h("rv"), "Content-Type": "application/json"},
    )
    assert dup.status_code == 422
    assert _draft(client, manifest, extra=1).status_code == 422
    assert _draft(client, manifest, ogrn="1257700846062").status_code == 422
    assert _draft(client, manifest, outcome="restriction").status_code == 422
    assert _draft(client, manifest, operations=["WIRE_ALL"]).status_code == 422
    assert _draft(client, manifest, valid_until="2020-01-01").status_code == 422
    assert [r["action"] for r in journal.read_journal(tmp_path / "j.jsonl")] == ["conclusion_create"]


def test_restriction_from_the_registry_is_enforced_in_a_strict_check(store, tmp_path):
    client = _client(store, tmp_path)
    manifest = _manifest()
    restriction = {"kind": "approval_threshold", "value_rub": 300_000, "inclusive": False}
    created = _draft(client, manifest, outcome="restriction", restrictions=[restriction], max_amount_rub=None).json()
    client.post(f"/v1/conclusions/{created['id']}/approve", json={"expected_version": 1}, headers=_h("rv"))
    report = _check(client, manifest)
    limits = [f["message"] for f in report["findings"] if f["rule_id"] == "R-CHR-001"]
    assert limits == [f"Сумма операции 480 000 ₽ выше порога устава 300 000 ₽ (заключение юриста № {created['id']})"]
    one = client.get(f"/v1/conclusions/{created['id']}", headers=_h("op")).json()
    assert one["applications"] == {"applied": 1, "cleared": 0}


def test_confirm_by_the_author_only_and_empty_case_draft(store, tmp_path):
    client = _client(store, tmp_path)
    manifest = _manifest()
    case_id = _check(client, manifest, profile="demo")["case_id"]
    for who in ("op", "cmp"):
        refused = client.post(f"/v1/cases/{case_id}/conclusion-draft", json={}, headers=_h(who))
        assert refused.status_code in {403, 404}, who
    draft = client.post(f"/v1/cases/{case_id}/conclusion-draft", json={}, headers=_h("rv")).json()
    assert (draft["origin"], draft["confirmed"], draft["outcome"], draft["source_case"]) == (
        "case",
        False,
        None,
        case_id,
    )
    assert "проект пуст" in draft["model_note"] and draft["outcome_label"] == "вывод не записан"
    path = f"/v1/conclusions/{draft['id']}"
    assert client.post(f"{path}/approve", json={"expected_version": 1}, headers=_h("rv2")).status_code == 409
    assert client.post(f"{path}/confirm", json={"expected_version": 1}, headers=_h("rv2")).status_code == 403
    assert client.post(f"{path}/confirm", json={"expected_version": 1}, headers=_h("rv")).status_code == 422
    filled = client.post(
        f"{path}/confirm",
        json={"expected_version": 1, "outcome": "no_restriction", "basis": BASIS, "max_amount_rub": 500_000},
        headers=_h("rv"),
    ).json()
    assert filled["confirmed"] is True and filled["max_amount_rub"] == 500_000 and filled["version"] == 2
    unlimited = client.post(f"{path}/confirm", json={"expected_version": 2, "max_amount_rub": None}, headers=_h("rv"))
    assert unlimited.json()["max_amount_rub"] is None
    approved = client.post(f"{path}/approve", json={"expected_version": 3}, headers=_h("rv2")).json()
    assert approved["status"] == "approved"
    assert any(f"№ {draft['id']}" in m for m in _charter_messages(_check(client, manifest)))
    actions = [r["action"] for r in journal.read_journal(tmp_path / "j.jsonl") if r["case_id"] == draft["id"]]
    assert actions == ["conclusion_create", "conclusion_confirm", "conclusion_confirm", "conclusion_approve"]


def test_case_draft_needs_a_charter_review_and_the_same_documents(tmp_path):
    client = _client(MemoryStore(), tmp_path)
    other = synth.build("director_changed", 2).inline_manifest()
    other["operation"]["code"] = "STATEMENT_VIEW"
    not_charter = _check(client, other, profile="demo")
    assert not_charter["verdict"] == "needs_review"
    refused = client.post(f"/v1/cases/{not_charter['case_id']}/conclusion-draft", json={}, headers=_h("rv"))
    assert refused.status_code == 409 and "не из-за устава" in refused.json()["detail"]
    manifest = _manifest()
    case_id = _check(client, manifest, profile="demo")["case_id"]
    wrong = client.post(f"/v1/cases/{case_id}/conclusion-draft", json={"manifest": _manifest(seed=5)}, headers=_h("rv"))
    assert wrong.status_code == 422 and "не этого кейса" in wrong.json()["detail"]
    broken = client.post(f"/v1/cases/{case_id}/conclusion-draft", json={"manifest": {"x": 1}}, headers=_h("rv"))
    assert broken.status_code == 422
    assert client.post("/v1/cases/case-0000/conclusion-draft", json={}, headers=_h("rv")).status_code == 404


@pytest.fixture
def fake_model(monkeypatch):
    """Подставная модель: запрос строит настоящий ``case_charter_messages``, ответ — заранее записанный."""
    seen: list[str] = []

    def complete(self, messages):
        seen.append(messages.messages[-1]["content"])
        sentence = "Сделки на сумму свыше 300 000 рублей совершаются генеральным директором только после одобрения"
        return json.dumps(
            {
                "facts": [
                    {"kind": "approval_threshold", "value": 300000, "quote": sentence, "confidence": 0.9},
                    {"kind": "joint_signature", "value": None, "quote": "Подписи ставят трое", "confidence": 0.9},
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setenv("MANDAT_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("MANDAT_LLM_ALLOWED_HOSTS", "127.0.0.1")
    monkeypatch.setattr(llm.OpenAICompatClient, "complete", complete)
    return seen


def test_model_drafted_conclusion_goes_through_the_gate_and_both_lawyers(tmp_path, fake_model):
    client = _client(MemoryStore(), tmp_path)
    manifest = _manifest("charter_limit")
    case_id = _check(client, manifest, profile="demo")["case_id"]
    draft = client.post(f"/v1/cases/{case_id}/conclusion-draft", json={"manifest": manifest}, headers=_h("rv")).json()
    assert draft["origin"] == "advisory" and draft["confirmed"] is False
    assert draft["dropped"] == ["цитата не найдена в документе"]
    (proposal,) = draft["proposals"]
    assert (proposal["kind"], proposal["value_rub"], proposal["method"]) == (
        "approval_threshold",
        300000,
        "llm_advisory",
    )
    assert draft["restrictions"][0]["quote"].startswith("Сделки на сумму свыше 300 000 рублей")
    assert draft["outcome"] == "restriction" and "шлюз цитаты пропустил: 1" in draft["model_note"]
    sent = fake_model[-1]
    people = [manifest["requester"]["full_name"].split()[0]]
    assert all(name not in sent for name in people)
    record = next(r for r in journal.read_journal(tmp_path / "j.jsonl") if r["action"] == "conclusion_create")
    assert record["details"]["origin"] == "advisory" and record["details"]["proposals"] == 1
    path = f"/v1/conclusions/{draft['id']}"
    edit = {"expected_version": 1, "restrictions": [{**draft["restrictions"][0], "inclusive": False}]}
    assert client.post(f"{path}/confirm", json=edit, headers=_h("rv")).json()["confirmed"] is True
    assert client.post(f"{path}/approve", json={"expected_version": 2}, headers=_h("rv")).status_code == 403
    assert (
        client.post(f"{path}/approve", json={"expected_version": 2}, headers=_h("rv2")).json()["status"] == "approved"
    )
    report = _check(client, manifest)
    assert [f["message"] for f in report["findings"] if f["rule_id"] == "R-CHR-001"] == [
        f"Сумма операции 480 000 ₽ выше порога устава 300 000 ₽ (заключение юриста № {draft['id']})"
    ]


def test_model_failure_leaves_an_empty_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("MANDAT_LLM_ALLOWED_HOSTS", "127.0.0.1")
    client = _client(MemoryStore(), tmp_path)
    manifest = _manifest()
    case_id = _check(client, manifest, profile="demo")["case_id"]
    draft = client.post(f"/v1/cases/{case_id}/conclusion-draft", json={"manifest": manifest}, headers=_h("rv")).json()
    assert draft["origin"] == "case" and draft["proposals"] == [] and "модель не ответила" in draft["model_note"]


def test_conclusions_survive_a_restart_of_the_api(tmp_path):
    db = tmp_path / "api.db"
    first = TestClient(
        create_app(users=_users(), rate_per_min=0, store=SQLiteStore(db), journal_path=tmp_path / "j.jsonl")
    )
    manifest = _manifest()
    approved = _approved(first, manifest)
    second = TestClient(
        create_app(users=_users(), rate_per_min=0, store=SQLiteStore(db), journal_path=tmp_path / "j.jsonl")
    )
    assert second.get(f"/v1/conclusions/{approved['id']}", headers=_h("op")).json()["status"] == "approved"
    assert [r["id"] for r in _check(second, manifest)["charter_conclusions"]["applied"]] == [approved["id"]]


def test_sealed_store_from_the_environment_keeps_the_basis_unreadable(tmp_path, monkeypatch):
    db = tmp_path / "api.db"
    monkeypatch.setenv("MANDAT_API_STORE", f"sqlite:///{db}")
    monkeypatch.setenv("MANDAT_STORE_KEY", random_key("conclusions-store"))
    client = TestClient(create_app(users=_users(), rate_per_min=0, journal_path=tmp_path / "j.jsonl"))
    manifest = _manifest()
    approved = _approved(client, manifest)
    assert approved["basis"] == BASIS
    rows = sqlite3.connect(db).execute("SELECT body FROM conclusions").fetchall()
    assert rows and all("директор" not in row[0] and '"sealed"' in row[0] for row in rows)
    assert [r["id"] for r in _check(client, manifest)["charter_conclusions"]["applied"]] == [approved["id"]]


def test_journal_and_store_failures_and_limits(tmp_path, monkeypatch):
    store = MemoryStore()
    client = _client(store, tmp_path)
    manifest = _manifest()
    monkeypatch.setattr(conclusion_api, "MAX_PER_OGRN", 1)
    assert _draft(client, manifest).status_code == 200
    full = _draft(client, manifest)
    assert full.status_code == 429 and "реестре организации" in full.json()["detail"]
    monkeypatch.setattr(conclusion_api, "MAX_PER_OGRN", 200)
    path = tmp_path / "j.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "{не json\n", encoding="utf-8")
    broken = _draft(client, manifest)
    assert broken.status_code == 503 and "журнал" in broken.json()["detail"]
    assert len(store.conclusion_list(manifest["organization"]["ogrn"])) == 1
    bare = TestClient(create_app(users=_users(), rate_per_min=0, store=store))
    monkeypatch.setattr(app_module, "protected", lambda: True)
    assert _draft(bare, manifest).status_code == 503


def test_store_outage_is_503_for_the_registry_and_the_strict_check(tmp_path, monkeypatch):
    store = MemoryStore()
    client = _client(store, tmp_path)
    manifest = _manifest()

    def down(*args, **kwargs):
        raise OSError("store down")

    monkeypatch.setattr(store, "conclusion_list", down)
    assert client.get(f"/v1/conclusions?ogrn={manifest['organization']['ogrn']}", headers=_h("op")).status_code == 503
    assert client.post("/v1/cases?profile=strict", json=manifest, headers=_h("op")).status_code == 503
    assert client.post("/v1/cases?profile=demo", json=manifest, headers=_h("op")).status_code == 200


def test_unreadable_record_is_skipped_and_logged(tmp_path, caplog):
    store = MemoryStore()
    client = _client(store, tmp_path)
    manifest = _manifest()
    ogrn, _ = _scope(manifest)
    store.conclusion_put("cnc-deadbeefcafe", {"id": "cnc-deadbeefcafe", "ogrn": ogrn, "status": "approved"}, 0)
    with caplog.at_level("WARNING", logger="mandat.api"):
        report = _check(client, manifest)
    assert "charter_conclusions" not in report and "conclusion_unreadable id=cnc-deadbeefcafe" in caplog.text
