"""Регрессионные тесты по аудиту версии 0.2.1 (октябрь 2026).

Каждый тест закрепляет один найденный дефект; номер в имени совпадает с таблицей
находок в задании на доработку (F — ложное «подтверждено», M — мониторинг,
D — отказ в обслуживании, C — падения API, I — целостность, A — доступ к API,
L — строгость разбора, K — объяснение и вторая свёртка, E — доказательства,
H — чистота материалов для жюри).

На версии 0.2.1 эти тесты падают. После доработки все должны проходить.
Ослаблять проверки нельзя: если меняется сигнатура функции, меняется вызов,
но не утверждение.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from mandat.eval import synth
from mandat.intake.case_loader import CaseError, load_case_inline
from mandat.services.verify import verify_case

ROOT = Path(__file__).resolve().parents[1]
W = synth.World.make(1)
SV = "Документ/Довер/СвДов"
SIGNER = "Документ/Довер/СвДоверит/Доверит/РосОргДовер/ЛицоБезДов/СвФЛ"
PRINCIPAL = "Документ/Довер/СвДоверит/Доверит/РосОргДовер/СвРосОрг"


# --------------------------------------------------------------------------- помощники


def manifest(defect: str) -> dict:
    return copy.deepcopy(synth.build(defect, 1).inline_manifest())


def doc(m: dict, doc_id: str) -> dict:
    return next(d for d in m["documents"] if d["id"] == doc_id)


def xml_set(document: dict, path: str, attr: str, value: str | None) -> None:
    root = ET.fromstring(document["content"].encode("utf-8"))
    element = root.find(path)
    assert element is not None, path
    if value is None:
        element.attrib.pop(attr, None)
    else:
        element.set(attr, value)
    document["content"] = ET.tostring(root, encoding="unicode")


def xml_get(document: dict, path: str, attr: str) -> str | None:
    element = ET.fromstring(document["content"].encode("utf-8")).find(path)
    return None if element is None else element.get(attr)


def verdict(m: dict, profile: str = "demo") -> str:
    """Вердикт кейса. Отказ загрузчика принять кейс считаем «недостаточно данных»."""
    try:
        case = load_case_inline(m)
    except CaseError:
        return "insufficient_data"
    return verify_case(case, profile=profile).verdict.value


def api_client():
    from fastapi.testclient import TestClient

    from mandat.api.app import create_app

    return TestClient(create_app(token="t"), raise_server_exceptions=False)


AUTH = {"Authorization": "Bearer t"}


# --------------------------------------------------------------------------- F: ложное «подтверждено»


def test_f01_subdelegation_signer_must_be_parent_representative():
    m = manifest("ok_subdelegation")
    xml_set(doc(m, "poa-child"), SIGNER, "ИННФЛ", W.stranger.inn)
    xml_set(doc(m, "poa-child"), SIGNER, "СНИЛС", W.stranger.snils)
    assert verdict(m) == "refuted"


def test_f02_child_issued_before_parent_is_not_confirmed():
    m = manifest("ok_subdelegation")
    assert xml_get(doc(m, "poa-root"), SV, "ДатаВыдДовер") == "2026-03-01"
    xml_set(doc(m, "poa-child"), SV, "ДатаВыдДовер", "2026-02-01")
    assert verdict(m) != "confirmed"


@pytest.mark.parametrize("profile", ["demo", "strict"])
@pytest.mark.parametrize("base", ["ok", "ok_director"])
def test_f03_inn_and_snils_of_different_people_is_not_confirmed(base, profile):
    m = manifest(base)
    m["requester"]["snils"] = W.stranger.snils
    assert verdict(m, profile) != "confirmed"


EGRUL_GAPS = {
    "f04_extract_2024": lambda e: e.update(extracted_at="2024-06-01"),
    "f04_extract_missing": lambda e: e.pop("extracted_at"),
    "f05_status_missing": lambda e: e.pop("status"),
    "f06_recorded_after_operation": lambda e: e["executives"][0].update(valid_from=None, record_date="2026-10-25"),
    "f06_no_dates_at_all": lambda e: e["executives"][0].update(valid_from=None, record_date=None, valid_to=None),
    "f13_ogrn_of_another_company": lambda e: e.update(ogrn="1027700132195"),
}


@pytest.mark.parametrize("profile", ["demo", "strict"])
@pytest.mark.parametrize("gap", sorted(EGRUL_GAPS))
def test_f04_f05_f06_f13_egrul_gaps_fail_closed(gap, profile):
    m = manifest("ok_director")
    EGRUL_GAPS[gap](doc(m, "egrul")["content"])
    assert verdict(m, profile) != "confirmed"


def _freshly_appointed_director(operation: str) -> dict:
    m = manifest("ok_director")
    egrul = doc(m, "egrul")["content"]
    egrul["executives"][0]["valid_to"] = "2026-10-17"
    egrul["executives"].append(
        {
            "full_name": W.stranger.full_name,
            "inn": W.stranger.inn,
            "grn": "2267700777777",
            "record_date": "2026-10-18",
            "valid_from": "2026-10-18",
            "valid_to": None,
        }
    )
    m["requester"] = {"full_name": W.stranger.full_name, "inn": W.stranger.inn, "snils": W.stranger.snils}
    m["operation"]["code"] = operation
    return m


@pytest.mark.parametrize("profile", ["demo", "strict"])
@pytest.mark.parametrize("operation", ["REQUISITES_CHANGE", "USER_ADMIN"])
def test_f07_fresh_director_high_risk_operation_goes_to_review(operation, profile):
    """Руководитель назначен 2 дня назад и сразу меняет реквизиты — картина захвата."""
    assert verdict(_freshly_appointed_director(operation), profile) == "needs_review"


def test_f08_fresh_change_flagged_even_if_old_record_not_closed():
    m = manifest("director_changed")
    assert verdict(m) == "needs_review"
    doc(m, "egrul")["content"]["executives"][0]["valid_to"] = None
    assert verdict(m) == "needs_review"


@pytest.mark.parametrize("profile", ["demo", "strict"])
def test_f09_duplicate_egrul_is_not_resolved_silently(profile):
    m = manifest("ok_director")
    first = copy.deepcopy(doc(m, "egrul"))
    first["id"] = "egrul-0"
    first["content"]["status"] = "liquidated"
    m["documents"].insert(0, first)
    assert verdict(m, profile) != "confirmed"


def test_f10_duplicate_charter_is_not_resolved_silently():
    m = manifest("ok_director")
    first = copy.deepcopy(doc(m, "charter"))
    first["id"] = "charter-0"
    first["content"] += " Сделки на сумму свыше 300 000 рублей требуют одобрения общего собрания участников."
    m["documents"].insert(0, first)
    assert verdict(m, "strict") != "confirmed"


CHARTER_VARIANTS = {
    "rublej": "Сделки на сумму свыше 300 000 рублей требуют одобрения общего собрания участников.",
    "rub_dot": "Сделки на сумму свыше 300 000 руб. совершаются только после одобрения общим собранием участников.",
    "tys": "Сделки на сумму свыше 300 тыс. рублей требуют одобрения общего собрания участников.",
    "mln": "Сделки на сумму свыше 0,3 млн рублей требуют одобрения общего собрания участников.",
    "dots": "Сделки на сумму свыше 300.000 рублей требуют одобрения общего собрания участников.",
    "commas": "Сделки на сумму свыше 300,000 рублей требуют одобрения общего собрания участников.",
    "kopecks": "Сделки на сумму свыше 300 000,00 рублей требуют одобрения общего собрания участников.",
    "words": "Сделки на сумму более трехсот тысяч рублей требуют одобрения общего собрания участников.",
    "and_more": "Сделки на сумму 300 000 рублей и более требуют одобрения общего собрания участников.",
    "consent": "Сделки на сумму свыше 300 000 рублей совершаются только с согласия общего собрания участников.",
    "sole_member": "Сделки на сумму свыше 300 000 рублей требуют решения единственного участника общества.",
    "rub_code": "Сделки на сумму свыше 300 000 RUB требуют одобрения общего собрания участников.",
    "rub_sign": "Сделки на сумму свыше 300 000 ₽ требуют одобрения общего собрания участников.",
    "in_words_too": "Сделки на сумму свыше 300 000 (трехсот тысяч) рублей требуют одобрения "
    "общего собрания участников.",
    "two_sentences": "Сделки на сумму свыше 300 000 рублей. Такие сделки требуют одобрения общего собрания участников.",
    "semicolon": "Генеральный директор совершает сделки на сумму свыше 300 000 рублей; на это требуется одобрение "
    "общего собрания.",
    "nbsp": "Сделки на сумму свыше 300 000 рублей требуют одобрения общего собрания участников.",
    "jointly_with": "Платёжные документы генеральный директор подписывает совместно с главным бухгалтером.",
    "second_signature": "Для платёжных документов обязательна вторая подпись главного бухгалтера.",
}


@pytest.mark.parametrize("variant", sorted(CHARTER_VARIANTS))
def test_f11_charter_restriction_phrasings_go_to_review(variant):
    """Сумма операции 480 000 ₽ выше порога 300 000 ₽ при любой записи порога.

    Ограничение, которое не удалось разобрать, — тоже повод для разбора, а не «подтверждено».
    """
    m = manifest("ok_director")
    doc(m, "charter")["content"] += " " + CHARTER_VARIANTS[variant]
    assert verdict(m, "strict") == "needs_review"


def test_f12_same_poa_number_with_different_content_is_not_confirmed():
    m = manifest("ok_subdelegation")
    twin = copy.deepcopy(doc(m, "poa-root"))
    twin["id"] = "poa-root-twin"
    xml_set(twin, SV, "ПрПередов", "1")
    m["documents"].insert(0, twin)
    assert verdict(m) != "confirmed"


def test_f14_original_number_must_match_chain_root():
    m = manifest("ok_subdelegation")
    xml_set(doc(m, "poa-child"), SV, "НомДоверПерв", "11111111-2222-3333-4444-555555555555")
    assert verdict(m) != "confirmed"


def test_f15_attribute_and_element_with_different_values_is_not_confirmed():
    m = manifest("ok")
    root = ET.fromstring(doc(m, "poa-root")["content"].encode("utf-8"))
    ET.SubElement(root.find(SV), "СрокДейст").text = "2026-01-01"
    doc(m, "poa-root")["content"] = ET.tostring(root, encoding="unicode")
    assert verdict(m) != "confirmed"


def test_f16_guid_letter_case_does_not_hide_transfer_with_loss():
    from mandat.domain.model import Organization
    from mandat.intake.emchd import build_emchd_xml

    m = manifest("ok")
    root_number = xml_get(doc(m, "poa-root"), SV, "НомДовер")
    transfer = build_emchd_xml(
        number="bbbbbbbb-0000-0000-0000-000000000001",
        issue_date=date(2026, 4, 1),
        valid_until=date(2027, 1, 1),
        principal=Organization(W.org.inn, W.org.ogrn, W.org.kpp, W.org_full_name),
        signer=W.accountant,
        representatives=[W.clerk1],
        machine_codes=[synth.PAY],
        parent_number=root_number.upper(),
        loses_authority_code="2",
    )
    m["documents"].append({"id": "transfer", "kind": "emchd_xml", "content": transfer})
    assert verdict(m) == "refuted"


def test_f17_joint_powers_of_parent_apply_to_subdelegation():
    """У родительской МЧД два представителя с совместными полномочиями, передоверие подписал один."""
    m = manifest("ok_subdelegation")
    root = ET.fromstring(doc(m, "poa-root")["content"].encode("utf-8"))
    body = root.find("Документ/Довер")
    first = body.find("СвУпПред")
    second = copy.deepcopy(first)
    for element in second.iter():
        if "ИННФЛ" in element.attrib:
            element.set("ИННФЛ", W.clerk2.inn)
        if "СНИЛС" in element.attrib:
            element.set("СНИЛС", W.clerk2.snils)
    body.insert(list(body).index(first) + 1, second)
    root.find(SV).set("ПрСовмПолн", "2")
    doc(m, "poa-root")["content"] = ET.tostring(root, encoding="unicode")
    assert verdict(m) != "confirmed"


# --------------------------------------------------------------------------- L: строгость разбора


@pytest.mark.parametrize(
    "path, attr",
    [
        (PRINCIPAL, "ОГРН"),
        (PRINCIPAL, "НаимОрг"),
        (SIGNER + "/СведФЛ/ФИО", "Фамилия"),
    ],
)
def test_l1_mandatory_format_fields_missing_is_not_confirmed(path, attr):
    m = manifest("ok")
    xml_set(doc(m, "poa-root"), path, attr, None)
    assert verdict(m) != "confirmed"


@pytest.mark.parametrize("value", ["2026-03-01garbage", "2026-W09-7", "20260301", "1.3.2026"])
def test_l2_malformed_issue_date_is_not_accepted(value):
    m = manifest("ok")
    xml_set(doc(m, "poa-root"), SV, "ДатаВыдДовер", value)
    assert verdict(m) != "confirmed"


def test_l2_fullwidth_digits_are_not_a_valid_inn():
    from mandat.domain.identifiers import inn_valid

    fullwidth = W.org.inn.translate(str.maketrans("0123456789", "０１２３４５６７８９"))
    assert inn_valid(W.org.inn)
    assert not inn_valid(fullwidth)


@pytest.mark.parametrize("amount", [-5, 0, "1e3", "много", True])
def test_l2_invalid_amount_is_rejected(amount):
    m = manifest("ok")
    m["operation"]["amount_rub"] = amount
    assert verdict(m) == "insufficient_data"


def test_l2_amount_with_kopecks_is_not_truncated():
    m = manifest("ok")
    m["operation"]["amount_rub"] = 1.9
    try:
        case = load_case_inline(m)
    except CaseError:
        return
    report = verify_case(case).as_dict()
    assert float(report["operation"]["amount_rub"]) == pytest.approx(1.9)


def test_l3_leaf_choice_does_not_depend_on_document_order():
    m = manifest("ok")
    twin = copy.deepcopy(doc(m, "poa-root"))
    twin["id"] = "poa-twin"
    twin_number = "cccccccc-0000-0000-0000-000000000001"
    xml_set(twin, SV, "НомДовер", twin_number)
    m["registry"][twin_number] = "revoked"
    first, last = copy.deepcopy(m), copy.deepcopy(m)
    first["documents"].insert(0, twin)
    last["documents"].append(twin)
    assert verdict(first) == verdict(last)


# --------------------------------------------------------------------------- K: объяснение и вторая свёртка


@pytest.mark.parametrize(
    "statement",
    [
        "Иванов вправе подписывать платёжные поручения.",
        "Операцию разрешено провести.",
        "Представитель имеет право подписать платёж.",
        "Платёж можно провести.",
    ],
)
def test_k01_explainer_drops_permission_language_against_refuted_verdict(statement):
    try:
        from mandat.advisory.explainer import Fact, explain
    except ImportError:  # до переименования модуля
        from mandat.advisory.copilot import Fact, explain

    facts = [Fact("verdict", "Вердикт: ОПРОВЕРГНУТО"), Fact("f1", "МЧД 123 отменена в реестре")]
    raw = json.dumps({"statements": [{"text": statement, "fact_ids": ["verdict"]}]}, ensure_ascii=False)
    kept, dropped = explain("Можно ли проводить платёж?", facts, lambda _messages: raw)
    assert kept == [] and dropped == 1


def test_k02_disagreement_of_two_folds_never_confirms(monkeypatch):
    """Вторая свёртка работает и в рабочем коде: расхождение даёт «недостаточно данных»."""
    import mandat.services.verify as verify_module
    from mandat.domain.verdict import Verdict

    monkeypatch.setattr(verify_module, "oracle_verdict", lambda *a, **k: Verdict.REFUTED, raising=False)
    report = verify_case(load_case_inline(manifest("ok")), profile="demo")
    assert report.verdict.value == "insufficient_data"


# --------------------------------------------------------------------------- M: мониторинг

RIGHTS = [
    {
        "right_id": "r1",
        "org_inn": "7732542572",
        "representative_inn": "1",
        "operation": "PAYMENT_SIGN",
        "poa_numbers": ["81ce2d18-ae0c-1a95-fbe5-09585ac55286"],
        "eio_inn": "1",
    }
]
REVOKED = {
    "kind": "POA_REVOKED",
    "org_inn": "7732542572",
    "at": "2026-10-20T10:00:00+00:00",
    "payload": {"number": "81ce2d18-ae0c-1a95-fbe5-09585ac55286"},
}


def _alerts(events: list[dict]) -> list[str]:
    from mandat.services.monitoring import evaluate, parse_events

    rights, parsed = parse_events({"rights": copy.deepcopy(RIGHTS), "events": events})
    return [alert.kind for alert in evaluate(rights, parsed)]


@pytest.mark.parametrize(
    "event",
    [
        {**REVOKED, "payload": {"number": "81CE2D18-AE0C-1A95-FBE5-09585AC55286"}},
        {**REVOKED, "org_inn": "7732542572 "},
        {**REVOKED, "org_inn": 7732542572},
        {**REVOKED, "payload": {"number": "{81ce2d18-ae0c-1a95-fbe5-09585ac55286}"}},
    ],
    ids=["guid_upper", "inn_space", "inn_int", "guid_braces"],
)
def test_m2_revocation_matches_regardless_of_formatting(event):
    assert "poa_invalidated" in _alerts([event])


def test_m2_revocation_without_number_is_not_silently_dropped():
    try:
        alerts = _alerts([{**REVOKED, "payload": {}}])
    except (KeyError, ValueError):
        return
    assert alerts, "событие отмены без номера МЧД пропало без следа"


def test_m1_mixed_naive_and_aware_time_keeps_revocation():
    naive = {"kind": "NEW_POA", "org_inn": "7732542572", "at": "2026-10-20T10:00:00"}
    response = api_client().post("/v1/events", json={"rights": RIGHTS, "events": [REVOKED, naive]}, headers=AUTH)
    assert response.status_code == 200, response.text
    assert "poa_invalidated" in [a["kind"] for a in response.json()["alerts"]]


@pytest.mark.parametrize("window", ["-1", "0", "1000000000000"])
def test_a3_window_hours_is_validated(window):
    response = api_client().post(
        f"/v1/events?window_hours={window}", json={"rights": RIGHTS, "events": [REVOKED]}, headers=AUTH
    )
    assert response.status_code == 422


def test_a3_event_time_of_wrong_type_is_422():
    response = api_client().post(
        "/v1/events", json={"rights": RIGHTS, "events": [{**REVOKED, "at": 1700000000}]}, headers=AUTH
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- C, A: API


def _egrul_of(m: dict) -> dict:
    return doc(m, "egrul")["content"]


MALFORMED = {
    "executives_strings": lambda m: _egrul_of(m).__setitem__("executives", ["x"]),
    "executives_number": lambda m: _egrul_of(m).__setitem__("executives", 5),
    "full_name_int": lambda m: _egrul_of(m).__setitem__("full_name", 1),
    "requester_inn_list": lambda m: m["requester"].__setitem__("inn", ["7700"]),
    "documents_dict": lambda m: m.__setitem__("documents", {"a": 1}),
    "documents_strings": lambda m: m.__setitem__("documents", ["x"]),
    "organization_list": lambda m: m.__setitem__("organization", []),
    "registry_list": lambda m: m.__setitem__("registry", ["x"]),
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_c1_malformed_manifest_is_not_500(name):
    m = manifest("ok")
    MALFORMED[name](m)
    response = api_client().post("/v1/cases", json=m, headers=AUTH)
    assert response.status_code in (200, 400, 422), response.text[:200]
    if response.status_code == 200:
        assert response.json()["verdict"] == "insufficient_data"


def test_c1_infinite_amount_is_not_500():
    body = json.dumps(manifest("ok"), ensure_ascii=False).replace('"amount_rub": 480000', '"amount_rub": Infinity')
    assert "Infinity" in body
    response = api_client().post(
        "/v1/cases", content=body.encode("utf-8"), headers={**AUTH, "Content-Type": "application/json"}
    )
    assert response.status_code in (200, 400, 422), response.text[:200]
    if response.status_code == 200:
        assert response.json()["verdict"] == "insufficient_data"


def test_c1_issue_year_9999_without_term_is_not_500():
    m = manifest("ok")
    xml_set(doc(m, "poa-root"), SV, "ДатаВыдДовер", "9999-03-01")
    xml_set(doc(m, "poa-root"), SV, "СрокДейст", None)
    response = api_client().post("/v1/cases", json=m, headers=AUTH)
    assert response.status_code in (200, 400, 422), response.text[:200]
    if response.status_code == 200:
        assert response.json()["verdict"] != "confirmed"


@pytest.mark.parametrize("path", ["/openapi.json", "/docs"])
def test_a2_api_description_requires_token(path):
    assert api_client().get(path).status_code in (401, 404)


def test_a1_unauthenticated_chunked_body_is_not_buffered():
    from mandat.api.app import create_app

    app = create_app(token="t")
    state = {"left": 40, "consumed": 0}

    async def receive():
        if state["left"] == 0:
            return {"type": "http.request", "body": b"", "more_body": False}
        state["left"] -= 1
        state["consumed"] += 1 << 20
        return {"type": "http.request", "body": b"x" * (1 << 20), "more_body": True}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/cases",
        "raw_path": b"/v1/cases",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"transfer-encoding", b"chunked")],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 80),
    }
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    assert status in (401, 411, 413)
    assert state["consumed"] <= 9 << 20, f"прочитано {state['consumed'] >> 20} МБ до отказа"


# --------------------------------------------------------------------------- D: отказ в обслуживании


def _long_chain(links: int, operation_date: str) -> dict:
    from mandat.domain.model import Organization
    from mandat.intake.emchd import build_emchd_xml

    principal = Organization(W.org.inn, W.org.ogrn, W.org.kpp, W.org_full_name)
    people = [synth.World.make(1000 + i).accountant for i in range(links)]
    documents, registry, parent = [], {}, None
    for i in range(links):
        number = f"00000000-0000-0000-0000-{i:012d}"
        xml = build_emchd_xml(
            number=number,
            issue_date=date(2026, 3, 1),
            valid_until=date(2027, 1, 1),
            principal=principal,
            signer=W.director if i == 0 else people[i - 1],
            representatives=[people[i]],
            machine_codes=[synth.PAY],
            subdelegation_code="3",
            parent_number=parent,
        )
        documents.append({"id": f"p{i}", "kind": "emchd_xml", "content": xml})
        registry[number] = "active"
        parent = number
    documents.append({"id": "egrul", "kind": "egrul_json", "content": synth._egrul(W)})
    return {
        "case_id": "long-chain",
        "organization": {"inn": W.org.inn, "ogrn": W.org.ogrn},
        "requester": {"inn": people[-1].inn, "snils": people[-1].snils},
        "operation": {"code": "PAYMENT_SIGN", "date": operation_date},
        "documents": documents,
        "registry": registry,
    }


def test_d1_long_expired_chain_is_bounded():
    m = _long_chain(300, "2027-06-01")
    started = time.perf_counter()
    report = verify_case(load_case_inline(m)).as_dict()
    elapsed = time.perf_counter() - started
    size = len(json.dumps(report, ensure_ascii=False).encode("utf-8"))
    assert report["verdict"] != "confirmed"
    assert elapsed < 2.0, f"{elapsed:.1f} с"
    assert size < 1_000_000, f"отчёт {size / 1e6:.1f} МБ"


@pytest.mark.parametrize("stem", ["совместн", "крупн"])
def test_d2_charter_parsing_is_linear(stem):
    from mandat.domain.model import TextDocument
    from mandat.intake.charter import extract_charter_facts

    started = time.perf_counter()
    extract_charter_facts(TextDocument("c", "x", "charter_text", stem * 6000))
    assert time.perf_counter() - started < 0.5


# --------------------------------------------------------------------------- I, L4: целостность


def test_i1_replayed_older_status_list_is_rejected(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from mandat.audit import attestation
    from mandat.audit.status_list import StatusRegistry, check_status

    key = Ed25519PrivateKey.generate()
    public = key.public_key()
    report = verify_case(load_case_inline(manifest("ok"))).as_dict()
    registry = StatusRegistry.open(tmp_path / "status")
    index = registry.allocate(
        {
            "case_id": report["case_id"],
            "org_inn": report["organization"]["inn"],
            "verdict": report["verdict"],
            "poa_numbers": report["chain"]["poa_numbers"],
        }
    )
    credential = attestation.issue(report, key, status=registry.credential_status(index))
    t0 = datetime(2026, 10, 20, 10, tzinfo=UTC)
    registry.save(key, now=t0)
    old = (tmp_path / "status" / "revocation.json").read_text(encoding="utf-8")
    registry.mark(index, "revocation", "POA_REVOKED")
    registry.save(key, now=t0 + timedelta(hours=1))
    seen: dict = {}
    assert check_status(credential, tmp_path / "status", public, seen=seen)["state"] == "отозвано"
    (tmp_path / "status" / "revocation.json").write_text(old, encoding="utf-8")
    try:
        state = check_status(credential, tmp_path / "status", public, seen=seen)["state"]
    except ValueError:
        state = "отклонено"
    assert state != "действует"


def test_i2_attest_verify_without_trusted_key_fails(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from mandat.audit import attestation

    report = verify_case(load_case_inline(manifest("ok"))).as_dict()
    forged = attestation.issue(report, Ed25519PrivateKey.generate())
    path = tmp_path / "forged.json"
    path.write_text(attestation.dump(forged), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "mandat.cli", "attest-verify", str(path)], capture_output=True, text=True
    )
    assert done.returncode != 0


def test_i3_truncation_with_rewritten_witness_is_detected_when_key_is_set(tmp_path, monkeypatch):
    from mandat.audit import journal

    monkeypatch.delenv("MANDAT_JOURNAL_KEY", raising=False)
    path = tmp_path / "journal.jsonl"
    for i in range(5):
        journal.append(path, action="verify", actor="a", role="r", case_id=f"c{i}", details={"i": i}, key="k3y")
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:3]) + "\n", encoding="utf-8")
    third = json.loads(lines[2])
    journal.witness_path(path).write_text(json.dumps({"records": 3, "hash": third["hash"]}), encoding="utf-8")
    assert not journal.verify_journal(path, key="k3y").ok


def test_l4_journal_line_with_duplicate_keys_is_rejected(tmp_path, monkeypatch):
    from mandat.audit import journal

    monkeypatch.delenv("MANDAT_JOURNAL_KEY", raising=False)
    path = tmp_path / "journal.jsonl"
    journal.append(path, action="verify", actor="a", role="r", case_id="c1", details={"verdict": "refuted"})
    line = path.read_text(encoding="utf-8").rstrip("\n")
    path.write_text('{"details":{"verdict":"confirmed"},' + line[1:] + "\n", encoding="utf-8")
    assert not journal.verify_journal(path).ok


def test_l4_malformed_journal_line_is_reported_not_raised(tmp_path, monkeypatch):
    from mandat.audit import journal

    monkeypatch.delenv("MANDAT_JOURNAL_KEY", raising=False)
    path = tmp_path / "journal.jsonl"
    path.write_text("not json\n", encoding="utf-8")
    assert not journal.verify_journal(path).ok


# --------------------------------------------------------------------------- E: доказательства


def test_e01_evidence_matches_current_engine():
    from mandat import ENGINE
    from mandat.intake.case_loader import load_case_dir

    evidence = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["engine"] == ENGINE, "пересоберите evidence.json: make evidence"
    for name, row in evidence["demo_cases"]["results"].items():
        report = verify_case(load_case_dir(ROOT / "examples" / "cases" / name), profile="demo")
        assert report.verdict.value == row["verdict"], name
        assert report.reproducibility_hash == row["reproducibility_hash"], name


# --------------------------------------------------------------------------- H: материалы для жюри

_FORBIDDEN_TRACES = re.compile("clau" + "de|anthr" + "opic|co-authored-by|/tmp/|ivan ivanov", re.IGNORECASE)
_SKIP_DIRS = {".git", ".venv", "venv", "var", "build", "dist", "node_modules", "__pycache__"}
_TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".toml", ".yml", ".yaml", ".html", ".cff", ".ps1", ".sh", ".js"}


def _repo_files():
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if path.is_file() and not any(p in _SKIP_DIRS or p.endswith((".egg-info", "_cache")) for p in rel.parts):
            yield path


def _office_xml(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        return "\n".join(
            archive.read(name).decode("utf-8", "ignore") for name in archive.namelist() if name.endswith(".xml")
        )


def _pdf_text(path: Path) -> str:
    raw = path.read_bytes()
    parts = [raw.decode("latin-1")]
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.DOTALL):
        try:
            parts.append(zlib.decompress(match.group(1)).decode("latin-1"))
        except zlib.error:
            continue
    return "\n".join(parts)


def test_h01_no_assistant_traces_or_local_paths_in_repository():
    hits = []
    for path in _repo_files():
        if path.name == Path(__file__).name:
            continue
        if path.suffix in _TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="ignore")
        elif path.suffix in {".pptx", ".docx", ".xlsx"}:
            text = _office_xml(path)
        elif path.suffix == ".pdf":
            text = _pdf_text(path)
        else:
            continue
        hits += [f"{path.relative_to(ROOT)}: {m.group(0)}" for m in _FORBIDDEN_TRACES.finditer(text)]
    assert not hits, "\n".join(hits[:20])


def _deck() -> Path:
    decks = sorted(ROOT.glob("*.pptx")) + sorted((ROOT / "submission").glob("**/*.pptx"))
    if not decks:
        pytest.skip("презентации в репозитории нет")
    return decks[0]


def test_h02_deck_images_have_meaningful_alt_text():
    xml = _office_xml(_deck())
    bad = [d for d in re.findall(r'descr="([^"]*)"', xml) if re.search(r"[/\\]|\.(png|jpe?g|svg|gif)$", d, re.I)]
    assert not bad, bad[:5]


def test_h03_deck_text_passes_claims_lint_and_has_no_stale_numbers():
    with zipfile.ZipFile(_deck()) as archive:
        text = " ".join(
            re.sub(r"<[^>]+>", " ", archive.read(name).decode("utf-8"))
            for name in archive.namelist()
            if re.match(r"ppt/(slides|notesSlides)/\w+\d+\.xml$", name)
        ).casefold()
    banned = [
        line.strip()
        for line in (ROOT / "BANNED_CLAIMS.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    stale = ["88 автотест", "v0.2.0", "murder board", "права на паузу"]
    hits = [p for p in banned + stale if p.casefold() in text]
    assert not hits, hits
