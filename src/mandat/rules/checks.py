"""Реализация правил. Каждое правило возвращает находки со ссылками на источник.

Метаданные (норма, статус, следующий шаг, подразделение) берутся из пакета правил,
поэтому текст нормы правится в JSON без изменения кода. Состав правил сверяется
тестом: у каждого правила пакета есть реализация, и наоборот.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from mandat.data import load_json, sha256_json
from mandat.domain.graph import AuthorityChain
from mandat.domain.identifiers import inn_valid, kpp_valid, ogrn_valid, snils_valid
from mandat.domain.model import Case, EvidenceRef, Finding, PowerOfAttorney, Subdelegation
from mandat.domain.verdict import LegalEffect
from mandat.intake.charter import CandidateFact, extract_charter_facts
from mandat.ports import BankruptcyStatus, Ports, RegistryStatus, SignatureStatus
from mandat.rules.classifier import Classifier, default_classifier
from mandat.rules.pack import OperationCatalog, RulePack

E = LegalEffect


@dataclass
class RuleContext:
    case: Case
    chain: AuthorityChain
    pack: RulePack
    catalog: OperationCatalog
    ports: Ports
    classifier: Classifier = field(default_factory=default_classifier)
    charter_facts: list[CandidateFact] = field(default_factory=list)
    profile_name: str = "demo"

    @property
    def case_ref(self) -> EvidenceRef:
        digest = sha256_json(
            {
                "case_id": self.case.case_id,
                "requester": self.case.requester.__dict__,
                "organization": self.case.organization.__dict__,
                "operation": {
                    **self.case.operation.__dict__,
                    "on_date": self.case.operation.on_date.isoformat(),
                },
            }
        )
        return EvidenceRef("case", digest, "case.json")

    def emit(
        self,
        rule_id: str,
        effect: LegalEffect,
        message: str,
        refs: list[EvidenceRef] | tuple[EvidenceRef, ...],
    ) -> Finding:
        spec = self.pack.get(rule_id)
        quiet = effect in (E.SUPPORTS, E.INFO)
        return Finding(
            rule_id=rule_id,
            check=spec.check,
            effect=effect,
            message=message,
            norm_ref=spec.norm_ref,
            refs=tuple(refs) or (self.case_ref,),
            rule_status=spec.status,
            next_step=None if quiet else spec.next_step,
            unit=None if quiet else spec.unit,
        )


Rule = Callable[[RuleContext], list[Finding]]
RULES: dict[str, Rule] = {}


def rule(rule_id: str) -> Callable[[Rule], Rule]:
    def register(func: Rule) -> Rule:
        if rule_id in RULES:
            raise ValueError(f"повторная регистрация {rule_id}")
        RULES[rule_id] = func
        return func

    return register


def _poas_in_scope(ctx: RuleContext) -> tuple[PowerOfAttorney, ...]:
    if ctx.chain.kind == "poa":
        return ctx.chain.links
    if ctx.chain.kind == "none":
        return ctx.case.poas
    return ()


def _input_sha(ctx: RuleContext, doc_id: str) -> str:
    for row in ctx.case.inputs:
        if row["doc_id"] == doc_id:
            return row["sha256"]
    return "unavailable"


# --- формат -----------------------------------------------------------------


@rule("R-FMT-001")
def fmt_parsed(ctx: RuleContext) -> list[Finding]:
    out: list[Finding] = []
    for err in ctx.case.intake_errors:
        ref = EvidenceRef(err.doc_id, _input_sha(ctx, err.doc_id), "document")
        out.append(ctx.emit("R-FMT-001", E.NOT_VERIFIED, f"Документ {err.doc_id} не принят: {err.error}", [ref]))
    if ctx.chain.number_conflicts:
        for number in ctx.chain.number_conflicts:
            out.append(
                ctx.emit(
                    "R-FMT-001",
                    E.NOT_VERIFIED,
                    f"Номер МЧД {number} встречается в документах с разным содержимым: цепочка по нему не выбирается",
                    [],
                )
            )
    if out:
        return out
    if ctx.chain.kind == "eio_direct" and ctx.case.egrul is not None:
        eg = ctx.case.egrul
        return [ctx.emit("R-FMT-001", E.SUPPORTS, "Сведения ЕГРЮЛ приняты", [EvidenceRef(eg.doc_id, eg.sha256, "$")])]
    if not ctx.case.poas and not ctx.case.paper_poas:
        return [ctx.emit("R-FMT-001", E.NOT_VERIFIED, "В пакете нет документа, подтверждающего полномочия", [])]
    return [
        ctx.emit(
            "R-FMT-001",
            E.SUPPORTS,
            f"МЧД {poa.number} разобрана, обязательные поля на месте",
            [poa.ref("number")],
        )
        for poa in ctx.case.poas
    ] or [ctx.emit("R-FMT-001", E.SUPPORTS, "Пакет принят", [])]


@rule("R-FMT-002")
def fmt_version(ctx: RuleContext) -> list[Finding]:
    out = []
    for poa in ctx.case.poas:
        ref = poa.ref("format_version")
        if poa.format_version == "EMCHD_1":
            out.append(ctx.emit("R-FMT-002", E.SUPPORTS, f"МЧД {poa.number}: единый формат 003", [ref]))
        else:
            out.append(
                ctx.emit(
                    "R-FMT-002",
                    E.NEEDS_REVIEW,
                    f"МЧД {poa.number}: формат {poa.format_version or 'не указан'}",
                    [ref],
                )
            )
    return out


_SUB_CODE = {Subdelegation.NONE: "1", Subdelegation.ONCE: "2", Subdelegation.CHAIN: "3"}


@rule("R-FMT-003")
def fmt_order_1001(ctx: RuleContext) -> list[Finding]:
    since = date.fromisoformat(ctx.pack.get("R-FMT-003").param("effective_from"))
    out = []
    for poa in _poas_in_scope(ctx):
        if poa.issue_date is None or poa.issue_date < since:
            continue
        missing = []
        if not poa.principal.kpp:
            missing.append("КПП доверителя")
        if not poa.signer.snils:
            missing.append("СНИЛС руководителя доверителя")
        unnamed = [a.code for a in poa.authorities if a.kind == "machine" and not (a.name or "").strip()]
        if unnamed:
            missing.append("наименование полномочия рядом с кодом (" + ", ".join(str(c) for c in unnamed) + ")")
        if missing:
            out.append(
                ctx.emit(
                    "R-FMT-003",
                    E.NEEDS_REVIEW,
                    f"МЧД {poa.number} выдана {poa.issue_date:%d.%m.%Y}, но в ней нет: " + "; ".join(missing),
                    [poa.ref("number")],
                )
            )
        else:
            out.append(
                ctx.emit(
                    "R-FMT-003",
                    E.SUPPORTS,
                    f"МЧД {poa.number}: реквизиты по единым требованиям в ред. Приказа № 1001 на месте",
                    [poa.ref("principal.kpp")],
                )
            )
    return out


@rule("R-FMT-004")
def fmt_irrevocable(ctx: RuleContext) -> list[Finding]:
    allowed_map: dict[str, list[str]] = load_json("emchd_003_mapping.json")["irrevocable_subdelegation_allowed"]
    out = []
    for poa in ctx.case.poas:
        flag = poa.irrevocable_subdelegation
        if not flag:
            continue
        code = _SUB_CODE.get(poa.subdelegation)
        allowed = allowed_map.get(flag)
        if allowed is None or code not in allowed:
            out.append(
                ctx.emit(
                    "R-FMT-004",
                    E.NEEDS_REVIEW,
                    f"МЧД {poa.number}: признак передоверия безотзывной доверенности {flag} "
                    f"не согласуется с признаком передоверия {code or 'не распознан'}",
                    [poa.ref("irrevocable_subdelegation"), poa.ref("subdelegation")],
                )
            )
    return out


# --- реквизиты --------------------------------------------------------------


@rule("R-ID-001")
def id_checksums(ctx: RuleContext) -> list[Finding]:
    bad: list[Finding] = []
    checked = 0

    def check(value: str | None, validator: Callable[[str | None], bool], label: str, ref: EvidenceRef) -> None:
        nonlocal checked
        if not value:
            return
        checked += 1
        if not validator(value):
            bad.append(ctx.emit("R-ID-001", E.REFUTES, f"{label}: контрольное число не сходится ({value})", [ref]))

    org, req = ctx.case.organization, ctx.case.requester
    check(org.inn, inn_valid, "ИНН клиента", ctx.case_ref)
    check(org.ogrn, ogrn_valid, "ОГРН клиента", ctx.case_ref)
    check(req.inn, inn_valid, "ИНН заявителя", ctx.case_ref)
    check(req.snils, snils_valid, "СНИЛС заявителя", ctx.case_ref)
    for poa in ctx.case.poas:
        check(poa.principal.inn, inn_valid, f"ИНН доверителя в МЧД {poa.number}", poa.ref("principal.inn"))
        check(poa.principal.ogrn, ogrn_valid, f"ОГРН доверителя в МЧД {poa.number}", poa.ref("principal.ogrn"))
        check(poa.principal.kpp, kpp_valid, f"КПП доверителя в МЧД {poa.number}", poa.ref("principal.kpp"))
        check(poa.signer.inn, inn_valid, f"ИНН подписанта МЧД {poa.number}", poa.ref("signer.inn"))
        check(poa.signer.snils, snils_valid, f"СНИЛС подписанта МЧД {poa.number}", poa.ref("signer.snils"))
        for index, rep in enumerate(poa.representatives, start=1):
            ref = poa.ref(f"representative.{index}")
            check(rep.inn, inn_valid, f"ИНН представителя в МЧД {poa.number}", ref)
            check(rep.snils, snils_valid, f"СНИЛС представителя в МЧД {poa.number}", ref)
    if bad:
        return bad
    if checked == 0:
        return [ctx.emit("R-ID-001", E.NOT_VERIFIED, "Нет реквизитов для проверки контрольных чисел", [])]
    return [ctx.emit("R-ID-001", E.SUPPORTS, f"Контрольные числа сходятся ({checked} реквизитов)", [])]


@rule("R-ID-002")
def id_principal(ctx: RuleContext) -> list[Finding]:
    org = ctx.case.organization
    if not org.inn:
        return [ctx.emit("R-ID-002", E.NOT_VERIFIED, "В заявке не указан ИНН организации-клиента", [])]
    out = []
    eg = ctx.case.egrul
    if eg is not None:
        ref = EvidenceRef(eg.doc_id, eg.sha256, "$.inn", quote=eg.organization.inn)
        if eg.organization.inn != org.inn:
            out.append(ctx.emit("R-ID-002", E.REFUTES, "Сведения ЕГРЮЛ относятся к другой организации", [ref]))
    for poa in _poas_in_scope(ctx):
        ref = poa.ref("principal.inn")
        same_inn = poa.principal.inn == org.inn
        same_ogrn = not (poa.principal.ogrn and org.ogrn) or poa.principal.ogrn == org.ogrn
        if same_inn and same_ogrn:
            out.append(ctx.emit("R-ID-002", E.SUPPORTS, f"МЧД {poa.number} выдана организацией клиента", [ref]))
        else:
            out.append(
                ctx.emit(
                    "R-ID-002",
                    E.REFUTES,
                    f"МЧД {poa.number} выдана другой организацией (ИНН {poa.principal.inn}, а клиент — {org.inn})",
                    [ref],
                )
            )
    if ctx.chain.kind == "eio_direct" and eg is not None and eg.organization.inn == org.inn:
        out.append(
            ctx.emit(
                "R-ID-002",
                E.SUPPORTS,
                "Сведения ЕГРЮЛ относятся к организации клиента",
                [EvidenceRef(eg.doc_id, eg.sha256, "$.inn", quote=org.inn)],
            )
        )
    return out


@rule("R-ID-003")
def id_representative(ctx: RuleContext) -> list[Finding]:
    req = ctx.case.requester
    if ctx.chain.kind in {"eio_direct", "paper"}:
        return []
    if not (req.inn or req.snils):
        return [ctx.emit("R-ID-003", E.NOT_VERIFIED, "У заявителя нет ИНН или СНИЛС для сверки с МЧД", [])]
    if ctx.chain.kind == "none":
        if not ctx.case.poas:
            return []
        refs = [poa.ref("representative.1") for poa in ctx.case.poas]
        return [ctx.emit("R-ID-003", E.REFUTES, f"{req.label()} не указан представителем ни в одной МЧД пакета", refs)]
    leaf = ctx.chain.leaf
    assert leaf is not None
    for index, rep in enumerate(leaf.representatives, start=1):
        if rep.same_as(req):
            return [
                ctx.emit(
                    "R-ID-003",
                    E.SUPPORTS,
                    f"Представитель {rep.label()} совпадает с заявителем",
                    [leaf.ref(f"representative.{index}")],
                )
            ]
    return [ctx.emit("R-ID-003", E.REFUTES, "Заявитель не совпадает с представителем", [leaf.ref("representative.1")])]


_ORG_FORMS = {
    "ооо": "общество с ограниченной ответственностью",
    "пао": "публичное акционерное общество",
    "зао": "закрытое акционерное общество",
    "оао": "открытое акционерное общество",
    "ао": "акционерное общество",
}


def normalize_org_name(name: str | None) -> str:
    """Наименование для сравнения: регистр, кавычки, пробелы, сокращения ООО/АО/ПАО раскрыты."""
    text = (name or "").casefold().replace("ё", "е")
    text = re.sub("[«»“”„\"'`]", " ", text)
    words = [_ORG_FORMS.get(word, word) for word in text.split()]
    return " ".join(words)


@rule("R-ID-004")
def id_principal_name(ctx: RuleContext) -> list[Finding]:
    eg = ctx.case.egrul
    if eg is None:
        return []
    names = {normalize_org_name(n) for n in (eg.full_name, eg.organization.name) if n}
    if not names:
        return []
    out = []
    for poa in _poas_in_scope(ctx):
        if not poa.principal.name:
            continue
        refs = [poa.ref("principal.name"), EvidenceRef(eg.doc_id, eg.sha256, "$.full_name", quote=eg.full_name)]
        if normalize_org_name(poa.principal.name) in names:
            message = f"Наименование доверителя в МЧД {poa.number} совпадает с ЕГРЮЛ"
            out.append(ctx.emit("R-ID-004", E.SUPPORTS, message, refs))
        else:
            out.append(
                ctx.emit(
                    "R-ID-004",
                    E.NEEDS_REVIEW,
                    f"Наименование доверителя в МЧД {poa.number} «{poa.principal.name}» "
                    f"не совпадает с ЕГРЮЛ «{eg.full_name or eg.organization.name}»",
                    refs,
                )
            )
    return out


# --- реестр и срок ----------------------------------------------------------


@rule("R-REG-001")
def registry_status(ctx: RuleContext) -> list[Finding]:
    out = []
    for poa in _poas_in_scope(ctx):
        number = poa.number or ""
        status = ctx.ports.registry.status(number)
        ref = EvidenceRef(
            f"registry:{ctx.ports.registry.name}",
            sha256_json({"number": number, "status": status.value}),
            f"registry:{number}",
            quote=status.value,
        )
        if status is RegistryStatus.ACTIVE:
            out.append(ctx.emit("R-REG-001", E.SUPPORTS, f"МЧД {number} действует в реестре", [ref]))
        elif status in (RegistryStatus.REVOKED, RegistryStatus.EXPIRED):
            word = "отменена" if status is RegistryStatus.REVOKED else "истекла"
            out.append(ctx.emit("R-REG-001", E.REFUTES, f"МЧД {number} {word} по данным реестра", [ref]))
        elif status is RegistryStatus.NOT_FOUND:
            out.append(ctx.emit("R-REG-001", E.NEEDS_REVIEW, f"МЧД {number} не найдена в реестре", [ref]))
        else:
            out.append(ctx.emit("R-REG-001", E.NOT_VERIFIED, f"Реестр МЧД недоступен для {number}", [ref]))
    return out


@rule("R-TERM-001")
def term_present(ctx: RuleContext) -> list[Finding]:
    out = []
    for poa in _poas_in_scope(ctx):
        if poa.valid_until is not None:
            out.append(
                ctx.emit(
                    "R-TERM-001",
                    E.SUPPORTS,
                    f"МЧД {poa.number} действует до {poa.valid_until:%d.%m.%Y}",
                    [poa.ref("valid_until")],
                )
            )
            continue
        until = poa.effective_until()
        tail = f": по закону — год со дня совершения, до {until:%d.%m.%Y}" if until else ""
        out.append(
            ctx.emit(
                "R-TERM-001",
                E.NEEDS_REVIEW,
                f"В МЧД {poa.number} не указан срок действия{tail}; в формате 003 срок обязателен — сверить с реестром",
                [poa.ref("issue_date")],
            )
        )
    return out


@rule("R-TERM-002")
def term_covers_date(ctx: RuleContext) -> list[Finding]:
    on = ctx.case.operation.on_date
    out = []
    for poa in _poas_in_scope(ctx):
        if poa.issue_date and on < poa.issue_date:
            out.append(
                ctx.emit(
                    "R-TERM-002",
                    E.REFUTES,
                    f"Операция {on:%d.%m.%Y} раньше выдачи МЧД {poa.number}",
                    [poa.ref("issue_date")],
                )
            )
        elif (until := poa.effective_until()) and on > until:
            basis = "" if poa.valid_until else " (срок по закону: год со дня совершения)"
            ref = poa.ref("valid_until") if poa.valid_until else poa.ref("issue_date")
            out.append(ctx.emit("R-TERM-002", E.REFUTES, f"МЧД {poa.number} истекла {until:%d.%m.%Y}{basis}", [ref]))
        elif until:
            ref = poa.ref("valid_until") if poa.valid_until else poa.ref("issue_date")
            out.append(ctx.emit("R-TERM-002", E.SUPPORTS, f"Дата операции в сроке МЧД {poa.number}", [ref]))
    return out


# --- руководитель по ЕГРЮЛ --------------------------------------------------


def _egrul_ref(ctx: RuleContext, locator: str = "$") -> EvidenceRef:
    eg = ctx.case.egrul
    assert eg is not None
    return EvidenceRef(eg.doc_id, eg.sha256, locator)


@rule("R-EIO-001")
def eio_signed_root(ctx: RuleContext) -> list[Finding]:
    if ctx.chain.kind == "eio_direct":
        return []
    root = ctx.chain.root or (ctx.case.poas[0] if ctx.case.poas else None)
    if root is None:
        return []
    if root.parent_number:
        return [
            ctx.emit(
                "R-EIO-001",
                E.NOT_VERIFIED,
                f"Корневая МЧД цепочки не предоставлена: подписанта МЧД {root.number} нельзя сверить с ЕГРЮЛ",
                [root.ref("parent_number")],
            )
        ]
    eg = ctx.case.egrul
    if eg is None:
        return [ctx.emit("R-EIO-001", E.NOT_VERIFIED, "Нет сведений ЕГРЮЛ о руководителе", [root.ref("signer.inn")])]
    if root.issue_date is None:
        return [ctx.emit("R-EIO-001", E.NOT_VERIFIED, f"У МЧД {root.number} нет даты выдачи", [root.ref("number")])]
    records = eg.executives_on(root.issue_date)
    if not records:
        return [
            ctx.emit(
                "R-EIO-001",
                E.NOT_VERIFIED,
                f"В ЕГРЮЛ нет руководителя на {root.issue_date:%d.%m.%Y}",
                [_egrul_ref(ctx, "$.executives")],
            )
        ]
    comparable = False
    for rec in records:
        same = rec.person.same_as(root.signer)
        if same:
            refs = [root.ref("signer.inn"), rec.ref or _egrul_ref(ctx)]
            return [
                ctx.emit(
                    "R-EIO-001",
                    E.SUPPORTS,
                    f"МЧД {root.number} подписал руководитель {rec.person.label()} (ГРН {rec.grn})",
                    refs,
                )
            ]
        comparable = comparable or same is not None
    names = ", ".join(r.person.label() for r in records)
    refs = [root.ref("signer.inn"), *(r.ref for r in records if r.ref)]
    if not comparable:
        return [ctx.emit("R-EIO-001", E.NEEDS_REVIEW, "Подписанта нельзя сверить с ЕГРЮЛ по ИНН или СНИЛС", refs)]
    return [
        ctx.emit(
            "R-EIO-001",
            E.REFUTES,
            f"МЧД {root.number} подписал {root.signer.label()}, а руководителем на "
            f"{root.issue_date:%d.%m.%Y} был {names}",
            refs,
        )
    ]


@rule("R-EIO-002")
def eio_changed(ctx: RuleContext) -> list[Finding]:
    """Смена руководителя не прекращает МЧД (п. 1 ст. 188 ГК РФ, ФНС): это сигнал, а не опровержение.

    Свежая смена (в пределах ``fresh_change_days``) или операция высокого риска — разбор:
    так начинается захват компании через подставного руководителя. Давняя смена — справка.
    """
    if ctx.chain.kind != "poa" or ctx.case.egrul is None or ctx.chain.root is None:
        return []
    root = ctx.chain.root
    if root.parent_number:
        return []
    on = ctx.case.operation.on_date
    known = [rec for rec in ctx.case.egrul.executives if rec.valid_from is not None]
    current = [rec for rec in known if rec.active_on(on)]
    if not current:
        unknown = [rec for rec in ctx.case.egrul.executives if rec.valid_from is None]
        text = (
            "На дату операции нет записи руководителя с известным началом полномочий. "
            "Это пробел сведений, а не прекращение доверенности"
        )
        effect = E.NOT_VERIFIED if unknown else E.NEEDS_REVIEW
        return [ctx.emit("R-EIO-002", effect, text, [_egrul_ref(ctx, "$.executives")])]
    if any(rec.person.same_as(root.signer) for rec in current):
        return []
    rec = current[0]
    since = rec.valid_from or rec.record_date
    when = f"{since:%d.%m.%Y}" if since else "дата не указана"
    fresh_days = int(ctx.pack.get("R-EIO-002").param("fresh_change_days"))
    op = ctx.catalog.get(ctx.case.operation.code)
    fresh = since is None or (on - since).days <= fresh_days
    risky = bool(op and op.high_risk)
    refs = [rec.ref or _egrul_ref(ctx), root.ref("signer.inn")]
    base = (
        f"После выдачи МЧД {root.number} руководитель сменился: сейчас {rec.person.label()} (ГРН {rec.grn}, с {when}). "
        "Смена руководителя доверенность не прекращает (п. 1 ст. 188 ГК РФ)"
    )
    if fresh or risky:
        reason = f"смена свежая — не более {fresh_days} дн." if fresh else "операция высокого риска"
        return [ctx.emit("R-EIO-002", E.NEEDS_REVIEW, f"{base}; {reason}: нужен разбор", refs)]
    return [ctx.emit("R-EIO-002", E.INFO, f"{base}; МЧД не отменена — сигнал мониторинга", refs)]


@rule("R-EIO-003")
def eio_unreliable(ctx: RuleContext) -> list[Finding]:
    eg = ctx.case.egrul
    if eg is None:
        return []
    days = {ctx.case.operation.on_date}
    if ctx.chain.root and ctx.chain.root.issue_date:
        days.add(ctx.chain.root.issue_date)
    flagged = {id(r): r for d in days for r in eg.executives_on(d) if r.unreliable}
    return [
        ctx.emit(
            "R-EIO-003",
            E.NEEDS_REVIEW,
            f"Сведения о руководителе {r.person.label()} помечены в ЕГРЮЛ как недостоверные",
            [r.ref or _egrul_ref(ctx)],
        )
        for r in flagged.values()
    ]


@rule("R-EIO-004")
def org_status(ctx: RuleContext) -> list[Finding]:
    return [*_org_status_egrul(ctx), *_bankruptcy(ctx)]


# Что процедура по 127-ФЗ значит для доверенности. Таблицу должен подтвердить юрист банка (docs/REGULATORY.md).
# Подп. 7 п. 1 ст. 188 ГК РФ прекращает доверенность, если лицо утрачивает право само выдавать доверенности.
# Для юрлица это внешнее управление (ст. 94) и конкурсное производство (ст. 126): полномочия руководителя
# прекращаются. На наблюдении (ст. 64) и финансовом оздоровлении (ст. 82) органы управления работают с
# ограничениями. Гражданин-банкрот по сложившейся практике право выдавать доверенности не теряет.
BANKRUPTCY_RULES: dict[BankruptcyStatus, tuple[E, str]] = {
    BankruptcyStatus.EXTERNAL_MANAGEMENT: (
        E.REFUTES,
        "внешнее управление: полномочия руководителя прекращены (ст. 94 127-ФЗ), доверенность прекратилась",
    ),
    BankruptcyStatus.BANKRUPTCY_PROCEEDINGS: (
        E.REFUTES,
        "конкурсное производство: полномочия руководителя и органов управления прекращены (ст. 126 127-ФЗ), "
        "доверенность прекратилась",
    ),
    BankruptcyStatus.OBSERVATION: (
        E.NEEDS_REVIEW,
        "наблюдение: руководитель не отстранён, но крупные сделки и займы только с согласия временного "
        "управляющего (ст. 64 127-ФЗ)",
    ),
    BankruptcyStatus.FINANCIAL_RECOVERY: (
        E.NEEDS_REVIEW,
        "финансовое оздоровление: органы управления работают с ограничениями (ст. 82 127-ФЗ)",
    ),
    BankruptcyStatus.SETTLEMENT: (
        E.NEEDS_REVIEW,
        "мировое соглашение: производство прекращено (ст. 159 127-ФЗ), нужно сверить дату доверенности с "
        "периодами внешнего управления и конкурсного производства",
    ),
    BankruptcyStatus.DEBT_RESTRUCTURING: (
        E.NEEDS_REVIEW,
        "реструктуризация долгов гражданина: право выдавать доверенности сохраняется, сделки с имуществом "
        "ограничены (ст. 213.11 127-ФЗ)",
    ),
    BankruptcyStatus.PROPERTY_SALE: (
        E.NEEDS_REVIEW,
        "реализация имущества гражданина: право выдавать доверенности сохраняется, имуществом распоряжается "
        "финансовый управляющий (ст. 213.25 127-ФЗ)",
    ),
}
ORG_PROCEDURES = frozenset(
    {
        BankruptcyStatus.OBSERVATION,
        BankruptcyStatus.FINANCIAL_RECOVERY,
        BankruptcyStatus.EXTERNAL_MANAGEMENT,
        BankruptcyStatus.BANKRUPTCY_PROCEEDINGS,
        BankruptcyStatus.SETTLEMENT,
    }
)


def _bankruptcy(ctx: RuleContext) -> list[Finding]:
    """Подп. 7 п. 1 ст. 188 ГК РФ и 127-ФЗ: что процедура банкротства делает с доверенностью."""
    port = ctx.ports.bankruptcy
    out = []
    for who, inn, is_org in (
        ("Доверитель", ctx.case.organization.inn, True),
        ("Представитель", ctx.case.requester.inn, False),
    ):
        if not inn:
            continue
        status = port.status(inn)
        if status in (BankruptcyStatus.NONE, BankruptcyStatus.NOT_CHECKED):
            continue
        ref = EvidenceRef(
            f"efrsb:{port.name}",
            sha256_json({"inn": inn, "status": status.value}),
            f"efrsb:{inn}",
            quote=status.value,
        )
        effect, why = BANKRUPTCY_RULES[status]
        if is_org != (status in ORG_PROCEDURES):
            # Процедура не того вида лица: данные противоречат друг другу, решает человек.
            effect = E.NEEDS_REVIEW
            why = f"процедура {status.value} не подходит к этому лицу, сведения нужно перепроверить"
        out.append(ctx.emit("R-EIO-004", effect, f"{who} (ИНН {inn}): {why}", [ref]))
    return out


def _org_status_egrul(ctx: RuleContext) -> list[Finding]:
    eg = ctx.case.egrul
    if eg is None or eg.status == "active":
        return []
    ref = _egrul_ref(ctx, "$.status")
    if eg.status == "unknown":
        return [
            ctx.emit(
                "R-EIO-004",
                E.NEEDS_REVIEW,
                "В снимке ЕГРЮЛ не указан статус организации: это не сведения о действующем юридическом лице",
                [ref],
            )
        ]
    if eg.status == "liquidated":
        return [ctx.emit("R-EIO-004", E.REFUTES, "Организация ликвидирована по данным ЕГРЮЛ", [ref])]
    return [ctx.emit("R-EIO-004", E.NEEDS_REVIEW, "Организация в процессе ликвидации", [ref])]


@rule("R-EIO-005")
def eio_direct(ctx: RuleContext) -> list[Finding]:
    rec = ctx.chain.eio_record
    if ctx.chain.kind != "eio_direct" or rec is None:
        return []
    return [
        ctx.emit(
            "R-EIO-005",
            E.SUPPORTS,
            f"Заявитель — руководитель по ЕГРЮЛ на дату операции (ГРН {rec.grn}), действует без доверенности",
            [rec.ref or _egrul_ref(ctx)],
        )
    ]


# --- покрытие операции и передоверие ----------------------------------------


@rule("R-COV-001")
def coverage(ctx: RuleContext) -> list[Finding]:
    op = ctx.catalog.get(ctx.case.operation.code)
    if op is None:
        return [ctx.emit("R-COV-001", E.NEEDS_REVIEW, f"Операция {ctx.case.operation.code} отсутствует в каталоге", [])]
    if ctx.chain.kind == "eio_direct" and ctx.chain.eio_record is not None:
        return [
            ctx.emit(
                "R-COV-001",
                E.SUPPORTS,
                f"«{op.name}»: руководитель действует без доверенности в пределах устава",
                [ctx.chain.eio_record.ref or _egrul_ref(ctx)],
            )
        ]
    leaf = ctx.chain.leaf
    if ctx.chain.kind != "poa" or leaf is None:
        return []
    for auth in leaf.authorities:
        if auth.kind == "machine" and auth.code in op.accepted_codes:
            refs = [auth.ref] if auth.ref else [leaf.ref("number")]
            return [ctx.emit("R-COV-001", E.SUPPORTS, f"Полномочие {auth.code} покрывает «{op.name}»", refs)]
    texts = [a for a in leaf.authorities if a.kind == "text"]
    if texts:
        refs = [a.ref for a in texts if a.ref] or [leaf.ref("number")]
        return [
            ctx.emit(
                "R-COV-001",
                E.NEEDS_REVIEW,
                f"«{op.name}» может покрываться только текстовым полномочием: решает сотрудник",
                refs,
            )
        ]
    refs = [a.ref for a in leaf.authorities if a.ref] or [leaf.ref("number")]
    return [ctx.emit("R-COV-001", E.REFUTES, f"В МЧД {leaf.number} нет полномочия на «{op.name}»", refs)]


@rule("R-COV-002")
def coverage_classifier(ctx: RuleContext) -> list[Finding]:
    op = ctx.catalog.get(ctx.case.operation.code)
    leaf = ctx.chain.leaf
    if op is None or ctx.chain.kind != "poa" or leaf is None:
        return []
    on = ctx.case.operation.on_date
    out = []
    for auth in leaf.authorities:
        if auth.kind != "machine" or auth.code not in op.accepted_codes:
            continue
        ref = auth.ref or leaf.ref("number")
        cref = EvidenceRef(f"classifier:{ctx.classifier.classifier_id}", ctx.classifier.sha256, f"code:{auth.code}")
        entry = ctx.classifier.get(auth.code)
        if entry is None:
            out.append(
                ctx.emit("R-COV-002", E.NEEDS_REVIEW, f"Кода {auth.code} нет в снимке классификатора", [ref, cref])
            )
            continue
        bad = [d for d in (leaf.issue_date, on) if d is not None and not entry.active_on(d)]
        if bad:
            tail = f"исключён {entry.revoked:%d.%m.%Y}" if entry.revoked else f"введён {entry.started:%d.%m.%Y}"
            out.append(
                ctx.emit(
                    "R-COV-002",
                    E.NEEDS_REVIEW,
                    f"Код {auth.code} не действует в классификаторе на {bad[-1]:%d.%m.%Y}: {tail}",
                    [ref, cref],
                )
            )
        else:
            out.append(ctx.emit("R-COV-002", E.SUPPORTS, f"Код {auth.code} действует в классификаторе", [ref, cref]))
    return out


@rule("R-JNT-001")
def joint_powers(ctx: RuleContext) -> list[Finding]:
    leaf = ctx.chain.leaf
    if leaf is None:
        return []
    # Нет признака — не значит «каждый подписывает сам» (Контур: ненайденное значение не считается соответствием).
    if leaf.joint is None and len(leaf.representatives) > 1:
        return [
            ctx.emit(
                "R-JNT-001",
                E.NEEDS_REVIEW,
                f"В МЧД {leaf.number} несколько представителей, а признак совместных полномочий не указан",
                [leaf.ref("joint")],
            )
        ]
    if not leaf.joint:
        return []
    others = [p.label() for p in leaf.representatives if not p.same_as(ctx.case.requester)]
    who = ", ".join(others) or "другими представителями"
    return [
        ctx.emit(
            "R-JNT-001",
            E.NEEDS_REVIEW,
            f"В МЧД {leaf.number} полномочия совместные: операцию подписывают вместе с {who}",
            [leaf.ref("joint")],
        )
    ]


def _pairs(chain: AuthorityChain) -> list[tuple[int, PowerOfAttorney, PowerOfAttorney]]:
    return [(i, chain.links[i - 1], chain.links[i]) for i in range(1, len(chain.links))]


@rule("R-SUB-001")
def sub_allowed(ctx: RuleContext) -> list[Finding]:
    out = []
    for i, parent, child in _pairs(ctx.chain):
        ref = parent.ref("subdelegation")
        grand = ctx.chain.links[i - 2] if i >= 2 else None
        if parent.subdelegation is Subdelegation.NONE:
            out.append(
                ctx.emit(
                    "R-SUB-001",
                    E.REFUTES,
                    f"МЧД {parent.number} выдана без права передоверия, а МЧД {child.number} — передоверие",
                    [ref, child.ref("parent_number")],
                )
            )
        elif grand is not None and grand.subdelegation is Subdelegation.ONCE:
            out.append(
                ctx.emit(
                    "R-SUB-001",
                    E.REFUTES,
                    f"Однократное передоверие по МЧД {grand.number} уже использовано МЧД {parent.number}",
                    [grand.ref("subdelegation"), child.ref("parent_number")],
                )
            )
        elif parent.subdelegation is Subdelegation.UNKNOWN:
            out.append(
                ctx.emit(
                    "R-SUB-001",
                    E.NEEDS_REVIEW,
                    f"Признак передоверия МЧД {parent.number} не распознан",
                    [ref],
                )
            )
        else:
            out.append(ctx.emit("R-SUB-001", E.SUPPORTS, f"МЧД {parent.number} разрешает передоверие", [ref]))
    return out


@rule("R-SUB-002")
def sub_narrower(ctx: RuleContext) -> list[Finding]:
    out = []
    for _, parent, child in _pairs(ctx.chain):
        if any(a.kind == "text" for a in (*parent.authorities, *child.authorities)):
            out.append(
                ctx.emit(
                    "R-SUB-002",
                    E.NEEDS_REVIEW,
                    f"Объём передоверия {parent.number} → {child.number} включает текстовые полномочия",
                    [child.ref("number")],
                )
            )
            continue
        parent_codes = {a.code for a in parent.authorities}
        extra = sorted({a.code for a in child.authorities} - parent_codes, key=str)
        if extra:
            out.append(
                ctx.emit(
                    "R-SUB-002",
                    E.REFUTES,
                    f"МЧД {child.number} передаёт полномочия, которых нет в МЧД {parent.number}: "
                    + ", ".join(str(c) for c in extra),
                    [child.ref("number"), parent.ref("number")],
                )
            )
        else:
            out.append(
                ctx.emit(
                    "R-SUB-002",
                    E.SUPPORTS,
                    f"Полномочия МЧД {child.number} не шире родительской",
                    [child.ref("number")],
                )
            )
    return out


@rule("R-SUB-003")
def sub_term(ctx: RuleContext) -> list[Finding]:
    out = []
    for _, parent, child in _pairs(ctx.chain):
        parent_until, child_until = parent.effective_until(), child.effective_until()
        if parent_until is None or child_until is None:
            continue
        if child_until > parent_until:
            out.append(
                ctx.emit(
                    "R-SUB-003",
                    E.REFUTES,
                    f"Срок МЧД {child.number} ({child_until:%d.%m.%Y}) дольше срока МЧД "
                    f"{parent.number} ({parent_until:%d.%m.%Y})",
                    [child.ref("valid_until"), parent.ref("valid_until")],
                )
            )
        else:
            out.append(
                ctx.emit(
                    "R-SUB-003",
                    E.SUPPORTS,
                    f"Срок МЧД {child.number} в пределах родительской",
                    [child.ref("valid_until")],
                )
            )
    return out


@rule("R-SUB-004")
def sub_complete(ctx: RuleContext) -> list[Finding]:
    leaf = ctx.chain.links[0] if ctx.chain.links else None
    return [
        ctx.emit(
            "R-SUB-004",
            E.NOT_VERIFIED,
            f"Родительская МЧД {number} не предоставлена",
            [leaf.ref("parent_number")] if leaf else [],
        )
        for number in ctx.chain.missing_parents
    ]


@rule("R-SUB-005")
def sub_same_principal(ctx: RuleContext) -> list[Finding]:
    return [
        ctx.emit(
            "R-SUB-005",
            E.REFUTES,
            f"МЧД {child.number} и {parent.number} выданы от разных доверителей",
            [child.ref("principal.inn"), parent.ref("principal.inn")],
        )
        for _, parent, child in _pairs(ctx.chain)
        if parent.principal.inn != child.principal.inn
    ]


@rule("R-SUB-006")
def sub_cascade(ctx: RuleContext) -> list[Finding]:
    on = ctx.case.operation.on_date
    out = []
    links = ctx.chain.links
    for i, parent, _child in _pairs(ctx.chain):
        status = ctx.ports.registry.status(parent.number or "")
        until = parent.effective_until()
        if status in (RegistryStatus.REVOKED, RegistryStatus.EXPIRED):
            why = "отменена" if status is RegistryStatus.REVOKED else "истекла"
        elif until is not None and on > until:
            why = f"истекла {until:%d.%m.%Y}"
        else:
            continue
        derived = ", ".join(str(p.number) for p in links[i:])
        out.append(
            ctx.emit(
                "R-SUB-006",
                E.REFUTES,
                f"Основная МЧД {parent.number} {why}: вместе с ней теряет силу передоверие ({derived})",
                [parent.ref("number"), *(p.ref("parent_number") for p in links[i:])],
            )
        )
    return out


@rule("R-SUB-007")
def sub_lost_authority(ctx: RuleContext) -> list[Finding]:
    leaf = ctx.chain.leaf
    if ctx.chain.kind != "poa" or leaf is None or not leaf.number:
        return []
    on = ctx.case.operation.on_date
    out = []
    for child in ctx.case.poas:
        if child.parent_number != leaf.number or not child.loses_authority:
            continue
        if child.issue_date is not None and child.issue_date > on:
            continue
        when = f"{child.issue_date:%d.%m.%Y}" if child.issue_date else "дата не указана"
        out.append(
            ctx.emit(
                "R-SUB-007",
                E.REFUTES,
                f"По МЧД {child.number} полномочия переданы с утратой (ПрУтрПолн = 2): "
                f"{ctx.case.requester.label()} не вправе действовать по МЧД {leaf.number} с {when}",
                [child.ref("loses_authority"), child.ref("parent_number")],
            )
        )
    return out


@rule("R-SUB-008")
def sub_long_chain(ctx: RuleContext) -> list[Finding]:
    max_links = int(ctx.pack.get("R-SUB-008").param("max_links"))
    links = ctx.chain.links
    if len(links) <= max_links:
        return []
    statuses = {p.number: ctx.ports.registry.status(p.number or "") for p in links}
    unconfirmed = [n for n, st in statuses.items() if st is not RegistryStatus.ACTIVE]
    refs = [p.ref("number") for p in links]
    if unconfirmed:
        return [
            ctx.emit(
                "R-SUB-008",
                E.NEEDS_REVIEW,
                f"Цепочка из {len(links)} МЧД длиннее {max_links}: по справке оператора ЭДО реестр ФНС "
                f"такую длину может не принять; звенья без подтверждения в реестре: "
                + ", ".join(str(n) for n in unconfirmed),
                refs,
            )
        ]
    return [
        ctx.emit(
            "R-SUB-008",
            E.INFO,
            f"Цепочка из {len(links)} МЧД длиннее {max_links}, но реестр подтвердил каждое звено",
            refs,
        )
    ]


@rule("R-SUB-009")
def sub_edge(ctx: RuleContext) -> list[Finding]:
    """Каждое ребро: подписант дочерней МЧД — представитель родительской, и родитель ещё действовал в день выдачи."""
    out = []
    root_number = ctx.chain.root.number if ctx.chain.root else None
    for _, parent, child in _pairs(ctx.chain):
        if child.issue_date is None:
            out.append(
                ctx.emit(
                    "R-SUB-009",
                    E.NOT_VERIFIED,
                    f"У МЧД {child.number} нет даты выдачи: нельзя проверить ребро передоверия",
                    [child.ref("number")],
                )
            )
            continue
        if parent.issue_date and child.issue_date < parent.issue_date:
            out.append(
                ctx.emit(
                    "R-SUB-009",
                    E.REFUTES,
                    f"МЧД {child.number} выдана раньше родительской МЧД {parent.number}",
                    [child.ref("issue_date"), parent.ref("issue_date")],
                )
            )
            continue
        until = parent.effective_until()
        if until is not None and child.issue_date > until:
            out.append(
                ctx.emit(
                    "R-SUB-009",
                    E.REFUTES,
                    f"МЧД {child.number} выдана, когда МЧД {parent.number} уже не действовала",
                    [child.ref("issue_date"), parent.ref("valid_until")],
                )
            )
            continue
        compared = [rep.same_as(child.signer) for rep in parent.representatives]
        if any(flag is True for flag in compared):
            index = next(i for i, flag in enumerate(compared, start=1) if flag is True)
            ref = parent.ref(f"representative.{index}")
            if parent.issue_date is None:
                message = f"У МЧД {parent.number} нет даты выдачи: нельзя утверждать, что она действовала"
                effect = E.NOT_VERIFIED
            elif parent.joint is None:
                message = f"У МЧД {parent.number} не указан признак совместных полномочий"
                effect = E.NEEDS_REVIEW
            elif parent.joint:
                message = f"МЧД {parent.number}: совместные полномочия, одного подписанта мало"
                effect = E.NEEDS_REVIEW
            elif child.original_number and root_number and child.original_number != root_number:
                message = f"Первоначальная доверенность в МЧД {child.number} не совпадает с корнем {root_number}"
                effect = E.NEEDS_REVIEW
            else:
                message = (
                    f"МЧД {child.number} подписал представитель МЧД {parent.number}, "
                    f"и на {child.issue_date:%d.%m.%Y} родительская ещё действовала"
                )
                effect = E.SUPPORTS
            out.append(ctx.emit("R-SUB-009", effect, message, [child.ref("signer.inn"), ref]))
        elif compared and all(flag is False for flag in compared):
            out.append(
                ctx.emit(
                    "R-SUB-009",
                    E.REFUTES,
                    f"Подписант МЧД {child.number} не совпадает с представителем МЧД {parent.number} по ИНН или СНИЛС",
                    [child.ref("signer.inn"), parent.ref("representative.1")],
                )
            )
        else:
            out.append(
                ctx.emit(
                    "R-SUB-009",
                    E.NOT_VERIFIED,
                    f"Подписанта МЧД {child.number} нельзя сверить с представителем МЧД {parent.number}",
                    [child.ref("signer.inn"), parent.ref("representative.1")],
                )
            )
    return out


# --- устав, текст, бумага, подпись ------------------------------------------


@rule("R-CHR-001")
def charter_limits(ctx: RuleContext) -> list[Finding]:
    amount = ctx.case.operation.amount_rub
    out = []
    thresholds = [fact for fact in ctx.charter_facts if fact.value is not None]
    if thresholds and (amount is None or amount <= 0):
        out.append(
            ctx.emit(
                "R-CHR-001",
                E.NEEDS_REVIEW,
                "Сумма операции не задана или неположительна: порог устава не с чем сравнить",
                [thresholds[0].ref],
            )
        )
    for fact in ctx.charter_facts:
        if fact.kind == "joint_signature":
            out.append(
                ctx.emit("R-CHR-001", E.NEEDS_REVIEW, "Устав требует совместных действий или подписей", [fact.ref])
            )
        elif fact.kind == "large_deal_approval" and fact.value is None:
            out.append(
                ctx.emit(
                    "R-CHR-001",
                    E.NEEDS_REVIEW,
                    "В уставе есть положение о крупных сделках без числового порога: "
                    "применимость к этой операции не установлена и одобрение не показано",
                    [fact.ref],
                )
            )
        elif fact.value is not None and amount is not None and amount > fact.value:
            out.append(
                ctx.emit(
                    "R-CHR-001",
                    E.NEEDS_REVIEW,
                    f"Сумма операции {amount:,} ₽ выше порога устава {fact.value:,} ₽".replace(",", " "),
                    [fact.ref],
                )
            )
    return out


@rule("R-CHR-002")
def charter_info(ctx: RuleContext) -> list[Finding]:
    doc = ctx.case.charter
    op = ctx.catalog.get(ctx.case.operation.code)
    required = bool(op and op.charter_required)
    if doc is None:
        if not required:
            return []
        return [
            ctx.emit(
                "R-CHR-002",
                E.NOT_VERIFIED,
                f"Для операции {ctx.case.operation.code} политика доказательств требует устав; документа нет",
                [],
            )
        ]
    ref = EvidenceRef(doc.doc_id, doc.sha256, "text")
    if not ctx.charter_facts:
        if ctx.case.charter_disposition == "no_applicable_restriction":
            return [
                ctx.emit(
                    "R-CHR-002",
                    E.INFO,
                    "В кейсе есть отметка, что для операции не нашли применимого ограничения. Это не вывод шаблонов",
                    [ref],
                )
            ]
        if required or (op and op.high_risk):
            return [
                ctx.emit(
                    "R-CHR-002",
                    E.NEEDS_REVIEW,
                    "Шаблоны не классифицировали устав: автоматического подтверждения нет",
                    [ref],
                )
            ]
        return [
            ctx.emit(
                "R-CHR-002",
                E.INFO,
                "Шаблоны не нашли ограничений в уставе; это не доказывает, что ограничений нет",
                [ref],
            )
        ]
    kinds = ", ".join(sorted({f.kind for f in ctx.charter_facts}))
    if op and op.high_risk and ctx.case.charter_disposition != "no_applicable_restriction":
        return [
            ctx.emit(
                "R-CHR-002",
                E.NEEDS_REVIEW,
                f"Найдены положения ({kinds}), но остальной текст устава шаблонами не закрыт",
                [ref],
            )
        ]
    return [ctx.emit("R-CHR-002", E.INFO, f"В уставе найдены положения: {kinds}", [ref])]


@rule("R-TXT-001")
def text_authorities(ctx: RuleContext) -> list[Finding]:
    leaf = ctx.chain.leaf
    if leaf is None:
        return []
    return [
        ctx.emit(
            "R-TXT-001",
            E.NEEDS_REVIEW,
            f"Текстовое полномочие: «{(a.text or '')[:160]}» — машинная проверка невозможна",
            [a.ref] if a.ref else [leaf.ref("number")],
        )
        for a in leaf.authorities
        if a.kind == "text"
    ]


@rule("R-PAP-001")
def paper_poa(ctx: RuleContext) -> list[Finding]:
    return [
        ctx.emit(
            "R-PAP-001",
            E.NEEDS_REVIEW,
            f"Бумажная доверенность {doc.doc_id}: нужна проверка сотрудником",
            [EvidenceRef(doc.doc_id, doc.sha256, "text", quote=doc.text[:200])],
        )
        for doc in ctx.case.paper_poas
    ]


@rule("R-SIG-001")
def signature(ctx: RuleContext) -> list[Finding]:
    out = []
    for poa in _poas_in_scope(ctx):
        result = ctx.ports.signature.verify(poa)
        ref = EvidenceRef(f"signature:{ctx.ports.signature.name}", poa.sha256, f"poa:{poa.number}", quote=result.detail)
        effect = {SignatureStatus.VERIFIED: E.SUPPORTS, SignatureStatus.INVALID: E.REFUTES}.get(
            result.status, E.NOT_VERIFIED
        )
        out.append(ctx.emit("R-SIG-001", effect, f"Подпись МЧД {poa.number}: {result.detail}", [ref]))
    return out


def run_rules(ctx: RuleContext) -> list[Finding]:
    ctx.charter_facts = extract_charter_facts(ctx.case.charter) if ctx.case.charter else []
    findings: list[Finding] = []
    for rule_id in ctx.pack.rules:
        if ctx.pack.rules[rule_id].status == "retired":
            continue
        findings.extend(RULES[rule_id](ctx))
    return findings
