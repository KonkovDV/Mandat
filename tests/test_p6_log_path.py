"""P6-03: журнал сервера не берёт номера из пути запроса.

P4-10 маскирует номера в причине отказа, а путь запроса шёл в журнал сервера как есть: клиент, который назвал
кейс по ИНН (``/v1/cases/7707083893/erase``), оставлял ИНН в журнале при каждом отказе по этому кейсу.
"""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from mandat.api import app as api
from mandat.api.storage import MemoryStore

TOKEN = "t" * 40
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def test_numbers_in_the_request_path_do_not_reach_the_server_log(caplog):
    client = TestClient(api.create_app(token=TOKEN, store=MemoryStore()), raise_server_exceptions=False)
    with caplog.at_level(logging.WARNING, "mandat.api"):
        answer = client.post("/v1/cases/7707083893/erase", content=b'{"reason": "x"}', headers=HEADERS)
    assert answer.status_code == 422
    assert "status=422" in caplog.text
    assert "7707083893" not in caplog.text
    assert "path=/v1/cases/<num>/erase" in caplog.text


def test_server_issued_case_ids_stay_readable_in_the_log(caplog):
    client = TestClient(api.create_app(token=TOKEN, store=MemoryStore()), raise_server_exceptions=False)
    with caplog.at_level(logging.WARNING, "mandat.api"):
        client.post("/v1/cases/case-ab12cd34ef56ab78/erase", content=b'{"reason": "x"}', headers=HEADERS)
    assert "path=/v1/cases/case-ab12cd34ef56ab78/erase" in caplog.text
