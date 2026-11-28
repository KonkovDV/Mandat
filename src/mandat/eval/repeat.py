"""Повторные клиенты: сколько платежей уходит к человеку без реестра заключений юриста и с ним.

Прогон считают настоящие правила в строгом профиле в конфигурации пилота: все правила пакета утверждены
записями юриста (здесь — ключом этого прогона), реестр МЧД, ЕГРЮЛ и ЕФРСБ отвечают через адаптеры банка (здесь —
данными кейса), подпись МЧД подтвердило СКЗИ (здесь — заглушка «проверено»). Тогда кейс к человеку ведёт только
устав, и видна работа реестра. Без реестра каждый платёж, для которого политика банка требует устав, идёт к
юристу. С реестром к человеку идут первая операция каждой редакции устава (по ней юрист и записывает заключение),
операции вне области заключения и операции, на которых срабатывает ограничение устава из заключения.

Клиенты и операции синтетические (``eval/synth``): десять организаций, у каждой десять платежей раз в две недели
с 20.10.2026. Виды уставов: без ограничений на платежи, с порогом одобрения 300 000 ₽, с непонятным шаблонам
ограничением о недвижимости, с совместной подписью. Один устав меняет редакцию посреди прогона. Ложное
«подтверждено» — подтверждённый платёж, который устав клиента ограничивает: выше порога или без второй подписи.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandat.audit import journal
from mandat.audit.signing import Keyring, LocalSigner
from mandat.domain.conclusion import MSK, Conclusion
from mandat.domain.model import Case, PowerOfAttorney
from mandat.eval.synth import OP_DATE, build
from mandat.intake.case_loader import load_case_inline
from mandat.ports import FixtureBankruptcy, FixtureRegistry, Ports, SignatureResult, SignatureStatus
from mandat.review import conclusions as lifecycle
from mandat.rules.approvals import Approvals, sign_record
from mandat.rules.pack import default_catalog, default_pack
from mandat.services.verify import verify_case

TRUSTED = frozenset({"registry", "egrul", "bankruptcy", "signature"})
OPERATIONS_PER_CLIENT = 10
STEP_DAYS = 14
CAP_RUB = 1_000_000
THRESHOLD_RUB = 300_000
# Суммы платежей по очереди; у каждого клиента очередь сдвинута, поэтому крупный платёж приходит в разное время.
AMOUNTS = (480_000, 120_000, 950_000, 260_000, 1_400_000, 75_000, 640_000, 310_000, 2_100_000, 180_000)
BASE = "Устав общества. Генеральный директор действует без доверенности от имени общества."
CHARTERS = {
    "clean": BASE,
    "threshold": BASE
    + " Сделки на сумму свыше 300 000 рублей совершаются генеральным директором только после одобрения "
    "общим собранием участников.",
    "unparsed": BASE + " Сделки с недвижимым имуществом общества совершаются по решению совета директоров.",
    "joint": BASE + " Платёжные документы подписываются генеральным директором совместно с главным бухгалтером.",
}
AMENDMENT = " Устав в новой редакции, утверждённой общим собранием участников 15.12.2026."
# Клиент: вид устава и номер операции, с которой действует новая редакция (None — без смены).
CLIENTS: tuple[tuple[str, int | None], ...] = (
    ("clean", None),
    ("clean", 6),
    ("clean", None),
    ("clean", None),
    ("threshold", None),
    ("threshold", None),
    ("threshold", None),
    ("unparsed", None),
    ("unparsed", None),
    ("joint", None),
)
# Что юрист пишет, прочитав устав каждого вида. Ограничение устава попадает в заключение, а не гасится.
READING: dict[str, dict[str, Any]] = {
    "clean": {"outcome": "no_restriction", "restrictions": [], "max_amount_rub": CAP_RUB},
    "unparsed": {"outcome": "no_restriction", "restrictions": [], "max_amount_rub": CAP_RUB},
    "threshold": {
        "outcome": "restriction",
        "restrictions": [{"kind": "approval_threshold", "value_rub": THRESHOLD_RUB, "inclusive": False}],
        "max_amount_rub": None,
    },
    "joint": {"outcome": "restriction", "restrictions": [{"kind": "joint_signature"}], "max_amount_rub": None},
}
BASIS = {
    "clean": "Устав прочитан целиком: полномочия директора на подписание платежей не ограничены",
    "unparsed": "Ограничение устава касается только сделок с недвижимостью: на платежи оно не распространяется",
    "threshold": "Устав: сделки свыше 300 000 ₽ — только с одобрения общего собрания участников",
    "joint": "Устав: платёжные документы подписываются директором совместно с главным бухгалтером",
}
AUTHOR = lifecycle.Actor("lawyer-author", "reviewer", "lawyer")
APPROVER = lifecycle.Actor("lawyer-approver", "reviewer", "lawyer")
OPERATOR = lifecycle.Actor("operator", "operator")
BOUNDARY = (
    "синтетика: строгий профиль в конфигурации пилота (правила утверждены ключом прогона, источники — данные кейса, "
    "подпись — заглушка «проверено»); доля на документах банка — на пилоте"
)


class StandVerifiedSignature:
    """Подпись МЧД подтверждена: место СКЗИ банка в конфигурации пилота. Только для оценки."""

    name = "pilot-skzi-stand"
    is_stub = True

    def verify(self, poa: PowerOfAttorney) -> SignatureResult:
        return SignatureResult(SignatureStatus.VERIFIED, "подпись подтверждена (конфигурация пилота в оценке)")


def pilot_approvals(moment: datetime) -> Approvals:
    """Все правила пакета утверждены записями юриста, подписанными ключом этого прогона."""
    pack, signer = default_pack(), LocalSigner(Ed25519PrivateKey.generate())
    signed = moment - timedelta(days=60)
    records = [
        sign_record(
            spec,
            pack.pack_id,
            signer,
            approver_role="юрист банка (оценка)",
            norm_edition="редакция пакета правил",
            norm_checked_on=signed.date(),
            valid_until=(signed + timedelta(days=365)).date(),
            signed_at=signed,
            catalog_sha256=default_catalog().sha256,
        )
        for spec in pack.rules.values()
        if spec.status != "retired"
    ]
    keyring = Keyring.parse({"keys": [{"kid": signer.key_id, "status": "active"}]})
    return Approvals(tuple(records), keyring, "0" * 64, "оценка: конфигурация пилота")


@dataclass
class Operation:
    client: int
    index: int
    kind: str
    day: date
    amount: int
    version: int
    without: str = ""
    with_registry: str = ""
    why: str = ""
    restricted: bool = False


@dataclass
class RepeatResult:
    operations: list[Operation] = field(default_factory=list)
    conclusions: int = 0
    versions: int = 0
    applications: int = 0
    cleared: int = 0
    rechecks: int = 0
    journal_records: int = 0
    journal_ok: bool = True

    def as_dict(self) -> dict[str, Any]:
        total = len(self.operations)
        human = {"needs_review", "insufficient_data"}
        without = sum(op.without in human for op in self.operations)
        with_registry = sum(op.with_registry in human for op in self.operations)
        reasons: dict[str, int] = {}
        for op in self.operations:
            if op.with_registry in human:
                reasons[op.why] = reasons.get(op.why, 0) + 1
        restricted = [op for op in self.operations if op.restricted]
        return {
            "profile": "strict",
            "clients": len({op.client for op in self.operations}),
            "operations": total,
            "charter_versions": self.versions,
            "to_human_without_registry": without,
            "to_human_with_registry": with_registry,
            "share_without_registry": round(without / total, 4) if total else None,
            "share_with_registry": round(with_registry / total, 4) if total else None,
            "conclusions_approved": self.conclusions,
            "applications": self.applications,
            "cleared_by_conclusions": self.cleared,
            "rechecks": self.rechecks,
            "reasons_with_registry": dict(sorted(reasons.items())),
            "restricted_operations": len(restricted),
            "false_confirmed_without_registry": sum(op.without == "confirmed" for op in restricted),
            "false_confirmed_with_registry": sum(op.with_registry == "confirmed" for op in restricted),
            "journal": {"records": self.journal_records, "intact": self.journal_ok},
            "boundary": BOUNDARY,
        }


def _case(client: int, index: int, kind: str, version: int) -> tuple[Case, int]:
    """Платёж клиента: МЧД синтетики, устав нужной редакции, дата и сумма операции, снимки источников на дату."""
    scenario = build("ok", seed=500 + client)
    manifest = scenario.inline_manifest()
    day = OP_DATE + timedelta(days=STEP_DAYS * index)
    amount = AMOUNTS[(index + client) % len(AMOUNTS)]
    manifest["operation"] = {**manifest["operation"], "date": day.isoformat(), "amount_rub": amount}
    manifest["case_id"] = f"repeat-{client:02d}-{index:02d}"
    for doc in manifest["documents"]:
        if doc["kind"] == "charter_text":
            doc["content"] = CHARTERS[kind] + (AMENDMENT if version > 1 else "")
        if doc["kind"] == "egrul_json":
            doc["content"] = {**doc["content"], "extracted_at": (day - timedelta(days=1)).isoformat()}
    manifest["registry"] = {
        number: {"status": "active", "observed_at": f"{day.isoformat()}T09:00:00+03:00"}
        for number in manifest["registry"]
    }
    case = load_case_inline(manifest)
    case = dataclasses.replace(case, bankruptcy={str(case.organization.inn): "none", str(case.requester.inn): "none"})
    return case, amount


def _restricted(kind: str, amount: int) -> bool:
    """Устав клиента ограничивает этот платёж: человек обязан его увидеть."""
    return kind == "joint" or (kind == "threshold" and amount > THRESHOLD_RUB)


def _why(report: dict[str, Any]) -> str:
    """Почему с реестром платёж всё же ушёл к человеку."""
    findings = report.get("findings") or []
    if any(f["rule_id"] == "R-CHR-001" and "заключение юриста №" in f["message"] for f in findings):
        return "ограничение устава из заключения"
    notes = [f["message"] for f in findings if f["rule_id"] == "R-CHR-002" and "не применено" in f["message"]]
    if any("выше предела заключения" in note for note in notes):
        return "сумма выше предела заключения"
    if any("другой редакции" in note or "на перепроверке" in note for note in notes):
        return "новая редакция устава"
    return "первая операция редакции устава"


def run(*, journal_path: Path | None = None) -> RepeatResult:
    """Прогон без реестра и с реестром. ``journal_path`` — журнал сценария (``mandat showcase``)."""
    moment = datetime.combine(OP_DATE, time(10, 0), MSK)
    approvals = pilot_approvals(moment)
    result = RepeatResult()
    if journal_path is not None:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        for stale in (journal_path, journal.witness_path(journal_path)):
            stale.unlink(missing_ok=True)
    for client, (kind, amended) in enumerate(CLIENTS):
        bodies: dict[str, tuple[dict[str, Any], int]] = {}
        versions: set[int] = set()
        for index in range(OPERATIONS_PER_CLIENT):
            version = 2 if amended is not None and index >= amended else 1
            versions.add(version)
            case, amount = _case(client, index, kind, version)
            now = datetime.combine(case.operation.on_date, time(10, 0), MSK)
            ports = Ports(FixtureRegistry(case.registry), StandVerifiedSignature(), FixtureBankruptcy(case.bankruptcy))
            op = Operation(client, index, kind, case.operation.on_date, amount, version)
            op.restricted = _restricted(kind, amount)
            plain = verify_case(
                case, profile="strict", ports=ports, trusted_sources=TRUSTED, now=now, approvals=approvals
            )
            op.without = plain.verdict.value
            conclusions = [Conclusion.from_record(body, ver) for body, ver in bodies.values()]
            report = verify_case(
                case,
                profile="strict",
                ports=ports,
                trusted_sources=TRUSTED,
                now=now,
                approvals=approvals,
                conclusions=conclusions,
            ).as_dict()
            op.with_registry = report["verdict"]
            section = report.get("charter_conclusions") or {}
            charter_status = report["checks"]["charter"]["status"]
            for row in section.get("applied") or []:
                result.applications += 1
                result.cleared += charter_status not in {"review", "not_verified", "refuted", "missing"}
                if journal_path is not None:
                    lifecycle.record_apply(
                        journal_path,
                        actor=OPERATOR,
                        case_id=report["case_id"],
                        report_hash=report["reproducibility_hash"],
                        applied=row,
                        via="showcase",
                    )
            if journal_path is not None:
                journal.append(
                    journal_path,
                    action="verify",
                    actor=OPERATOR.name,
                    role=OPERATOR.role,
                    case_id=report["case_id"],
                    payload_hash=report["reproducibility_hash"],
                    details={"verdict": report["verdict"], "profile": "strict"},
                    now=now,
                )
            for row in section.get("previous") or []:
                cid = str(row["id"])
                body, ver = bodies[cid]
                if body.get("status") == "approved":
                    moved = lifecycle.recheck(
                        body,
                        ver,
                        actor=OPERATOR,
                        case_id=report["case_id"],
                        charter_sha256=str(case.charter.sha256 if case.charter else ""),
                        now=now,
                        journal_path=journal_path,
                    )
                    bodies[cid] = (moved, ver + 1)
                    result.rechecks += 1
            if op.with_registry in {"needs_review", "insufficient_data"}:
                op.why = _why(report)
                covered = case.charter is not None and any(
                    body.get("charter_sha256") == case.charter.sha256 for body, _ in bodies.values()
                )
                if not covered:
                    body = _lawyer_reads(case, kind, client, index, now, journal_path)
                    bodies[str(body["id"])] = (body, 2)
                    result.conclusions += 1
            result.operations.append(op)
        result.versions += len(versions)
    if journal_path is not None:
        check = journal.verify_journal(journal_path)
        result.journal_records, result.journal_ok = check.records, check.ok
    return result


def _lawyer_reads(
    case: Case, kind: str, client: int, index: int, now: datetime, journal_path: Path | None
) -> dict[str, Any]:
    """Юрист разобрал первый кейс редакции устава: заключение пишет один юрист, утверждает второй."""
    assert case.charter is not None and case.organization.ogrn is not None
    fields = {
        "ogrn": case.organization.ogrn,
        "charter_sha256": case.charter.sha256,
        "basis": BASIS[kind],
        "operations": ["PAYMENT_SIGN"],
        "valid_until": (now.date() + timedelta(days=365)).isoformat(),
        **READING[kind],
    }
    codes = frozenset(default_catalog().operations)
    draft = lifecycle.create(
        fields,
        actor=AUTHOR,
        conclusion_id=f"cnc-{client:06x}{index:06x}",
        now=now,
        operations=codes,
        journal_path=journal_path,
    )
    return lifecycle.approve(
        draft,
        1,
        actor=APPROVER,
        reason="сверено с уставом",
        now=now + timedelta(minutes=30),
        operations=codes,
        journal_path=journal_path,
        expected_version=1,
    )


def render_text(metrics: dict[str, Any]) -> str:
    """Итог прогона словами, для ``mandat showcase``."""
    total = metrics["operations"]

    def share(value: int) -> str:
        return f"{value / total:.0%}".replace("%", " %") if total else "—"

    reasons = "; ".join(f"{name} — {count}" for name, count in metrics["reasons_with_registry"].items())
    lines = [
        "«Мандат»: повторный клиент — устав читает юрист один раз на редакцию "
        "(стенд: синтетика, строгий профиль в конфигурации пилота)",
        "",
        f"{metrics['clients']} клиентов, {total} платежей, для которых банк требует устав; "
        f"редакций устава: {metrics['charter_versions']}.",
        f"Без реестра заключений к юристу уходят {metrics['to_human_without_registry']} из {total} "
        f"({share(metrics['to_human_without_registry'])}).",
        f"С реестром — {metrics['to_human_with_registry']} из {total} ({share(metrics['to_human_with_registry'])}): "
        f"{reasons}.",
        f"Заключений записано и утверждено вторым юристом: {metrics['conclusions_approved']}; они применены "
        f"{metrics['applications']} раз и закрыли устав в {metrics['cleared_by_conclusions']} платежах.",
        f"Платежей, которые устав ограничивает: {metrics['restricted_operations']}; ложных «подтверждено» среди них "
        f"с реестром: {metrics['false_confirmed_with_registry']}.",
    ]
    journal_line = metrics.get("journal") or {}
    if journal_line.get("records"):
        state = "цела" if journal_line.get("intact") else "НАРУШЕНА"
        lines.append(f"Журнал сценария: {_records(journal_line['records'])}, цепочка {state}.")
    return "\n".join(lines)


def _records(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} запись"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} записи"
    return f"{n} записей"


def write(metrics: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
