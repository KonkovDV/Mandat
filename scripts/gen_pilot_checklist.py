"""Чек-лист перед первым пилотом: docs/PILOT_CHECKLIST.md из src/mandat/data/pilot_checklist.json и evidence.json.

Статус пункта матрицы готовности не пишется руками: «проверено» ставит сборка evidence.json, когда тесты пункта
прошли на этих исходниках (``readiness`` ниже). Пункт без тестов — «не проверено» с владельцем и условием допуска:
так воркшоп 20.11.2026 просит не выдавать план за выполненную проверку.

Запуск: ``python scripts/gen_pilot_checklist.py``; ``--check`` — только сравнить, код 1 при расхождении.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "mandat" / "data" / "pilot_checklist.json"
TARGET = ROOT / "docs" / "PILOT_CHECKLIST.md"
BLOCKS = ("Данные", "Основание", "Маршруты", "Права", "Завершение", "Проверка")
BLOCK_SUMMARY = {
    "Данные": "у каждого поля ПДн названа цель; поля без цели в отчёт не попадают",
    "Основание": "основание выбирает юрист банка; стенд и синтетика отделены от рабочей среды",
    "Маршруты": "каждая стрелка: что передаётся, зачем и кому; сетевой модуль без записи в карте не пройдёт CI",
    "Права": "матрица «роль × путь», изоляция кейсов на каждом пути",
    "Завершение": "сроки хранения, удаление, отзыв доступа, восстановление без воскрешения удалённого",
    "Проверка": "статусы ниже собирает evidence.json: проверено — тест прошёл на этих исходниках",
}


def checklist() -> dict[str, Any]:
    return json.loads(DATA.read_text(encoding="utf-8"))


def readiness(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Прогнать тесты пунктов матрицы готовности и вернуть статусы для evidence.json."""
    data = data or checklist()
    nodes = sorted({test for item in data["readiness"] for test in item["tests"]})
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "-o", "addopts=", "-p", "no:cacheprovider", "-p", "no:warnings"]
        + nodes,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    passed = set(re.findall(r"^PASSED (\S+)", proc.stdout, re.MULTILINE))
    failed = set(re.findall(r"^(?:FAILED|ERROR) (\S+)", proc.stdout, re.MULTILINE))

    def mine(node: str, tests: list[str]) -> bool:
        return any(node == t or node.startswith(t + "::") for t in tests)

    checks: dict[str, Any] = {}
    for item in data["readiness"]:
        if not item["tests"]:
            checks[item["id"]] = {"status": "не проверено", "tests": 0, "failed": 0}
            continue
        ran = [n for n in passed if mine(n, item["tests"])]
        bad = [n for n in failed if mine(n, item["tests"])]
        status = "проверено" if ran and not bad and proc.returncode == 0 else "нарушено"
        checks[item["id"]] = {"status": status, "tests": len(ran) + len(bad), "failed": len(bad)}
    return {"command": "python -m pytest -rA " + " ".join(nodes), "checks": checks, "source": data["source"]}


def _status(evidence: dict[str, Any], item: dict[str, Any]) -> str:
    found = ((evidence.get("pilot_readiness") or {}).get("checks") or {}).get(item["id"])
    if not found:
        return "не собрано"
    if found["status"] == "проверено":
        return f"**проверено** ({found['tests']} тестов)" if found["tests"] != 1 else "**проверено** (1 тест)"
    return f"**{found['status']}**"


def _tests(item: dict[str, Any]) -> str:
    if not item["tests"]:
        return item["condition"]
    return ", ".join(f"`{t.split('/', 1)[-1]}`" for t in item["tests"])


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cell.replace("|", "/") for cell in cells) + " |"


def render(data: dict[str, Any], evidence: dict[str, Any]) -> str:
    out = [
        "# Чек-лист перед первым пилотом",
        "",
        f"Пункты воркшопа «Данные, безопасность и комплаенс: что учесть до первого пилота» (Академия Финтеха, "
        f"20.11.2026), разложенные на «Мандат». Файл собирает `python scripts/gen_pilot_checklist.py` из "
        f"`src/mandat/data/pilot_checklist.json` и `evidence.json`, руками его не правят. "
        f"Сверено {data['reviewed_at']}.",
        "",
        data["boundary"] + ".",
        "",
        "## Шесть вопросов",
        "",
        "| Вопрос | Что сделано |",
        "|---|---|",
        *[_row([block, BLOCK_SUMMARY[block]]) for block in BLOCKS],
        "",
        "## Матрица готовности к пилоту",
        "",
        "| Блок | Проверка | Критерий | Владелец | Статус | Чем проверено или условие допуска |",
        "|---|---|---|---|---|---|",
        *[
            _row([i["block"], i["check"], i["criterion"], i["owner"], _status(evidence, i), _tests(i)])
            for block in BLOCKS
            for i in data["readiness"]
            if i["block"] == block
        ],
        "",
        "## Матрица применимости",
        "",
        "| Требование | Норма | Почему относится | Решение | Владелец | Проверка |",
        "|---|---|---|---|---|---|",
        *[
            _row([a["requirement"], a["norm"], a["why"], a["decision"], a["owner"], a["check"] or "не проверено"])
            for a in data["applicability"]
        ],
        "",
        "## Поля с персональными данными",
        "",
        "Каждое поле объясняет свою цель. Поле, которое правилам не нужно, в отчёт не попадает, "
        "а передавать его не нужно.",
        "",
        "| Поле | Что это | Цель | Правила | В отчёте | В модель |",
        "|---|---|---|---|---|---|",
        *[
            _row(
                [
                    f"`{f['field']}`",
                    f["kind"],
                    f["purpose"],
                    ", ".join(f["rules"]) or "—",
                    "да" if f["in_report"] else "нет",
                    f["to_model"],
                ]
            )
            for f in data["fields"]
        ],
        "",
        "## Маршруты: что передаётся, зачем и кому",
        "",
        "| Откуда → куда | Данные | Цель | Контроль | Модули |",
        "|---|---|---|---|---|",
        *[
            _row(
                [
                    f"{r['from']} → {r['to']}",
                    r["data"],
                    r["purpose"],
                    r["control"],
                    ", ".join(f"`{m.split('/', 2)[-1]}`" for m in r["modules"]) or "—",
                ]
            )
            for r in data["routes"]
        ],
        "",
        "## Копии и сроки",
        "",
        "| Где | Данные | Защита | Срок | Удаление | После восстановления |",
        "|---|---|---|---|---|---|",
        *[
            _row([c["where"], c["data"], c["protection"], c["retention"], c["deletion"], c["restore"]])
            for c in data["copies"]
        ],
        "",
        "## Перед пилотом",
        "",
        "```bash",
        "mandat access-review --strict",
        "```",
        "",
        "Ревизия учётных записей API: код 1, если у действующей записи нет срока или владельца отзыва.",
        "",
        "```bash",
        "mandat restore-check --journal var/api/journal.jsonl --store sqlite:///var/api/store.db "
        "--status-dir var/status --repair",
        "```",
        "",
        "После восстановления из копии: удалённое по 152-ФЗ удаляется снова, отозванное отзывается снова, "
        "журнал сверен.",
        "",
        "```bash",
        "mandat incident-report --journal var/api/journal.jsonl --detected-at 2026-11-20T09:00:00+03:00",
        "```",
        "",
        "Сроки 24 и 72 часа от выявления и кто какие кейсы открывал — для уведомления Роскомнадзора и расследования.",
        "",
    ]
    return "\n".join(out)


def main(argv: list[str]) -> int:
    evidence = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
    text = render(checklist(), evidence)
    if "--check" in argv:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != text:
            print("docs/PILOT_CHECKLIST.md устарел: python scripts/gen_pilot_checklist.py", file=sys.stderr)
            return 1
        print("чек-лист совпадает с данными и evidence.json")
        return 0
    TARGET.write_text(text, encoding="utf-8")
    print(f"записано: {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
