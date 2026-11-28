"""Вкладка «Аналитика» консоли стенда: эффект для банка, у каждого числа — источник.

Страница чисел не хранит, сервер берёт их из кода и сборки:

- проверки этой сессии — вердикты отчётов её кейсов в хранилище API: только счётчики, без имён, ИНН и номеров;
- повторные клиенты, качество и тесты — ``evidence.json`` (``scripts/build_evidence.py``);
- юнит-экономика — значения финансовой модели (``submission/analytics/out/model_values.json``) через
  ``mandat.eval.economics``, окупаемость — из той же модели;
- расход модели — счётчики консультанта (``advisory/economy.py``).

``evidence.json`` и значения модели читаются из рабочего каталога службы, как ``BUILD``: на стенде их кладёт туда
``scripts/deploy_stand.sh``, при запуске из репозитория это его корень. Нет файла — нет и его карточек: вкладка
называет, чего не хватает, и ничего не подставляет. Замеров ёмкости в ``evidence.json`` нет, поэтому нет и карточки.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mandat.api.conclusions import cleared
from mandat.api.storage import Store
from mandat.console.advisor import _usd, usage_view
from mandat.console.sessions import Session
from mandat.domain.verdict import VERDICT_LABEL_RU, Verdict
from mandat.eval.economics import unit_economics

EVIDENCE = Path("evidence.json")
MODEL_VALUES = Path("submission", "analytics", "out", "model_values.json")
CHECKOUT = Path(__file__).resolve().parents[3]
# Базовый сценарий модели — тот же, что в колоде, на сайте и в docs/ECONOMICS.md.
BASE = 1
# К человеку — как в прогоне консоли и в evidence.json (repeat_clients): разбор и нехватка данных.
TO_PERSON = (Verdict.NEEDS_REVIEW, Verdict.INSUFFICIENT_DATA)


def read_source(relative: Path) -> dict[str, Any] | None:
    """JSON из рабочего каталога службы или из репозитория, откуда запущен пакет; нет или не читается — ``None``."""
    roots = [CHECKOUT]
    with contextlib.suppress(OSError):
        roots.insert(0, Path.cwd())
    for root in roots:
        try:
            value = json.loads((root / relative).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


def session_reports(session: Session, store: Store) -> list[dict[str, Any]]:
    """Отчёты кейсов этой сессии из хранилища API: стёртые и чужие не попадают."""
    owner = session.principal_name("operator")
    found = (store.get(case["case_id"]) for case in session.cases)
    return [item[0] for item in found if item is not None and item[1] == owner]


def view(session: Session, store: Store) -> dict[str, Any]:
    """Карточки вкладки по порядку и список файлов, которых служба не нашла."""
    evidence, values = read_source(EVIDENCE), read_source(MODEL_VALUES)
    reports = session_reports(session, store)
    cards = [_session(reports), _repeat(evidence, reports), _unit(values), _quality(evidence), _spend()]
    missing = [path.as_posix() for path, found in ((EVIDENCE, evidence), (MODEL_VALUES, values)) if found is None]
    return {"cards": [card for card in cards if card is not None], "missing": missing}


def _num(value: float, digits: int = 0) -> str:
    """Число по-русски: неразрывный пробел между разрядами, запятая перед дробной частью."""
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",")


def _pct(share: float) -> str:
    value = round(share * 100, 1)
    return f"{_num(value, 0 if value.is_integer() else 1)} %"


def _day(stamp: Any) -> str:
    """Дата сборки из ISO-строки: «2026-11-28T…» → «28.11.2026»."""
    parts = str(stamp or "")[:10].split("-")
    return ".".join(reversed(parts)) if len(parts) == 3 else "без даты"


def _section(evidence: Mapping[str, Any] | None, name: str) -> Mapping[str, Any] | None:
    value = (evidence or {}).get(name)
    return value if isinstance(value, Mapping) else None


def _tile(value: str, label: str, src: str, *, verdict: str | None = None, hero: bool = False) -> dict[str, Any]:
    return {"value": value, "label": label, "src": src, "verdict": verdict, "hero": hero}


def _row(label: str, value: str, src: str) -> dict[str, str]:
    return {"label": label, "value": value, "src": src}


def _card(
    cid: str,
    title: str,
    source: str,
    *,
    tiles: list[dict[str, Any]] | None = None,
    rows: list[dict[str, str]] | None = None,
    note: str = "",
) -> dict[str, Any]:
    return {
        "id": cid,
        "title": title,
        "source": source,
        "tiles": tiles or [],
        "rows": rows or [],
        "note": note,
    }


def _session(reports: list[dict[str, Any]]) -> dict[str, Any]:
    counts = dict.fromkeys(Verdict, 0)
    for report in reports:
        with contextlib.suppress(ValueError):
            counts[Verdict(str(report.get("verdict")))] += 1
    total = sum(counts.values())
    to_person = sum(counts[v] for v in TO_PERSON)
    share = f"{_num(to_person)} из {_num(total)}, {_pct(to_person / total)}" if total else "проверок пока нет"
    note = (
        "К человеку — вердикты НА РАЗБОР и НЕДОСТАТОЧНО ДАННЫХ, как в прогоне и в evidence.json."
        if total
        else "Проверьте демо-кейс на вкладке «Проверка»: счётчики обновятся, когда вкладка откроется снова."
    )
    return _card(
        "session",
        "Проверки этой сессии",
        "сессия консоли: вердикты отчётов её кейсов в хранилище API — только счётчики, без имён, ИНН и номеров кейсов",
        tiles=[
            _tile(_num(counts[v]), VERDICT_LABEL_RU[v], f"сессия · verdict {v.value}", verdict=v.value) for v in Verdict
        ],
        rows=[_row("Уходит к человеку", share, "сессия · needs_review и insufficient_data")],
        note=note,
    )


def _repeat(evidence: Mapping[str, Any] | None, reports: list[dict[str, Any]]) -> dict[str, Any]:
    applied = [r for r in reports if (r.get("charter_conclusions") or {}).get("applied")]
    closed = sum(cleared(r) for r in applied)
    session_value = _num(closed) + (f" из {_num(len(applied))} с применённым заключением" if applied else "")
    rows = [_row("Операции этой сессии, закрытые заключением", session_value, "сессия · charter_conclusions.applied")]
    section = _section(evidence, "repeat_clients")
    if section is None:
        return _card(
            "repeat",
            "Повторные клиенты и реестр заключений",
            "сессия консоли: раздел charter_conclusions отчётов её кейсов",
            rows=rows,
            note="evidence.json не найден: прогона повторных клиентов здесь нет.",
        )
    key = "evidence.json · repeat_clients."
    payments = _num(section["payments"])
    tiles = [
        _tile(
            f"{_num(section['to_human_with'])} из {payments}",
            "уходят к человеку, когда юрист ведёт реестр заключений",
            key + "to_human_with",
        ),
        _tile(f"{_num(section['to_human_without'])} из {payments}", "к человеку без реестра", key + "to_human_without"),
        _tile(
            _num(section["confirmed_restricted"]),
            f"ПОДТВЕРЖДЕНО вопреки ограничению устава; платежей с ограничением: {_num(section['restricted_payments'])}",
            key + "confirmed_restricted",
            hero=section["confirmed_restricted"] == 0,
        ),
    ]
    rows[:0] = [
        _row("Клиентов в прогоне", _num(section["clients"]), key + "clients"),
        _row("Платежей повторных клиентов", payments, key + "payments"),
    ]
    return _card(
        "repeat",
        "Повторные клиенты и реестр заключений",
        f"evidence.json · repeat_clients, сборка {_day((evidence or {}).get('generated_at'))}; "
        "операции сессии — раздел charter_conclusions её отчётов",
        tiles=tiles,
        rows=rows,
        note=f"{section.get('boundary', '')}. Реестр на вкладке «Юрист» считает применения заключений, здесь — "
        "операции: операция с двумя заключениями считается один раз.",
    )


def _unit(values: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if values is None:
        return None
    base = unit_economics(values)[BASE]
    payback = float(values["bank"]["payback_cash"][BASE])
    calc = "mandat.eval.economics · "
    tiles = [
        _tile(
            f"{_num(base['manual_check_rub'])} ₽",
            "ручная проверка: минуты сотрудника по стоимости его минуты",
            calc + "manual_check_rub",
        ),
        _tile(
            f"{_num(base['mandat_cost_per_check_rub'])} ₽",
            "с «Мандатом»: подписка и сопровождение на одну проверку",
            calc + "mandat_cost_per_check_rub",
            hero=True,
        ),
        _tile(
            f"в {_num(base['manual_to_mandat'], 1)} раза",
            "дешевле ручной проверки",
            calc + "manual_to_mandat",
        ),
        _tile(
            f"{_num(payback, 1 if payback < 12 else 0)} мес.",
            "окупаются разовые затраты банка по денежному эффекту",
            "model_values.json · bank.payback_cash",
        ),
    ]
    rows = [
        _row(
            "Сценарий модели",
            f"{base['scenario']}, проверок в год: {_num(base['checks_per_year'])}",
            "model_values.json · scenarios, bank.events",
        )
    ]
    return _card(
        "unit",
        "Юнит-экономика проверки",
        f"финансовая модель {values['version']} от {values['as_of']} (submission/analytics/out/model_values.json); "
        "расчёт на проверку — mandat.eval.economics, тот же, что в docs/ECONOMICS.md и на сайте",
        tiles=tiles,
        rows=rows,
        note="Допущения модели и три сценария — в пакете аналитики; здесь базовый сценарий.",
    )


def _quality(evidence: Mapping[str, Any] | None) -> dict[str, Any] | None:
    corpus = _section(evidence, "synthetic_corpus")
    if corpus is None:
        return None
    tiles: list[dict[str, Any]] = []
    for name, label in (
        ("synthetic_corpus", "ложных ПОДТВЕРЖДЕНО на синтетическом корпусе"),
        ("synthetic_corpus_strict", "то же в строгом профиле"),
        ("synthetic_corpus_holdout", "то же на отложенном seed"),
    ):
        section = _section(evidence, name)
        if section is not None:
            wrong = section["false_confirmed"]
            tiles.append(_tile(_num(wrong), label, f"evidence.json · {name}.false_confirmed", hero=wrong == 0))
    rows = [
        _row("Кейсов в корпусе", _num(corpus["cases"]), "evidence.json · synthetic_corpus.cases"),
        _row("Совпало с ожидаемым вердиктом", _pct(corpus["agreement"]), "evidence.json · synthetic_corpus.agreement"),
    ]
    tests = _section(evidence, "tests")
    if tests is not None:
        tiles.append(_tile(_num(tests["passed"]), "автотестов прошли", "evidence.json · tests.passed"))
        rows.append(
            _row(
                "Покрытие кода тестами",
                _pct(tests["coverage_percent"] / 100),
                "evidence.json · tests.coverage_percent",
            )
        )
    stamp = evidence or {}
    return _card(
        "quality",
        "Качество",
        f"evidence.json · synthetic_corpus, synthetic_corpus_strict, synthetic_corpus_holdout, tests; "
        f"сборка {_day(stamp.get('generated_at'))}, {stamp.get('engine', 'движок')}",
        tiles=tiles,
        rows=rows,
        note=str(corpus.get("boundary", "")),
    )


def _spend() -> dict[str, Any]:
    usage = usage_view()
    counters, today = usage["usage"], usage["usage"]["today"]
    src = "счётчики модели · "
    tiles = [
        _tile(_usd(today["cost_usd"]), f"расход модели сегодня, лимит дня {_usd(today['cap_usd'])}", src + "today"),
        _tile(_num(counters["cache_hits"]), "ответов из кэша без нового вызова модели", src + "cache_hits"),
        _tile(_num(counters["calls"]), "вызовов модели с запуска службы", src + "calls"),
    ]
    return _card(
        "spend",
        "Расход модели",
        "счётчики консультанта (advisory/economy.py) с запуска службы, общие для всех сессий стенда; день — по Москве",
        tiles=tiles,
        note=str(usage["note"]),
    )
