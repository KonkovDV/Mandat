"""Синтетические кейсы с внесёнными дефектами.

Все организации и люди вымышлены. Реквизиты строятся с корректными контрольными
числами, поэтому случайное совпадение с реальным ИНН возможно: данные не
предназначены для поиска по реестрам. Синтетика проверяет логику движка и
не равна точности на реальных документах (docs/EVALUATION.md).
"""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
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
    def make(cls, seed: int) -> World:
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
        return cls(
            rng=rng,
            org=org,
            director=person(False, "Генеральный директор"),
            accountant=person(True),
            deputy=person(False),
            stranger=person(False),
            clerk1=person(True),
            clerk2=person(False),
        )

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
) -> dict:
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


def build(defect: str, seed: int = 1) -> Scenario:
    if defect not in DEFECTS:
        raise ValueError(f"неизвестный дефект {defect!r}")
    w = World.make(seed)
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
