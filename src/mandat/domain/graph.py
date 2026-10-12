"""Граф полномочий: организация → руководитель по ЕГРЮЛ → МЧД → передоверие → представитель.

Строим путь от представителя, который просит права, к корневой доверенности,
выданной руководителем. Если представитель сам является руководителем на дату
операции, путь состоит из одной записи ЕГРЮЛ (действует без доверенности, ст. 53 ГК РФ).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mandat.domain.model import Case, ExecutiveRecord, PowerOfAttorney


@dataclass(frozen=True)
class AuthorityChain:
    kind: str  # "eio_direct" | "poa" | "none"
    links: tuple[PowerOfAttorney, ...] = ()  # от корня к листу
    eio_record: ExecutiveRecord | None = None
    missing_parents: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def leaf(self) -> PowerOfAttorney | None:
        return self.links[-1] if self.links else None

    @property
    def root(self) -> PowerOfAttorney | None:
        return self.links[0] if self.links else None

    def applicable_checks(self, case: Case) -> set[str]:
        if self.kind == "paper":
            checks = {"format", "identity", "text"}
            return checks | ({"charter"} if case.charter is not None else set())
        checks = {"format", "identity", "eio", "coverage"}
        if self.kind in {"poa", "none"}:
            checks |= {"registry", "term", "signature"}
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
    return any(rep.same_as(case.requester) for rep in poa.representatives)


def build_chain(case: Case) -> AuthorityChain:
    on = case.operation.on_date
    if case.egrul is not None:
        for record in case.egrul.executives_on(on):
            if record.person.same_as(case.requester):
                return AuthorityChain(kind="eio_direct", eio_record=record)

    leaves = [poa for poa in case.poas if _represents(poa, case)]
    if not leaves and not case.poas and case.paper_poas:
        return AuthorityChain(kind="paper", notes=("Полномочия подтверждаются бумажной доверенностью",))
    if not leaves:
        notes = ("Представитель не найден ни в одной МЧД пакета",) if case.poas else ()
        return AuthorityChain(kind="none", notes=notes)

    leaves.sort(key=lambda p: (p.issue_date is not None, p.issue_date), reverse=True)
    by_number = {poa.number: poa for poa in case.poas if poa.number}
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
    chain.reverse()
    notes = tuple(f"Ещё МЧД на представителя: {p.number}" for p in leaves[1:])
    return AuthorityChain(kind="poa", links=tuple(chain), missing_parents=tuple(missing), notes=notes)
