"""Консоль стенда: включается только флагом вне pilot и prod, защищена как стенд «Свода», каждая вкладка работает
через HTTP API от демо-учётных записей сессии, а сессии не видят чужих кейсов."""

from __future__ import annotations

import http.server
import json
import threading

import pytest
from fastapi.testclient import TestClient

from mandat import cli
from mandat import console as stand
from mandat.api.app import create_app
from mandat.api.storage import MemoryStore
from mandat.console import inproc
from mandat.console.demo import UploadError, demo_manifest, upload_manifest
from mandat.console.issuer import DemoIssuer, IssuerExhausted, bank_today, rebased_today
from mandat.console.sessions import Sessions

SECURITY = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "x-robots-tag": "noindex, nofollow",
    "cache-control": "no-store",
}


@pytest.fixture(autouse=True)
def _demo_env(monkeypatch):
    for name in ("MANDAT_ENV", "MANDAT_STAND", "MANDAT_API_USERS", "MANDAT_API_JOURNAL", "MANDAT_LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def app():
    return create_app(console=True, store=MemoryStore(), rate_per_min=0)


def _client(app) -> TestClient:
    client = TestClient(app)
    assert client.post("/console/api/session", json={}).status_code == 200
    return client


def _session(app, client: TestClient):
    return app.state.console_sessions.get(client.cookies.get("mandat_console"))


def _check(client: TestClient, demo: str = "09_charter_limit") -> dict:
    reply = client.post("/console/api/check", json={"demo": demo})
    assert reply.status_code == 200, reply.text
    return reply.json()


# --- включение -------------------------------------------------------------------------------------------------


def test_console_is_off_by_default():
    client = TestClient(create_app(token="t" * 40, store=MemoryStore()))
    assert client.get("/console/").status_code == 401
    with_token = client.get("/console/", headers={"Authorization": "Bearer " + "t" * 40})
    assert with_token.status_code == 404
    assert client.get("/").status_code == 401


@pytest.mark.parametrize("env", ["pilot", "prod"])
def test_console_is_refused_in_protected_modes(monkeypatch, env, capsys):
    monkeypatch.setenv("MANDAT_ENV", env)
    with pytest.raises(stand.StandRefused):
        create_app(console=True, store=MemoryStore())
    monkeypatch.setenv("MANDAT_STAND", "1")
    with pytest.raises(stand.StandRefused):
        create_app(store=MemoryStore())
    assert cli.main(["serve"]) == 2
    assert "MANDAT_STAND" in capsys.readouterr().err


def test_serve_with_console_needs_one_worker(monkeypatch, capsys):
    monkeypatch.setenv("MANDAT_STAND", "1")
    assert cli.main(["serve", "--workers", "2"]) == 2
    assert "одним воркером" in capsys.readouterr().err
    monkeypatch.setenv("MANDAT_API_USERS", "[{")
    assert cli.main(["serve"]) == 2


# --- защита ----------------------------------------------------------------------------------------------------


def test_pages_have_security_headers_and_strict_csp(app):
    client = TestClient(app)
    page = client.get("/console/")
    assert page.status_code == 200
    assert "noindex" in page.text and "<script>" not in page.text
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp
    for name, value in SECURITY.items():
        assert page.headers[name] == value, name
    assert page.headers["strict-transport-security"].startswith("max-age=")
    image = client.get("/console/static/anna-ok-160.webp")
    assert image.headers["content-type"] == "image/webp" and "max-age" in image.headers["cache-control"]
    assert image.headers["content-security-policy"].startswith("default-src 'none'")
    assert client.get("/console/static/missing.js").status_code == 404
    assert client.get("/robots.txt").text == "User-agent: *\nDisallow: /\n"
    root = client.get("/", follow_redirects=False)
    assert root.status_code == 307 and root.headers["location"] == "/console/"


def test_head_only_on_safe_routes(app):
    client = _client(app)
    assert client.head("/console/").status_code == 200
    assert client.head("/console/static/anna-hello-480.webp").status_code == 200
    for path in ("/console/api/session", "/console/api/check", "/console/api/dbo/authorize", "/console/api/review"):
        refused = client.head(path)
        assert refused.status_code == 405, path
        assert refused.headers["allow"] in {"POST", "GET"}
    token = _session(app, client).token("operator")
    assert client.head("/v1/cases", headers={"Authorization": f"Bearer {token}"}).status_code == 405


def test_post_is_json_only_same_origin_and_bounded(app):
    client = _client(app)
    form = client.post("/console/api/check", content="demo=01_ok", headers={"Content-Type": "text/plain"})
    assert form.status_code == 415
    foreign = client.post("/console/api/check", json={"demo": "01_ok"}, headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403
    assert client.post("/console/api/check", json=["01_ok"]).status_code == 422
    assert client.put("/console/api/check", json={}).status_code == 405
    huge = client.post(
        "/console/api/check", content=b"{" + b" " * (600 * 1024) + b"}", headers={"Content-Type": "application/json"}
    )
    assert huge.status_code == 413


def test_session_principals_have_no_admin_and_cannot_erase(app):
    client = _client(app)
    case_id = _check(client)["case_id"]
    session = _session(app, client)
    roles = {app.state.console_sessions.principal(session.token(r)).role for r in ("operator", "reviewer", "dbo")}
    assert roles == {"operator", "reviewer", "dbo"}
    for role in ("operator", "reviewer", "dbo"):
        erase = client.post(
            f"/v1/cases/{case_id}/erase",
            json={"reason": "purpose_achieved"},
            headers={"Authorization": f"Bearer {session.token(role)}"},
        )
        assert erase.status_code == 403, role
    # Общий токен стенда (роль admin) рядом с консолью не действует.
    legacy = TestClient(create_app(token="t" * 40, console=True, store=MemoryStore()))
    assert legacy.get("/v1/review/queue", headers={"Authorization": "Bearer " + "t" * 40}).status_code == 401
    assert client.post("/console/api/review/" + case_id + "/erase", json={"expected_version": 1}).status_code == 404


def test_sessions_do_not_see_each_others_cases(app):
    first, second = _client(app), _client(app)
    case_id = _check(first)["case_id"]
    assert second.get(f"/console/api/report/{case_id}").status_code == 404
    assert second.post("/console/api/consult", json={"case_id": case_id}).status_code == 404
    assert second.post(f"/console/api/review/{case_id}/open", json={"expected_version": 1}).status_code == 404
    assert second.get("/console/api/review").json()["items"] == []
    token = _session(app, second).token("operator")
    assert second.get(f"/v1/cases/{case_id}", headers={"Authorization": f"Bearer {token}"}).status_code == 404
    assert TestClient(app).post("/console/api/check", json={"demo": "01_ok"}).status_code == 401


def test_heavy_routes_are_rate_limited():
    app = create_app(console=True, store=MemoryStore(), rate_per_min=0)
    client = _client(app)
    statuses = [client.post("/console/api/eval", json={"per_defect": n}).status_code for n in (1, 2, 3, 4, 5)]
    assert statuses[:4] == [200] * 4 and statuses[4] == 429
    assert client.post("/console/api/eval", json={"per_defect": 1}).json()["cached"] is True
    assert client.post("/console/api/eval", json={"per_defect": 7}).status_code == 422


# --- вкладки ---------------------------------------------------------------------------------------------------


def test_check_tab_demo_and_upload_with_evidence_and_report(app):
    client = _client(app)
    assert len(client.get("/console/api/cases").json()["demo"]) >= 14
    result = _check(client)
    assert result["verdict_label"] == "НА РАЗБОР" and result["elapsed_ms"] >= 0
    first = result["evidence"][0]
    assert first["rule_id"] == "R-CHR-001" and "ГК РФ" in first["norm_ref"]
    assert first["sources"][0]["source"] == "Устав" and "300 000" in first["sources"][0]["quote"]
    sources = {s["source"] for row in result["evidence"] for s in row["sources"]}
    assert {"МЧД", "ЕГРЮЛ", "Устав", "Реестр МЧД"} <= sources
    card = client.get(result["report_url"])
    assert card.status_code == 200 and "style-src 'unsafe-inline'" in card.headers["content-security-policy"]
    docs = client.get("/console/api/cases/09_charter_limit").json()
    upload = {
        "poas": docs["poas"],
        "egrul": docs["egrul"],
        "charter": docs["charter"].replace("300 000", "900 000"),
        "operation": docs["operation"],
        "requester": docs["requester"],
        "registry_status": docs["registry_status"],
    }
    lifted = client.post("/console/api/check", json={"upload": upload}).json()
    assert lifted["verdict"] != result["verdict"] or lifted["objection"] != result["objection"]
    revoked = client.post("/console/api/check", json={"upload": {**upload, "registry_status": "revoked"}}).json()
    assert revoked["verdict_label"] == "ОПРОВЕРГНУТО"
    assert client.post("/console/api/check", json={"upload": {**upload, "poas": []}}).status_code == 422
    assert client.post("/console/api/check", json={"demo": "nope"}).status_code == 404
    assert client.get("/console/api/cases/nope").status_code == 404


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"egrul": "{"}, "JSON"),
        ({"egrul": "[]"}, "объектом"),
        ({"operation": {"code": "X", "date": "2026-10-20"}}, "каталога"),
        ({"operation": {"code": "PAYMENT_SIGN", "date": "20.10.2026"}}, "ГГГГ"),
        ({"operation": {"code": "PAYMENT_SIGN", "date": "2026-10-20", "amount_rub": -1}}, "сумма"),
        ({"requester": {"full_name": "А", "inn": "1"}}, "ИНН"),
        ({"requester": {"full_name": "А", "inn": "1234567890", "snils": "1"}}, "СНИЛС"),
        ({"registry_status": "maybe"}, "реестре"),
        ({"charter": 5}, "строкой"),
        ({"requester": None}, "заявитель"),
    ],
)
def test_upload_errors_are_named(change, message):
    docs = demo_manifest("01_ok")["documents"]
    base = {
        "poas": [d["content"] for d in docs if d["kind"] == "emchd_xml"],
        "egrul": json.dumps(next(d["content"] for d in docs if d["kind"] == "egrul_json")),
        "operation": {"code": "PAYMENT_SIGN", "date": "2026-10-20", "amount_rub": 1},
        "requester": {"full_name": "Лебедева Ирина Петровна", "inn": "777588807322"},
    }
    assert upload_manifest(base)["registry"]
    with pytest.raises(UploadError, match=message):
        upload_manifest({**base, **change})


def test_consultant_tab_without_model(app):
    client = _client(app)
    case_id = _check(client)["case_id"]
    reply = client.post("/console/api/consult", json={"case_id": case_id, "use_model": True}).json()
    assert reply["model"]["enabled"] is False and "MANDAT_LLM_BASE_URL" in reply["model"]["note"]
    assert reply["advice"] is None and reply["advice_label"] == "справка, вердикт не меняет"
    assert reply["explanation"][0]["fact_ids"] == ["verdict"]
    assert all(s["fact_ids"] for s in reply["explanation"])
    charter = reply["charter"]
    assert charter["present"] and charter["facts"][0]["kind"] == "approval_threshold"
    assert charter["facts"][0]["value"] == "300000"


class _Model(http.server.BaseHTTPRequestHandler):
    """Подставная модель: факт с цитатой из присланного текста и выдуманный факт без цитаты."""

    sent: list[str] = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        text = body["messages"][-1]["content"]
        _Model.sent.append(text)
        line = next(x for x in text.splitlines() if "руб" in x)
        facts = [
            {"kind": "approval_threshold", "value": 300000, "quote": line, "confidence": 0.9},
            {"kind": "joint_signature", "value": None, "quote": "выдуманная цитата", "confidence": 0.9},
        ]
        data = json.dumps({"choices": [{"message": {"content": json.dumps({"facts": facts})}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def model(monkeypatch):
    server = http.server.HTTPServer(("127.0.0.1", 0), _Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("MANDAT_LLM_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("MANDAT_LLM_MODEL", "stand-model")
    monkeypatch.setenv("MANDAT_LLM_ALLOWED_HOSTS", "127.0.0.1")
    yield server
    server.shutdown()


def test_consultant_tab_with_model_goes_through_the_gate(app, model):
    client = _client(app)
    case_id = _check(client)["case_id"]
    reply = client.post("/console/api/consult", json={"case_id": case_id, "use_model": True}).json()
    assert reply["model"] == {
        "enabled": True,
        "model": "stand-model",
        "host": "127.0.0.1",
        "note": reply["model"]["note"],
    }
    advice = reply["advice"]
    assert advice["label"] == "справка, вердикт не меняет"
    assert advice["proposed"] == 2 and len(advice["accepted"]) == 1
    assert advice["accepted"][0]["method"] == "llm_advisory"
    assert advice["dropped"] == ["цитата не найдена в документе"]
    assert advice["verdict_before"] == advice["verdict_after"] == "НА РАЗБОР" and advice["verdict_unchanged"]
    assert "Лебедева" not in _Model.sent[-1] and "Петров" not in _Model.sent[-1]
    director = client.post("/console/api/check", json={"demo": "02_director_without_poa"}).json()
    assert (
        client.post("/console/api/consult", json={"case_id": director["case_id"], "use_model": True}).status_code == 200
    )


def test_consultant_reports_model_failure_without_changing_anything(app, monkeypatch):
    monkeypatch.setenv("MANDAT_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("MANDAT_LLM_ALLOWED_HOSTS", "127.0.0.1")
    client = _client(app)
    case_id = _check(client)["case_id"]
    advice = client.post("/console/api/consult", json={"case_id": case_id, "use_model": True}).json()["advice"]
    assert advice["error"].startswith("модель не ответила") and advice["sent"]
    monkeypatch.setenv("MANDAT_LLM_BASE_URL", "http://model.example/v1")
    note = client.post("/console/api/consult", json={"case_id": case_id}).json()["model"]
    assert note["enabled"] is False and "не принят" in note["note"]


def test_review_tab_take_decide_return_and_appeal(app):
    client = _client(app)
    first = _check(client)["case_id"]
    second = _check(client, "05_director_changed")["case_id"]
    queue = client.get("/console/api/review").json()
    assert {i["case_id"] for i in queue["items"]} == {first, second}
    item = next(i for i in queue["items"] if i["case_id"] == first)
    assert item["sla_hours"] and item["due_at"] and item["title"]

    def act(case_id, action, version, reason="основание"):
        reply = client.post(
            f"/console/api/review/{case_id}/{action}", json={"expected_version": version, "reason": reason}
        )
        assert reply.status_code == 200, reply.text
        return reply.json()

    taken = act(first, "open", item["version"], "")
    assert taken["state"] == "in_review" and taken["actor"].endswith("-reviewer")
    returned = act(first, "request_docs", taken["version"], "нужно решение собрания")
    again = act(first, "open", returned["version"], "")
    accepted = act(first, "accept", again["version"])
    assert accepted["state"] == "accepted"
    closed = act(first, "close", accepted["version"], "")
    appealed = act(first, "appeal", closed["version"], "клиент приложил решение")
    assert appealed["state"] == "queued" and appealed["actor"].endswith("-operator")
    stale = client.post(f"/console/api/review/{first}/open", json={"expected_version": 0, "reason": ""})
    assert stale.status_code == 409
    no_reason = client.post(f"/console/api/review/{second}/open", json={"expected_version": "1"})
    assert no_reason.status_code == 422


def test_dbo_tab_admits_the_payment_and_stops_the_rest(app):
    client = _client(app)
    assert client.post("/console/api/dbo/authorize", json={"scenario": "pay"}).status_code == 409
    issued = client.post("/console/api/dbo/issue", json={}).json()
    assert issued["verdict_label"] == "ПОДТВЕРЖДЕНО" and issued["operation"]["date"] == bank_today()
    assert issued["credential"]["subject"]["audience"] == "stand-dbo-msb"

    def ask(scenario, channel="rest"):
        reply = client.post("/console/api/dbo/authorize", json={"scenario": scenario, "channel": channel})
        assert reply.status_code == 200, reply.text
        return reply.json()

    assert ask("over")["problems"][0].startswith("сумма")
    paid = ask("pay")
    assert paid["allowed"] is True and paid["endpoint"] == "/v1/operations/authorize"
    replay = ask("replay")
    assert replay["allowed"] is False and replay["problems"]
    client.post("/console/api/dbo/issue", json={})
    assert ask("pay", "authzen")["allowed"] is True
    client.post("/console/api/dbo/issue", json={})
    stopped = ask("takeover", "authzen")
    assert stopped["allowed"] is False and stopped["alerts"] and "приостановлено" in stopped["problems"][0]
    assert stopped["answer"]["decision"] is False
    bad = client.post("/console/api/dbo/authorize", json={"scenario": "steal"})
    assert bad.status_code == 422


def test_eval_tab_counts_and_zero_false_confirmed(app):
    client = _client(app)
    result = client.post("/console/api/eval", json={"per_defect": 1}).json()
    assert result["false_confirmed"] == 0 and result["false_confirmed_label"] == "ложных ПОДТВЕРЖДЕНО: 0"
    assert result["cases"] == result["defect_types"] and sum(result["by_verdict"].values()) == result["cases"]
    app.state.console_evals.lock.acquire()
    try:
        assert client.post("/console/api/eval", json={"per_defect": 2}).status_code == 429
    finally:
        app.state.console_evals.lock.release()


# --- части консоли ---------------------------------------------------------------------------------------------


def test_sessions_expire_and_are_evicted():
    now = [1000.0]
    sessions = Sessions(max_sessions=2, idle_seconds=10, lifetime_seconds=100, clock=lambda: now[0])
    first = sessions.create()
    token = first.token("operator")
    assert sessions.principal(token).role == "operator"
    assert sessions.get("short") is None and sessions.get(None) is None
    now[0] += 11
    assert sessions.get(first.sid) is None and sessions.principal(token) is None
    second, third = sessions.create(), sessions.create()
    sessions.create()
    assert sessions.get(second.sid) is None and sessions.get(third.sid) is not None and len(sessions) == 2


def test_issuer_cap_snapshots_and_rebase(tmp_path, monkeypatch):
    from mandat.console import issuer as module

    monkeypatch.setattr(module, "MAX_ISSUED", 1)
    demo = DemoIssuer(tmp_path)
    manifest = rebased_today(demo_manifest("14_old_director_charter_cleared"))
    assert manifest["operation"]["date"] == bank_today()
    report = {
        "case_id": "c",
        "organization": {"inn": "7729985006"},
        "verdict": "confirmed",
        "chain": {"poa_numbers": ["n"], "kind": "poa"},
        "operation": {"code": "PAYMENT_SIGN", "date": bank_today(), "amount_rub": 1},
    }
    _, index = demo.issue(report, audience="a", nonce="n" * 20)
    with pytest.raises(IssuerExhausted):
        demo.issue(report, audience="a", nonce="m" * 20)
    for _ in range(4):
        demo.suspend(index, "test")
    snapshots = [p for p in (demo.status_dir / "snapshots").iterdir()]
    assert len(snapshots) <= 3


def test_inproc_call_turns_a_crash_into_500():
    async def broken(scope, receive, send):
        raise RuntimeError("boom")

    import asyncio

    reply = asyncio.run(inproc.call(broken, "GET", "/x", token="t", client="c"))
    assert reply.status == 500 and not reply.ok and reply.json() == {"detail": "ответ API не JSON"}
