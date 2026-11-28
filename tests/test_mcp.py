"""«Мандат» по MCP (stdio): ИИ-агент спрашивает перед действием и не может переписать ответ.

Сервер ведётся строками через ``BytesIO``, как его ведёт клиент MCP: initialize → tools/list → tools/call. Вердикт
``check_authority`` совпадает с ``verify_case``; допуск — тот же, что в HTTP: один ``nonce`` в общем хранилище, те же
отказы и записи в журнале. Отчёт выдаётся только своей сессии. Ошибки протокола — коды JSON-RPC, ошибки инструмента —
``isError`` с текстом по-русски.
"""

from __future__ import annotations

import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import random_key
from test_authzen import AUDIENCE, HEADERS, REP_INN, Issuer, _client, _evaluation, _nonce

from mandat import cli, mcp
from mandat.api import authzen
from mandat.api.principals import Directory, hash_token, parse_users
from mandat.api.storage import SQLiteStore
from mandat.audit import journal
from mandat.intake.case_loader import load_case_inline
from mandat.services.verify import verify_case
from mandat.strictjson import MAX_BODY

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "t" * 40
CASES = {
    "14_old_director_charter_cleared": "confirmed",
    "07_revoked": "refuted",
    "01_ok": "needs_review",
    "08_registry_unavailable": "insufficient_data",
}


def inline(name: str) -> dict:
    """Демо-кейс из examples/cases в виде тела POST /v1/cases: документы строкой в content."""
    case_dir = ROOT / "examples" / "cases" / name
    manifest = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    documents = [
        {"id": d["id"], "kind": d["kind"], "content": (case_dir / d["path"]).read_text(encoding="utf-8")}
        for d in manifest["documents"]
    ]
    return {**manifest, "documents": documents}


def session(tmp_path, issuer=None, *, users=(), token=TOKEN) -> mcp.Session:
    """Сессия на тех же хранилище и журнале, что и ``test_authzen._client``: каналы делят ``nonce``."""
    return mcp.Session(
        directory=Directory(tuple(users), "" if users else TOKEN),
        token=token,
        store=SQLiteStore(tmp_path / "store.db"),
        journal_path=tmp_path / "journal.jsonl",
        admission_config=issuer.config() if issuer else None,
    )


def exchange(sess: mcp.Session, *messages) -> list[dict]:
    raw = b"".join(m if isinstance(m, bytes) else json.dumps(m, ensure_ascii=False).encode() + b"\n" for m in messages)
    out = io.BytesIO()
    assert mcp.serve(io.BytesIO(raw), out, sess) == 0
    return [json.loads(line) for line in out.getvalue().splitlines()]


def call(sess: mcp.Session, name: str, arguments) -> dict:
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    (reply,) = exchange(sess, request)
    return reply["result"]


def text(result: dict) -> str:
    return result["content"][0]["text"]


def records(tmp_path, action: str) -> list[dict]:
    return [r for r in journal.read_journal(tmp_path / "journal.jsonl") if r["action"] == action]


def account(name: str, role: str, **extra) -> dict:
    expires = (datetime.now(UTC) + timedelta(days=30)).isoformat(timespec="seconds")
    return {"name": name, "role": role, "token_sha256": hash_token(name * 8), "expires_at": expires, **extra}


def test_handshake_lists_the_three_tools_and_answers_ping(tmp_path):
    replies = exchange(
        session(tmp_path),
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": "p", "method": "ping"},
        {"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
        {"jsonrpc": "2.0", "id": 4, "method": "initialize"},
    )
    assert [r["id"] for r in replies] == [1, 2, "p", 3, 4], "на уведомление ответа нет"
    init = replies[0]["result"]
    assert init["protocolVersion"] == "2025-06-18" and init["capabilities"] == {"tools": {"listChanged": False}}
    assert init["serverInfo"]["name"] == "mandat" and init["serverInfo"]["title"] == "Мандат — проверка полномочий"
    assert "окончательный" in init["instructions"]
    tools = {tool["name"]: tool for tool in replies[1]["result"]["tools"]}
    assert set(tools) == {"check_authority", "admit_operation", "get_report"}
    assert all(tool["inputSchema"]["type"] == "object" and tool["description"] for tool in tools.values())
    assert "на разбор" in tools["check_authority"]["description"]
    assert replies[2]["result"] == {}
    assert replies[3]["result"]["protocolVersion"] == "2025-03-26", "версию клиента сервер повторяет"
    assert replies[4]["result"]["protocolVersion"] == mcp.PROTOCOL_VERSION


@pytest.mark.parametrize(("name", "expected"), CASES.items())
def test_check_authority_gives_the_verdict_of_verify_case(tmp_path, name, expected):
    sess = session(tmp_path)
    manifest = inline(name)
    result = call(sess, "check_authority", {"manifest": manifest})
    assert result["isError"] is False
    summary = result["structuredContent"]
    assert json.loads(text(result)) == summary
    direct = verify_case(load_case_inline(manifest), profile="demo").as_dict()
    assert summary["verdict"] == direct["verdict"] == expected
    assert summary["hand_to_human"] is (expected in {"needs_review", "insufficient_data"})
    assert summary["what_to_do"] == mcp.GUIDANCE[expected]
    driving = {
        f["rule_id"]
        for f in direct["findings"]
        if f["effect"] in {"refutes", "not_verified", "needs_review"} and f["check"] not in {"signature", "bankruptcy"}
    }
    assert {r["rule_id"] for r in summary["reasons"]} == driving
    assert [s["action"] for s in summary["next_steps"]] == [s["action"] for s in direct["next_steps"]]
    report = sess.reports[summary["report_id"]]
    assert report["reproducibility_hash"] == summary["reproducibility_hash"]
    assert report["case_id"] == summary["report_id"] != name and report["client_case_id"] == name
    (record,) = records(tmp_path, "verify")
    assert record["case_id"] == summary["report_id"] and record["payload_hash"] == summary["reproducibility_hash"]
    assert record["actor"] == "default" and record["details"] == {"verdict": expected, "profile": "demo", "via": "mcp"}


def test_get_report_serves_only_ids_this_session_issued(tmp_path):
    sess = session(tmp_path)
    summary = call(sess, "check_authority", {"manifest": inline("07_revoked")})["structuredContent"]
    report = call(sess, "get_report", {"report_id": summary["report_id"]})
    assert report["isError"] is False and report["structuredContent"]["findings"]
    assert report["structuredContent"]["reproducibility_hash"] == summary["reproducibility_hash"]
    (read,) = records(tmp_path, "read_case")
    assert read["case_id"] == summary["report_id"] and read["details"] == {"via": "mcp"}
    for other, report_id in ((session(tmp_path), summary["report_id"]), (sess, "case-0000000000000000")):
        refused = call(other, "get_report", {"report_id": report_id})
        assert refused["isError"] is True and "эта сессия не выдавала" in text(refused)
    assert len(records(tmp_path, "read_case")) == 1, "отказ не читает отчёт"


def test_admission_is_given_once_and_the_replay_is_refused(tmp_path):
    issuer = Issuer(tmp_path)
    nonce = _nonce("mcp-once")
    body = _evaluation(issuer.issue(nonce), nonce)
    sess = session(tmp_path, issuer)
    first = call(sess, "admit_operation", body)["structuredContent"]
    assert first["decision"] is True and first["what_to_do"] == mcp.ADMITTED
    again = call(sess, "admit_operation", body)["structuredContent"]
    assert again["decision"] is False and again["context"]["already_admitted"] is True
    assert again["what_to_do"] == mcp.REPLAYED
    rows = records(tmp_path, "authorize_operation")
    assert [r["details"]["allowed"] for r in rows] == [True, False] and {r["details"]["via"] for r in rows} == {"mcp"}
    assert first["evidence"] == {"report_sha256": "b" * 64, "journal_record": {"seq": 1, "hash": rows[0]["hash"]}}
    assert REP_INN not in (tmp_path / "journal.jsonl").read_text(encoding="utf-8")


def test_refusal_has_the_reasons_of_the_http_endpoint(tmp_path):
    issuer = Issuer(tmp_path)
    a, b = _nonce("mcp-refuted"), _nonce("http-refuted")
    via_mcp = call(session(tmp_path, issuer), "admit_operation", _evaluation(issuer.issue(a, "refuted"), a))
    via_http = _client(issuer, tmp_path).post(
        authzen.EVALUATION_PATH, json=_evaluation(issuer.issue(b, "refuted"), b), headers=HEADERS
    )
    decision = via_mcp["structuredContent"]
    assert decision["decision"] is via_http.json()["decision"] is False
    assert decision["context"]["problems"] == via_http.json()["context"]["problems"]
    assert any("нужен confirmed" in p for p in decision["context"]["problems"]) and decision["what_to_do"] == mcp.DENIED


def test_nonce_spent_over_http_is_refused_over_mcp(tmp_path):
    issuer = Issuer(tmp_path)
    nonce = _nonce("cross")
    body = _evaluation(issuer.issue(nonce), nonce)
    assert _client(issuer, tmp_path).post(authzen.EVALUATION_PATH, json=body, headers=HEADERS).json()["decision"]
    replay = call(session(tmp_path, issuer), "admit_operation", body)["structuredContent"]
    assert replay["decision"] is False and replay["context"]["already_admitted"] is True


def test_malformed_admission_request_is_a_tool_error_and_spends_no_nonce(tmp_path):
    issuer = Issuer(tmp_path)
    nonce = _nonce("spoil")
    credential = issuer.issue(nonce)
    sess = session(tmp_path, issuer)
    for spoil in ({"subject": ["x"]}, {"subject": {"type": "representative", "id": 7}}, {"context": "x"}):
        result = call(sess, "admit_operation", {**_evaluation(credential, nonce), **spoil})
        assert result["isError"] is True and authzen.BAD_REQUEST in text(result)
    assert call(sess, "admit_operation", _evaluation(credential, nonce))["structuredContent"]["decision"] is True
    unset = call(session(tmp_path), "admit_operation", _evaluation(credential, nonce))
    assert unset["isError"] is True and "допуск не настроен" in text(unset)


def test_protocol_errors_get_json_rpc_codes_and_the_session_goes_on(tmp_path):
    oversized = b'{"jsonrpc": "2.0", "id": 9, "method": "ping", "pad": "' + b"x" * MAX_BODY + b'"}\n'
    replies = exchange(
        session(tmp_path),
        b"{not json\n",
        b'{"jsonrpc": "2.0", "id": 1, "id": 2, "method": "ping"}\n',
        b"[]\n",
        {"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
        {"jsonrpc": "2.0", "id": 4.5, "method": "ping"},
        {"jsonrpc": "1.0", "id": 5, "method": "ping"},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": []},
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "delete_everything"}},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
        {"jsonrpc": "2.0", "id": 1, "result": {}},
        b"\n",
        oversized,
        {"jsonrpc": "2.0", "id": 8, "method": "ping"},
    )
    codes = [(r["id"], r.get("error", {}).get("code")) for r in replies]
    assert codes == [
        (None, -32700),
        (None, -32700),
        (None, -32600),
        (3, -32601),
        (None, -32600),
        (5, -32600),
        (6, -32602),
        (7, -32602),
        (None, -32600),
        (8, None),
    ]


@pytest.mark.parametrize(
    ("name", "arguments", "words"),
    [
        ("check_authority", {"manifest": "case.json"}, "manifest — объект"),
        ("check_authority", {"manifest": {}, "profile": ["demo"]}, "profile: demo или strict"),
        ("check_authority", {"manifest": {}, "extra": 1}, "лишний аргумент 'extra'"),
        ("check_authority", {"manifest": {"operation": {"code": "PAYMENT_SIGN"}}}, "входных данных"),
        ("get_report", {"report_id": 7}, "report_id — строка"),
        ("get_report", [1], "arguments — объект"),
        ("admit_operation", {"subject": "x"}, "subject: объект JSON"),
    ],
)
def test_wrong_arguments_are_tool_errors_in_russian(tmp_path, name, arguments, words):
    result = call(session(tmp_path, Issuer(tmp_path)), name, arguments)
    assert result["isError"] is True and words in text(result)
    assert "structuredContent" not in result


def test_caller_is_an_api_account_and_its_role_decides(tmp_path, monkeypatch):
    issuer = Issuer(tmp_path)
    nonce = _nonce("roles")
    body = _evaluation(issuer.issue(nonce), nonce)
    stranger = call(session(tmp_path, issuer, token="x" * 40), "check_authority", {"manifest": inline("01_ok")})
    assert stranger["isError"] is True and mcp.TOKEN_ENV in text(stranger)
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    monkeypatch.setenv("MANDAT_JOURNAL_KEY", random_key("journal"))
    monkeypatch.setenv("MANDAT_PII_KEY", random_key("pii"))
    users = parse_users(
        [
            account("anna", "operator"),
            account("dbomsb", "dbo", audiences=[AUDIENCE]),
            account("dbocorp", "dbo", audiences=["bank-dbo-corp"]),
        ]
    )
    operator, dbo, other = (session(tmp_path, issuer, users=users, token=n * 8) for n in ("anna", "dbomsb", "dbocorp"))
    assert "роль dbo" in text(call(operator, "admit_operation", body))
    assert "strict" in text(call(operator, "check_authority", {"manifest": inline("01_ok"), "profile": "demo"}))
    assert call(operator, "check_authority", {"manifest": inline("01_ok")})["structuredContent"]["profile"] == "strict"
    assert "кейсы не проверяет" in text(call(dbo, "check_authority", {"manifest": inline("01_ok")}))
    assert "audience" in text(call(other, "admit_operation", body))
    assert call(dbo, "admit_operation", body)["structuredContent"]["decision"] is True, "отказы nonce не тратят"
    assert {r["actor"] for r in records(tmp_path, "authorize_operation")} == {"dbomsb"}


def test_without_a_journal_record_there_is_no_answer_and_crashes_are_refusals(tmp_path, monkeypatch, caplog):
    sess = session(tmp_path)

    def broken(*args, **kwargs):
        raise OSError("журнал недоступен")

    monkeypatch.setattr(journal, "append", broken)
    refused = call(sess, "check_authority", {"manifest": inline("01_ok")})
    assert refused["isError"] is True and "журнал аудита недоступен, кейс не сохранён" in text(refused)
    assert not sess.reports
    monkeypatch.undo()

    def boom(*args, **kwargs):
        raise RuntimeError("сбой")

    monkeypatch.setattr(mcp, "verify_case", boom)
    crashed = call(sess, "check_authority", {"manifest": inline("01_ok")})
    assert crashed["isError"] is True and text(crashed) == mcp.INTERNAL
    assert "RuntimeError" in caplog.text and "сбой" not in caplog.text


def test_cli_serves_stdio_and_does_not_start_without_a_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MANDAT_API_TOKEN", TOKEN)
    monkeypatch.delenv(mcp.TOKEN_ENV, raising=False)
    assert cli.main(["mcp"]) == 2
    assert mcp.TOKEN_ENV in capsys.readouterr().err
    monkeypatch.setenv(mcp.TOKEN_ENV, TOKEN)
    monkeypatch.setenv(mcp.JOURNAL_ENV, str(tmp_path / "journal.jsonl"))
    stdin = io.TextIOWrapper(io.BytesIO(b'{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n'))
    stdout = io.TextIOWrapper(io.BytesIO())
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    assert cli.main(["mcp"]) == 0
    assert json.loads(stdout.buffer.getvalue()) == {"jsonrpc": "2.0", "id": 1, "result": {}}
