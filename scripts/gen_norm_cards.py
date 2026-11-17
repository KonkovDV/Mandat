"""Карточки норм для юриста банка: одна на правило, в docs/legal/cards/, и сводка docs/legal/README.md.

    python scripts/gen_norm_cards.py

Карточка собирает в одно место то, что юрист проверяет перед подписью: текст правила из пакета, хеш
утверждаемого текста (его подписывает запись утверждения, ADR-009), источники нормы и глубину их проверки
из трассировки норм, дословные цитаты только из официальной публикации (docs/legal/quotes.json), поля
документов, на которые правило ссылается в демо-кейсах и корпусе, тесты и вопрос юристу. Руками карточки
не правят: тест сверяет файлы с генератором.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from mandat.domain.verdict import CHECK_LABEL_RU
from mandat.eval.run import HOLDOUT_SEED
from mandat.eval.synth import DEFECTS, build
from mandat.examples import DEMO, OVERLAYS
from mandat.intake.case_loader import load_case_dir, load_case_inline
from mandat.rules.approvals import CATALOG_RULES, rule_text_sha256
from mandat.rules.checks import RULES
from mandat.rules.pack import RulePack, RuleSpec, default_catalog, default_pack
from mandat.services.verify import verify_case

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_links import slug  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "docs" / "legal" / "cards"
INDEX = ROOT / "docs" / "legal" / "README.md"
QUOTES = ROOT / "docs" / "legal" / "quotes.json"
REVIEW = ROOT / "docs" / "LEGAL_REVIEW.md"
TRACE = ROOT / "docs" / "audit" / "LEGAL_TRACEABILITY.md"
CORPUS_PER_DEFECT = 6
CORPUS_SEED = 7

# Официальное опубликование и сайты органов власти. Справочно-правовые системы первоисточником не считаются.
OFFICIAL_HOSTS = (
    "publication.pravo.gov.ru",
    "pravo.gov.ru",
    "kremlin.ru",
    "government.ru",
    "digital.gov.ru",
    "nalog.gov.ru",
    "cbr.ru",
    "fedsfm.ru",
)
EFFECT_RU = {
    "supports": "подтверждает",
    "refutes": "опровергает",
    "needs_review": "на разбор",
    "not_verified": "не проверено",
    "info": "справка",
}
SOURCE_RU = {
    "emchd_xml": "МЧД (XML)",
    "egrul_json": "выписка ЕГРЮЛ",
    "charter_text": "устав",
    "paper_poa_text": "бумажная доверенность",
    "registry": "реестр МЧД",
    "efrsb": "ЕФРСБ",
    "signature": "проверка подписи",
    "classifier": "классификатор полномочий",
    "case": "манифест кейса",
    "source-trust": "доверие к источнику",
    "dbo": "ДБО банка",
}
_RULE_ID = r"R-[A-Z]+-(?:\d{3}|\*)"
_RANGE = re.compile(rf"({_RULE_ID})(?:\s*…\s*(?:R-[A-Z]+-)?(\d{{3}}))?")


def expand(text: str, known: list[str]) -> list[str]:
    """Идентификаторы правил из текста: «R-SUB-001…005», «R-SUB-001…R-SUB-010», «R-EIO-*». Скобки не читаются."""
    text = re.sub(r"\([^)]*\)", " ", text)
    out: list[str] = []
    for m in _RANGE.finditer(text):
        first, last = m.group(1), m.group(2)
        prefix = first.rsplit("-", 1)[0]
        if first.endswith("*"):
            out += [r for r in known if r.startswith(prefix + "-")]
        elif last:
            lo, hi = int(first.rsplit("-", 1)[1]), int(last)
            out += [r for r in known if r.startswith(prefix + "-") and lo <= int(r.rsplit("-", 1)[1]) <= hi]
        elif first in known:
            out.append(first)
    return list(dict.fromkeys(out))


def review_sections(known: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Правило → разделы docs/LEGAL_REVIEW.md, где о нём спрашивают юриста: (заголовок, якорь)."""
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    title, body = None, []
    for line in [*REVIEW.read_text(encoding="utf-8").splitlines(), "## конец"]:
        if line.startswith("## "):
            if title and re.match(r"\d+\. ", title):
                for rule in expand("\n".join(body), known):
                    out[rule].append((title, slug(title)))
            title, body = line[3:].strip(), []
        else:
            body.append(line)
    return out


def trace_sources(known: list[str]) -> dict[str, list[tuple[str, str, str]]]:
    """Правило → строки таблицы источников трассировки норм: (источник, редакция и сила, как проверен)."""
    out: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    in_table = False
    for line in TRACE.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_table = line.startswith("## Источники норм")
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if in_table and line.startswith("|") else []
        if len(cells) != 4 or cells[0] in {"Источник", ""} or set(cells[0]) <= {"-"}:
            continue
        for rule in expand(cells[3], known):
            out[rule].append((cells[0], cells[1], cells[2]))
    return out


def load_quotes(known: list[str]) -> dict[str, list[dict[str, str]]]:
    """Дословные цитаты из официальной публикации. Цитата без адреса, даты или с другого сайта — ошибка сборки."""
    payload = json.loads(QUOTES.read_text(encoding="utf-8"))
    if payload.get("schema") != 1 or not isinstance(payload.get("quotes"), list):
        raise SystemExit('docs/legal/quotes.json: ожидался {"schema": 1, "quotes": [...]}')
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in payload["quotes"]:
        rule, url = row.get("rule_id"), str(row.get("url", ""))
        host = urlparse(url).hostname or ""
        if rule not in known:
            raise SystemExit(f"quotes.json: правила {rule!r} нет в пакете")
        if urlparse(url).scheme != "https" or not any(host == h or host.endswith("." + h) for h in OFFICIAL_HOSTS):
            raise SystemExit(f"quotes.json: {rule}: адрес не официальной публикации ({url})")
        if not all(str(row.get(k, "")).strip() for k in ("source", "text", "checked_on")):
            raise SystemExit(f"quotes.json: {rule}: нужны source, text и checked_on")
        date.fromisoformat(row["checked_on"])
        out[rule].append({k: str(row[k]) for k in ("source", "url", "checked_on", "text")})
    return out


def _generic(locator: str) -> str:
    """Локатор без значений конкретного кейса: номера МЧД, ИНН, диапазоны символов и индексы."""
    locator = re.sub(r"\[\d+\]", "[i]", locator)
    for prefix, label in (
        ("registry:", "registry:<номер МЧД>"),
        ("poa:", "poa:<номер МЧД>"),
        ("efrsb:", "efrsb:<ИНН>"),
        ("code:", "code:<код полномочия>"),
    ):
        if locator.startswith(prefix):
            return label
    return re.sub(r"^text:\d+-\d+$", "text:<диапазон символов>", locator)


def observed_fields(pack: RulePack) -> dict[str, dict[str, set[str]]]:
    """Правило → источник → поля, на которые правило ссылалось в демо-кейсах и в корпусе evidence.json."""
    cases = [load_case_dir(ROOT / "examples" / "cases" / name) for name in [*DEMO, *OVERLAYS]]
    for seed in (CORPUS_SEED, HOLDOUT_SEED):
        for defect in DEFECTS:
            for k in range(CORPUS_PER_DEFECT):
                cases.append(load_case_inline(build(defect, seed=seed * 1000 + k).inline_manifest()))
    out: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for case in cases:
        kinds = {row["doc_id"]: row["kind"] for row in case.inputs}
        for profile in ("demo", "strict"):
            for finding in verify_case(case, profile=profile, pack=pack).findings:
                if finding.rule_id not in pack.rules:
                    continue
                for ref in finding.refs:
                    kind = kinds.get(ref.doc_id) or ref.doc_id.split(":")[0]
                    out[finding.rule_id][SOURCE_RU.get(kind, kind)].add(_generic(ref.locator))
    return out


def _md(text: str | None) -> str:
    return (text or "—").replace("|", "\\|").replace("\n", " ")


def _quote_md(text: str) -> str:
    """Цитата в блоке «>»: начальное «3)» или «7.» Markdown иначе показал бы нумерованным списком."""
    return re.sub(r"^(\d+)([.)])", r"\1\\\2", _md(text))


def render_card(
    spec: RuleSpec,
    pack: RulePack,
    sources: list[tuple[str, str, str]],
    questions: list[tuple[str, str]],
    quotes: list[dict[str, str]],
    fields: dict[str, set[str]],
) -> str:
    effects = ", ".join(EFFECT_RU.get(e, e) for e in spec.effects)
    function = RULES[spec.id].__name__ if spec.id in RULES else "—"
    params = ", ".join(f"`{k} = {json.dumps(v, ensure_ascii=False)}`" for k, v in sorted(spec.params.items()))
    lines = [
        f"# {spec.id}. {spec.title}",
        "",
        "> Карточка собрана командой `python scripts/gen_norm_cards.py` из пакета правил, трассировки норм и "
        "вопросов юристу. Руками её не правят: тест `test_norm_cards_are_up_to_date` сверяет файл с генератором.",
        "",
        f"**Статус:** черновик (`{spec.status}`). Юрист банка правило не утверждал. Утвердить его может только "
        "подписанная запись юриста ([ADR-009](../../adr/ADR-009-rule-approval-records.md)), "
        "а не строка в `rules.json`.",
        "",
        "| Что | Значение |",
        "|---|---|",
        f"| Пакет | `{pack.pack_id}`, версия `{pack.version}` |",
        f"| Группа проверок | {CHECK_LABEL_RU.get(spec.check, spec.check)} (`{spec.check}`) |",
        f"| Возможные эффекты | {effects} |",
        f"| Следующий шаг сотрудника | {_md(spec.next_step)} |",
        f"| Подразделение | {_md(spec.unit)} |",
        f"| Параметры банка (подписью не покрываются) | {params or '—'} |",
        f"| Реализация | `src/mandat/rules/checks.py::{function}` |",
        f"| Тесты | {', '.join(f'`{t}`' for t in spec.tests) or '—'} |",
        "",
        "## Норма",
        "",
        _md(spec.norm_ref),
        "",
    ]
    if sources:
        lines += [
            "Источник по [трассировке норм](../../audit/LEGAL_TRACEABILITY.md#источники-норм-и-глубина-их-проверки):",
            "",
            "| Источник | Редакция и сила | Как проверен |",
            "|---|---|---|",
            *(f"| {_md(a)} | {_md(b)} | {_md(c)} |" for a, b, c in sources),
            "",
        ]
    else:
        lines += ["В трассировке норм для этого правила отдельный источник не выделен.", ""]
    lines += ["## Дословная цитата нормы", ""]
    if quotes:
        for q in quotes:
            lines += [
                f"> {_quote_md(q['text'])}",
                "",
                f"— {_md(q['source'])}, <{q['url']}>, сверено {q['checked_on']}.",
                "",
            ]
    else:
        lines += [
            "Цитаты из официальной публикации с адресом и датой сверки нет. Пересказ здесь не приводится: "
            "норму нужно сверить по первоисточнику.",
            "",
        ]
    lines += ["## Поля документов", ""]
    if fields:
        lines += [
            "На что правило ссылается в находках демо-кейсов и корпуса `evidence.json` (значения кейса заменены "
            "метками, индексы — `[i]`):",
            "",
            "| Источник | Поле |",
            "|---|---|",
            *(
                f"| {_md(kind)} | {', '.join(f'`{_md(loc)}`' for loc in sorted(locs))} |"
                for kind, locs in sorted(fields.items())
            ),
            "",
        ]
    else:
        lines += ["В демо-кейсах и корпусе правило не сработало, полей для примера нет.", ""]
    lines += ["## Вопрос юристу", ""]
    if questions:
        lines += [f"- [{title}](../../LEGAL_REVIEW.md#{anchor})" for title, anchor in questions] + [""]
    else:
        lines += [
            "Отдельного вопроса в [вопросах юристу](../../LEGAL_REVIEW.md) нет: проверяется норма и формулировка.",
            "",
        ]
    lines += [
        "## Решение юриста",
        "",
        "Подпись ставится под хешем утверждаемого текста правила: группа, название, норма и эффекты "
        "(`rules/approvals.py::rule_text_sha256`). Правка любого из них меняет хеш и снимает утверждение.",
        "",
        f"`{rule_text_sha256(spec)}`",
        "",
        *(
            [
                "Для этого правила хеш включает и каталог операций `src/mandat/data/operations.json` "
                f"(sha256 `{default_catalog().sha256[:16]}…`): какие коды полномочий покрывают операцию, нужен ли "
                "устав и высок ли риск. Правка каталога тоже снимает утверждение.",
                "",
            ]
            if spec.id in CATALOG_RULES
            else []
        ),
        "- [ ] Утверждаю: подписать запись утверждения с этим хешем "
        "([как утвердить](../../LEGAL_REVIEW.md#как-утвердить-правило)).",
        "- [ ] Утверждаю с правкой: правило меняется, получает новый хеш и новую карточку. Правка: ____________",
        "- [ ] Отклоняю. Причина: ____________",
        "",
        "Редакция нормы: ____________ Дата сверки: ____________ Роль утверждающего (без ФИО): ____________",
        "",
    ]
    return "\n".join(lines)


def render_all() -> dict[Path, str]:
    pack = default_pack()
    known = list(pack.rules)
    sources, questions, quotes = trace_sources(known), review_sections(known), load_quotes(known)
    fields = observed_fields(pack)
    files = {
        CARDS / f"{rule_id}.md": render_card(
            spec, pack, sources.get(rule_id, []), questions.get(rule_id, []), quotes.get(rule_id, []), fields[rule_id]
        )
        for rule_id, spec in pack.rules.items()
    }
    quoted = sum(1 for rule in known if quotes.get(rule))

    def asked(rule_id: str) -> str:
        found = questions.get(rule_id, [])
        return ", ".join(f"[{title.split('.')[0]}](../LEGAL_REVIEW.md#{anchor})" for title, anchor in found) or "—"

    rows = [
        f"| [{spec.id}](cards/{spec.id}.md) | {_md(spec.title)} | {CHECK_LABEL_RU.get(spec.check, spec.check)} | "
        f"{'есть' if quotes.get(spec.id) else 'нет'} | {asked(spec.id)} | `{rule_text_sha256(spec)[:16]}…` |"
        for spec in pack.rules.values()
    ]
    files[INDEX] = "\n".join(
        [
            "# Пакет для юриста банка",
            "",
            "> Собран командой `python scripts/gen_norm_cards.py`. Руками не правится.",
            "",
            f"Карточка нормы на каждое из {len(known)} правил пакета `{pack.pack_id}` (версия `{pack.version}`). "
            "Юрист читает карточку, сверяет норму по первоисточнику и решает: утверждаю, утверждаю с правкой или "
            "отклоняю. Утверждение — подписанная запись под хешем из карточки "
            "([ADR-009](../adr/ADR-009-rule-approval-records.md), "
            "[как утвердить](../LEGAL_REVIEW.md#как-утвердить-правило)).",
            "",
            f"Сейчас все правила — черновики, файл утверждений пуст. Дословных цитат из официальной публикации: "
            f"{quoted} из {len(known)}; остальные ждут сверки по первоисточнику. Цитаты добавляются в "
            "[`quotes.json`](quotes.json) только с официального сайта, с адресом и датой сверки.",
            "",
            "| Правило | Что проверяет | Группа | Цитата | Вопрос юристу | Хеш текста |",
            "|---|---|---|---|---|---|",
            *rows,
            "",
        ]
    )
    return files


def main() -> int:
    CARDS.mkdir(parents=True, exist_ok=True)
    files = render_all()
    stale = {p for p in CARDS.glob("*.md")} - set(files)
    for path in stale:
        path.unlink()
    for path, text in files.items():
        path.write_text(text, encoding="utf-8")
    print(f"карточек: {len(files) - 1}, сводка: {INDEX.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
