"""Повторные клиенты: доля платежей к человеку без реестра заключений юриста и с ним считается настоящими правилами.

Строгий профиль в конфигурации пилота. Без реестра устав отправляет к юристу каждый платёж; с реестром — первую
операцию каждой редакции, платежи выше предела заключения и платежи, которые устав ограничивает. Ни один платёж,
который устав ограничивает, реестр не подтверждает.
"""

from __future__ import annotations

import json

import pytest

from mandat.audit import journal
from mandat.cli import main
from mandat.eval import repeat


@pytest.fixture(scope="module")
def metrics():
    return repeat.run().as_dict()


def test_registry_takes_repeat_payments_off_the_lawyer(metrics):
    assert (metrics["clients"], metrics["operations"], metrics["charter_versions"]) == (10, 100, 11)
    assert metrics["to_human_without_registry"] == 100 and metrics["share_without_registry"] == 1.0
    assert metrics["to_human_with_registry"] == sum(metrics["reasons_with_registry"].values())
    assert metrics["to_human_with_registry"] < metrics["to_human_without_registry"]
    assert metrics["conclusions_approved"] == metrics["charter_versions"]
    assert metrics["reasons_with_registry"]["первая операция редакции устава"] == 10
    assert metrics["reasons_with_registry"]["новая редакция устава"] == 1 and metrics["rechecks"] == 1
    assert metrics["applications"] > metrics["cleared_by_conclusions"] > 0


def test_registry_never_confirms_a_payment_the_charter_restricts(metrics):
    assert metrics["restricted_operations"] > 0
    assert metrics["false_confirmed_with_registry"] == metrics["false_confirmed_without_registry"] == 0
    assert metrics["reasons_with_registry"]["ограничение устава из заключения"] > 0


def test_scenario_journal_holds_every_step(tmp_path):
    path = tmp_path / "repeat" / "journal.jsonl"
    result = repeat.run(journal_path=path).as_dict()
    assert result["journal"]["intact"] is True
    actions = [r["action"] for r in journal.read_journal(path)]
    assert actions.count("verify") == 100
    assert actions.count("conclusion_create") == actions.count("conclusion_approve") == 11
    assert actions.count("conclusion_apply") == result["applications"]
    assert actions.count("conclusion_recheck") == 1
    again = repeat.run(journal_path=path).as_dict()
    assert again["journal"]["records"] == result["journal"]["records"]


def test_eval_and_showcase_report_the_numbers(tmp_path, capsys):
    assert main(["eval", "--per-defect", "1", "--out", str(tmp_path / "eval.json")]) == 0
    printed = json.loads((tmp_path / "eval.json").read_text(encoding="utf-8"))["repeat_clients"]
    assert printed["to_human_without_registry"] == 100 and printed["false_confirmed_with_registry"] == 0
    capsys.readouterr()
    assert main(["showcase", "--out", str(tmp_path / "show")]) == 0
    out = capsys.readouterr().out
    assert "захват компании останавливается до платежа" in out and "Без реестра заключений к юристу уходят 100" in out
    saved = json.loads((tmp_path / "show" / "repeat_clients.json").read_text(encoding="utf-8"))
    assert saved["to_human_with_registry"] == printed["to_human_with_registry"]
    takeover = json.loads((tmp_path / "show" / "showcase.json").read_text(encoding="utf-8"))
    assert "repeat_clients" not in takeover, "файл сценария захвата читают сайт и колода: он прежний"


def test_render_uses_russian_plurals():
    text = repeat.render_text({**repeat.run().as_dict(), "journal": {"records": 201, "intact": True}})
    assert "201 запись" in text and "цепочка цела" in text
    assert repeat._records(3) == "3 записи" and repeat._records(11) == "11 записей"
