"""Регрессии аудита 13.10.2026 (вторая глубокая волна): каждая находка — тест, который падал до исправления."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mandat.api.app import create_app
from mandat.api.principals import hash_token, parse_users
from mandat.api.storage import MemoryStore
from mandat.audit import attestation
from mandat.cli import main
from mandat.domain.verdict import Verdict
from mandat.eval.synth import build
from mandat.intake.case_loader import load_case_inline
from mandat.privacy import pii
from mandat.services.verify import verify_case


def _manifest_with_xml_encoding(encoding: str) -> dict:
    manifest = build("ok_charter_cleared", 7).inline_manifest()
    doc = next(d for d in manifest["documents"] if d["kind"] == "emchd_xml")
    doc["content"] = doc["content"].replace('encoding="UTF-8"', f'encoding="{encoding}"')
    return manifest


def test_xml_with_unknown_encoding_is_a_rejected_document():
    report = verify_case(load_case_inline(_manifest_with_xml_encoding("bogus-enc")))
    assert report.verdict is Verdict.INSUFFICIENT_DATA
    assert any("неизвестной кодировке" in f.message for f in report.findings if f.rule_id == "R-FMT-001")


def test_api_does_not_answer_500_on_unknown_xml_encoding():
    app = create_app(token="t", rate_per_min=0, store=MemoryStore())
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/v1/cases", json=_manifest_with_xml_encoding("TF-8"), headers={"Authorization": "Bearer t"})
    assert response.status_code == 200
    assert response.json()["verdict"] == "insufficient_data"


def test_two_users_with_one_token_are_rejected():
    digest = hash_token("shared")
    raw = [
        {"name": "ivanova", "role": "operator", "token_sha256": digest},
        {"name": "petrov", "role": "admin", "token_sha256": digest.upper()},
    ]
    with pytest.raises(ValueError, match="совпадает с токеном"):
        parse_users(raw)


def test_pii_refuses_the_public_dev_key_in_protected_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MANDAT_PII_KEY", raising=False)
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    with pytest.raises(pii.PiiKeyMissing):
        pii.pseudonymize("ИНН 7707083893")
    monkeypatch.setenv("MANDAT_PII_KEY", "k" * 32)
    assert "7707083893" not in pii.pseudonymize("ИНН 7707083893")


def test_pii_dev_key_still_works_on_the_stand(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MANDAT_PII_KEY", raising=False)
    monkeypatch.setenv("MANDAT_ENV", "demo")
    assert "7707083893" not in pii.pseudonymize("ИНН 7707083893")


@pytest.mark.skipif(os.name != "posix", reason="права POSIX")
def test_world_readable_private_key_is_refused(tmp_path: Path):
    key_path = tmp_path / "keys" / "attestation_ed25519.pem"
    attestation.load_or_create_key(key_path)
    hmac_path = tmp_path / "keys" / "representative_hmac.key"
    attestation.load_or_create_pseudonym_key(hmac_path)
    for path, loader in (
        (key_path, attestation.load_or_create_key),
        (hmac_path, attestation.load_or_create_pseudonym_key),
    ):
        loader(path)  # 0600 читается
        path.chmod(0o644)
        with pytest.raises(ValueError, match="chmod 600"):
            loader(path)


def test_attest_verify_prints_the_verdict_and_unchecked_status(root: Path, tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(root)
    demo = tmp_path / "demo"
    assert main(["demo", "--out", str(demo)]) == 0
    key = (demo / "public_key.txt").read_text().strip()
    capsys.readouterr()
    refuted = str(demo / "attestations" / "07_revoked.json")
    assert main(["attest-verify", refuted, "--public-key", key]) == 0
    captured = capsys.readouterr()
    assert "вердикт в удостоверении: refuted" in captured.out
    assert "статус не проверялся" in captured.err
