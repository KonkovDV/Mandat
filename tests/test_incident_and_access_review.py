"""Инцидент с ПДн и ревизия доступа — пункты чек-листа воркшопа 20.11.2026 «Завершение» и «Инцидент».

Инцидент: оба срока считаются от выявления — 24 часа на уведомление и 72 часа на результаты расследования, а не
24 + 72 (152-ФЗ, ч. 3.1 ст. 21). Факты — из журнала, только если он цел, без ПДн клиентов.
Ревизия доступа: учётная запись без срока, с долгим сроком или без владельца отзыва видна до того, как
«временный доступ остался после работы» (кейс 3 воркшопа).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from mandat.api.principals import parse_users, review_access
from mandat.audit import journal
from mandat.cli import main
from mandat.services.incident import MOSCOW, deadlines, incident_report

DETECTED = datetime(2026, 11, 20, 9, 0, tzinfo=MOSCOW)


def _journal(tmp_path):
    path = tmp_path / "audit.jsonl"
    rows = [
        ("verify", "alice", "operator", "case-1", DETECTED - timedelta(days=2)),
        ("read_case", "bob", "auditor", "case-1", DETECTED - timedelta(days=1)),
        ("read_report", "bob", "auditor", "case-2", DETECTED - timedelta(hours=5)),
        ("read_case", "bob", "auditor", "case-3", DETECTED - timedelta(hours=1)),
        ("event", "monitor", "security", "case-1", DETECTED - timedelta(hours=1)),
        ("erase_case", "admin", "admin", "case-1", DETECTED + timedelta(hours=2)),
    ]
    for action, actor, role, case_id, moment in rows:
        journal.append(path, action=action, actor=actor, role=role, case_id=case_id, payload_hash="h", now=moment)
    return path


def test_both_deadlines_run_from_detection_not_one_after_another():
    notice, investigation = deadlines(DETECTED)
    assert notice - DETECTED == timedelta(hours=24)
    assert investigation - DETECTED == timedelta(hours=72), "72 часа от выявления, а не 24 + 72"
    with pytest.raises(ValueError):
        deadlines(DETECTED.replace(tzinfo=None))


def test_report_lists_who_touched_which_cases_before_detection(tmp_path):
    report = incident_report(_journal(tmp_path), detected_at=DETECTED)
    assert report.journal_ok
    assert report.notice_due.startswith("2026-11-21T09:00") and report.investigation_due.startswith("2026-11-23T09:00")
    actors = {a.actor: a for a in report.actors}
    assert set(actors) == {"alice", "bob"}, "событие мониторинга не про ПДн, удаление — после выявления"
    assert actors["bob"].actions == {"read_case": 2, "read_report": 1} and actors["bob"].cases == 3
    assert report.actors[0].actor == "bob", "сначала тот, кто затронул больше кейсов"
    assert report.events == 4 and report.cases == 3


def test_window_and_actor_filters(tmp_path):
    path = _journal(tmp_path)
    narrow = incident_report(path, detected_at=DETECTED, since=DETECTED - timedelta(hours=6), actor="bob")
    assert [a.actor for a in narrow.actors] == ["bob"] and narrow.actors[0].actions == {
        "read_case": 1,
        "read_report": 1,
    }
    later = incident_report(path, detected_at=DETECTED, until=DETECTED + timedelta(hours=3))
    assert "admin" in {a.actor for a in later.actors}


def test_broken_journal_gives_deadlines_but_no_facts(tmp_path):
    path = _journal(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[1])
    record["actor"] = "mallory"
    path.write_text("\n".join([lines[0], json.dumps(record, ensure_ascii=False), *lines[2:]]) + "\n", encoding="utf-8")
    report = incident_report(path, detected_at=DETECTED)
    assert not report.journal_ok and report.actors == [] and report.investigation_due
    assert any("целостности" in w for w in report.warnings)


def test_cli_incident_report_reads_naive_time_as_moscow(tmp_path, capsys):
    path = _journal(tmp_path)
    assert main(["incident-report", "--journal", str(path), "--detected-at", "2026-11-20T09:00:00"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["detected_at"] == "2026-11-20T09:00+03:00"
    assert any("московское" in w for w in result["warnings"])
    assert "не 24 + 72" in result["rule"]


# --- ревизия доступа -----------------------------------------------------------------------------------------

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _user(name: str, role: str = "operator", **extra) -> dict:
    return {"name": name, "role": role, "token_sha256": hashlib.sha256(name.encode()).hexdigest(), **extra}


def _at(days: float) -> str:
    return (NOW + timedelta(days=days)).isoformat()


def test_access_review_names_what_to_remove_or_assign():
    entries = parse_users(
        [
            _user("ok", expires_at=_at(30), owner="Иванова, ИБ"),
            _user("forever", owner="ИБ"),
            _user("orphan", expires_at=_at(30)),
            _user("soon", expires_at=_at(5), owner="ИБ"),
            _user("long", expires_at=_at(200), owner="ИБ"),
            _user("gone", expires_at=_at(-1), owner="ИБ"),
            _user("revoked", revoked=True),
            _user("root", "admin", expires_at=_at(30), owner="ИБ"),
        ],
        require_expiry=False,
        now=NOW,
    )
    result = review_access(entries, now=NOW)
    rows = {row["name"]: row for row in result["rows"]}
    assert rows["ok"]["flags"] == [] and rows["ok"]["owner"] == "Иванова, ИБ"
    assert "нет срока действия" in rows["forever"]["flags"]
    assert "не назначен владелец отзыва" in rows["orphan"]["flags"]
    assert any("кончается через" in f for f in rows["soon"]["flags"])
    assert any("пересмотреть" in f for f in rows["long"]["flags"])
    assert rows["gone"]["status"] == "expired" and rows["gone"]["flags"] == []
    assert rows["revoked"]["status"] == "revoked"
    assert "права администратора" in rows["root"]["flags"]
    assert result["active"] == 6 and result["needs_action"] == 2
    assert "token_sha256" not in json.dumps(result)


@pytest.mark.parametrize("owner", ["", "   ", 7, "x" * 201])
def test_owner_must_be_a_short_text(owner):
    with pytest.raises(ValueError, match="owner"):
        parse_users([_user("a", owner=owner)], require_expiry=False)


def test_cli_access_review_strict_and_api_user_owner(tmp_path, capsys):
    users = tmp_path / "users.json"
    users.write_text(json.dumps([_user("orphan", expires_at=_at(30))]), encoding="utf-8")
    assert main(["access-review", "--users", str(users)]) == 0
    capsys.readouterr()
    assert main(["access-review", "--users", str(users), "--strict"]) == 1
    capsys.readouterr()
    assert main(["api-user", "--name", "contractor", "--role", "operator", "--owner", "Петров, ИБ"]) == 0
    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert json.loads(line)["owner"] == "Петров, ИБ"
