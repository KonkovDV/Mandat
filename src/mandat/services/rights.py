"""Рекомендация роли в интернет-банке по итогу проверки.

«Мандат» права не выдаёт: он готовит обоснованную рекомендацию, а права
предоставляются по регламенту банка. На пилоте режим теневой.
"""

from __future__ import annotations

from typing import Any

from mandat.domain.graph import AuthorityChain
from mandat.domain.model import Case
from mandat.domain.verdict import Verdict
from mandat.rules.pack import OperationSpec


def propose_right(
    verdict: Verdict, case: Case, chain: AuthorityChain, op: OperationSpec | None
) -> dict[str, Any] | None:
    if op is None or verdict in (Verdict.REFUTED, Verdict.INSUFFICIENT_DATA):
        return None
    basis = (
        [f"ЕГРЮЛ, ГРН {chain.eio_record.grn}"]
        if chain.kind == "eio_direct" and chain.eio_record
        else [f"МЧД {poa.number}" for poa in chain.links]
    )
    until = chain.leaf.effective_until() if chain.leaf else None
    valid_until = until.isoformat() if until else None
    return {
        "status": "recommended" if verdict is Verdict.CONFIRMED else "pending_review",
        "mode": "shadow_recommendation",
        "role": op.proposed_role,
        "operation": op.code,
        "representative_inn": case.requester.inn,
        "checked_amount_rub": case.operation.amount_rub,
        "valid_until": valid_until,
        "basis": basis,
        "high_risk_operation": op.high_risk,
    }
