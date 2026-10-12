"""Проверка кейса: граф полномочий → правила → свёртка → следующий шаг → отчёт.

Отчёт воспроизводим: ``reproducibility_hash`` считается по всему, кроме времени
формирования, поэтому одинаковый вход даёт одинаковый хеш (проверено тестом).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from mandat import ENGINE
from mandat.data import sha256_json
from mandat.domain.graph import AuthorityChain, build_chain
from mandat.domain.model import Case, Finding
from mandat.domain.verdict import CHECK_LABEL_RU, VERDICT_LABEL_RU, LegalEffect, Verdict, fold, get_profile
from mandat.ports import FixtureRegistry, NotConnectedSignature, Ports
from mandat.rules.checks import RuleContext, run_rules
from mandat.rules.classifier import Classifier, default_classifier
from mandat.rules.pack import OperationCatalog, RulePack, default_catalog, default_pack
from mandat.services.next_steps import build_next_steps, route, strongest_objection
from mandat.services.rights import propose_right

# Справки, которые сотрудник должен увидеть сразу: они не меняют вердикт, но требуют внимания.
SIGNAL_RULES = {"R-EIO-002", "R-SUB-008"}

_SEVERITY = {
    LegalEffect.REFUTES: 0,
    LegalEffect.NOT_VERIFIED: 1,
    LegalEffect.NEEDS_REVIEW: 2,
    LegalEffect.SUPPORTS: 3,
    LegalEffect.INFO: 4,
}


@dataclass(frozen=True)
class CaseReport:
    case_id: str
    verdict: Verdict
    profile: str
    findings: tuple[Finding, ...]
    checks: dict[str, str]
    chain: AuthorityChain
    warnings: tuple[str, ...]
    next_steps: tuple[dict[str, Any], ...]
    routing: dict[str, Any]
    proposed_right: dict[str, Any] | None
    operation: dict[str, Any]
    organization: dict[str, Any]
    requester: dict[str, Any]
    inputs: tuple[dict[str, str], ...]
    rule_pack: dict[str, Any]
    catalog: dict[str, Any]
    classifier: dict[str, Any]
    stand: tuple[str, ...]
    objection: str
    generated_at: str

    @property
    def verdict_label(self) -> str:
        return VERDICT_LABEL_RU[self.verdict]

    def body(self) -> dict[str, Any]:
        return {
            "engine": ENGINE,
            "case_id": self.case_id,
            "verdict": self.verdict.value,
            "verdict_label": self.verdict_label,
            "profile": self.profile,
            "operation": self.operation,
            "organization": self.organization,
            "requester": self.requester,
            "chain": {
                "kind": self.chain.kind,
                "links": self.chain.describe(),
                "poa_numbers": [p.number for p in self.chain.links if p.number],
                "notes": list(self.chain.notes),
            },
            "checks": {
                k: {"status": self.checks[k], "label": CHECK_LABEL_RU.get(k, k)}
                for k in sorted(self.checks, key=lambda c: list(CHECK_LABEL_RU).index(c) if c in CHECK_LABEL_RU else 99)
            },
            "findings": [f.as_dict() for f in self.findings],
            "warnings": list(self.warnings),
            "next_steps": list(self.next_steps),
            "routing": self.routing,
            "proposed_right": self.proposed_right,
            "inputs": list(self.inputs),
            "rule_pack": self.rule_pack,
            "catalog": self.catalog,
            "classifier": self.classifier,
            "stand": list(self.stand),
            "objection": self.objection,
            "signals": [f.message for f in self.findings if f.effect is LegalEffect.INFO and f.rule_id in SIGNAL_RULES],
        }

    @property
    def reproducibility_hash(self) -> str:
        return sha256_json(self.body())

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.body(),
            "generated_at": self.generated_at,
            "reproducibility_hash": self.reproducibility_hash,
        }


def _stand(profile: str, notes: list[str]) -> list[str]:
    if profile == "demo":
        notes.append("Профиль demo считает результат на фикстурах кейса. Это не допуск к операции в банке")
    elif profile == "strict":
        notes.append("Профиль strict не подтверждает полномочия по черновикам правил и снимкам из пакета кейса")
    return notes


def stand_notes(ports: Ports, catalog: OperationCatalog, classifier: Classifier) -> list[str]:
    """Честная плашка стенда: что в этой проверке заглушка, а не система банка."""
    notes = []
    if getattr(ports.registry, "is_stub", False):
        notes.append("Реестр МЧД — заглушка: статусы взяты из пакета кейса, а не из реестра ФНС")
    if getattr(ports.signature, "is_stub", False):
        notes.append("КЭП не проверялась: СКЗИ не подключены. Подпись Ed25519 — не ГОСТ и не внешний контроль W3C")
    notes.append("ЕГРЮЛ — сведения из пакета кейса в нормализованном JSON, а не ответ ФНС")
    if catalog.synthetic_codes or classifier.synthetic:
        notes.append("Коды полномочий синтетические (SYN-…), а не коды классификатора из ЕСНСИ")
    return notes


def default_ports(case: Case) -> Ports:
    return Ports(registry=FixtureRegistry(case.registry), signature=NotConnectedSignature())


def verify_case(
    case: Case,
    *,
    profile: str = "demo",
    ports: Ports | None = None,
    pack: RulePack | None = None,
    catalog: OperationCatalog | None = None,
    classifier: Classifier | None = None,
    advisory: list[Finding] | None = None,
    now: datetime | None = None,
) -> CaseReport:
    """``advisory`` — находки модели: попадают в отчёт как справка и не влияют на вердикт (ADR-001)."""
    prof = get_profile(profile)
    pack = pack or default_pack()
    catalog = catalog or default_catalog()
    classifier = classifier or default_classifier()
    ports = ports or default_ports(case)
    chain = build_chain(case)
    ctx = RuleContext(
        case=case,
        chain=chain,
        pack=pack,
        catalog=catalog,
        ports=ports,
        classifier=classifier,
        profile_name=prof.name,
    )
    findings = run_rules(ctx)
    findings.extend(f for f in (advisory or []) if f.origin == "advisory")
    findings.sort(key=lambda f: (_SEVERITY[f.effect], f.rule_id, f.finding_id))
    applicable = set(chain.applicable_checks(case))
    op_spec = catalog.get(case.operation.code)
    if op_spec and op_spec.charter_required:
        applicable.add("charter")
    result = fold(findings, prof, applicable)
    warnings = list(result.warnings)
    warnings.extend(chain.notes)
    objection = strongest_objection(result.verdict, findings, warnings)
    # Проверки, которые профиль не выполняет, не попадают в список действий: они уже названы в предупреждениях.
    next_steps = build_next_steps([f for f in findings if f.check not in prof.waived], result.verdict)
    return CaseReport(
        case_id=case.case_id,
        verdict=result.verdict,
        profile=prof.name,
        findings=tuple(findings),
        checks=result.checks,
        chain=chain,
        warnings=tuple(dict.fromkeys(warnings)),
        next_steps=tuple(next_steps),
        routing=route(result.verdict, findings, high_risk=bool(op_spec and op_spec.high_risk)),
        proposed_right=propose_right(result.verdict, case, chain, op_spec),
        operation={
            "code": case.operation.code,
            "name": op_spec.name if op_spec else None,
            "date": case.operation.on_date.isoformat(),
            "amount_rub": case.operation.amount_rub,
        },
        organization={
            "inn": case.organization.inn,
            "ogrn": case.organization.ogrn,
            "name": case.organization.name,
        },
        requester={"full_name": case.requester.full_name, "inn": case.requester.inn},
        inputs=case.inputs,
        rule_pack=pack.summary(),
        catalog={"catalog_id": catalog.catalog_id, "version": catalog.version, "sha256": catalog.sha256},
        classifier=classifier.summary(),
        stand=tuple(_stand(prof.name, stand_notes(ports, catalog, classifier))),
        objection=objection,
        generated_at=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
    )
