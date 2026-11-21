"""Загрузка пакета правил и каталога операций с хешем версии.

Хеш пакета попадает в каждый отчёт: по нему видно, какой набор правил вынес вердикт.
Идея и проверки при загрузке перенесены из AeroBIM (``json_norm_rule_pack_loader``,
``norm_pack_hash``, ``norm_rule_eligibility``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlparse

from mandat.data import load_json, sha256_json

CHECKS = {
    "format",
    "identity",
    "registry",
    "term",
    "eio",
    "coverage",
    "subdelegation",
    "charter",
    "text",
    "signature",
    "bankruptcy",
}
STATUSES = {"draft", "approved", "retired"}
# Юридическая сила акта, на котором стоит правило (идея реестра инструментов AeroBIM, npa_legal_force).
LEGAL_FORCE = {"code", "federal_law", "government_decree", "agency_order", "cbr_act"}
OFFICIAL_HOSTS = ("publication.pravo.gov.ru", "pravo.gov.ru")
EFFECTS = {"supports", "refutes", "needs_review", "not_verified", "info"}
MAX_RULES = 500


class RulePackError(ValueError):
    pass


@dataclass(frozen=True)
class RuleSpec:
    id: str
    check: str
    title: str
    norm_ref: str
    status: str
    effects: tuple[str, ...]
    next_step: str | None
    unit: str | None
    tests: tuple[str, ...]
    params: dict[str, Any] = field(default_factory=dict)

    def param(self, name: str) -> Any:
        if name not in self.params:
            raise RulePackError(f"{self.id}: нет параметра {name!r}")
        return self.params[name]


@dataclass(frozen=True)
class NormSpec:
    """Нормативный акт, на котором стоят правила пакета, и срок его действия.

    ``in_force_until`` — первый день, когда акт уже не действует: «действует до 1 марта 2028 г.» → 2028-03-01.
    Правило, чей акт на дату операции утратил силу, «подтверждено» не даёт (``checks.legal_horizon``).
    """

    id: str
    title: str
    legal_force: str
    url: str
    in_force_from: date | None
    in_force_until: date | None
    rules: tuple[str, ...]
    note: str | None = None

    def in_force_on(self, day: date) -> bool:
        if self.in_force_from is not None and day < self.in_force_from:
            return False
        return self.in_force_until is None or day < self.in_force_until

    def state_on(self, day: date) -> str:
        if self.in_force_from is not None and day < self.in_force_from:
            return "ещё не вступил в силу"
        return "действует" if self.in_force_on(day) else "утратил силу"


@dataclass(frozen=True)
class RulePack:
    pack_id: str
    version: str
    status: str
    sha256: str
    rules: dict[str, RuleSpec]
    # Действующие подписанные утверждения (rules/approvals.py): хеш файла и id правил. Нет утверждений — None.
    approvals: dict[str, Any] | None = None
    # Каталог нормативных актов со сроками действия: на каких актах стоят правила и когда акты меняются.
    norms: tuple[NormSpec, ...] = ()

    def get(self, rule_id: str) -> RuleSpec:
        try:
            return self.rules[rule_id]
        except KeyError as exc:
            raise RulePackError(f"правила {rule_id} нет в пакете {self.pack_id}") from exc

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "pack_id": self.pack_id,
            "version": self.version,
            "status": self.status,
            "sha256": self.sha256,
        }
        if self.approvals is not None:
            out["approvals"] = self.approvals
        return out


def validate_pack(payload: dict[str, Any]) -> RulePack:
    if payload.get("schema") != 1:
        raise RulePackError("поддерживается только schema=1")
    rules_raw = payload.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise RulePackError("в пакете нет правил")
    if len(rules_raw) > MAX_RULES:
        raise RulePackError("слишком много правил в пакете")
    rules: dict[str, RuleSpec] = {}
    for row in rules_raw:
        rule_id = str(row.get("id", "")).strip()
        if not rule_id or rule_id in rules:
            raise RulePackError(f"пустой или повторный id правила: {rule_id!r}")
        if row.get("check") not in CHECKS:
            raise RulePackError(f"{rule_id}: неизвестная группа {row.get('check')!r}")
        if row.get("status") not in STATUSES:
            raise RulePackError(f"{rule_id}: неизвестный статус {row.get('status')!r}")
        effects = tuple(row.get("effects") or ())
        if not effects or not set(effects) <= EFFECTS:
            raise RulePackError(f"{rule_id}: неверный список эффектов {effects}")
        if not str(row.get("norm_ref", "")).strip():
            raise RulePackError(f"{rule_id}: нет ссылки на норму")
        params = row.get("params") or {}
        if not isinstance(params, dict):
            raise RulePackError(f"{rule_id}: params должен быть объектом")
        rules[rule_id] = RuleSpec(
            id=rule_id,
            check=row["check"],
            title=str(row.get("title", rule_id)),
            norm_ref=str(row["norm_ref"]),
            status=row["status"],
            effects=effects,
            next_step=row.get("next_step"),
            unit=row.get("unit"),
            tests=tuple(row.get("tests") or ()),
            params=dict(params),
        )
    return RulePack(
        pack_id=str(payload.get("pack_id")),
        version=str(payload.get("version")),
        status=str(payload.get("status")),
        sha256=sha256_json(payload),
        rules=rules,
        norms=_validate_norms(payload.get("norms") or [], rules),
    )


def _norm_date(value: Any, norm_id: str, field_name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise RulePackError(f"норма {norm_id}: {field_name} не дата ISO: {value!r}") from exc


def _validate_norms(rows: Any, rules: dict[str, RuleSpec]) -> tuple[NormSpec, ...]:
    """Каталог норм: id без повторов, адрес официального опубликования, даты по порядку, правила из пакета.

    Правило, у которого акт может утратить силу, обязано уметь «на разбор»: иначе горизонт нечем выразить.
    """
    if not isinstance(rows, list):
        raise RulePackError("norms должен быть списком")
    out: list[NormSpec] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RulePackError("норма должна быть объектом")
        norm_id = str(row.get("id", "")).strip()
        if not norm_id or norm_id in seen:
            raise RulePackError(f"пустой или повторный id нормы: {norm_id!r}")
        seen.add(norm_id)
        title = str(row.get("title", "")).strip()
        if not title:
            raise RulePackError(f"норма {norm_id}: нет названия")
        if row.get("legal_force") not in LEGAL_FORCE:
            raise RulePackError(f"норма {norm_id}: неизвестная юридическая сила {row.get('legal_force')!r}")
        url = str(row.get("url", ""))
        host = urlparse(url).hostname or ""
        if urlparse(url).scheme != "https" or not any(host == h or host.endswith("." + h) for h in OFFICIAL_HOSTS):
            raise RulePackError(f"норма {norm_id}: адрес не официального опубликования ({url})")
        start = _norm_date(row.get("in_force_from"), norm_id, "in_force_from")
        until = _norm_date(row.get("in_force_until"), norm_id, "in_force_until")
        if start is not None and until is not None and until <= start:
            raise RulePackError(f"норма {norm_id}: срок действия кончается раньше, чем начинается")
        linked = tuple(str(r) for r in row.get("rules") or ())
        if not linked:
            raise RulePackError(f"норма {norm_id}: не названы правила")
        unknown = [r for r in linked if r not in rules]
        if unknown:
            raise RulePackError(f"норма {norm_id}: правил нет в пакете: {unknown}")
        if until is not None:
            mute = [r for r in linked if "needs_review" not in rules[r].effects]
            if mute:
                raise RulePackError(f"норма {norm_id} утратит силу, а правила {mute} не умеют «на разбор»")
        out.append(
            NormSpec(
                id=norm_id,
                title=title,
                legal_force=str(row["legal_force"]),
                url=url,
                in_force_from=start,
                in_force_until=until,
                rules=linked,
                note=row.get("note"),
            )
        )
    return tuple(out)


def default_pack() -> RulePack:
    return validate_pack(load_json("rules.json"))


@dataclass(frozen=True)
class OperationSpec:
    code: str
    name: str
    accepted_codes: frozenset[str]
    text_keywords: tuple[str, ...]
    proposed_role: str
    high_risk: bool
    charter_required: bool


@dataclass(frozen=True)
class OperationCatalog:
    catalog_id: str
    version: str
    sha256: str
    synthetic_codes: bool
    operations: dict[str, OperationSpec]

    def get(self, code: str) -> OperationSpec | None:
        return self.operations.get(code)


def default_catalog() -> OperationCatalog:
    payload = load_json("operations.json")
    ops = {
        row["code"]: OperationSpec(
            code=row["code"],
            name=row["name"],
            accepted_codes=frozenset(row.get("accepted_authority_codes") or ()),
            text_keywords=tuple(row.get("text_keywords") or ()),
            proposed_role=row["proposed_role"],
            high_risk=bool(row.get("high_risk", False)),
            charter_required=bool(row.get("charter_required", False)),
        )
        for row in payload["operations"]
    }
    return OperationCatalog(
        catalog_id=payload["catalog_id"],
        version=payload["version"],
        sha256=sha256_json(payload),
        synthetic_codes=bool(payload.get("synthetic_codes", False)),
        operations=ops,
    )
