"""CLI, HTTP API, загрузка кейсов, демо-кейсы и контроль формулировок."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mandat.cli import main
from mandat.eval.synth import build
from mandat.intake.case_loader import CaseError, load_case_dir


def test_examples_match_generator(root: Path, tmp_path: Path):
    from mandat.examples import write_examples

    write_examples(tmp_path / "cases")
    for generated in sorted((tmp_path / "cases").rglob("*")) + sorted((tmp_path / "events").rglob("*")):
        if generated.is_file():
            rel = generated.relative_to(tmp_path)
            committed = root / "examples" / rel
            assert committed.is_file(), f"нет {rel}: запустите `mandat examples`"
            assert committed.read_bytes() == generated.read_bytes(), f"{rel} устарел: запустите `mandat examples`"


def test_cli_demo_matches_expected_verdicts(root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(root)
    assert main(["demo", "--out", str(tmp_path / "demo")]) == 0
    assert (tmp_path / "demo" / "01_ok.html").is_file()
    assert main(["journal-verify", str(tmp_path / "demo" / "journal.jsonl")]) == 0
    key = (tmp_path / "demo" / "public_key.txt").read_text().strip()
    assert main(["attest-verify", str(tmp_path / "demo" / "attestations" / "01_ok.json"), "--public-key", key]) == 0


def test_cli_verify_exit_codes(root: Path, tmp_path: Path):
    cases = root / "examples" / "cases"
    assert main(["verify", str(cases / "01_ok"), "--exit-code", "--json", str(tmp_path / "r.json")]) == 0
    assert main(["verify", str(cases / "07_revoked"), "--exit-code"]) == 12
    assert main(["verify", str(cases / "08_registry_unavailable"), "--exit-code"]) == 11
    assert main(["verify", str(tmp_path)]) == 2
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["verdict"] == "confirmed"


def test_case_loader_blocks_path_traversal(tmp_path: Path):
    (tmp_path / "case.json").write_text(
        json.dumps(
            {
                "case_id": "x",
                "operation": {"code": "PAYMENT_SIGN", "date": "2026-10-20"},
                "documents": [{"id": "d", "kind": "charter_text", "path": "../secret.txt"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path.parent / "secret.txt").write_text("секрет", encoding="utf-8")
    case = load_case_dir(tmp_path)
    assert case.intake_errors and "выходит за каталог" in case.intake_errors[0].error
    (tmp_path / "case.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CaseError):
        load_case_dir(tmp_path)


def test_api_closed_without_token_and_works_with_token():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from mandat.api.app import create_app

    closed = TestClient(create_app(token=""))
    assert closed.get("/health").json()["auth_configured"] is False
    assert closed.post("/v1/cases", json={}).status_code == 503

    client = TestClient(create_app(token="secret"))
    manifest = build("ok", seed=4).inline_manifest()
    assert client.post("/v1/cases", json=manifest).status_code == 401
    headers = {"Authorization": "Bearer secret"}
    response = client.post("/v1/cases", json=manifest, headers=headers)
    assert response.status_code == 200 and response.json()["verdict"] == "confirmed"
    html = client.get(f"/v1/cases/{manifest['case_id']}/report.html", headers=headers)
    assert html.status_code == 200 and "default-src 'none'" in html.headers["content-security-policy"]
    assert client.post("/v1/cases", json={"case_id": "bad"}, headers=headers).status_code == 422
    assert (
        client.post(
            "/v1/events",
            json={"events": [{"kind": "X", "org_inn": "1", "at": "2026-01-01T00:00:00"}]},
            headers=headers,
        ).status_code
        == 422
    )


def test_api_caps_body_and_rejects_odd_token():
    pytest.importorskip("fastapi")
    import asyncio

    from mandat.api.app import create_app

    app = create_app(token="secret")

    async def call(headers: list[tuple[bytes, bytes]], body: bytes) -> int:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/cases",
            "raw_path": b"/v1/cases",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 80),
        }
        sent = {"status": 0}

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                sent["status"] = message["status"]

        await app(scope, receive, send)
        return sent["status"]

    auth = [(b"authorization", b"Bearer secret"), (b"content-type", b"application/json")]
    assert asyncio.run(call(auth, b'{"pad":"' + b"A" * (9 * 1024 * 1024) + b'"}')) == 413
    assert asyncio.run(call([(b"authorization", b"Bearer \xff")], b"{}")) == 401


def test_claims_lint_passes(root: Path):
    result = subprocess.run([sys.executable, str(root / "scripts" / "lint_claims.py")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_rules_doc_is_up_to_date(root: Path):
    sys.path.insert(0, str(root / "scripts"))
    from gen_rules_doc import render_rules_doc

    assert (root / "docs" / "RULES.md").read_text(encoding="utf-8") == render_rules_doc(), (
        "docs/RULES.md устарел: python scripts/gen_rules_doc.py"
    )
