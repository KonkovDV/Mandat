"""Жизнь заключения юриста: черновик, подтверждение автором, утверждение вторым юристом, отзыв, перепроверка.

«Четыре глаза»: автор своё заключение не утверждает, утверждает только юрист. Каждый шаг — запись журнала с
отпечатком вывода, свободный текст — отпечатком; одна версия заключения — одно действие.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from conftest import random_key

from mandat.audit import journal
from mandat.domain.conclusion import Conclusion, content_sha256
from mandat.review import conclusions as lc

NOW = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)
OPS = {"PAYMENT_SIGN", "STATEMENT_VIEW", "USER_ADMIN", "REQUISITES_CHANGE"}
SHA = "9f" * 32
OGRN = "1257700846061"
BASIS = "Клиент Иванов И. И., п. 9.2 устава: ограничений на подписание платежей нет"
OPERATOR = lc.Actor("ivanova", "operator")
LAWYER = lc.Actor("petrova", "reviewer", "lawyer")
SECOND = lc.Actor("smirnov", "reviewer", "lawyer")
COMPLIANCE = lc.Actor("kozlov", "reviewer", "compliance")


def _fields(**over):
    fields = {
        "ogrn": OGRN,
        "charter_sha256": SHA,
        "outcome": "no_restriction",
        "restrictions": [],
        "basis": BASIS,
        "operations": ["PAYMENT_SIGN"],
        "max_amount_rub": 1_000_000,
        "valid_until": "2027-11-27",
    }
    fields.update(over)
    return fields


def _create(path=None, actor=OPERATOR, **over):
    return lc.create(
        _fields(**over), actor=actor, conclusion_id="cnc-00000000000a", now=NOW, operations=OPS, journal_path=path
    )


def _approve(body, version=1, actor=SECOND, path=None, reason="", now=NOW):
    return lc.approve(
        body, version, actor=actor, reason=reason, now=now, operations=OPS, journal_path=path, expected_version=version
    )


def test_maker_checker_happy_path_and_journal(tmp_path):
    path = tmp_path / "j.jsonl"
    draft = _create(path)
    assert draft["status"] == "draft" and draft["confirmed"] is True and draft["origin"] == "manual"
    approved = _approve(draft, path=path, reason="сверено с уставом")
    revoked = lc.revoke(
        approved,
        2,
        actor=LAWYER,
        reason="устав изменён решением участников",
        now=NOW,
        journal_path=path,
        expected_version=2,
    )
    assert (approved["status"], approved["approver"], revoked["status"]) == ("approved", "smirnov", "revoked")
    records = journal.read_journal(path)
    assert [r["action"] for r in records] == ["conclusion_create", "conclusion_approve", "conclusion_revoke"]
    assert all(r["case_id"] == "cnc-00000000000a" for r in records)
    assert [r["details"]["version"] for r in records] == [0, 1, 2]
    assert records[1]["payload_hash"] == content_sha256(approved) == content_sha256(draft)
    assert records[1]["details"]["author"] == "ivanova" and records[1]["details"]["specialization"] == "lawyer"
    text = path.read_text(encoding="utf-8")
    assert "Иванов" not in text and records[0]["reason"] == "sha256:" + hashlib.sha256(BASIS.encode()).hexdigest()
    assert journal.verify_journal(path).ok
    assert [h["action"] for h in revoked["history"]] == ["create", "approve", "revoke"]
    assert Conclusion.from_record(approved, 2).status_on(NOW.date()) == "approved"


def test_author_never_approves_own_conclusion_even_as_a_lawyer():
    draft = _create(actor=LAWYER)
    with pytest.raises(lc.ConclusionForbidden, match="автор не утверждает"):
        _approve(draft, actor=LAWYER)
    assert _approve(draft, actor=SECOND)["approver"] == "smirnov"


@pytest.mark.parametrize("actor", [OPERATOR, COMPLIANCE, lc.Actor("root", "admin"), lc.Actor("dbo", "dbo")])
def test_only_a_lawyer_approves(actor):
    with pytest.raises(lc.ConclusionForbidden, match="юрист"):
        _approve(_create(actor=lc.Actor("author", "operator")), actor=actor)


@pytest.mark.parametrize("actor", [lc.Actor("root", "admin"), lc.Actor("aud", "auditor"), lc.Actor("d", "dbo")])
def test_only_operator_or_reviewer_writes_a_draft(actor):
    with pytest.raises(lc.ConclusionForbidden):
        _create(actor=actor)


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"ogrn": "1257700846062"}, "ОГРН"),
        ({"ogrn": "304500116000157"}, "ОГРН"),
        ({"charter_sha256": "XYZ"}, "charter_sha256"),
        ({"outcome": "maybe"}, "вывод"),
        ({"outcome": "restriction"}, "без единого ограничения"),
        ({"restrictions": [{"kind": "joint_signature"}]}, "не несёт ограничений"),
        ({"outcome": "restriction", "restrictions": [{"kind": "approval_threshold"}]}, "нужна сумма"),
        ({"outcome": "restriction", "restrictions": [{"kind": "joint_signature", "value_rub": 5}]}, "суммы нет"),
        ({"outcome": "restriction", "restrictions": [{"kind": "grant_right"}]}, "вид ограничения"),
        ({"outcome": "restriction", "restrictions": [{"kind": "joint_signature", "extra": 1}]}, "лишние"),
        ({"outcome": "restriction", "restrictions": [{"kind": "joint_signature", "inclusive": "да"}]}, "inclusive"),
        ({"outcome": "restriction", "restrictions": ["joint_signature"]}, "объект"),
        ({"basis": "   "}, "основание"),
        ({"basis": "x" * 4001}, "длиннее"),
        ({"operations": ["WIRE_ALL"]}, "не из каталога"),
        ({"operations": "PAYMENT_SIGN"}, "operations"),
        ({"max_amount_rub": 0}, "max_amount_rub"),
        ({"max_amount_rub": 1.5}, "max_amount_rub"),
        ({"valid_until": "2026-11-27"}, "уже прошёл"),
        ({"valid_until": "2027-12-31"}, "не дальше"),
        ({"valid_until": "2027-02-30"}, "ГГГГ-ММ-ДД"),
        ({"valid_until": 20271127}, "ГГГГ-ММ-ДД"),
    ],
)
def test_invalid_content_is_refused(over, message):
    with pytest.raises(lc.ConclusionInvalid, match=message):
        _create(**over)


def test_approval_needs_a_confirmed_current_draft():
    draft = _create()
    with pytest.raises(lc.ConclusionConflict, match="устарела"):
        lc.approve(draft, 1, actor=SECOND, reason="", now=NOW, operations=OPS, journal_path=None, expected_version=0)
    approved = _approve(draft)
    with pytest.raises(lc.ConclusionConflict, match="только черновик"):
        _approve(approved, version=2)
    with pytest.raises(lc.ConclusionConflict, match="не подтвердил"):
        _approve({**draft, "confirmed": False})
    late = NOW + timedelta(days=400)
    with pytest.raises(lc.ConclusionConflict, match="уже прошёл"):
        _approve(draft, now=late)


def test_revoke_needs_a_lawyer_and_a_reason_and_is_final():
    approved = _approve(_create())
    with pytest.raises(lc.ConclusionForbidden):
        lc.revoke(approved, 2, actor=OPERATOR, reason="x", now=NOW, journal_path=None, expected_version=2)
    with pytest.raises(lc.ConclusionInvalid, match="причина"):
        lc.revoke(approved, 2, actor=LAWYER, reason=" ", now=NOW, journal_path=None, expected_version=2)
    revoked = lc.revoke(approved, 2, actor=LAWYER, reason="ошибка", now=NOW, journal_path=None, expected_version=2)
    with pytest.raises(lc.ConclusionConflict, match="отозвано"):
        lc.revoke(revoked, 3, actor=LAWYER, reason="ещё раз", now=NOW, journal_path=None, expected_version=3)
    draft = _create()
    assert (
        lc.revoke(draft, 1, actor=LAWYER, reason="лишний", now=NOW, journal_path=None, expected_version=1)["status"]
        == "revoked"
    )


def _from_case(path=None, proposals=(), previous=(), actor=LAWYER):
    return lc.draft_from_case(
        ogrn=OGRN,
        charter_sha256=SHA,
        operation="PAYMENT_SIGN",
        source_case="case-0000000000000001",
        proposals=list(proposals),
        origin="advisory" if proposals else "case",
        note="модель предложила фактов: 1, шлюз цитаты пропустил: 1" if proposals else "модель не подключена",
        previous=list(previous),
        actor=actor,
        conclusion_id="cnc-00000000000b",
        now=NOW,
        journal_path=path,
    )


PROPOSAL = {
    "kind": "approval_threshold",
    "value_rub": 300000,
    "quote": "Сделки на сумму свыше 300 000 рублей совершаются только после одобрения общим собранием участников.",
    "confidence": 0.9,
    "locator": "text:84-190",
    "method": "llm_advisory",
}


def test_model_drafted_conclusion_shows_quotes_and_needs_the_author_then_a_second_lawyer(tmp_path):
    path = tmp_path / "j.jsonl"
    draft = _from_case(path, proposals=[PROPOSAL], previous=[{"id": "cnc-00000000000a", "outcome": "no_restriction"}])
    assert (draft["origin"], draft["confirmed"], draft["outcome"]) == ("advisory", False, "restriction")
    assert draft["restrictions"][0]["quote"] == PROPOSAL["quote"] and draft["proposals"] == [PROPOSAL]
    assert draft["basis"].startswith("Проект модели по уставу: «Сделки на сумму свыше 300 000")
    assert draft["supersedes"] == ["cnc-00000000000a"] and draft["operations"] == ["PAYMENT_SIGN"]
    with pytest.raises(lc.ConclusionConflict, match="не подтвердил"):
        _approve(draft, actor=SECOND, path=path)
    with pytest.raises(lc.ConclusionForbidden, match="автор"):
        lc.confirm(draft, 1, fields={}, actor=SECOND, now=NOW, operations=OPS, journal_path=path, expected_version=1)
    edited = {"inclusive": False, "kind": "approval_threshold", "value_rub": 300000, "quote": PROPOSAL["quote"]}
    confirmed = lc.confirm(
        draft,
        1,
        fields={"restrictions": [edited], "basis": "П. 10.4: свыше 300 000 ₽ — одобрение собрания"},
        actor=LAWYER,
        now=NOW,
        operations=OPS,
        journal_path=path,
        expected_version=1,
    )
    assert confirmed["confirmed"] is True and confirmed["restrictions"][0]["inclusive"] is False
    with pytest.raises(lc.ConclusionForbidden, match="автор не утверждает"):
        _approve(confirmed, version=2, actor=LAWYER, path=path)
    approved = _approve(confirmed, version=2, actor=SECOND, path=path)
    assert approved["status"] == "approved"
    actions = [r["action"] for r in journal.read_journal(path)]
    assert actions == ["conclusion_create", "conclusion_confirm", "conclusion_approve"]
    created = journal.read_journal(path)[0]["details"]
    assert created["origin"] == "advisory" and created["proposals"] == 1 and created["supersedes"]


def test_empty_case_draft_is_filled_by_the_author():
    draft = _from_case()
    assert (draft["origin"], draft["outcome"], draft["basis"], draft["restrictions"]) == ("case", None, "", [])
    with pytest.raises(lc.ConclusionInvalid, match="вывод"):
        lc.confirm(draft, 1, fields={}, actor=LAWYER, now=NOW, operations=OPS, journal_path=None, expected_version=1)
    filled = lc.confirm(
        draft,
        1,
        fields={"outcome": "no_restriction", "basis": "Прочитан устав целиком: ограничений нет"},
        actor=LAWYER,
        now=NOW,
        operations=OPS,
        journal_path=None,
        expected_version=1,
    )
    assert filled["confirmed"] and filled["outcome"] == "no_restriction"
    with pytest.raises(lc.ConclusionForbidden, match="юрист"):
        _from_case(actor=OPERATOR)


def test_confirm_only_a_draft_and_only_by_its_author():
    approved = _approve(_create())
    with pytest.raises(lc.ConclusionConflict, match="только черновик"):
        lc.confirm(
            approved, 2, fields={}, actor=OPERATOR, now=NOW, operations=OPS, journal_path=None, expected_version=2
        )
    with pytest.raises(lc.ConclusionInvalid, match="не из каталога"):
        lc.confirm(
            _create(),
            1,
            fields={"operations": ["X"]},
            actor=OPERATOR,
            now=NOW,
            operations=OPS,
            journal_path=None,
            expected_version=1,
        )


def test_recheck_moves_only_an_approved_conclusion(tmp_path):
    path = tmp_path / "j.jsonl"
    approved = _approve(_create(path), path=path)
    moved = lc.recheck(
        approved, 2, actor=OPERATOR, case_id="case-1", charter_sha256="ab" * 32, now=NOW, journal_path=path
    )
    assert moved["status"] == "recheck" and moved["recheck_case"] == "case-1"
    record = journal.read_journal(path)[-1]
    assert record["action"] == "conclusion_recheck" and record["details"]["case"] == "case-1"
    with pytest.raises(lc.ConclusionConflict):
        lc.recheck(moved, 3, actor=OPERATOR, case_id="case-2", charter_sha256="cd" * 32, now=NOW, journal_path=path)
    assert (
        lc.revoke(moved, 3, actor=LAWYER, reason="новая редакция", now=NOW, journal_path=path, expected_version=3)[
            "status"
        ]
        == "revoked"
    )


def test_one_version_one_action_in_the_journal(tmp_path):
    path = tmp_path / "j.jsonl"
    draft = _create(path)
    _approve(draft, path=path, reason="сверено")
    _approve(draft, path=path, reason="сверено")  # повтор того же решения: новой записи нет
    assert [r["action"] for r in journal.read_journal(path)].count("conclusion_approve") == 1
    with pytest.raises(lc.ConclusionConflict, match="уже записана"):
        lc.revoke(draft, 1, actor=LAWYER, reason="другое решение", now=NOW, journal_path=path, expected_version=1)
    with pytest.raises(lc.ConclusionConflict, match="уже записана"):
        _approve(draft, actor=lc.Actor("third", "reviewer", "lawyer"), path=path, reason="сверено")


def test_apply_is_recorded_with_the_conclusion_fingerprint(tmp_path):
    path = tmp_path / "j.jsonl"
    applied = {"id": "cnc-00000000000a", "version": 2, "content_sha256": "c" * 64, "outcome": "no_restriction"}
    lc.record_apply(path, actor=OPERATOR, case_id="case-7", report_hash="d" * 64, applied=applied, attempt="a1")
    (record,) = journal.read_journal(path)
    assert (record["action"], record["case_id"], record["payload_hash"]) == ("conclusion_apply", "case-7", "d" * 64)
    assert record["details"] == {
        "conclusion": "cnc-00000000000a",
        "conclusion_version": 2,
        "conclusion_sha256": "c" * 64,
        "outcome": "no_restriction",
        "via": "api",
        "attempt": "a1",
    }


def test_protected_mode_requires_the_journal_and_a_broken_journal_stops_the_action(tmp_path, monkeypatch):
    path = tmp_path / "j.jsonl"
    draft = _create(path)
    path.write_text(path.read_text(encoding="utf-8") + "{не json\n", encoding="utf-8")
    with pytest.raises(lc.ConclusionError, match="журнал не цел"):
        _approve(draft, path=path)
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    monkeypatch.setenv("MANDAT_JOURNAL_KEY", random_key("conclusions"))
    with pytest.raises(lc.ConclusionInvalid, match="журнала"):
        _create(None)


def test_effective_status_shows_expiry():
    approved = _approve(_create(valid_until="2026-12-01"))
    assert lc.effective_status(approved, datetime(2026, 12, 1, tzinfo=UTC).date()) == "approved"
    assert lc.effective_status(approved, datetime(2026, 12, 2, tzinfo=UTC).date()) == "expired"
    assert Conclusion.from_record(approved, 2).status_on(datetime(2026, 12, 2, tzinfo=UTC).date()) == "expired"
    assert lc.effective_status(_create(), datetime(2028, 1, 1, tzinfo=UTC).date()) == "draft"
    assert lc.new_id().startswith("cnc-") and len(lc.new_id()) == 16
