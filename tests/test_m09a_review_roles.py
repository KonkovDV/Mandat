"""M-09a: роли разбора и правила переходов (docs/REVIEW.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mandat.api.principals import ROLES as API_ROLES
from mandat.api.principals import Principal, hash_token, parse_users
from mandat.audit import journal
from mandat.review.state_machine import (
    ALLOWED_ROLES,
    SPECIALIZATIONS,
    TRANSITIONS,
    ReviewCase,
    ReviewError,
    ReviewState,
)

H = "h" * 64


def _in_review(**kw: object) -> ReviewCase:
    return ReviewCase("c1", H, state=ReviewState.IN_REVIEW, **kw)  # type: ignore[arg-type]


def test_every_action_has_role_rules() -> None:
    assert {action for _, action in TRANSITIONS} == set(ALLOWED_ROLES)


def test_api_roles_are_final_set() -> None:
    assert set(API_ROLES) == {"operator", "reviewer", "auditor", "admin"}


@pytest.mark.parametrize("action", sorted(ALLOWED_ROLES))
@pytest.mark.parametrize("role", ["admin", "auditor"])
def test_admin_and_auditor_never_move_cases(action: str, role: str) -> None:
    for state in ReviewState:
        case = ReviewCase("c1", H, state=state)
        with pytest.raises(ReviewError, match="не двигает кейсы"):
            case.apply(action, actor="root", role=role, reason="основание")
        assert case.state is state
        assert case.history == []


@pytest.mark.parametrize("action", ["accept", "reject", "request_docs"])
def test_operator_cannot_decide(action: str) -> None:
    with pytest.raises(ReviewError, match="не для роли"):
        _in_review().apply(action, actor="op", role="operator", reason="основание")


def test_only_operator_appeals_and_journal_marks_client(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    with pytest.raises(ReviewError, match="не для роли"):
        ReviewCase("c1", H, state=ReviewState.AUTO_DECIDED).apply(
            "appeal", actor="rv", role="reviewer", reason="клиент не согласен"
        )
    case = ReviewCase("c1", H, state=ReviewState.AUTO_DECIDED)
    case.apply("appeal", actor="op", role="operator", reason="клиент не согласен", journal_path=path)
    assert case.history[-1]["on_behalf_of"] == "client"
    details = journal.read_journal(path)[0]["details"]
    assert details["on_behalf_of"] == "client"
    assert details["actor"] == "op"


def test_reviewer_decides() -> None:
    case = _in_review()
    assert case.apply("accept", actor="rv", role="reviewer", reason="основание") is ReviewState.ACCEPTED
    assert case.apply("close", actor="op", role="operator") is ReviewState.CLOSED


@pytest.mark.parametrize("field", ["created_by", "appealed_decision_by"])
def test_conflicted_actor_cannot_decide(field: str) -> None:
    case = _in_review(**{field: "rv"})
    with pytest.raises(ReviewError, match="решать кейс нельзя"):
        case.apply("accept", actor="rv", role="reviewer", reason="основание")
    assert case.apply("accept", actor="rv2", role="reviewer", reason="основание") is ReviewState.ACCEPTED


def test_appeal_filer_cannot_decide_but_opener_can() -> None:
    case = ReviewCase("c1", H, state=ReviewState.AUTO_DECIDED)
    case.apply("appeal", actor="op", role="operator", reason="клиент не согласен")
    case.apply("open", actor="rv", role="reviewer")
    with pytest.raises(ReviewError, match="решать кейс нельзя"):
        case.apply("reject", actor="op", role="reviewer", reason="основание")
    assert case.apply("reject", actor="rv", role="reviewer", reason="основание") is ReviewState.REJECTED


def test_required_specialization_is_checked() -> None:
    case = _in_review(required_specialization="lawyer")
    with pytest.raises(ReviewError, match="специализация 'lawyer'"):
        case.apply("accept", actor="a", role="reviewer", specialization="compliance", reason="основание")
    with pytest.raises(ReviewError, match="специализация 'lawyer'"):
        case.apply("accept", actor="a", role="reviewer", reason="основание")
    assert (
        case.apply("accept", actor="a", role="reviewer", specialization="lawyer", reason="ок") is ReviewState.ACCEPTED
    )


def test_legacy_role_names_are_reviewer_specializations(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    case = _in_review(required_specialization="lawyer")
    case.apply("accept", actor="a", role="lawyer", reason="основание", journal_path=path)
    assert case.history[-1]["role"] == "reviewer"
    assert case.history[-1]["specialization"] == "lawyer"
    assert journal.read_journal(path)[0]["details"]["specialization"] == "lawyer"
    with pytest.raises(ReviewError, match="не совпадают"):
        _in_review().apply("accept", actor="a", role="lawyer", specialization="security", reason="основание")


def test_bad_specialization_rejected() -> None:
    with pytest.raises(ReviewError, match="только у роли reviewer"):
        _in_review().apply("open", actor="op", role="operator", specialization="lawyer")
    with pytest.raises(ReviewError, match="не из"):
        _in_review().apply("accept", actor="a", role="reviewer", specialization="intern", reason="основание")


def _users(**extra: object) -> list[dict[str, object]]:
    return [{"name": "rv", "role": "reviewer", "token_sha256": hash_token("t" * 32), **extra}]


def test_principal_specialization_loaded() -> None:
    (entry,) = parse_users(_users(specialization="lawyer"), require_expiry=False)
    assert entry.principal == Principal("rv", "reviewer", "lawyer")
    (plain,) = parse_users(_users(), require_expiry=False)
    assert plain.principal.specialization is None


def test_principal_specialization_validated() -> None:
    with pytest.raises(ValueError, match="не из"):
        parse_users(_users(specialization="intern"), require_expiry=False)
    raw = json.loads(json.dumps(_users(specialization="lawyer")))
    raw[0]["role"] = "operator"
    with pytest.raises(ValueError, match="только у роли reviewer"):
        parse_users(raw, require_expiry=False)


def test_specializations_match_docs() -> None:
    doc = (Path(__file__).resolve().parents[1] / "docs" / "REVIEW.md").read_text(encoding="utf-8")
    for spec in SPECIALIZATIONS:
        assert f"`{spec}`" in doc
