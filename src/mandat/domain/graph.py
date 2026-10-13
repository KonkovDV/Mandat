"""Граф полномочий: организация → руководитель по ЕГРЮЛ → МЧД → передоверие → представитель.

Строим путь от представителя, который просит права, к корневой доверенности,
выданной руководителем. Если представитель сам является руководителем на дату
операции, путь состоит из одной записи ЕГРЮЛ (действует без доверенности, ст. 53 ГК РФ).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from mandat.domain.model import Case, ExecutiveRecord, PowerOfAttorney


@dataclass(frozen=True)
class AuthorityChain:
    kind: str  # "eio_direct" | "poa" | "none"
    links: tuple[PowerOfAttorney, ...] = ()  # от корня к листу
    eio_record: ExecutiveRecord | None = None
    missing_parents: tuple[str, ...] = ()
    number_conflicts: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    too_long: int = 0  # цепочка длиннее порога: сколько звеньев успели пройти
    undecided: str = ""  # почему лист не выбран: несколько равноправных МЧД или слишком длинная цепочка

    @property
    def leaf(self) -> PowerOfAttorney | None:
        return self.links[-1] if self.links else None

    @property
    def root(self) -> PowerOfAttorney | None:
        return self.links[0] if self.links else None

    def applicable_checks(self, case: Case) -> set[str]:
        if self.kind == "paper":
            checks = {"format", "identity", "text", "bankruptcy"}
            return checks | ({"charter"} if case.charter is not None else set())
        checks = {"format", "identity", "eio", "coverage", "bankruptcy"}
        if self.kind in {"poa", "none"}:
            checks |= {"registry", "term", "signature"}
        if self.kind == "eio_direct":
            # Статус ЕИО по ЕГРЮЛ не доказывает, что распоряжение подписал именно он.
            checks.add("signature")
        if self.kind == "poa" and (len(self.links) > 1 or self.missing_parents):
            checks.add("subdelegation")
        if case.charter is not None:
            checks.add("charter")
        return checks

    def describe(self) -> list[str]:
        if self.kind == "eio_direct" and self.eio_record:
            return [f"ЕИО по ЕГРЮЛ: {self.eio_record.person.label()} (ГРН {self.eio_record.grn})"]
        rows = []
        for poa in self.links:
            reps = ", ".join(p.label() for p in poa.representatives) or "—"
            rows.append(f"МЧД {poa.number}: {poa.signer.label()} → {reps}")
        for number in self.missing_parents:
            rows.append(f"Родительская МЧД {number} не предоставлена")
        return rows


def _represents(poa: PowerOfAttorney, case: Case) -> bool:
    """Заявитель указан в МЧД. Противоречивые идентификаторы тоже ведут к этой МЧД: разбираться будет R-ID-005."""
    return any(rep.same_as(case.requester) or rep.conflicts_with(case.requester) for rep in poa.representatives)


MAX_CHAIN_LINKS = 10


def build_chain(case: Case, *, max_links: int = MAX_CHAIN_LINKS) -> AuthorityChain:
    on = case.operation.on_date
    if case.egrul is not None:
        for record in case.egrul.executives_on(on):
            if record.person.same_as(case.requester):
                return AuthorityChain(kind="eio_direct", eio_record=record)

    leaves = [poa for poa in case.poas if _represents(poa, case)]
    if not leaves and not case.poas and case.paper_poas:
        return AuthorityChain(kind="paper", notes=("Полномочия подтверждаются бумажной доверенностью",))
    if not leaves:
        hint = ("Представитель не найден ни в одной МЧД пакета",) if case.poas else ()
        return AuthorityChain(kind="none", notes=hint)

    # Порядок документов в пакете не должен влиять на выбор: сортируем по дате, потом по номеру и хешу.
    leaves.sort(
        key=lambda p: (p.issue_date is not None, p.issue_date or date.min, p.number or "", p.sha256), reverse=True
    )
    top = leaves[0]
    rivals = [p for p in leaves[1:] if p.issue_date == top.issue_date and p.sha256 != top.sha256]
    others = [p for p in leaves[1:] if p.sha256 != top.sha256 and p not in rivals]
    if others and not rivals:
        # Несколько разных МЧД на представителя: самая новая может быть отозвана или не покрывать операцию,
        # а более старая — действовать. Произвольный выбор листа даёт ложный отказ или ложное подтверждение,
        # поэтому до оценки независимых цепочек решает сотрудник.
        numbers = ", ".join(str(p.number) for p in (top, *others))
        return AuthorityChain(
            kind="none",
            undecided="tie",
            notes=(f"На представителя несколько разных МЧД ({numbers}): какую применить, решает сотрудник",),
        )
    if rivals:
        numbers = ", ".join(str(p.number) for p in (top, *rivals))
        return AuthorityChain(
            kind="none",
            undecided="tie",
            notes=(
                f"На представителя несколько МЧД с одной датой выдачи ({numbers}): какую применить, решает сотрудник",
            ),
        )
    by_number, conflicts = _index_by_number(case.poas)
    if leaves[0].number in conflicts:
        return AuthorityChain(
            kind="none",
            number_conflicts=conflicts,
            notes=("Одинаковый номер МЧД у документов с разным содержимым: цепочка не выбирается",),
        )
    chain = [leaves[0]]
    missing: list[str] = []
    seen = {leaves[0].number}
    current = leaves[0]
    while current.parent_number:
        parent = by_number.get(current.parent_number)
        if parent is None:
            missing.append(current.parent_number)
            break
        if parent.number in seen:
            missing.append(f"{current.parent_number} (цикл)")
            break
        seen.add(parent.number)
        chain.append(parent)
        current = parent
        if len(chain) > max_links:
            return AuthorityChain(
                kind="none",
                too_long=len(chain),
                undecided="too_long",
                notes=(
                    f"Цепочка передоверия длиннее {max_links} звеньев: автоматически не разбирается, "
                    "нужно подтверждение реестра МЧД по каждому звену",
                ),
            )
    chain.reverse()
    notes: tuple[str, ...] = tuple(f"Ещё МЧД на представителя: {p.number}" for p in leaves[1:])
    if conflicts:
        notes = (*notes, "Есть номера МЧД, которым соответствуют разные документы: " + ", ".join(conflicts))
    return AuthorityChain(
        kind="poa",
        links=tuple(chain),
        missing_parents=tuple(missing),
        number_conflicts=conflicts,
        notes=notes,
    )


def _index_by_number(poas: tuple[PowerOfAttorney, ...]) -> tuple[dict[str, PowerOfAttorney], tuple[str, ...]]:
    """Один sha256 — дубликат. Разное содержимое одного номера — конфликт."""
    grouped: dict[str, list[PowerOfAttorney]] = {}
    for poa in poas:
        if poa.number:
            grouped.setdefault(poa.number, []).append(poa)
    chosen: dict[str, PowerOfAttorney] = {}
    conflicts: list[str] = []
    for number, items in grouped.items():
        if len({item.sha256 for item in items}) == 1:
            chosen[number] = items[0]
        else:
            conflicts.append(number)
    return chosen, tuple(conflicts)
