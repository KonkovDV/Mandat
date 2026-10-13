"""Загрузка пакета правил и каталога операций с хешем версии.

Хеш пакета попадает в каждый отчёт: по нему видно, какой набор правил вынес вердикт.
Идея и проверки при загрузке перенесены из AeroBIM (``json_norm_rule_pack_loader``,
``norm_pack_hash``, ``norm_rule_eligibility``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
class RulePack:
    pack_id: str
    version: str
    status: str
    sha256: str
    rules: dict[str, RuleSpec]

    def get(self, rule_id: str) -> RuleSpec:
        try:
            return self.rules[rule_id]
        except KeyError as exc:
            raise RulePackError(f"правила {rule_id} нет в пакете {self.pack_id}") from exc

    def summary(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "version": self.version,
            "status": self.status,
            "sha256": self.sha256,
        }


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
    )


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
