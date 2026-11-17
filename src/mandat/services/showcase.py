"""Сценарий «захват компании останавливается до платежа»: вся цепочка «Мандата» одной командой.

    mandat showcase

Кейс проверяется, банк выпускает удостоверение на один платёж, ДБО получает допуск. Повтор и завышенная сумма
отвергаются. Потом в ЕГРЮЛ меняется руководитель, регистрируется новая МЧД и приходит заявка на смену
реквизитов: мониторинг видит признаки захвата и приостанавливает удостоверения организации. Следующий платёж по
удостоверению, выпущенному до события, отвергается до списания денег. Каждое решение — в журнале, и включение
записи об отказе доказывается деревом Меркла.

Это стенд: данные вымышлены, профиль ``demo``, часы сценария — день операции кейса. Допуск считает та же
функция, что стоит за ``POST /v1/operations/authorize`` (``services.admission.decide``), с хранилищем в памяти.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandat.api.storage import MemoryStore
from mandat.audit import attestation, journal, merkle
from mandat.audit.attestation import BANK_TZ, OperationRequest
from mandat.audit.signing import Keyring
from mandat.audit.status_list import StatusRegistry
from mandat.intake.case_loader import load_case_dir
from mandat.services import admission
from mandat.services.monitoring import evaluate, parse_events
from mandat.services.verify import verify_case

CASE = "14_old_director_charter_cleared"
AUDIENCE = "bank-dbo-msb"


@dataclass
class Step:
    title: str
    outcome: str
    detail: str = ""


@dataclass
class Showcase:
    steps: list[Step] = field(default_factory=list)
    journal_records: int = 0
    journal_ok: bool = False
    proof_ok: bool = False
    merkle_root: str = ""
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": [s.__dict__ for s in self.steps],
            "journal": {"records": self.journal_records, "intact": self.journal_ok, "merkle_root": self.merkle_root},
            "refusal_inclusion_proof_verified": self.proof_ok,
            "seconds": round(self.seconds, 3),
            "boundary": "стенд: данные вымышлены, профиль demo, часы сценария — день операции кейса",
        }


def _request(report: dict[str, Any], nonce: str, amount: int) -> OperationRequest:
    return OperationRequest(
        operation=report["operation"]["code"],
        amount_rub=amount,
        representative_inn=str(report["requester"]["inn"]),
        audience=AUDIENCE,
        nonce=nonce,
        organization_inn=str(report["organization"]["inn"]),
        operation_date=datetime.fromisoformat(report["operation"]["date"]).date(),
    )


def _rub(amount: int) -> str:
    """480000 → «480 000»: разряды через неразрывный пробел."""
    return f"{amount:,}".replace(",", "\u00a0")


def _outcome(problems: list[str]) -> tuple[str, str]:
    return ("ДОПУЩЕНО", "") if not problems else ("ОТКАЗ", problems[0])


def run(root: Path, out: Path | None = None) -> Showcase:
    """Прогнать сценарий в каталоге ``out`` (по умолчанию — во временном) и вернуть шаги."""
    started = time.perf_counter()
    workdir = Path(tempfile.mkdtemp(prefix="mandat-showcase-")) if out is None else out
    workdir.mkdir(parents=True, exist_ok=True)
    log = workdir / "journal.jsonl"
    log.unlink(missing_ok=True)
    head = log.with_name(log.name + ".head")
    head.unlink(missing_ok=True)
    show = Showcase()

    case = load_case_dir(root / "examples" / "cases" / CASE)
    day = case.operation.on_date
    # Часы сценария: день операции кейса, 10:00 по Москве. Удостоверение и списки статусов — на эти часы.
    clock = datetime(day.year, day.month, day.day, 10, 0, tzinfo=BANK_TZ)
    report = verify_case(case, now=clock).as_dict()
    amount = int(report["operation"]["amount_rub"])
    org = report["organization"]
    show.steps.append(
        Step(
            f"Проверка кейса {CASE}",
            report["verdict_label"].upper(),
            f"{org['name']}, подписание платежа на {_rub(amount)} ₽, {day:%d.%m.%Y}",
        )
    )
    journal.append(
        log,
        action="verify",
        actor="showcase",
        role="operator",
        case_id=report["case_id"],
        payload_hash=report["reproducibility_hash"],
        details={"verdict": report["verdict"]},
        now=clock,
    )

    key = Ed25519PrivateKey.generate()
    pseudonym_key = b"s" * 32
    status_dir = workdir / "status"
    for stale in ("registry.json", "revocation.json", "suspension.json"):
        (status_dir / stale).unlink(missing_ok=True)
    registry = StatusRegistry.open(status_dir)

    def issue(nonce: str, at: datetime) -> dict[str, Any]:
        index = registry.allocate(
            {
                "case_id": report["case_id"],
                "org_inn": org["inn"],
                "verdict": report["verdict"],
                "poa_numbers": report["chain"]["poa_numbers"],
                "chain_kind": report["chain"]["kind"],
            }
        )
        cred = attestation.issue(
            report,
            key,
            now=at,
            status=registry.credential_status(index),
            pseudonym_key=pseudonym_key,
            audience=AUDIENCE,
            nonce=nonce,
        )
        registry.save(key, now=at)
        return cred

    first_nonce, second_nonce = "showcase-payment-1-0001", "showcase-payment-2-0002"
    first = issue(first_nonce, clock)
    show.steps.append(
        Step(
            "Удостоверение на платёж",
            "ВЫПУЩЕНО",
            f"действует до {datetime.fromisoformat(first['validUntil']):%d.%m.%Y %H:%M}; "
            "привязано к организации, дню, операции, "
            f"сумме, представителю, получателю {AUDIENCE} и одноразовому nonce",
        )
    )
    config = admission.AdmissionConfig(Keyring.of([key.public_key()]), status_dir, pseudonym_key)
    store = MemoryStore()

    def admit(cred: dict[str, Any], request: OperationRequest, at: datetime, title: str) -> None:
        problems = admission.decide(
            admission.AdmissionRequest(cred, request, "payment"), config, store, now=at, protected=False
        )
        outcome, why = _outcome(problems)
        show.steps.append(Step(title, outcome, why))
        journal.append(
            log,
            action="authorize_operation",
            actor="dbo",
            role="operator",
            case_id=report["case_id"],
            details={"allowed": not problems, "problems": len(problems), "audience": AUDIENCE},
            now=at,
        )

    admit(
        first,
        _request(report, first_nonce, amount),
        clock + timedelta(minutes=1),
        f"ДБО: платёж {_rub(amount)} ₽",
    )
    admit(first, _request(report, first_nonce, amount), clock + timedelta(minutes=2), "ДБО: повтор того же запроса")
    bigger = amount * 4
    admit(
        first,
        _request(report, first_nonce, bigger),
        clock + timedelta(minutes=3),
        f"ДБО: платёж {_rub(bigger)} ₽ по тому же удостоверению",
    )
    second = issue(second_nonce, clock + timedelta(minutes=5))

    # Признаки захвата: смена руководителя, новая МЧД и заявка на смену реквизитов за несколько часов.
    events_at = clock + timedelta(minutes=20)
    payload = {
        "rights": [
            {
                "right_id": "R-PAY",
                "org_inn": org["inn"],
                "representative_inn": report["requester"]["inn"],
                "operation": report["operation"]["code"],
                "poa_numbers": report["chain"]["poa_numbers"],
            }
        ],
        "events": [
            {
                "kind": "EIO_CHANGED",
                "org_inn": org["inn"],
                "at": events_at.isoformat(),
                "payload": {"grn": "2267700999001"},
            },
            {
                "kind": "NEW_POA",
                "org_inn": org["inn"],
                "at": (events_at + timedelta(minutes=40)).isoformat(),
                "payload": {"number": "11111111-2222-3333-4444-555555555555"},
            },
            {
                "kind": "REQUISITES_CHANGE_REQUEST",
                "org_inn": org["inn"],
                "at": (events_at + timedelta(minutes=55)).isoformat(),
                "payload": {"field": "счёт для выплат"},
            },
        ],
    }
    rights, events = parse_events(payload)
    alerts = [a for a in evaluate(rights, events) if a.attestation_action]
    marked = 0
    for alert in alerts:
        for index in registry.find(org_inn=alert.org_inn, chain_kind=alert.chain_kind):
            if registry.mark(index, str(alert.attestation_action), alert.kind):
                marked += 1
                journal.append(
                    log,
                    action="status_change",
                    actor="monitor",
                    role="security",
                    case_id=report["case_id"],
                    payload_hash=str(index),
                    reason=alert.kind,
                    details={"status": alert.attestation_action},
                    now=events_at + timedelta(minutes=56),
                )
    after_event = events_at + timedelta(minutes=57)
    registry.save(key, now=after_event)
    kinds = ", ".join(sorted({a.kind for a in alerts})) or "нет"
    show.steps.append(
        Step(
            "Событие: смена руководителя, новая МЧД и заявка на смену реквизитов",
            "ПРИЗНАКИ ЗАХВАТА",
            f"тревога: {kinds}; приостановлено удостоверений организации: {marked}",
        )
    )
    admit(
        second,
        _request(report, second_nonce, amount),
        after_event + timedelta(minutes=1),
        "ДБО: следующий платёж по удостоверению, выпущенному до события",
    )

    check = journal.verify_journal(log)
    records = journal.read_journal(log)
    leaves = merkle.journal_leaves(records)
    tree = merkle.root(leaves)
    last = len(leaves) - 1
    proof = merkle.inclusion_proof(last, leaves)
    show.journal_records = check.records
    show.journal_ok = bool(check.ok)
    show.proof_ok = merkle.verify_inclusion(leaves[last], last, len(leaves), proof, tree)
    show.merkle_root = tree.hex()
    show.seconds = time.perf_counter() - started
    (workdir / "showcase.json").write_text(json.dumps(show.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return show


def render_text(show: Showcase) -> str:
    lines = ["«Мандат»: захват компании останавливается до платежа (стенд: данные вымышлены, профиль demo)", ""]
    for n, step in enumerate(show.steps, start=1):
        lines.append(f"{n}. {step.title} — {step.outcome}")
        if step.detail:
            lines.append(f"   {step.detail}")
    lines += [
        "",
        f"Журнал: {show.journal_records} записей, цепочка {'цела' if show.journal_ok else 'НАРУШЕНА'}; "
        f"включение записи об отказе {'доказано' if show.proof_ok else 'НЕ ДОКАЗАНО'} "
        f"(дерево Меркла, корень {show.merkle_root[:16]}…)",
        f"Сценарий занял {show.seconds:.2f} с.".replace(".", ",", 1),
    ]
    return "\n".join(lines)
