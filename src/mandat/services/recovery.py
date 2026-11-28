"""Проверка после восстановления из резервной копии: запущенное копирование ещё не доказывает восстановление.

Чек-лист воркшопа «Данные, безопасность и комплаенс» (Академия Финтеха, 20.11.2026): «запущено копирование ≠
проверено восстановление»; «удаление — не одна строка в базе: учесть остальные копии и восстановление».
NIST SP 800-53 rev. 5, CP-9 и CP-10: резервные копии проверяются восстановлением, система после него приводится в
известное состояние. Для ПДн это ещё и 152-ФЗ, ст. 21: кейс, удалённый по основанию закона, не должен вернуться
из копии, снятой до удаления.

Восстанавливаются хранилище отчётов и списки статусов. Журнал аудита ведётся дальше: он только дописывается,
его хвост держат файл-свидетель и, по настройке, внешний якорь, поэтому журнал — опорная точка. Проверка сверяет
с ним восстановленное состояние:

1. журнал цел: хеш-цепочка, подписи, свидетель, внешний якорь (``journal.verify_journal``); с повреждённым
   журналом сверять не с чем — дальше проверка не идёт и ничего не чинит;
2. удалённое не воскресло: если последнее событие кейса в журнале — ``erase_case``, отчёта и истории разбора
   в хранилище быть не должно. Кейс, созданный снова после удаления, пишется в журнал как ``verify``, и его
   отчёт остаётся;
3. отзыв не откатился: каждое ``status_change`` журнала отражено битом в восстановленном списке статусов.
   Иначе отозванное после копии удостоверение снова прошло бы проверку статуса;
4. отчёты читаются: запись, которую нельзя расшифровать ключом хранилища, — нарушение, а не пропуск.

``repair``: воскресшие отчёты удаляются снова, разбор закрывается без истории, в журнал пишется ``erase_case`` с
пометкой ``reapplied_after_restore``; с ключом подписи списков отозванные и приостановленные биты выставляются
снова и списки сохраняются. Отчёты, созданные после снятия копии, потеряны — это цена точки восстановления (RPO),
их число печатается, но проверку не валит.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mandat.api.sealing import SealError
from mandat.api.storage import ReviewVersionConflict, Store
from mandat.audit import journal
from mandat.audit.signing import Signer
from mandat.audit.status_list import PURPOSES, StatusRegistry, get_bit

ACTOR = "restore-check"


@dataclass
class RestoreFinding:
    kind: str  # erased_resurrected | review_resurrected | status_rollback | unreadable
    case_id: str | None
    detail: str
    repaired: bool = False


@dataclass
class RestoreReport:
    journal_ok: bool
    journal_reason: str | None
    journal_records: int
    cases_in_journal: int
    erased_in_journal: int
    lost_after_backup: int
    status_sequence: int | None
    findings: list[RestoreFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.journal_ok and all(f.repaired for f in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": self.ok}


def _last_events(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Последнее событие жизни отчёта по каждому кейсу: создание (``verify``), удаление (``erase_case``) или
    вытеснение квотой (``evict_case``)."""
    last: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("action") in ("verify", "erase_case", "evict_case") and isinstance(record.get("case_id"), str):
            last[record["case_id"]] = record
    return last


def _close_review(store: Store, case_id: str, reason: str) -> bool:
    """Разбор удалённого кейса — без истории, как при удалении через API. True — разбор пришлось закрыть."""
    for attempt in range(3):
        stored = store.review_get(case_id)
        if stored is None or stored[0].get("erased"):
            return False
        previous, version = stored
        keep = ("case_id", "report_hash", "created_by", "required_specialization")
        tombstone: dict[str, Any] = {key: previous.get(key) for key in keep}
        tombstone.update(state="closed", history=[], superseded=[], erased={"reason": reason})
        try:
            store.review_put(case_id, tombstone, version)
            return True
        except ReviewVersionConflict:
            if attempt == 2:
                raise
    return False


def _erased(
    store: Store,
    case_id: str,
    erase: dict[str, Any],
    journal_path: Path,
    repair: bool,
    findings: list[RestoreFinding],
) -> None:
    reason = str((erase.get("details") or {}).get("reason") or "")
    try:
        found = store.get(case_id)
    except SealError as exc:
        findings.append(RestoreFinding("unreadable", case_id, f"запись удалённого кейса не читается: {exc}"))
        return
    review = store.review_get(case_id)
    review_back = review is not None and not review[0].get("erased")
    if found is None and not review_back:
        return
    when = str(erase.get("at") or "")[:19]
    if found is not None:
        detail = f"отчёт удалён {when} по основанию {reason or 'не указано'}, но вернулся из копии"
        findings.append(RestoreFinding("erased_resurrected", case_id, detail))
    if review_back:
        findings.append(RestoreFinding("review_resurrected", case_id, "история разбора удалённого кейса вернулась"))
    if not repair:
        return
    journal.append(
        journal_path,
        action="erase_case",
        actor=ACTOR,
        role="admin",
        case_id=case_id,
        payload_hash=(found[0].get("reproducibility_hash") if found else erase.get("payload_hash")),
        reason=str(erase.get("reason") or ""),
        details={
            "reason": reason,
            "categories": list((erase.get("details") or {}).get("categories") or []),
            "system": "Мандат",
            "via": ACTOR,
            "reapplied_after_restore": True,
        },
    )
    _close_review(store, case_id, reason)
    store.erase(case_id)
    for finding in findings:
        if finding.case_id == case_id and finding.kind in ("erased_resurrected", "review_resurrected"):
            finding.repaired = True


def _status(
    records: list[dict[str, Any]],
    status_dir: Path,
    signer: Signer | None,
    findings: list[RestoreFinding],
) -> int:
    registry = StatusRegistry.open(status_dir)
    changed = False
    for record in records:
        if record.get("action") != "status_change":
            continue
        purpose = (record.get("details") or {}).get("status")
        raw = record.get("payload_hash")
        if purpose not in PURPOSES or not isinstance(raw, str) or not raw.isdigit():
            continue
        index = int(raw)
        known = str(index) in registry.entries
        if known and index < registry.size and get_bit(registry.lists[purpose], index):
            continue
        detail = (
            f"удостоверение {index}: в журнале {purpose} ({record.get('reason')}), "
            f"в восстановленном списке статусов его {'нет' if not known else 'бита нет'}"
        )
        finding = RestoreFinding("status_rollback", str(record.get("case_id")), detail)
        findings.append(finding)
        if signer is not None and 0 <= index < registry.size:
            if not known:
                # Удостоверение выдано после снятия копии: восстанавливаем запись о нём, чтобы бит не потерялся.
                registry.entries[str(index)] = {"case_id": record.get("case_id"), "restored_from_journal": True}
            registry.mark(index, purpose, f"{ACTOR}: {record.get('reason')}")
            finding.repaired = changed = True
    if changed and signer is not None:
        registry.save(signer)
    return registry.sequence


def check_restore(
    journal_path: Path,
    store: Store,
    *,
    status_dir: Path | None = None,
    repair: bool = False,
    signer: Signer | None = None,
    verify: Callable[[Path], journal.JournalCheck] = journal.verify_journal,
) -> RestoreReport:
    """Сверить восстановленные хранилище и списки статусов с журналом. ``repair`` — повторить удаления и отзывы."""
    check = verify(journal_path)
    report = RestoreReport(
        journal_ok=check.ok,
        journal_reason=None if check.ok else check.reason,
        journal_records=check.records,
        cases_in_journal=0,
        erased_in_journal=0,
        lost_after_backup=0,
        status_sequence=None,
    )
    if not check.ok:
        return report
    records = journal.read_journal(journal_path)
    last = _last_events(records)
    report.cases_in_journal = len(last)
    for case_id, record in sorted(last.items()):
        if record["action"] == "erase_case":
            report.erased_in_journal += 1
            _erased(store, case_id, record, journal_path, repair, report.findings)
            continue
        if record["action"] == "evict_case":
            continue  # отчёт вытеснен квотой: его нет в хранилище по правилу, а не из-за копии
        if (record.get("details") or {}).get("via") != "api":
            continue  # отчёты CLI и показа в хранилище API не попадают
        try:
            if store.get(case_id) is None:
                report.lost_after_backup += 1
        except SealError as exc:
            report.findings.append(RestoreFinding("unreadable", case_id, f"запись не читается: {exc}"))
    if status_dir is not None:
        report.status_sequence = _status(records, status_dir, signer if repair else None, report.findings)
    return report
