"""Повторный аудит: подмена цифр в реквизитах, общий токен рядом с пользователями, границы срока МЧД."""

from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import date

import pytest

from mandat.domain.identifiers import (
    inn_valid,
    kpp_valid,
    make_inn10,
    make_inn12,
    make_ogrn,
    make_snils,
    ogrn_valid,
    snils_valid,
)
from mandat.intake.case_loader import CaseError, load_case_dir, load_case_inline


def _arabic(s: str) -> str:
    return "".join(chr(0x660 + int(c)) if c.isdigit() else c for c in s)


def _fullwidth(s: str) -> str:
    return "".join(chr(0xFF10 + int(c)) if c.isdigit() else c for c in s)


@pytest.mark.parametrize("swap", [_arabic, _fullwidth])
def test_non_ascii_digits_fail_checksums(swap):
    rnd = random.Random(1)
    for _ in range(50):
        inn10 = make_inn10("".join(rnd.choice("0123456789") for _ in range(9)))
        inn12 = make_inn12("".join(rnd.choice("0123456789") for _ in range(10)))
        ogrn = make_ogrn("1" + "".join(rnd.choice("0123456789") for _ in range(11)))
        snils = make_snils("".join(rnd.choice("23456789") for _ in range(9)))
        assert inn_valid(inn10) and inn_valid(inn12) and ogrn_valid(ogrn) and snils_valid(snils)
        assert not inn_valid(swap(inn10))
        assert not inn_valid(swap(inn12))
        assert not ogrn_valid(swap(ogrn))
        assert not snils_valid(swap(snils))
    assert not kpp_valid(swap("770101001"))


def test_any_single_digit_change_breaks_inn10():
    rnd = random.Random(2)
    for _ in range(200):
        inn = make_inn10("".join(rnd.choice("0123456789") for _ in range(9)))
        bad = inn[:9] + str((int(inn[9]) + rnd.randint(1, 9)) % 10)
        assert not inn_valid(bad)


@pytest.mark.parametrize("value", ["7729985006\n", " 7729985006", "+7729985006", "7729985006 "])
def test_inn_with_extra_characters_is_rejected(value):
    assert not inn_valid(value)


def _manifest(root):
    return json.loads((root / "examples/cases/01_ok/case.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(("section", "key"), [("organization", "inn"), ("requester", "inn"), ("organization", "ogrn")])
def test_numeric_identifiers_are_rejected(root, section, key):
    manifest = _manifest(root)
    manifest["documents"] = []
    manifest[section][key] = 7729985006
    with pytest.raises(CaseError, match="строкой"):
        load_case_inline(manifest)


def test_api_rejects_numeric_inn_with_422(root):
    import fastapi  # noqa: F401  # защитный тест не пропускается без зависимости
    from fastapi.testclient import TestClient

    from mandat.api.app import create_app

    manifest = _manifest(root)
    manifest["documents"] = []
    manifest["organization"]["inn"] = 7729985006
    client = TestClient(create_app("t", rate_per_min=0))
    r = client.post("/v1/cases", json=manifest, headers={"Authorization": "Bearer t"})
    assert r.status_code == 422


def test_shared_token_does_not_work_next_to_users():
    import fastapi  # noqa: F401  # защитный тест не пропускается без зависимости
    from fastapi.testclient import TestClient

    from mandat.api.app import create_app
    from mandat.api.principals import hash_token

    users = json.dumps([{"name": "op", "role": "operator", "token_sha256": hash_token("op")}])
    client = TestClient(create_app("shared", users=users, rate_per_min=0))
    assert client.get("/v1/cases/x", headers={"Authorization": "Bearer shared"}).status_code == 401
    assert client.get("/v1/cases/x", headers={"Authorization": "Bearer op"}).status_code == 404


def test_rate_limit_ignores_forwarded_for():
    import fastapi  # noqa: F401  # защитный тест не пропускается без зависимости
    from fastapi.testclient import TestClient

    from mandat.api.app import create_app

    client = TestClient(create_app("t", rate_per_min=2))
    codes = [
        client.get("/v1/cases/a", headers={"Authorization": "Bearer t", "X-Forwarded-For": f"10.0.0.{i}"}).status_code
        for i in range(3)
    ]
    assert codes[-1] == 429


def test_chunked_body_over_limit_is_rejected():
    import fastapi  # noqa: F401  # защитный тест не пропускается без зависимости
    from fastapi.testclient import TestClient

    from mandat.api.app import MAX_BODY, create_app

    def chunks():
        yield b'{"a":"'
        for _ in range(MAX_BODY // (1024 * 1024) + 1):
            yield b"x" * 1024 * 1024
        yield b'"}'

    client = TestClient(create_app("t", rate_per_min=0))
    r = client.post("/v1/events", content=chunks(), headers={"Authorization": "Bearer t"})
    assert r.status_code == 413


def _poa(root, issue, until=None):
    case = load_case_dir(root / "examples/cases/12_no_term")
    return replace(case.poas[0], issue_date=issue, valid_until=until)


@pytest.mark.parametrize(
    ("issue", "last_day"),
    [
        (date(2025, 3, 10), date(2026, 3, 10)),
        (date(2024, 2, 29), date(2025, 2, 28)),
        (date(2023, 2, 28), date(2024, 2, 28)),
        (date(2025, 12, 31), date(2026, 12, 31)),
    ],
)
def test_no_term_poa_lasts_one_year_inclusive(root, issue, last_day):
    # П. 1 ст. 186 и п. 1 ст. 192 ГК РФ: годовой срок истекает в те же месяц и число следующего года.
    # Для 29 февраля берём 28-е по аналогии с п. 3 ст. 192 (последний день месяца).
    assert _poa(root, issue).effective_until() == last_day


def test_explicit_term_wins(root):
    assert _poa(root, date(2025, 1, 1), date(2025, 6, 30)).effective_until() == date(2025, 6, 30)


def test_eval_has_a_confirmed_path():
    from mandat.eval.run import run_corpus

    metrics = run_corpus(1)
    assert metrics["confusion"].get("confirmed->confirmed", 0) >= 1
    assert not metrics["mismatches"]
