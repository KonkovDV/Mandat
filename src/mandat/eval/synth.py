"""Синтетические кейсы с внесёнными дефектами.

Все организации и люди вымышлены. Реквизиты строятся с корректными контрольными
числами, поэтому случайное совпадение с реальным ИНН возможно: данные не
предназначены для поиска по реестрам. Синтетика проверяет логику движка и
не равна точности на реальных документах (docs/EVALUATION.md).
"""

from __future__ import annotations

import hashlib
import heapq
import json
import random
import re
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mandat.domain.identifiers import make_inn10, make_inn12, make_ogrn, make_snils
from mandat.domain.model import Organization, Person
from mandat.intake.emchd import build_emchd_xml

OP_DATE = date(2026, 10, 20)
PAY = ("SYN-BANK-PAY-SIGN", "SYN.BANK.PAY.SIGN", "Подписание платёжных документов в интернет-банке")
PAY_OLD = ("SYN-BANK-PAY-SIGN-OLD", "SYN.BANK.PAY.SIGN.OLD", "Подписание платёжных документов (исключённый код)")
VIEW = ("SYN-BANK-STATEMENT", "SYN.BANK.STATEMENT", "Получение выписок по счетам")
ADMIN = ("SYN-BANK-USER-ADMIN", "SYN.BANK.USER.ADMIN", "Управление пользователями интернет-банка")

LAST = [
    "Иванов",
    "Петров",
    "Смирнов",
    "Кузнецов",
    "Соколов",
    "Попов",
    "Лебедев",
    "Козлов",
    "Новиков",
    "Морозов",
]
FIRST_M = ["Иван", "Пётр", "Алексей", "Сергей", "Дмитрий", "Олег"]
FIRST_F = ["Анна", "Мария", "Елена", "Ольга", "Ирина", "Татьяна"]
ORG_NAMES = ["Северный ветер", "Тихая гавань", "Речной порт", "Горный ключ", "Ясная поляна", "Синий мост"]

# Ожидаемый вердикт в профиле demo для каждого вида дефекта.
DEFECTS: dict[str, str] = {
    "ok": "needs_review",
    # Тот же кейс, но юрист отметил, что применимых ограничений устава нет: единственный путь к «подтверждено».
    "ok_charter_cleared": "confirmed",
    "ok_subdelegation": "needs_review",
    "ok_director": "needs_review",
    "director_changed_long_ago": "needs_review",
    "long_chain": "needs_review",
    "revoked": "refuted",
    "expired": "refuted",
    "no_term_expired": "refuted",
    "not_covered": "refuted",
    "subdelegation_forbidden": "refuted",
    "child_wider": "refuted",
    "child_longer": "refuted",
    "parent_revoked": "refuted",
    "lost_on_subdelegation": "refuted",
    "wrong_representative": "refuted",
    "signer_not_eio": "refuted",
    "org_mismatch": "refuted",
    "bad_checksum": "refuted",
    "liquidated": "refuted",
    "director_changed": "needs_review",
    "no_term": "needs_review",
    "text_authority": "needs_review",
    "charter_limit": "needs_review",
    "not_in_registry": "needs_review",
    "paper_poa": "needs_review",
    "joint_powers": "needs_review",
    "missing_1001": "needs_review",
    "name_mismatch": "needs_review",
    "code_revoked": "needs_review",
    "long_chain_unregistered": "needs_review",
    "registry_unavailable": "insufficient_data",
    "missing_parent": "insufficient_data",
    "malformed_xml": "insufficient_data",
    "xml_bomb": "insufficient_data",
    # Виды 0.3.0: пути к ложному «подтверждено», закреплённые в tests/test_regressions.py.
    "sub_signer_stranger": "refuted",
    "sub_child_before_parent": "refuted",
    "snils_of_other_person": "needs_review",
    "egrul_stale": "insufficient_data",
    "egrul_no_status": "insufficient_data",
    "fresh_director_admin": "needs_review",
    "duplicate_egrul": "insufficient_data",
    "same_number_twin": "insufficient_data",
    "ogrn_of_other_company": "refuted",
    "guid_case_transfer": "refuted",
    "leaf_tie": "insufficient_data",
    "charter_limit_tys": "needs_review",
    "charter_limit_and_more": "needs_review",
    "charter_second_signature": "needs_review",
}
# Новые виды строятся мутацией базового сценария: вид → базовый вид.
DERIVED: dict[str, str] = {
    "sub_signer_stranger": "ok_subdelegation",
    "sub_child_before_parent": "ok_subdelegation",
    "snils_of_other_person": "ok_charter_cleared",
    "egrul_stale": "ok_charter_cleared",
    "egrul_no_status": "ok_charter_cleared",
    "fresh_director_admin": "ok_director",
    "duplicate_egrul": "ok_charter_cleared",
    "same_number_twin": "ok_charter_cleared",
    "ogrn_of_other_company": "ok_charter_cleared",
    "guid_case_transfer": "ok_charter_cleared",
    "leaf_tie": "ok_charter_cleared",
    "charter_limit_tys": "ok_charter_cleared",
    "charter_limit_and_more": "ok_charter_cleared",
    "charter_second_signature": "ok_charter_cleared",
}
CHAIN_DEFECTS = {
    "ok_subdelegation",
    "subdelegation_forbidden",
    "child_wider",
    "child_longer",
    "missing_parent",
    "parent_revoked",
    "long_chain",
    "long_chain_unregistered",
}

XML_BOMB = (
    '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
    '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]><Доверенность>&lol2;</Доверенность>'
)


def full_org_name(short: str | None) -> str:
    """Полное наименование для синтетики: «ООО «X»» → «ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ «X»»."""
    text = short or ""
    return "ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ " + text[4:] if text.startswith("ООО ") else text


@dataclass
class World:
    rng: random.Random
    org: Organization
    director: Person
    accountant: Person
    deputy: Person
    stranger: Person
    clerk1: Person
    clerk2: Person

    @classmethod
    def make(cls, seed: int, org_seed: int | None = None) -> World:
        """Мир кейса. ``org_seed`` — общий клиент: одна организация и один руководитель на много кейсов."""
        rng = random.Random(seed)  # noqa: S311 — синтетика, не криптография

        def person(female: bool, position: str | None = None) -> Person:
            last = rng.choice(LAST) + ("а" if female else "")
            first = rng.choice(FIRST_F if female else FIRST_M)
            middle = rng.choice(
                ["Ивановна", "Сергеевна", "Петровна"] if female else ["Иванович", "Сергеевич", "Петрович"]
            )
            inn = make_inn12(f"77{rng.randrange(10**7, 10**8)}")
            snils = make_snils(f"{rng.randrange(2 * 10**8, 9 * 10**8)}")
            return Person(f"{last} {first} {middle}", inn, snils, position)

        prefix = f"77{rng.randrange(10**6, 10**7)}"
        org = Organization(
            inn=make_inn10(prefix),
            ogrn=make_ogrn(f"1257700{rng.randrange(10**4, 10**5)}"),
            kpp=f"77{rng.randrange(10, 99)}01001",
            name=f"ООО «{rng.choice(ORG_NAMES)}»",
        )
        world = cls(
            rng=rng,
            org=org,
            director=person(False, "Генеральный директор"),
            # Должности нужны МЧД в порядке передоверия: её подписывает представитель, и должность
            # подписанта у такой МЧД — открытый вопрос формата (без неё проверка уходит на разбор).
            accountant=person(True, "Главный бухгалтер"),
            deputy=person(False, "Заместитель генерального директора"),
            stranger=person(False),
            clerk1=person(True, "Специалист"),
            clerk2=person(False, "Специалист"),
        )
        if org_seed is not None:
            # Реквизиты и руководитель — из seed клиента, остальные люди и номера МЧД — из seed кейса:
            # последовательность rng та же, что без org_seed, и кейсы одного клиента различаются представителями.
            company = cls.make(org_seed)
            world.org, world.director = company.org, company.director
        return world

    @property
    def org_full_name(self) -> str:
        return full_org_name(self.org.name)


@dataclass
class Scenario:
    case_id: str
    defect: str
    expected: str
    manifest: dict[str, Any]
    files: dict[str, bytes] = field(default_factory=dict)
    # Как кейс получен в потоке make_case: вид, seed и условия. Пусто у кейсов build().
    recipe: dict[str, Any] = field(default_factory=dict)

    def inline_manifest(self) -> dict[str, Any]:
        docs = []
        for doc in self.manifest["documents"]:
            raw = self.files[doc["path"]].decode("utf-8")
            content: Any = json.loads(raw) if doc["kind"] == "egrul_json" else raw
            docs.append({"id": doc["id"], "kind": doc["kind"], "content": content})
        return {**{k: v for k, v in self.manifest.items() if k != "documents"}, "documents": docs}


def _egrul(
    world: World,
    *,
    status: str = "active",
    changed: Person | None = None,
    changed_on: date = date(2026, 10, 1),
    unreliable: bool = False,
) -> dict[str, Any]:
    executives = [
        {
            "full_name": world.director.full_name,
            "inn": world.director.inn,
            "position": "Генеральный директор",
            "grn": "2247700123456",
            "record_date": "2024-05-01",
            "valid_from": "2024-05-01",
            "valid_to": (changed_on - timedelta(days=1)).isoformat() if changed else None,
            "unreliable": unreliable,
        }
    ]
    if changed:
        executives.append(
            {
                "full_name": changed.full_name,
                "inn": changed.inn,
                "position": "Генеральный директор",
                "grn": "2267700654321",
                "record_date": changed_on.isoformat(),
                "valid_from": changed_on.isoformat(),
                "valid_to": None,
            }
        )
    return {
        "inn": world.org.inn,
        "ogrn": world.org.ogrn,
        "kpp": world.org.kpp,
        "name": world.org.name,
        "full_name": world.org_full_name,
        "status": status,
        "extracted_at": "2026-10-19",
        "executives": executives,
    }


def build(defect: str, seed: int = 1, *, org_seed: int | None = None) -> Scenario:
    if defect not in DEFECTS:
        raise ValueError(f"неизвестный дефект {defect!r}")
    if defect in DERIVED:
        return _derive(defect, build(DERIVED[defect], seed, org_seed=org_seed), World.make(seed, org_seed))
    w = World.make(seed, org_seed)
    # Номера МЧД зависят и от вида дефекта: у демо-кейсов одной организации разные доверенности.
    salt = int.from_bytes(hashlib.sha256(defect.encode()).digest()[:16], "big")

    def new_number() -> str:
        return str(uuid.UUID(int=w.rng.getrandbits(128) ^ salt))

    number = new_number()
    child_number = new_number()
    issue, until = date(2026, 3, 1), date(2027, 2, 28)
    requester = w.accountant
    codes = [PAY, VIEW]
    files: dict[str, bytes] = {}
    docs: list[dict[str, str]] = []
    registry: dict[str, str] = {number: "active"}
    egrul = _egrul(w)
    amount = 480_000

    principal = Organization(inn=w.org.inn, ogrn=w.org.ogrn, kpp=w.org.kpp, name=w.org_full_name)
    signer = w.director
    if defect == "org_mismatch":
        principal = Organization(
            inn=make_inn10("770000001"),
            ogrn=make_ogrn("125770000001"),
            kpp=w.org.kpp,
            name="ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ «Другая компания»",
        )
    if defect == "name_mismatch":
        principal = Organization(inn=w.org.inn, ogrn=w.org.ogrn, kpp=w.org.kpp, name="ООО «Похожее название»")
    if defect == "signer_not_eio":
        signer = Person(w.stranger.full_name, w.stranger.inn, w.stranger.snils, "Генеральный директор")
    if defect == "bad_checksum":
        principal = Organization(
            inn=(w.org.inn or "")[:-1] + str((int((w.org.inn or "0")[-1]) + 1) % 10),
            ogrn=w.org.ogrn,
            kpp=w.org.kpp,
            name=w.org_full_name,
        )
    if defect == "not_covered":
        codes = [VIEW]
    if defect == "code_revoked":
        codes = [PAY_OLD, VIEW]
    if defect == "missing_1001":
        codes = [(code, mnemonic, "") for code, mnemonic, _ in codes]
    if defect == "expired":
        until = OP_DATE - timedelta(days=10)
    if defect == "no_term_expired":
        issue = date(2025, 9, 1)
    if defect in {"revoked", "parent_revoked"}:
        registry[number] = "revoked"
    if defect == "registry_unavailable":
        registry.pop(number)
    if defect == "not_in_registry":
        registry[number] = "not_found"
    if defect == "director_changed":
        egrul = _egrul(w, changed=w.stranger)
    if defect == "director_changed_long_ago":
        egrul = _egrul(w, changed=w.stranger, changed_on=date(2026, 5, 1))
    if defect == "liquidated":
        egrul = _egrul(w, status="liquidated")

    chain_case = defect in CHAIN_DEFECTS
    delegates = chain_case or defect == "lost_on_subdelegation"
    text = (
        ["Представлять интересы общества в банке, в том числе подписывать платёжные документы до 500 000 рублей"]
        if defect == "text_authority"
        else []
    )
    root_codes = [] if defect == "text_authority" else codes
    root_reps = [w.deputy] if delegates else [requester]
    if defect == "joint_powers":
        root_reps = [requester, w.deputy]
    sub_code = "1" if defect == "subdelegation_forbidden" else ("3" if delegates else "1")

    if defect not in {"ok_director", "paper_poa"}:
        root_xml = build_emchd_xml(
            number=number,
            issue_date=issue,
            valid_until=None if defect in {"no_term", "no_term_expired"} else until,
            principal=principal,
            signer=signer,
            representatives=root_reps,
            machine_codes=root_codes,
            text_authorities=text,
            subdelegation_code=sub_code,
            joint_code="2" if defect == "joint_powers" else "1",
        )
        if defect == "malformed_xml":
            root_xml = root_xml.replace("</Доверенность>", "")
        if defect == "xml_bomb":
            root_xml = XML_BOMB
        if defect != "missing_parent":
            files["poa-root.xml"] = root_xml.encode("utf-8")
            docs.append({"id": "poa-root", "kind": "emchd_xml", "path": "poa-root.xml"})

    if defect == "lost_on_subdelegation":
        # Заместитель передал полномочия бухгалтеру с утратой (ПрУтрПолн = 2) и сам пришёл подписывать.
        lost_xml = build_emchd_xml(
            number=child_number,
            issue_date=date(2026, 4, 1),
            valid_until=until - timedelta(days=30),
            principal=principal,
            signer=w.deputy,
            representatives=[w.accountant],
            machine_codes=codes,
            subdelegation_code="1",
            parent_number=number,
            original_number=number,
            loses_authority_code="2",
        )
        files["poa-child.xml"] = lost_xml.encode("utf-8")
        docs.append({"id": "poa-child", "kind": "emchd_xml", "path": "poa-child.xml"})
        registry[child_number] = "active"
        requester = w.deputy

    if chain_case:
        long = defect in {"long_chain", "long_chain_unregistered"}
        holders = [w.deputy, w.clerk1, w.clerk2, requester] if long else [w.deputy, requester]
        parent = number
        for step in range(1, len(holders)):
            last = step == len(holders) - 1
            link_number = child_number if last else new_number()
            link_codes = codes + [ADMIN] if defect == "child_wider" and last else codes
            if defect == "child_longer" and last:
                link_until = until + timedelta(days=60)
            else:
                link_until = until - timedelta(days=30 * step)
            xml = build_emchd_xml(
                number=link_number,
                issue_date=date(2026, 3 + step, 1),
                valid_until=link_until,
                principal=principal,
                signer=holders[step - 1],
                representatives=[holders[step]],
                machine_codes=link_codes,
                subdelegation_code="1" if last else "3",
                parent_number=parent,
                original_number=number,
                loses_authority_code="1",
            )
            name = "poa-child.xml" if last else f"poa-link{step}.xml"
            files[name] = xml.encode("utf-8")
            docs.append({"id": name.removesuffix(".xml"), "kind": "emchd_xml", "path": name})
            registry[link_number] = "not_found" if defect == "long_chain_unregistered" and last else "active"
            parent = link_number

    if defect == "ok_director":
        requester = w.director
    if defect == "wrong_representative":
        requester = w.stranger
    if defect == "paper_poa":
        files["paper-poa.txt"] = (
            f"ДОВЕРЕННОСТЬ. {w.org.name} в лице генерального директора {w.director.full_name} уполномочивает "
            f"{requester.full_name} подписывать платёжные документы в банке. Срок — до 28.02.2027."
        ).encode()
        docs.append({"id": "paper-poa", "kind": "paper_poa_text", "path": "paper-poa.txt"})

    charter = "Устав общества. Генеральный директор действует без доверенности от имени общества."
    if defect == "charter_limit":
        charter += (
            " Сделки на сумму свыше 300 000 рублей совершаются генеральным директором только после "
            "одобрения общим собранием участников."
        )
        amount = 480_000
    files["charter.txt"] = charter.encode("utf-8")
    docs.append({"id": "charter", "kind": "charter_text", "path": "charter.txt"})
    files["egrul.json"] = json.dumps(egrul, ensure_ascii=False, indent=2).encode("utf-8")
    docs.append({"id": "egrul", "kind": "egrul_json", "path": "egrul.json"})

    manifest: dict[str, Any] = {
        "case_id": f"{defect}-{seed}",
        "organization": {"inn": w.org.inn, "ogrn": w.org.ogrn, "name": w.org.name},
        "requester": {"full_name": requester.full_name, "inn": requester.inn, "snils": requester.snils},
        "operation": {"code": "PAYMENT_SIGN", "date": OP_DATE.isoformat(), "amount_rub": amount},
        "documents": docs,
        "registry": registry,
    }
    if defect == "ok_charter_cleared":
        manifest["charter_disposition"] = "no_applicable_restriction"
    return Scenario(manifest["case_id"], defect, DEFECTS[defect], manifest, files)


_SV = "Документ/Довер/СвДов"
_SIGNER = "Документ/Довер/СвДоверит/Доверит/РосОргДовер/ЛицоБезДов/СвФЛ"


def _xml_edit(raw: bytes, path: str, **attrs: str) -> bytes:
    import xml.etree.ElementTree as ET  # синтетика пишет XML сама, внешних данных здесь нет

    root = ET.fromstring(raw)  # noqa: S314
    element = root.find(path)
    assert element is not None, path
    for name, value in attrs.items():
        element.set(name, value)
    return ET.tostring(root, encoding="unicode").encode("utf-8")


def _derive(variant: str, base: Scenario, w: World) -> Scenario:
    """Мутация базового сценария под вид дефекта 0.3.0. Ожидаемый вердикт — в ``DEFECTS``."""
    manifest = json.loads(json.dumps(base.manifest))
    files = dict(base.files)
    egrul = json.loads(files["egrul.json"])
    if variant == "sub_signer_stranger":
        files["poa-child.xml"] = _xml_edit(
            files["poa-child.xml"], _SIGNER, ИННФЛ=w.stranger.inn or "", СНИЛС=w.stranger.snils or ""
        )
    elif variant == "sub_child_before_parent":
        files["poa-child.xml"] = _xml_edit(files["poa-child.xml"], _SV, ДатаВыдДовер="2026-02-01")
    elif variant == "snils_of_other_person":
        manifest["requester"]["snils"] = w.stranger.snils
    elif variant == "egrul_stale":
        egrul["extracted_at"] = "2024-06-01"
    elif variant == "egrul_no_status":
        egrul.pop("status")
    elif variant == "fresh_director_admin":
        egrul["executives"][0]["valid_to"] = (OP_DATE - timedelta(days=3)).isoformat()
        start = (OP_DATE - timedelta(days=2)).isoformat()
        egrul["executives"].append(
            {
                "full_name": w.stranger.full_name,
                "inn": w.stranger.inn,
                "grn": "2267700777777",
                "record_date": start,
                "valid_from": start,
                "valid_to": None,
            }
        )
        manifest["requester"] = {"full_name": w.stranger.full_name, "inn": w.stranger.inn, "snils": w.stranger.snils}
        manifest["operation"]["code"] = "USER_ADMIN"
    elif variant == "duplicate_egrul":
        twin = {**egrul, "status": "liquidated"}
        files["egrul-0.json"] = json.dumps(twin, ensure_ascii=False, indent=2).encode("utf-8")
        manifest["documents"].insert(0, {"id": "egrul-0", "kind": "egrul_json", "path": "egrul-0.json"})
    elif variant == "same_number_twin":
        files["poa-twin.xml"] = _xml_edit(files["poa-root.xml"], _SV, ПрПередов="3")
        manifest["documents"].insert(0, {"id": "poa-twin", "kind": "emchd_xml", "path": "poa-twin.xml"})
    elif variant == "ogrn_of_other_company":
        egrul["ogrn"] = make_ogrn("102770013219")
    elif variant == "guid_case_transfer":
        import xml.etree.ElementTree as ET

        found = ET.fromstring(files["poa-root.xml"]).find(_SV)  # noqa: S314
        root_number = found.get("НомДовер") if found is not None else None
        principal = Organization(inn=w.org.inn, ogrn=w.org.ogrn, kpp=w.org.kpp, name=w.org_full_name)
        files["poa-transfer.xml"] = build_emchd_xml(
            number="bbbbbbbb-0000-0000-0000-000000000001",
            issue_date=date(2026, 4, 1),
            valid_until=date(2027, 1, 1),
            principal=principal,
            signer=w.accountant,
            representatives=[w.clerk1],
            machine_codes=[PAY],
            parent_number=(root_number or "").upper(),
            loses_authority_code="2",
        ).encode("utf-8")
        manifest["documents"].append({"id": "poa-transfer", "kind": "emchd_xml", "path": "poa-transfer.xml"})
    elif variant == "leaf_tie":
        twin_number = "cccccccc-0000-0000-0000-000000000001"
        files["poa-twin.xml"] = _xml_edit(files["poa-root.xml"], _SV, НомДовер=twin_number)
        manifest["documents"].append({"id": "poa-twin", "kind": "emchd_xml", "path": "poa-twin.xml"})
        manifest["registry"][twin_number] = "revoked"
    elif variant.startswith("charter_"):
        extra = {
            "charter_limit_tys": "Сделки на сумму свыше 300 тыс. руб. требуют одобрения общего собрания участников.",
            "charter_limit_and_more": (
                "Сделки на сумму 300 000 рублей и более совершаются только с согласия участников."
            ),
            "charter_second_signature": "Для платёжных документов обязательна вторая подпись главного бухгалтера.",
        }[variant]
        files["charter.txt"] = files["charter.txt"] + (" " + extra).encode("utf-8")
    files["egrul.json"] = json.dumps(egrul, ensure_ascii=False, indent=2).encode("utf-8")
    manifest["case_id"] = f"{variant}-{base.case_id.rsplit('-', 1)[-1]}"
    return Scenario(manifest["case_id"], variant, DEFECTS[variant], manifest, files)


# --- Поток, похожий на банковский: смесь видов и пограничные условия (data/load_profiles.json) ---------------
#
# run_corpus берёт каждый вид поровну: так проверяется логика каждого правила. Поток банка устроен иначе: почти
# все пакеты чистые, дефекты — длинный хвост, и есть условия, которых в ровном корпусе нет: конец операционного
# дня, часовые пояса регионов, крупные клиенты с сотнями представителей, повторные проверки той же МЧД.
# Доли берутся из профиля, у каждой доли есть обоснование (basis): допущение или ссылка на источник.

MOSCOW = timezone(timedelta(hours=3), "MSK")  # дата операции — календарный день банка, по Москве (UTC+3)
# Виды поверх готового сценария, которых нет в DEFECTS: run_corpus и evidence.json от них не меняются.
# Вид → (базовый вид, где меняется, значение, ожидаемый вердикт профиля demo).
OVERLAYS: dict[str, tuple[str, str, str, str]] = {
    # Банкротство доверителя по ЕФРСБ (подп. 7 п. 1 ст. 188 ГК РФ; 127-ФЗ).
    "bankruptcy_proceedings": ("ok_charter_cleared", "bankruptcy", "bankruptcy_proceedings", "refuted"),  # ст. 126
    "external_management": ("ok_charter_cleared", "bankruptcy", "external_management", "refuted"),  # ст. 94
    "bankruptcy_observation": ("ok_charter_cleared", "bankruptcy", "observation", "needs_review"),  # ст. 64
    # Статус организации в ЕГРЮЛ.
    "in_liquidation": ("ok_charter_cleared", "egrul", "in_liquidation", "needs_review"),
    "reorganizing": ("ok_charter_cleared", "egrul", "reorganizing", "needs_review"),
    "terminated": ("ok_charter_cleared", "egrul", "terminated", "refuted"),  # подп. 4 п. 1 ст. 188 ГК РФ
}
# Длинные цепочки передоверия: вид → число МЧД. До domain.graph.MAX_CHAIN_LINKS (10) цепочка разбирается
# и ведёт себя как long_chain; длиннее — автоматически не разбирается, нужна сверка звеньев.
CHAIN_DEPTHS: dict[str, tuple[int, str]] = {
    "deep_chain_8": (8, "needs_review"),
    "deep_chain_12": (12, "insufficient_data"),
}
KINDS: dict[str, str] = {
    **DEFECTS,
    **{kind: spec[3] for kind, spec in OVERLAYS.items()},
    **{kind: spec[1] for kind, spec in CHAIN_DEPTHS.items()},
}
CONDITIONS = ("many_representatives", "repeat_check", "registry_moment", "term_last_day", "term_ended_yesterday")
# Сценарии с одной МЧД «poa-root.xml», у которых срок можно сдвинуть на день операции или на день раньше.
TERM_BASES = frozenset(
    {
        "ok",
        "ok_charter_cleared",
        "director_changed",
        "director_changed_long_ago",
        "not_covered",
        "charter_limit",
        "code_revoked",
        "name_mismatch",
        "revoked",
        "registry_unavailable",
        "snils_of_other_person",
        "bankruptcy_observation",
        "in_liquidation",
    }
)
# Момент запроса к реестру не добавляется, где запись реестра не относится к МЧД кейса (нет МЧД, бумажная
# доверенность, нет родителя, двойники) или статус в реестре не «действует» и не «отменена».
MOMENT_EXCLUDED = frozenset(
    {
        "ok_director",
        "fresh_director_admin",
        "paper_poa",
        "missing_parent",
        "malformed_xml",
        "xml_bomb",
        "registry_unavailable",
        "not_in_registry",
        "long_chain_unregistered",
        "same_number_twin",
        "leaf_tie",
        "guid_case_transfer",
    }
)
_SOURCE_ID = re.compile(r"\b[MC]-\d{2}\b")
_OFFSET = re.compile(r"[+-](0\d|1[0-4]):[0-5]\d")
_VERDICTS = frozenset({"confirmed", "refuted", "needs_review", "insufficient_data"})


def _max_snapshot_age_days() -> int:
    from mandat.rules.pack import default_pack

    return int(default_pack().get("R-REG-001").param("max_snapshot_age_days"))


def _person(rng: random.Random, position: str) -> Person:
    """Ещё один вымышленный сотрудник: для длинных цепочек людей мира кейса не хватает."""
    last, first = rng.choice(LAST), rng.choice(FIRST_M)
    inn = make_inn12(f"77{rng.randrange(10**7, 10**8)}")
    snils = make_snils(f"{rng.randrange(2 * 10**8, 9 * 10**8)}")
    return Person(f"{last} {first} {rng.choice(['Иванович', 'Сергеевич', 'Петрович'])}", inn, snils, position)


def _deep_chain(kind: str, seed: int, org_seed: int | None) -> Scenario:
    """Цепочка из ``links`` МЧД: руководитель → заместитель → специалисты → бухгалтер, все звенья в реестре."""
    links, expected = CHAIN_DEPTHS[kind]
    w = World.make(seed, org_seed)
    principal = Organization(inn=w.org.inn, ogrn=w.org.ogrn, kpp=w.org.kpp, name=w.org_full_name)
    holders = [w.deputy, *(_person(w.rng, "Специалист") for _ in range(links - 2)), w.accountant]
    numbers = [str(uuid.UUID(int=w.rng.getrandbits(128))) for _ in range(links)]
    until = date(2027, 2, 28)
    files: dict[str, bytes] = {}
    docs: list[dict[str, str]] = []
    for step in range(links):
        last = step == links - 1
        xml = build_emchd_xml(
            number=numbers[step],
            issue_date=date(2026, 3, 1) + timedelta(days=7 * step),
            valid_until=until - timedelta(days=step),
            principal=principal,
            signer=w.director if step == 0 else holders[step - 1],
            representatives=[holders[step]],
            machine_codes=[PAY, VIEW],
            subdelegation_code="1" if last else "3",
            parent_number=numbers[step - 1] if step else None,
            original_number=numbers[0] if step else None,
            loses_authority_code="1" if step else None,
        )
        name = "poa-root.xml" if step == 0 else ("poa-child.xml" if last else f"poa-link{step}.xml")
        files[name] = xml.encode("utf-8")
        docs.append({"id": name.removesuffix(".xml"), "kind": "emchd_xml", "path": name})
    files["charter.txt"] = "Устав общества. Генеральный директор действует без доверенности от имени общества.".encode()
    docs.append({"id": "charter", "kind": "charter_text", "path": "charter.txt"})
    files["egrul.json"] = json.dumps(_egrul(w), ensure_ascii=False, indent=2).encode("utf-8")
    docs.append({"id": "egrul", "kind": "egrul_json", "path": "egrul.json"})
    requester = w.accountant
    manifest: dict[str, Any] = {
        "case_id": f"{kind}-{seed}",
        "organization": {"inn": w.org.inn, "ogrn": w.org.ogrn, "name": w.org.name},
        "requester": {"full_name": requester.full_name, "inn": requester.inn, "snils": requester.snils},
        "operation": {"code": "PAYMENT_SIGN", "date": OP_DATE.isoformat(), "amount_rub": 480_000},
        "documents": docs,
        "registry": dict.fromkeys(numbers, "active"),
    }
    return Scenario(manifest["case_id"], kind, expected, manifest, files)


def _build_kind(kind: str, seed: int, org_seed: int | None) -> Scenario:
    if kind in CHAIN_DEPTHS:
        return _deep_chain(kind, seed, org_seed)
    if kind not in OVERLAYS:
        return build(kind, seed, org_seed=org_seed)
    base_kind, where, value, expected = OVERLAYS[kind]
    base = build(base_kind, seed, org_seed=org_seed)
    manifest = json.loads(json.dumps(base.manifest))
    files = dict(base.files)
    if where == "bankruptcy":
        manifest["bankruptcy"] = {manifest["organization"]["inn"]: value}
    else:
        egrul = json.loads(files["egrul.json"])
        egrul["status"] = value
        files["egrul.json"] = json.dumps(egrul, ensure_ascii=False, indent=2).encode("utf-8")
    manifest["case_id"] = f"{kind}-{seed}"
    return Scenario(manifest["case_id"], kind, expected, manifest, files)


def moscow_age_days(moment: str) -> int:
    """Сколько календарных дней по Москве между моментом запроса к реестру и днём операции."""
    return (OP_DATE - datetime.fromisoformat(moment).astimezone(MOSCOW).date()).days


def make_case(recipe: Mapping[str, Any]) -> Scenario:
    """Кейс потока по рецепту: вид, seed, общий клиент и пограничные условия. Один рецепт — один и тот же кейс.

    Ожидаемый вердикт выводится из вида и условий по нормам, а не из ответа движка:
    * ``term`` = ``last_day`` — операция в последний день срока МЧД: действие можно совершить до 24 часов
      последнего дня срока (п. 1 ст. 194 ГК РФ), вердикт вида не меняется; ``ended_yesterday`` — срок истёк
      накануне (подп. 1 п. 1 ст. 188 ГК РФ): «опровергнуто»;
    * ``registry_moment`` — момент запроса к реестру с часовым поясом, в котором его записала система региона.
      Возраст снимка считается в днях банка, по Москве: тот же момент, записанный в другом поясе, — тот же
      снимок. Снимок «действует» старше ``max_snapshot_age_days`` правила R-REG-001 не подтверждает МЧД на
      дату операции: вместо «подтверждено» и «на разбор» — «недостаточно данных».
    """
    kind, seed = str(recipe["kind"]), int(recipe["seed"])
    if kind not in KINDS:
        raise ValueError(f"неизвестный вид {kind!r}")
    org_seed = recipe.get("org_seed")
    scenario = _build_kind(kind, seed, None if org_seed is None else int(org_seed))
    expected = scenario.expected
    manifest = json.loads(json.dumps(scenario.manifest))
    files = dict(scenario.files)
    term = recipe.get("term")
    if term is not None:
        if kind not in TERM_BASES or term not in {"last_day", "ended_yesterday"}:
            raise ValueError(f"term={term!r} не применяется к виду {kind!r}")
        until = OP_DATE if term == "last_day" else OP_DATE - timedelta(days=1)
        files["poa-root.xml"] = _xml_edit(files["poa-root.xml"], _SV, СрокДейст=until.isoformat())
        expected = expected if term == "last_day" else "refuted"
    moment = recipe.get("registry_moment")
    if moment is not None:
        if kind in MOMENT_EXCLUDED:
            raise ValueError(f"registry_moment не применяется к виду {kind!r}")
        statuses = [str(v) for v in manifest["registry"].values()]
        manifest["registry"] = {n: {"status": s, "observed_at": moment} for n, s in manifest["registry"].items()}
        stale = moscow_age_days(str(moment)) > _max_snapshot_age_days()
        if stale and expected in {"confirmed", "needs_review"} and all(s == "active" for s in statuses):
            expected = "insufficient_data"
    case_id = str(recipe.get("case_id") or scenario.case_id)
    manifest["case_id"] = case_id
    return Scenario(case_id, kind, expected, manifest, files, recipe=dict(recipe))


def conditions_of(recipe: Mapping[str, Any]) -> list[str]:
    """Названия условий рецепта для сводок: общий клиент, повтор, момент реестра (с поясом), срок."""
    out = []
    if recipe.get("org_seed") is not None:
        out.append("many_representatives")
    if recipe.get("repeat_of"):
        out.append("repeat_check")
    moment = recipe.get("registry_moment")
    if moment is not None:
        out.append(f"registry_moment{str(moment)[-6:]}")
    if recipe.get("term"):
        out.append("term_" + str(recipe["term"]))
    return out


def _share(problems: list[str], where: str, value: Any) -> None:
    if not isinstance(value, int | float) or isinstance(value, bool) or not 0 <= value <= 1:
        problems.append(f"{where}: доля от 0 до 1")


def _weights(problems: list[str], where: str, value: Any, key_ok: Any) -> None:
    if not isinstance(value, dict) or not value:
        problems.append(f"{where}: непустой объект «значение → доля»")
        return
    for key, weight in value.items():
        if not key_ok(key):
            problems.append(f"{where}: недопустимый ключ {key!r}")
        _share(problems, f"{where}.{key}", weight)
    if all(isinstance(w, int | float) for w in value.values()) and abs(sum(value.values()) - 1) > 1e-9:
        problems.append(f"{where}: доли в сумме {sum(value.values()):.6f}, нужна 1")


def _basis(problems: list[str], where: str, value: Any) -> None:
    """Каждая доля названа допущением или ссылается на источник реестра утверждений (M-xx, C-xx)."""
    if not isinstance(value, str) or not (value.startswith("допущение") or _SOURCE_ID.search(value)):
        problems.append(f"{where}.basis: «допущение: …» или ссылка на источник (M-xx, C-xx)")


def validate_mixes(payload: Any) -> list[str]:
    """Ошибки файла профилей потока; пустой список — файл годен."""
    if not isinstance(payload, dict) or not isinstance(payload.get("profiles"), dict) or not payload["profiles"]:
        return ["нужен объект с непустым profiles"]
    problems: list[str] = []
    for name, spec in payload["profiles"].items():
        if not isinstance(spec, dict):
            problems.append(f"{name}: профиль — объект")
            continue
        extra = set(spec) - {"description", "seed", "mix", "conditions"}
        if extra:
            problems.append(f"{name}: лишние поля {sorted(extra)}")
        if not isinstance(spec.get("description"), str) or not spec["description"].strip():
            problems.append(f"{name}.description: нужен текст")
        if not isinstance(spec.get("seed"), int) or isinstance(spec.get("seed"), bool):
            problems.append(f"{name}.seed: целое число")
        mix = spec.get("mix")
        if not isinstance(mix, list) or not mix:
            problems.append(f"{name}.mix: непустой список")
            mix = []
        seen: set[str] = set()
        for i, entry in enumerate(mix):
            where = f"{name}.mix[{i}]"
            if not isinstance(entry, dict) or set(entry) - {"kind", "group", "weight", "basis"}:
                problems.append(f"{where}: поля kind, group, weight, basis")
                continue
            kind = entry.get("kind")
            if kind not in KINDS:
                problems.append(f"{where}.kind: неизвестный вид {kind!r}")
            elif kind in seen:
                problems.append(f"{where}.kind: {kind} указан дважды")
            seen.add(str(kind))
            if not isinstance(entry.get("group"), str) or not entry["group"].strip():
                problems.append(f"{where}.group: нужна группа")
            weight = entry.get("weight")
            if not isinstance(weight, int | float) or isinstance(weight, bool) or not 0 < weight <= 1:
                problems.append(f"{where}.weight: доля больше 0 и не больше 1")
            _basis(problems, where, entry.get("basis"))
        weights = [e.get("weight") for e in mix if isinstance(e, dict)]
        numbers = [float(w) for w in weights if isinstance(w, int | float)]
        if numbers and len(numbers) == len(weights) and abs(sum(numbers) - 1) > 1e-9:
            problems.append(f"{name}.mix: доли в сумме {sum(numbers):.6f}, нужна 1")
        conditions = spec.get("conditions")
        if not isinstance(conditions, dict):
            problems.append(f"{name}.conditions: объект")
            continue
        for cond, params in conditions.items():
            where = f"{name}.conditions.{cond}"
            allowed = {
                "many_representatives": {"share", "size", "basis"},
                "repeat_check": {"share", "max_repeats", "max_gap", "basis"},
                "registry_moment": {"share", "age_days", "end_of_day", "offsets", "basis"},
                "term_last_day": {"share", "basis"},
                "term_ended_yesterday": {"share", "basis"},
            }.get(cond)
            if allowed is None:
                problems.append(f"{where}: неизвестное условие, есть {', '.join(CONDITIONS)}")
                continue
            if not isinstance(params, dict) or set(params) != allowed:
                problems.append(f"{where}: поля {sorted(allowed)}")
                continue
            _share(problems, f"{where}.share", params["share"])
            _basis(problems, where, params["basis"])
            if cond == "many_representatives":
                size = params["size"]
                if not (isinstance(size, list) and len(size) == 2 and all(isinstance(x, int) for x in size)) or not (
                    2 <= size[0] <= size[1]
                ):
                    problems.append(f"{where}.size: [от, до], 2 ≤ от ≤ до")
            if cond == "repeat_check":
                for key in ("max_repeats", "max_gap"):
                    if not isinstance(params[key], int) or isinstance(params[key], bool) or params[key] < 1:
                        problems.append(f"{where}.{key}: целое не меньше 1")
            if cond == "registry_moment":
                _share(problems, f"{where}.end_of_day", params["end_of_day"])
                _weights(problems, f"{where}.age_days", params["age_days"], lambda k: k.isdigit() and int(k) <= 30)
                _weights(problems, f"{where}.offsets", params["offsets"], lambda k: bool(_OFFSET.fullmatch(k)))
        term_total = sum(
            p.get("share", 0)
            for c, p in conditions.items()
            if c.startswith("term_") and isinstance(p, dict) and isinstance(p.get("share"), int | float)
        )
        if term_total > 1:
            problems.append(f"{name}.conditions: доли term_* в сумме больше 1")
    return problems


def load_mixes(path: Path | None = None) -> dict[str, Any]:
    """Профили потока из ``data/load_profiles.json`` или из файла банка; негодный файл — ``ValueError``."""
    from mandat.data import load_json

    payload = json.loads(path.read_text(encoding="utf-8")) if path else load_json("load_profiles.json")
    problems = validate_mixes(payload)
    if problems:
        raise ValueError("профили потока: " + "; ".join(problems))
    profiles: dict[str, Any] = payload["profiles"]
    return profiles


def _pick(rng: random.Random, weights: Mapping[str, float]) -> str:
    return rng.choices(list(weights), list(weights.values()))[0]


def _draw_moment(rng: random.Random, spec: Mapping[str, Any]) -> str:
    """Момент запроса к реестру: день по Москве, время рабочего дня или конец дня, пояс системы региона."""
    day = OP_DATE - timedelta(days=int(_pick(rng, spec["age_days"])))
    if rng.random() < spec["end_of_day"]:
        second = 23 * 3600 + 30 * 60 + rng.randrange(1800)  # последние полчаса операционного дня
    else:
        second = 9 * 3600 + rng.randrange(10 * 3600)  # с 9 до 19 по Москве
    moment = datetime(day.year, day.month, day.day, tzinfo=MOSCOW) + timedelta(seconds=second)
    offset = _pick(rng, spec["offsets"])
    sign = -1 if offset.startswith("-") else 1
    zone = timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[4:6])))
    return moment.astimezone(zone).isoformat(timespec="seconds")


def production_recipes(
    cases: int = 10_000,
    mix: str = "vtb_prod",
    *,
    seed: int | None = None,
    profiles: Mapping[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Рецепты ``cases`` кейсов потока по профилю ``mix``: тот же профиль и seed — тот же поток.

    Повторная проверка той же МЧД — копия рецепта с новым ``case_id``, она приходит позже, через 1–``max_gap``
    кейсов. Кейсы общего клиента идут подряд по мере выдачи: у клиента от ``size[0]`` до ``size[1]`` представителей.
    """
    if cases < 0:
        raise ValueError("cases: не меньше 0")
    specs = profiles if profiles is not None else load_mixes()
    if mix not in specs:
        raise ValueError(f"нет профиля {mix!r}, есть {', '.join(sorted(specs))}")
    spec = specs[mix]
    rng = random.Random(spec["seed"] if seed is None else seed)  # noqa: S311 — синтетика
    kinds = [entry["kind"] for entry in spec["mix"]]
    weights = [entry["weight"] for entry in spec["mix"]]
    cond = spec["conditions"]
    reps, repeat, moment = cond.get("many_representatives"), cond.get("repeat_check"), cond.get("registry_moment")
    last_day = cond.get("term_last_day", {}).get("share", 0)
    ended = cond.get("term_ended_yesterday", {}).get("share", 0)
    client: list[int] = [0, 0]  # seed клиента, сколько его представителей ещё впереди
    pending: list[tuple[int, int, dict[str, Any]]] = []
    emitted = order = 0
    while emitted < cases:
        if pending and pending[0][0] <= emitted:
            yield heapq.heappop(pending)[2]
            emitted += 1
            continue
        kind = rng.choices(kinds, weights)[0]
        recipe: dict[str, Any] = {
            "case_id": f"{mix}-{emitted:06d}-{kind}",
            "kind": kind,
            "seed": rng.randrange(1, 2**31),
        }
        if reps and rng.random() < reps["share"]:
            if client[1] == 0:
                client = [rng.randrange(1, 2**31), rng.randint(*reps["size"])]
            recipe["org_seed"] = client[0]
            client[1] -= 1
        if kind in TERM_BASES and (last_day or ended):
            draw = rng.random()
            if draw < last_day:
                recipe["term"] = "last_day"
            elif draw < last_day + ended:
                recipe["term"] = "ended_yesterday"
        if moment and kind not in MOMENT_EXCLUDED and rng.random() < moment["share"]:
            recipe["registry_moment"] = _draw_moment(rng, moment)
        yield recipe
        emitted += 1
        if repeat and rng.random() < repeat["share"]:
            for k in range(1, rng.randint(1, repeat["max_repeats"]) + 1):
                order += 1
                copy = {**recipe, "case_id": f"{recipe['case_id']}-r{k}", "repeat_of": recipe["case_id"]}
                heapq.heappush(pending, (emitted + rng.randint(1, repeat["max_gap"]), order, copy))


def production_corpus(
    cases: int = 10_000, mix: str = "vtb_prod", *, seed: int | None = None, profiles: Mapping[str, Any] | None = None
) -> Iterator[Scenario]:
    """Кейсы потока по профилю: ``make_case`` на каждый рецепт ``production_recipes``."""
    for recipe in production_recipes(cases, mix, seed=seed, profiles=profiles):
        yield make_case(recipe)


def summarize(recipes: list[dict[str, Any]], profiles: Mapping[str, Any], mix: str) -> dict[str, Any]:
    """Состав потока: группы видов, ожидаемые вердикты, условия, клиенты."""
    groups = {entry["kind"]: entry["group"] for entry in profiles[mix]["mix"]}
    by_group: dict[str, int] = {}
    conditions: dict[str, int] = {}
    for recipe in recipes:
        by_group[groups[recipe["kind"]]] = by_group.get(groups[recipe["kind"]], 0) + 1
        for name in conditions_of(recipe):
            key = "registry_moment" if name.startswith("registry_moment") else name
            conditions[key] = conditions.get(key, 0) + 1
    clients = {r["org_seed"] for r in recipes if r.get("org_seed") is not None}
    return {
        "mix": mix,
        "cases": len(recipes),
        "kinds": len({r["kind"] for r in recipes}),
        "groups": dict(sorted(by_group.items(), key=lambda kv: -kv[1])),
        "conditions": dict(sorted(conditions.items())),
        "shared_clients": len(clients),
    }


def main(argv: list[str] | None = None) -> int:
    """``python -m mandat.eval.synth --mix vtb_prod --cases 10000 --out var/corpus.jsonl``."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m mandat.eval.synth", description="поток кейсов по профилю")
    parser.add_argument("--mix", default="vtb_prod", help="профиль из load_profiles.json")
    parser.add_argument("--cases", type=int, default=10_000)
    parser.add_argument("--seed", type=int, help="вместо seed профиля")
    parser.add_argument("--profiles", type=Path, help="свой файл профилей вместо data/load_profiles.json")
    parser.add_argument("--out", type=Path, help="JSONL: рецепт и ожидаемый вердикт на строку")
    parser.add_argument("--with-manifests", action="store_true", help="добавить в строку манифест с документами")
    parser.add_argument("--recipe", help="один рецепт (JSON): записать кейс в каталог --case-dir")
    parser.add_argument("--case-dir", type=Path, help="каталог кейса для --recipe (case.json и файлы)")
    args = parser.parse_args(argv)
    if args.recipe:
        scenario = make_case(json.loads(args.recipe))
        target = args.case_dir or Path(scenario.case_id)
        target.mkdir(parents=True, exist_ok=True)
        manifest = {**scenario.manifest, "expected_verdict": scenario.expected}
        (target / "case.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for name, raw in scenario.files.items():
            (target / name).write_bytes(raw)
        print(json.dumps({"case_dir": str(target), "expected": scenario.expected}, ensure_ascii=False))
        return 0
    profiles = load_mixes(args.profiles)
    recipes = list(production_recipes(args.cases, args.mix, seed=args.seed, profiles=profiles))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as fh:
            for recipe in recipes:
                scenario = make_case(recipe)
                row: dict[str, Any] = {"recipe": recipe, "expected": scenario.expected}
                if args.with_manifests:
                    row["manifest"] = scenario.inline_manifest()
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(summarize(recipes, profiles, args.mix), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
