"""SOTA-M1: снимок классификатора ФНС (kpmp.nalog.gov.ru), даты по Москве, повторы кодов, возраст снимка."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from conftest import rule_effects

from mandat.data import load_json
from mandat.eval import synth
from mandat.intake.case_loader import load_case_inline
from mandat.rules.cedar_export import export_policies
from mandat.rules.classifier import (
    DEFAULT_MAX_AGE_DAYS,
    MAX_AGE_ENV,
    ClassifierEntry,
    from_payload,
    import_kpmp,
    max_age_days,
)
from mandat.services.verify import verify_case

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "kpmp" / "GetAllRecords-2026-10-15-sample.json"
# Вырезка из ответа GetAllRecords от 15.10.2026 (полный ответ: 2837 записей, sha256 d44b6077…bc11d).
FIXTURE_SHA256 = "cbe888a8c7471b8eafa773cc0aa1ec8292f72f894404e0ac9fcedfa31681a33d"


def _raw() -> bytes:
    return FIXTURE.read_bytes()


def _feed() -> dict:
    return json.loads(_raw())


def _import(feed: dict) -> dict:
    return import_kpmp(json.dumps(feed, ensure_ascii=False).encode(), source="test")


def test_fixture_is_the_committed_cut():
    assert hashlib.sha256(_raw()).hexdigest() == FIXTURE_SHA256


def test_import_keeps_utc_and_snapshot_date():
    payload = import_kpmp(_raw(), source="fixture")
    assert payload["snapshot_at"] == "2026-10-15T19:04:33.609015Z"
    entry = {e["code"]: e for e in payload["entries"]}["DIT_PP0004"]
    assert entry["started"] == "2023-08-29T21:00:00Z"
    classifier = from_payload(payload)
    assert classifier.snapshot_day() == date(2026, 10, 15)
    assert classifier.summary()["snapshot_at"].startswith("2026-10-15T19:04:33")


def test_utc_21_00_is_next_day_in_moscow():
    # 29.08.2023 21:00 UTC — это 30.08.2023 00:00 по Москве: 29-го код ещё не действует, 30-го уже действует.
    entry = from_payload(import_kpmp(_raw(), source="fixture")).get("DIT_PP0004")
    assert entry is not None and entry.started == date(2023, 8, 30)
    assert not entry.active_on(date(2023, 8, 29))
    assert entry.active_on(date(2023, 8, 30))


def test_revoked_boundary_in_moscow():
    feed = _feed()
    row = next(r for r in feed["classRecords"] if r["code"] == "DIT_PP0004")
    row["revoked"] = "2026-03-31T20:59:59Z"  # 31.03.2026 23:59:59 по Москве
    entry = from_payload(_import(feed)).get("DIT_PP0004")
    assert entry is not None and entry.revoked == date(2026, 3, 31)
    assert entry.active_on(date(2026, 3, 30)) and not entry.active_on(date(2026, 3, 31))
    row["revoked"] = "2026-03-31T21:00:00Z"  # уже 01.04.2026 по Москве
    entry = from_payload(_import(feed)).get("DIT_PP0004")
    assert entry is not None and entry.active_on(date(2026, 3, 31)) and not entry.active_on(date(2026, 4, 1))


def test_identical_duplicates_collapse_and_are_logged(caplog):
    with caplog.at_level("INFO", logger="mandat.classifier"):
        payload = import_kpmp(_raw(), source="fixture")
    codes = [e["code"] for e in payload["entries"]]
    assert codes.count("DIT_DF1240") == 1
    assert payload["duplicates"]["collapsed"] == 1
    assert "classifier_duplicate_collapsed code=DIT_DF1240 rows=3" in caplog.text
    assert not from_payload(payload).get("DIT_DF1240").ambiguous


def test_duplicates_differing_only_by_group_keep_all_groups():
    feed = _feed()
    row = copy.deepcopy(next(r for r in feed["classRecords"] if r["code"] == "BRDOCS_GNRL_BRDOCSGENERAL"))
    row["groupId"] = "00000000-0000-0000-0000-000000000001"
    feed["classRecords"].append(row)
    payload = _import(feed)
    entry = from_payload(payload).get("BRDOCS_GNRL_BRDOCSGENERAL")
    assert entry is not None and not entry.ambiguous and len(entry.groups) == 2
    assert "00000000-0000-0000-0000-000000000001" in entry.groups
    assert payload["duplicates"]["grouped"] == 1


def test_substantive_duplicates_make_code_ambiguous(caplog):
    # В настоящей выгрузке у FK9800_GIISEB_EB10019 две записи с разным названием.
    with caplog.at_level("WARNING", logger="mandat.classifier"):
        payload = import_kpmp(_raw(), source="fixture")
    assert payload["duplicates"]["ambiguous"] == ["FK9800_GIISEB_EB10019"]
    entry = from_payload(payload).get("FK9800_GIISEB_EB10019")
    assert entry is not None and entry.ambiguous and entry.name is None and entry.started is None
    assert "classifier_code_ambiguous code=FK9800_GIISEB_EB10019" in caplog.text


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda f: f.update(date="0001-01-01T00:00:00"), "пустая"),
        (lambda f: f.pop("date"), "нет даты"),
        (lambda f: f.update(classRecords=[]), "нет записей"),
        (lambda f: f["classRecords"][0].update(started="2023-08-30"), "без времени"),
    ],
)
def test_bad_feed_is_rejected(change, message):
    feed = _feed()
    change(feed)
    with pytest.raises(ValueError, match=message):
        _import(feed)


def test_import_command_reads_file_only(tmp_path):
    out = tmp_path / "snapshot.json"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "import_classifier.py"), str(FIXTURE), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["classifier_id"] == "fns-kpmp-authority-classifier"
    assert payload["source"].endswith(f"sha256={FIXTURE_SHA256}")


# --- R-COV-002 по снимку ---


def _classifier(*, snapshot_at: str | None = None, ambiguous: str | None = None):
    payload = copy.deepcopy(load_json("authority_classifier.json"))
    if snapshot_at:
        payload["snapshot_at"] = snapshot_at
    for entry in payload["entries"]:
        if entry["code"] == ambiguous:
            entry["ambiguous"] = True
    return from_payload(payload)


def _ok_case():
    return load_case_inline(synth.build("ok", 11).inline_manifest())


def _codes(case) -> list[str]:
    return [a.code for poa in case.poas for a in poa.authorities if a.kind == "machine" and a.code]


def test_ambiguous_code_always_needs_review():
    case = _ok_case()
    report = verify_case(case, profile="demo", classifier=_classifier(ambiguous=_codes(case)[0]))
    assert rule_effects(report, "R-COV-002") == {"needs_review"}
    assert report.verdict.value != "confirmed"


def test_code_without_start_date_is_not_in_force():
    """Нет даты начала — не «действует», как у записи руководителя без начала полномочий."""
    case = _ok_case()
    payload = copy.deepcopy(load_json("authority_classifier.json"))
    for entry in payload["entries"]:
        if entry["code"] in _codes(case):
            entry["started"] = None
    report = verify_case(case, profile="demo", classifier=from_payload(payload))
    assert rule_effects(report, "R-COV-002") == {"needs_review"}
    assert report.verdict.value != "confirmed"
    assert any("нет даты начала" in f.message for f in report.findings if f.rule_id == "R-COV-002")
    assert ClassifierEntry("X", None, None, None, None).active_on(date(2026, 10, 16)) is False
    policy = export_policies(classifier=from_payload(payload), on=case.operation.on_date)
    assert all(f'"{code}"' not in policy for code in _codes(case))


def test_unknown_and_outdated_codes_need_review():
    case = _ok_case()
    payload = copy.deepcopy(load_json("authority_classifier.json"))
    payload["entries"] = [e for e in payload["entries"] if e["code"] not in _codes(case)]
    report = verify_case(case, profile="demo", classifier=from_payload(payload))
    assert rule_effects(report, "R-COV-002") == {"needs_review"}
    old = copy.deepcopy(load_json("authority_classifier.json"))
    for entry in old["entries"]:
        if entry["code"] in _codes(case):
            entry["revoked"] = "2000-01-01T00:00:00Z"
    report = verify_case(case, profile="demo", classifier=from_payload(old))
    assert rule_effects(report, "R-COV-002") == {"needs_review"}


def test_snapshot_age_threshold(monkeypatch):
    case = _ok_case()
    on = case.operation.on_date
    fresh = f"{on.isoformat()}T09:00:00Z"
    assert rule_effects(verify_case(case, profile="demo", classifier=_classifier(snapshot_at=fresh)), "R-COV-002") == {
        "supports"
    }
    stale_day = date.fromordinal(on.toordinal() - DEFAULT_MAX_AGE_DAYS - 1)
    stale = _classifier(snapshot_at=f"{stale_day.isoformat()}T09:00:00Z")
    assert stale.stale_for(on) == DEFAULT_MAX_AGE_DAYS + 1
    report = verify_case(case, profile="demo", classifier=stale)
    assert "needs_review" in rule_effects(report, "R-COV-002")
    monkeypatch.setenv(MAX_AGE_ENV, "30")
    assert rule_effects(verify_case(case, profile="demo", classifier=stale), "R-COV-002") == {"supports"}


@pytest.mark.parametrize("bad", ["-1", "x", "366", "1.5"])
def test_max_age_config_is_validated(monkeypatch, bad):
    monkeypatch.setenv(MAX_AGE_ENV, bad)
    with pytest.raises(ValueError, match=MAX_AGE_ENV):
        max_age_days()


def test_default_max_age_is_strict(monkeypatch):
    monkeypatch.delenv(MAX_AGE_ENV, raising=False)
    assert max_age_days() == DEFAULT_MAX_AGE_DAYS <= 3


def test_synthetic_snapshot_has_no_date():
    assert from_payload(load_json("authority_classifier.json")).stale_for(date(2099, 1, 1)) is None
