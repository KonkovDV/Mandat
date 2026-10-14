"""R2, правовая линза: реорганизация и прекращение юрлица (ГК РФ, ст. 188 п. 1 подп. 4; ст. 64.2)."""

from __future__ import annotations

import json

import pytest

from mandat.eval import synth
from mandat.intake.case_loader import load_case_inline
from mandat.intake.egrul import parse_egrul
from mandat.services.verify import verify_case


def _with_status(status: str):
    manifest = synth.build("ok", 7).inline_manifest()
    for doc in manifest["documents"]:
        if doc["kind"] == "egrul_json":
            content = doc["content"] if isinstance(doc["content"], dict) else json.loads(doc["content"])
            doc["content"] = {**content, "status": status}
    return verify_case(load_case_inline(manifest))


def _effects(report, rule):
    return {f.effect.value for f in report.findings if f.rule_id == rule}


def test_terminated_org_refutes():
    report = _with_status("terminated")
    assert "refutes" in _effects(report, "R-EIO-004")
    assert report.verdict.value == "refuted"


def test_reorganizing_org_goes_to_review():
    report = _with_status("reorganizing")
    assert "needs_review" in _effects(report, "R-EIO-004")
    assert report.verdict.value != "confirmed"
    text = " ".join(f.message for f in report.findings if f.rule_id == "R-EIO-004")
    assert "188" in text and "выделении" in text


@pytest.mark.parametrize("status", ["reorganizing", "terminated"])
def test_new_statuses_are_parsed(status):
    assert parse_egrul(json.dumps({"status": status, "executives": []}), doc_id="e").status == status


def test_unknown_status_still_rejected():
    from mandat.intake.egrul import EgrulError

    with pytest.raises(EgrulError):
        parse_egrul(json.dumps({"status": "merged-ish"}), doc_id="e")
