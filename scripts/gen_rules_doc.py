"""Собирает docs/RULES.md из пакета правил: правило → норма → эффект → тест.

python scripts/gen_rules_doc.py
"""

from __future__ import annotations

from pathlib import Path

from mandat.domain.verdict import CHECK_LABEL_RU
from mandat.rules.pack import default_catalog, default_pack

ROOT = Path(__file__).resolve().parents[1]
EFFECT_RU = {
    "supports": "подтверждает",
    "refutes": "опровергает",
    "needs_review": "на разбор",
    "not_verified": "не проверено",
    "info": "справка",
}


def _cell(text: str | None) -> str:
    return (text or "—").replace("|", "\\|")


def render_rules_doc() -> str:
    pack, catalog = default_pack(), default_catalog()
    lines = [
        "# Правила «Мандата»",
        "",
        "Файл собирается командой `python scripts/gen_rules_doc.py` из `src/mandat/data/rules.json`.",
        "Тест `test_rules_doc_is_up_to_date` не даёт ему разойтись с пакетом.",
        "",
        f"Пакет `{pack.pack_id}` версии `{pack.version}`, статус `{pack.status}`, sha256 `{pack.sha256[:16]}…`.",
        "Статус `draft` означает: ссылка на норму подобрана командой и ждёт проверки юристом.",
        "",
        "| Правило | Группа | Что проверяет | Норма | Возможные эффекты | Следующий шаг | Тесты |",
        "|---|---|---|---|---|---|---|",
    ]
    for spec in pack.rules.values():
        effects = ", ".join(EFFECT_RU[e] for e in spec.effects)
        tests = "<br>".join(f"`{t}`" for t in spec.tests)
        params = "".join(f"<br>параметр `{k}` = {v}" for k, v in spec.params.items())
        lines.append(
            f"| {spec.id} | {CHECK_LABEL_RU.get(spec.check, spec.check)} | {_cell(spec.title)}{params} "
            f"| {_cell(spec.norm_ref)} | {effects} | {_cell(spec.next_step)} | {tests} |"
        )
    lines += [
        "",
        "## Каталог операций",
        "",
        f"Каталог `{catalog.catalog_id}` версии `{catalog.version}`."
        + (
            " Коды полномочий синтетические (`SYN-…`): заменить кодами из выгрузки классификатора ЕСНСИ "
            "(`scripts/import_classifier.py`); снимок с датами — `data/authority_classifier.json`."
            if catalog.synthetic_codes
            else ""
        ),
        "",
        "| Операция | Название | Принимаемые коды полномочий | Рекомендуемая роль | Повышенный риск |",
        "|---|---|---|---|---|",
    ]
    for op in catalog.operations.values():
        codes = ", ".join(f"`{c}`" for c in sorted(op.accepted_codes))
        lines.append(
            f"| `{op.code}` | {op.name} | {codes} | `{op.proposed_role}` | {'да' if op.high_risk else 'нет'} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    (ROOT / "docs" / "RULES.md").write_text(render_rules_doc(), encoding="utf-8")
    print("docs/RULES.md обновлён")
