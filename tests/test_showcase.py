"""mandat showcase: захват компании останавливается до платежа — вся цепочка «Мандата» одной командой."""

from __future__ import annotations

import json

import pytest
from conftest import ROOT

from mandat.audit import journal
from mandat.cli import main
from mandat.services import admission, showcase

EXPECTED = ["ПОДТВЕРЖДЕНО", "ВЫПУЩЕНО", "ДОПУЩЕНО", "ОТКАЗ", "ОТКАЗ", "ПРИЗНАКИ ЗАХВАТА", "ОТКАЗ"]


def test_takeover_is_stopped_before_the_payment(tmp_path):
    show = showcase.run(ROOT, tmp_path)
    assert [s.outcome for s in show.steps] == EXPECTED
    assert "nonce уже использован" in show.steps[3].detail
    assert "больше проверенной" in show.steps[4].detail
    assert "possible_takeover" in show.steps[5].detail
    assert "приостановлено" in show.steps[6].detail
    assert show.journal_ok and show.proof_ok
    records = journal.read_journal(tmp_path / "journal.jsonl")
    assert [r["action"] for r in records].count("authorize_operation") == 4
    assert [r["action"] for r in records].count("status_change") == 2
    saved = json.loads((tmp_path / "showcase.json").read_text(encoding="utf-8"))
    assert [s["outcome"] for s in saved["steps"]] == EXPECTED
    assert "стенд" in saved["boundary"]


def test_rerun_in_the_same_directory_starts_clean(tmp_path):
    first = showcase.run(ROOT, tmp_path)
    second = showcase.run(ROOT, tmp_path)
    assert [s.outcome for s in second.steps] == EXPECTED
    assert second.journal_records == first.journal_records


def test_scenario_admission_is_the_function_behind_the_http_route(tmp_path, monkeypatch):
    """Сценарий не обходит допуск: если decide отказывает во всём, первого допуска нет."""
    monkeypatch.setattr(admission, "decide", lambda *args, **kwargs: ["отказ теста"])
    show = showcase.run(ROOT, tmp_path)
    assert show.steps[2].outcome == "ОТКАЗ"


def test_cli_prints_the_story_and_exits_zero(tmp_path, capsys):
    assert main(["showcase", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "захват компании останавливается до платежа" in out
    assert "доказано" in out and "цела" in out


@pytest.mark.parametrize("mode", ["pilot", "prod"])
def test_cli_refuses_to_run_outside_the_stand(tmp_path, monkeypatch, capsys, mode):
    monkeypatch.setenv("MANDAT_ENV", mode)
    assert main(["showcase", "--out", str(tmp_path)]) == 2
    assert "стенд" in capsys.readouterr().err
