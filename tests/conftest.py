from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mandat.eval.synth import Scenario, build  # noqa: E402
from mandat.intake.case_loader import load_case_inline  # noqa: E402
from mandat.services.verify import CaseReport, verify_case  # noqa: E402


def run(
    defect: str,
    *,
    profile: str = "demo",
    seed: int = 11,
    mutate: Callable[[Scenario], None] | None = None,
) -> CaseReport:
    scenario = build(defect, seed=seed)
    if mutate is not None:
        mutate(scenario)
    return verify_case(load_case_inline(scenario.inline_manifest()), profile=profile)


def rule_effects(report: CaseReport, rule_id: str) -> set[str]:
    return {f.effect.value for f in report.findings if f.rule_id == rule_id}


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("MANDAT_JOURNAL_KEY", "MANDAT_PII_KEY", "MANDAT_API_TOKEN", "MANDAT_LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


# --- перебор двухзвенных цепочек для проверки свойств передоверия --------------------------------

CODE_UNIVERSE = ("SYN-BANK-PAY-SIGN", "SYN-BANK-STATEMENT", "SYN-BANK-USER-ADMIN")
PAY_CODE = "SYN-BANK-PAY-SIGN"


def _subsets(items: tuple[str, ...]) -> list[frozenset[str]]:
    return [frozenset(c for i, c in enumerate(items) if mask >> i & 1) for mask in range(1 << len(items))]


def chain_matrix() -> list[tuple[frozenset[str], frozenset[str], str, bool]]:
    """Все комбинации: коды родителя × коды передоверия × ПрПередов родителя × срок передоверия длиннее."""
    return [
        (parent, child, flag, longer)
        for parent in _subsets(CODE_UNIVERSE)
        for child in _subsets(CODE_UNIVERSE)
        for flag in ("1", "2", "3")
        for longer in (False, True)
    ]


def chain_scenario(parent: frozenset[str], child: frozenset[str], flag: str, longer: bool) -> Scenario:
    """Кейс «руководитель → МЧД заместителю → передоверие бухгалтеру» с заданными параметрами."""
    from datetime import timedelta

    from mandat.data import load_json
    from mandat.intake.emchd import build_emchd_xml, parse_emchd

    entries = load_json("authority_classifier.json")["entries"]
    names = {e["code"]: (e["code"], e["mnemonic"], e["name"]) for e in entries}
    sc = build("ok_subdelegation", seed=17)
    root = parse_emchd(sc.files["poa-root.xml"], doc_id="poa-root")
    leaf = parse_emchd(sc.files["poa-child.xml"], doc_id="poa-child")
    sc.files["poa-root.xml"] = build_emchd_xml(
        number=root.number,
        issue_date=root.issue_date,
        valid_until=root.valid_until,
        principal=root.principal,
        signer=root.signer,
        representatives=list(root.representatives),
        machine_codes=[names[c] for c in sorted(parent)],
        subdelegation_code=flag,
    ).encode()
    until = root.valid_until + timedelta(days=30) if longer else root.valid_until - timedelta(days=30)
    sc.files["poa-child.xml"] = build_emchd_xml(
        number=leaf.number,
        issue_date=leaf.issue_date,
        valid_until=until,
        principal=leaf.principal,
        signer=leaf.signer,
        representatives=list(leaf.representatives),
        machine_codes=[names[c] for c in sorted(child)],
        subdelegation_code="1",
        parent_number=root.number,
        original_number=root.number,
        loses_authority_code="1",
    ).encode()
    return sc


def verify_scenario(sc: Scenario, profile: str = "demo") -> CaseReport:
    return verify_case(load_case_inline(sc.inline_manifest()), profile=profile)
