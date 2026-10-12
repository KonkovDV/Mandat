"""Собирает docs/RULES.md из пакета правил: правило → норма → эффект → тест.

python scripts/gen_rules_doc.py
"""

from __future__ import annotations

from pathlib import Path

from mandat.domain.verdict import CHECK_LABEL_RU, PROFILES
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
    groups: dict[str, int] = {}
    for spec in pack.rules.values():
        groups[spec.check] = groups.get(spec.check, 0) + 1
    demo, strict = PROFILES["demo"], PROFILES["strict"]

    def mark(check: str) -> str:
        cells = []
        for profile in (demo, strict):
            if check in profile.mandatory:
                cells.append("обязательна")
            elif check in profile.waived:
                cells.append("снята")
            else:
                cells.append("по ситуации")
        return " | ".join(cells)

    lines = [
        "# Правила «Мандата»",
        "",
        f"Здесь все {len(pack.rules)} правил: что проверяет каждое, на какую норму опирается, "
        "чем может закончиться и какой тест это держит.",
        "",
        "> Файл собирается из `src/mandat/data/rules.json` командой `python scripts/gen_rules_doc.py`. "
        "Руками его не правят: тест `test_rules_doc_is_up_to_date` упадёт, если он разойдётся с пакетом.",
        "",
        f"Пакет `{pack.pack_id}`, версия `{pack.version}`, статус `{pack.status}`, sha256 `{pack.sha256[:16]}…`. "
        "Статус `draft` значит, что норму подобрала команда, а юрист банка её ещё не проверил.",
        "",
        "## Как правила превращаются в вердикт",
        "",
        "Каждое правило выдаёт находки с эффектом. Потом находки сворачиваются по группам, "
        "а группы в один ответ. Порядок жёсткий: сильный эффект всегда перебивает слабый.",
        "",
        "```mermaid",
        "flowchart TD",
        '    F["Находки правил"] --> R{"Есть опровержение?"}',
        '    R -- да --> V1["ОПРОВЕРГНУТО"]',
        '    R -- нет --> M{"Обязательная группа не проверена?"}',
        '    M -- да --> V2["НЕДОСТАТОЧНО ДАННЫХ"]',
        '    M -- нет --> N{"Есть повод для разбора?"}',
        '    N -- да --> V3["НА РАЗБОР"]',
        '    N -- нет --> O{"Все обязательные группы подтверждены?"}',
        '    O -- да --> V4["ПОДТВЕРЖДЕНО"]',
        "    O -- нет --> V3",
        "```",
        "",
        "| Эффект | Что значит |",
        "|---|---|",
        "| подтверждает | правило нашло, на что опереться |",
        "| опровергает | полномочия нет, и это видно из документа или реестра |",
        "| на разбор | спорно, решает человек |",
        "| не проверено | источника нет или он недоступен. Это не «всё хорошо» |",
        "| справка | для карточки, на вердикт не влияет |",
        "",
        "## Группы проверок",
        "",
        "Профиль `demo` честно снимает проверку подписи, потому что СКЗИ в стенде нет. "
        "Профиль `strict` требует всё и не доверяет правилам в статусе `draft`.",
        "",
        "| Группа | Правил | `demo` | `strict` |",
        "|---|---|---|---|",
    ]
    for check, count in groups.items():
        lines.append(f"| {CHECK_LABEL_RU.get(check, check)} | {count} | {mark(check)} |")
    lines += [
        "",
        "## Все правила",
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
        "Операция из запроса сверяется с кодами полномочий в МЧД. Если ни один код не подходит, "
        "правила покрытия дают опровержение или разбор.",
        "",
        f"Каталог `{catalog.catalog_id}`, версия `{catalog.version}`."
        + (
            " Коды полномочий пока синтетические (`SYN-…`). Настоящие берутся из выгрузки классификатора ЕСНСИ "
            "командой `scripts/import_classifier.py`, снимок с датами лежит в `data/authority_classifier.json`."
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
