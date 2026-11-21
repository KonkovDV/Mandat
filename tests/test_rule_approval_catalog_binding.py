"""Утверждение правил, которые читают каталог операций, привязано и к каталогу.

От каталога зависит, какие коды полномочий покрывают операцию (R-COV-001, R-COV-002), нужен ли устав
(R-CHR-002) и высок ли риск (R-EIO-002). Без привязки правка operations.json расширяла покрытие операции,
а подпись юриста оставалась действующей.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime

from conftest import ROOT
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandat.audit.signing import Keyring, LocalSigner
from mandat.domain.graph import build_chain
from mandat.eval.synth import DEFECTS, build
from mandat.examples import DEMO, OVERLAYS
from mandat.intake.case_loader import load_case_dir, load_case_inline
from mandat.intake.charter import extract_charter_facts
from mandat.rules.approvals import CATALOG_RULES, Approvals, check_record, effective_pack, rule_text_sha256, sign_record
from mandat.rules.checks import RULES, RuleContext
from mandat.rules.classifier import default_classifier
from mandat.rules.pack import default_catalog, default_pack
from mandat.services.verify import default_ports

PACK = default_pack()
CATALOG = default_catalog()
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


class _Recording:
    """Каталог, который отмечает каждое обращение к себе."""

    def __init__(self, inner):
        self._inner = inner
        self.touched = False

    def __getattr__(self, name):
        self.touched = True
        return getattr(self._inner, name)


def test_catalog_rules_are_exactly_the_rules_that_read_the_catalog():
    cases = [load_case_dir(ROOT / "examples" / "cases" / name) for name in [*DEMO, *OVERLAYS]]
    cases += [load_case_inline(build(defect, seed=1).inline_manifest()) for defect in DEFECTS]
    readers: set[str] = set()
    for case in cases:
        recording = _Recording(CATALOG)
        ctx = RuleContext(
            case=case,
            chain=build_chain(case, max_links=int(PACK.get("R-SUB-008").param("max_chain_links"))),
            pack=PACK,
            catalog=recording,
            ports=default_ports(case),
            classifier=default_classifier(),
            profile_name="strict",
            now=None,
        )
        ctx.charter_facts = extract_charter_facts(case.charter) if case.charter else []
        for rule_id, func in RULES.items():
            recording.touched = False
            func(ctx)
            if recording.touched:
                readers.add(rule_id)
    assert readers == CATALOG_RULES


def _widened():
    """Каталог, где подписание платежа покрывает ещё и код просмотра выписки."""
    pay = CATALOG.operations["PAYMENT_SIGN"]
    wider = dataclasses.replace(pay, accepted_codes=pay.accepted_codes | {"SYN-BANK-STATEMENT"})
    return dataclasses.replace(CATALOG, sha256="f" * 64, operations={**CATALOG.operations, "PAYMENT_SIGN": wider})


def _signed(rule_id: str, signer: LocalSigner) -> dict:
    return sign_record(
        PACK.rules[rule_id],
        PACK.pack_id,
        signer,
        approver_role="юрист банка",
        norm_edition="ред.",
        norm_checked_on=date(2026, 11, 19),
        valid_until=date(2027, 11, 19),
        signed_at=datetime(2026, 11, 20, 9, 0, tzinfo=UTC),
    )


def test_editing_the_catalog_drops_the_approval_of_catalog_rules_only():
    signer = LocalSigner(Ed25519PrivateKey.generate())
    keyring = Keyring.parse({"keys": [{"kid": signer.key_id, "status": "active"}]})
    wider = _widened()
    for rule_id in CATALOG_RULES:
        record = _signed(rule_id, signer)
        assert check_record(record, PACK, keyring, NOW, CATALOG.sha256) is None
        assert "каталог операций" in check_record(record, PACK, keyring, NOW, wider.sha256)
    plain = _signed("R-TERM-002", signer)
    assert check_record(plain, PACK, keyring, NOW, wider.sha256) is None
    assert rule_text_sha256(PACK.rules["R-TERM-002"], wider.sha256) == rule_text_sha256(PACK.rules["R-TERM-002"])


def test_verify_case_checks_approvals_against_its_own_catalog(monkeypatch):
    signer = LocalSigner(Ed25519PrivateKey.generate())
    keyring = Keyring.parse({"keys": [{"kid": signer.key_id, "status": "active"}]})
    records = tuple(_signed(rule_id, signer) for rule_id in PACK.rules)
    approvals = Approvals(records, keyring, "0" * 64, "тест")
    pack, notes = effective_pack(PACK, NOW, approvals, CATALOG.sha256)
    assert notes == [] and {s.status for s in pack.rules.values()} == {"approved"}
    pack, notes = effective_pack(PACK, NOW, approvals, _widened().sha256)
    assert {k for k, s in pack.rules.items() if s.status == "draft"} == CATALOG_RULES
    assert len(notes) == len(CATALOG_RULES)

    from mandat.services.verify import verify_case

    monkeypatch.setattr("mandat.rules.approvals.load_approvals", lambda *a, **k: approvals)
    case = load_case_dir(ROOT / "examples" / "cases" / "14_old_director_charter_cleared")
    report = verify_case(case, profile="strict", catalog=_widened(), now=NOW)
    statuses = {f.rule_id: f.rule_status for f in report.findings if f.origin == "deterministic"}
    assert all(statuses[r] == "draft" for r in CATALOG_RULES if r in statuses)
    assert any(status == "approved" for rule, status in statuses.items() if rule not in CATALOG_RULES)
