"""Заключение юриста из реестра в правилах устава: строгий профиль применяет только подходящее заключение.

Подходящее — утверждено вторым юристом, не отозвано, не на перепроверке, не истекло, сделано по той же редакции
устава (sha256 документа в кейсе) для того же ОГРН, и его область покрывает операцию. Тогда R-CHR-002 называет
заключение, а его ограничения проверяет R-CHR-001. Любое другое заключение не применяется, причина — в отчёте,
а кейс идёт тем же путём, что и без реестра. Профиль demo реестр не читает и работает как раньше.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandat.audit.signing import Keyring, LocalSigner
from mandat.domain.conclusion import Conclusion
from mandat.domain.model import PowerOfAttorney
from mandat.eval import synth
from mandat.intake.case_loader import load_case_inline
from mandat.ports import FixtureBankruptcy, FixtureRegistry, Ports, SignatureResult, SignatureStatus
from mandat.rules.approvals import Approvals, sign_record
from mandat.rules.pack import default_catalog, default_pack
from mandat.services.verify import verify_case

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
TRUSTED = frozenset({"registry", "egrul", "bankruptcy", "signature"})
BASIS = "П. 9.2 устава: директор действует без доверенности, ограничений на платежи устав не ставит"


class _Verified:
    """Стенд СКЗИ: подпись каждой МЧД подтверждена — так строгий профиль может дойти до «подтверждено»."""

    name = "test-skzi"
    is_stub = False

    def verify(self, poa: PowerOfAttorney) -> SignatureResult:
        return SignatureResult(SignatureStatus.VERIFIED, "подпись проверена (тест)")


@pytest.fixture(scope="module")
def approvals() -> Approvals:
    """Все правила пакета утверждены записями юриста: конфигурация пилота, в которой работает строгий профиль."""
    pack, signer = default_pack(), LocalSigner(Ed25519PrivateKey.generate())
    records = [
        sign_record(
            pack.rules[rule_id],
            pack.pack_id,
            signer,
            approver_role="юрист банка",
            norm_edition="редакция теста",
            norm_checked_on=date(2026, 10, 1),
            valid_until=date(2027, 9, 30),
            signed_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
            catalog_sha256=default_catalog().sha256,
        )
        for rule_id in pack.rules
    ]
    keyring = Keyring.parse({"keys": [{"kid": signer.key_id, "status": "active"}]})
    return Approvals(tuple(records), keyring, "0" * 64, "тест")


def _case(defect: str = "ok", *, amount: int | None = 480_000, code: str = "PAYMENT_SIGN", charter: str | None = None):
    manifest = synth.build(defect, 1).inline_manifest()
    manifest["operation"]["code"] = code
    if amount is None:
        manifest["operation"].pop("amount_rub", None)
    else:
        manifest["operation"]["amount_rub"] = amount
    day = manifest["operation"]["date"]
    for number, entry in list(manifest["registry"].items()):
        status = entry if isinstance(entry, str) else entry["status"]
        manifest["registry"][number] = {"status": status, "observed_at": f"{day}T09:00:00+03:00"}
    for doc in manifest["documents"]:
        if doc["kind"] == "charter_text" and charter is not None:
            doc["content"] = charter
    case = load_case_inline(manifest)
    return dataclasses.replace(case, bankruptcy={case.organization.inn: "none", case.requester.inn: "none"})


def _conclusion(case, **over) -> Conclusion:
    body = {
        "id": "cnc-0000000000a1",
        "ogrn": case.organization.ogrn,
        "charter_sha256": case.charter.sha256,
        "status": "approved",
        "outcome": "no_restriction",
        "restrictions": [],
        "basis": BASIS,
        "operations": ["PAYMENT_SIGN"],
        "max_amount_rub": 1_000_000,
        "valid_until": "2027-09-30",
        "author": "ivanova",
        "created_at": "2026-10-05T09:00:00+00:00",
        "confirmed": True,
        "approver": "petrova",
        "approved_at": "2026-10-06T09:00:00+00:00",
    }
    body.update(over)
    return Conclusion.from_record(body, 2)


def _strict(case, approvals, conclusions=(), now=NOW):
    ports = Ports(FixtureRegistry(case.registry), _Verified(), FixtureBankruptcy(case.bankruptcy))
    return verify_case(
        case,
        profile="strict",
        ports=ports,
        trusted_sources=TRUSTED,
        now=now,
        conclusions=conclusions,
        approvals=approvals,
    )


def _charter(report) -> list[tuple[str, str, str]]:
    return [(f.rule_id, f.effect.value, f.message) for f in report.findings if f.rule_id.startswith("R-CHR")]


def test_without_a_conclusion_the_charter_sends_every_payment_to_a_human(approvals):
    report = _strict(_case(), approvals)
    assert report.verdict.value == "needs_review"
    assert _charter(report) == [
        ("R-CHR-002", "needs_review", "Шаблоны не классифицировали устав: автоматического подтверждения нет")
    ]
    assert "charter_conclusions" not in report.as_dict()


def test_matching_conclusion_is_applied_and_named_in_plain_russian(approvals):
    case = _case()
    report = _strict(case, approvals, [_conclusion(case)])
    assert report.verdict.value == "confirmed", report.warnings
    ((rule, effect, message),) = _charter(report)
    assert (rule, effect) == ("R-CHR-002", "info")
    for part in ("№ cnc-0000000000a1", "Утвердил petrova 06.10.2026", BASIS, "Действует до 30.09.2027"):
        assert part in message
    finding = next(f for f in report.findings if f.rule_id == "R-CHR-002")
    assert finding.refs[0].doc_id == "conclusion:cnc-0000000000a1"
    section = report.as_dict()["charter_conclusions"]
    assert [row["id"] for row in section["applied"]] == ["cnc-0000000000a1"]
    assert section["applied"][0]["content_sha256"] == finding.refs[0].doc_sha256


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"charter_sha256": "ab" * 32}, "другой редакции устава"),
        ({"valid_until": "2026-10-19"}, "срок заключения истёк 19.10.2026"),
        ({"status": "revoked", "revoked_at": "2026-10-10T09:00:00+00:00"}, "отозвано 10.10.2026"),
        ({"status": "recheck", "recheck_at": "2026-10-11T09:00:00+00:00"}, "на перепроверке с 11.10.2026"),
        ({"status": "draft", "approver": None, "approved_at": None}, "черновик"),
        ({"status": "draft", "approver": None, "approved_at": None, "confirmed": False}, "не подтвердил"),
        ({"approved_at": "2026-10-21T09:00:00+00:00"}, "позже момента проверки"),
        ({"max_amount_rub": 300_000}, "выше предела заключения 300 000 ₽"),
        ({"operations": ["USER_ADMIN"]}, "операция PAYMENT_SIGN вне области заключения (USER_ADMIN)"),
    ],
)
def test_conclusion_that_does_not_fit_is_not_applied_and_the_reason_is_stated(approvals, over, reason):
    case = _case()
    report = _strict(case, approvals, [_conclusion(case, **over)])
    assert report.verdict.value == "needs_review"
    findings = _charter(report)
    assert (
        "R-CHR-002",
        "needs_review",
        "Шаблоны не классифицировали устав: автоматического подтверждения нет",
    ) in findings
    (note,) = [m for r, e, m in findings if m.startswith("Заключение юриста № cnc-0000000000a1 не применено:")]
    assert reason in note and len(findings) == 2 and {e for _, e, _ in findings} == {"needs_review"}
    section = report.as_dict()["charter_conclusions"]
    assert section["applied"] == [] and section["not_applied"][0]["id"] == "cnc-0000000000a1"


def test_conclusion_valid_until_the_operation_day_still_applies_on_that_day(approvals):
    case = _case()
    assert _strict(case, approvals, [_conclusion(case, valid_until="2026-10-20")]).verdict.value == "confirmed"


def test_changed_charter_lists_the_previous_conclusion_for_re_review(approvals):
    old = _case()
    new = _case(charter=old.charter.text + " Устав в новой редакции от 01.10.2026.")
    report = _strict(new, approvals, [_conclusion(old)])
    assert report.verdict.value == "needs_review"
    previous = report.as_dict()["charter_conclusions"]["previous"]
    assert [(p["id"], p["outcome"], p["charter_sha256"]) for p in previous] == [
        ("cnc-0000000000a1", "no_restriction", old.charter.sha256)
    ]


def test_amount_cap_and_missing_amount(approvals):
    case = _case(amount=900_000)
    assert _strict(case, approvals, [_conclusion(case)]).verdict.value == "confirmed"
    unknown = _case(amount=None)
    report = _strict(unknown, approvals, [_conclusion(unknown)])
    assert any("сумма операции не указана" in m for _, _, m in _charter(report))


def test_threshold_from_the_conclusion_is_enforced_not_cleared(approvals):
    limit = {"kind": "approval_threshold", "value_rub": 300_000, "inclusive": False, "quote": "свыше 300 000 рублей"}
    over = {"outcome": "restriction", "restrictions": [limit], "max_amount_rub": None}
    above = _case(amount=480_000)
    report = _strict(above, approvals, [_conclusion(above, **over)])
    assert report.verdict.value == "needs_review"
    assert (
        "R-CHR-001",
        "needs_review",
        "Сумма операции 480 000 ₽ выше порога устава 300 000 ₽ (заключение юриста № cnc-0000000000a1)",
    ) in _charter(report)
    assert any(e == "info" and "Ограничение проверяет R-CHR-001" in m for _, e, m in _charter(report))
    below = _case(amount=250_000)
    assert _strict(below, approvals, [_conclusion(below, **over)]).verdict.value == "confirmed"
    edge = _case(amount=300_000)
    inclusive = {**over, "restrictions": [{**limit, "inclusive": True}]}
    assert _strict(edge, approvals, [_conclusion(edge, **inclusive)]).verdict.value == "needs_review"
    assert _strict(edge, approvals, [_conclusion(edge, **over)]).verdict.value == "confirmed"


@pytest.mark.parametrize(
    ("restriction", "message"),
    [
        ({"kind": "joint_signature"}, "Устав требует совместных действий или подписей"),
        ({"kind": "large_deal_approval"}, "В уставе есть положение о крупных сделках без числового порога"),
    ],
)
def test_joint_signature_and_large_deal_from_the_conclusion_go_to_a_human(approvals, restriction, message):
    case = _case(amount=100_000)
    report = _strict(case, approvals, [_conclusion(case, outcome="restriction", restrictions=[restriction])])
    assert report.verdict.value == "needs_review"
    assert any(r == "R-CHR-001" and m.startswith(message) and "№ cnc-0000000000a1" in m for r, _, m in _charter(report))


def test_conclusions_that_disagree_keep_the_restriction_and_go_to_a_human(approvals):
    case = _case(amount=100_000)
    clear = _conclusion(case)
    joint = _conclusion(case, id="cnc-0000000000b2", outcome="restriction", restrictions=[{"kind": "joint_signature"}])
    report = _strict(case, approvals, [clear, joint])
    assert report.verdict.value == "needs_review"
    findings = _charter(report)
    assert any(r == "R-CHR-001" and "№ cnc-0000000000b2" in m for r, _, m in findings)
    assert any(e == "needs_review" and "cnc-0000000000a1 не применено: противоречит" in m for _, e, m in findings)
    section = report.as_dict()["charter_conclusions"]
    assert [row["id"] for row in section["applied"]] == ["cnc-0000000000b2"]


def test_conclusion_replaces_the_heuristic_reading_of_the_same_charter_version(approvals):
    """Шаблоны нашли порог, юрист прочитал эту редакцию: вывод юриста, а не шаблон, решает, и это сказано."""
    case = _case("charter_limit", amount=480_000)
    report = _strict(case, approvals, [_conclusion(case)])
    assert report.verdict.value == "confirmed"
    ((_, _, message),) = _charter(report)
    assert "Шаблоны нашли в тексте: approval_threshold; вывод юриста по ним — в заключении" in message


def test_conclusion_of_another_organization_is_ignored(approvals):
    case = _case()
    foreign = _conclusion(case, ogrn="1027700132195")
    report = _strict(case, approvals, [foreign])
    assert "charter_conclusions" not in report.as_dict()
    assert report.reproducibility_hash == _strict(case, approvals).reproducibility_hash


def test_case_without_a_charter_cannot_use_a_conclusion(approvals):
    case = _case()
    conclusion = _conclusion(case)
    bare = dataclasses.replace(case, charter=None)
    report = _strict(bare, approvals, [conclusion])
    assert report.verdict.value != "confirmed"
    assert any("устава в кейсе нет" in m for _, _, m in _charter(report))


def test_manifest_mark_next_to_an_applied_conclusion_is_only_a_note(approvals):
    case = dataclasses.replace(_case(), charter_disposition="no_applicable_restriction")
    report = _strict(case, approvals, [_conclusion(case)])
    assert report.verdict.value == "confirmed"
    assert any("Отметка манифеста об уставе не учитывается" in m for _, _, m in _charter(report))


def test_note_for_an_operation_without_charter_requirement_is_only_info(approvals):
    case = _case(code="STATEMENT_VIEW")
    report = _strict(case, approvals, [_conclusion(case)])
    assert report.verdict.value == "confirmed"
    assert [e for r, e, _ in _charter(report)] == ["info", "info"]


def test_demo_profile_keeps_its_behaviour_and_ignores_the_registry():
    manifest = synth.build("ok_charter_cleared", 3).inline_manifest()
    case = load_case_inline(manifest)
    plain = verify_case(case, profile="demo")
    with_registry = verify_case(case, profile="demo", conclusions=[_conclusion(case, status="revoked")])
    assert plain.as_dict()["reproducibility_hash"] == with_registry.as_dict()["reproducibility_hash"]
    assert plain.verdict.value == with_registry.verdict.value == "confirmed"
    bare = load_case_inline(synth.build("ok", 3).inline_manifest())
    assert verify_case(bare, profile="demo", conclusions=[_conclusion(bare)]).verdict.value == "needs_review"


@pytest.mark.parametrize(
    "broken",
    [
        {"id": "CNC-1"},
        {"ogrn": "1234567890123"},
        {"status": "approved", "approver": None},
        {"status": "approved", "basis": " "},
        {"status": "approved", "outcome": "restriction", "restrictions": []},
        {"status": "approved", "outcome": "no_restriction", "restrictions": [{"kind": "joint_signature"}]},
        {"status": "approved", "restrictions": [{"kind": "approval_threshold"}], "outcome": "restriction"},
        {"status": "maybe"},
        {"valid_until": "31.12.2026"},
        {"created_at": "2026-10-05T09:00:00"},
        {"author": ""},
        {"max_amount_rub": True},
        {"operations": "PAYMENT_SIGN"},
    ],
)
def test_damaged_record_is_not_a_conclusion(broken):
    case = _case()
    with pytest.raises(ValueError):
        _conclusion(case, **broken)


def test_empty_draft_from_a_case_still_parses_and_is_not_applied(approvals):
    case = _case()
    draft = _conclusion(
        case,
        status="draft",
        outcome=None,
        restrictions=[{"kind": "approval_threshold", "value_rub": None}],
        basis="",
        approver=None,
        approved_at=None,
        confirmed=False,
    )
    assert draft.outcome is None and draft.restrictions == ()
    report = _strict(case, approvals, [draft])
    assert any("автор ещё не подтвердил проект" in m for _, _, m in _charter(report))
