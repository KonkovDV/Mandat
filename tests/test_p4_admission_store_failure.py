"""P4-01: сбой хранилища во время допуска не оставляет «уже допущен» без допуска.

Раньше high-watermark списков статусов писался после захвата ``nonce``. Если хранилище отказывало между этими
шагами, клиент получал 503 «допуск не выдан» и запись в журнале не появлялась, а ``nonce`` оставался занят
отпечатком запроса. Повтор того же запроса получал ``already_admitted`` с временем допуска, которого не было,
и ДБО по контракту B6 провёл бы операцию без записи о допуске.
"""

from __future__ import annotations

import json

from test_a5_operation_admission import HEADERS, Issuer, _body, _client

from mandat.api.storage import SQLiteStore


class FailingWatermarks(SQLiteStore):
    """Хранилище, которое один раз отказывает на записи high-watermark, как Redis при обрыве соединения."""

    failures = 1

    def watermark_raise(self, key: str, value: int) -> int:
        if self.failures:
            self.failures -= 1
            raise ConnectionError("хранилище недоступно")
        return super().watermark_raise(key, value)


def _records(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_store_failure_during_admission_leaves_no_phantom_admission(tmp_path):
    issuer = Issuer(tmp_path)
    nonce = "n-" + "4" * 20
    client = _client(issuer, tmp_path, store=FailingWatermarks(tmp_path / "store.db"))
    body = _body(issuer.issue(nonce), nonce)
    first = client.post("/v1/operations/authorize", json=body, headers=HEADERS)
    assert first.status_code == 200, first.text
    assert first.json()["allowed"] is False
    assert first.json()["already_admitted"] is False
    again = client.post("/v1/operations/authorize", json=body, headers=HEADERS)
    assert again.status_code == 200, again.text
    assert again.json()["allowed"] is True, again.json()
    assert again.json()["already_admitted"] is False
    allowed = [r["details"]["allowed"] for r in _records(tmp_path / "journal.jsonl")]
    assert allowed == [False, True]


def test_store_that_stays_down_refuses_without_claiming_the_nonce(tmp_path):
    issuer = Issuer(tmp_path)
    nonce = "n-" + "5" * 20
    store = FailingWatermarks(tmp_path / "store.db")
    store.failures = 2
    client = _client(issuer, tmp_path, store=store)
    body = _body(issuer.issue(nonce), nonce)
    first = client.post("/v1/operations/authorize", json=body, headers=HEADERS)
    assert first.status_code == 503
    again = client.post("/v1/operations/authorize", json=body, headers=HEADERS)
    assert again.status_code == 200, again.text
    assert again.json()["allowed"] is True and again.json()["already_admitted"] is False
