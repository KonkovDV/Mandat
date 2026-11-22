"""Сайт проекта для GitHub Pages: девять страниц, у каждой свои данные из настоящего прогона.

Страницы — шаблоны ``site/*.html``. Числа подставляются из ``evidence.json``, деньги — из пересчитанной модели пакета
аналитики, календарь норм — из пакета правил, критерии — из таблиц ``submission/README.md`` и ``README.md``,
слайды — из ``site/slides`` (их выгружает ``submission/deck/export_slides.py`` после сборки колоды), фото амбассадора
и 3D-значки — из ``site/img`` (их пишет ``submission/deck/import_visuals.py`` из файлов Figma, docs/VISUALS.md).
Кейсы, удостоверение, журнал и сценарий захвата берутся из ``mandat demo``, ``mandat monitor`` и ``mandat showcase``
этого же коммита. На сайт уходят HTML-отчёты, выдержки из отчётов JSON, одно удостоверение со списками статусов,
открытый ключ стенда и доказательство одной записи журнала. Закрытые ключи стенда и журнал целиком остаются
во временном каталоге. Внешних запросов у страниц нет: шрифт, скрипты и стили лежат рядом, политика CSP
разрешает только свой адрес.

    python scripts/build_site.py --out _site
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from string import Template
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mandat.audit import journal, merkle  # noqa: E402
from mandat.data import load_json  # noqa: E402
from mandat.rules.pack import default_pack  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import effect_model  # noqa: E402

SITE = ROOT / "site"
# Шрифты сайта — WOFF2 (site/fonts), в разы легче TTF из колоды: Golos Text для текста, Manrope для заголовков
# и чисел, курсив Cormorant Garamond для акцентного слова.
FONTS = (
    "GolosText-Regular.woff2",
    "GolosText-SemiBold.woff2",
    "GolosText-Bold.woff2",
    "Manrope-cyrillic.woff2",
    "Manrope-latin.woff2",
    "CormorantGaramond-SemiBoldItalic-cyrillic.woff2",
)
LICENSES = ("OFL.txt", "OFL-Manrope.txt", "OFL-CormorantGaramond.txt")  # Golos Text, Manrope, Cormorant Garamond
STATIC = (
    "style.css",
    "favicon.svg",
    "manifest.webmanifest",
    "apple-touch-icon.png",
    "icon-192.png",
    "icon-512.png",
    "og.jpg",
)
MATERIALS = {
    "Mandat_VTB.pdf": ROOT / "submission" / "slides" / "Mandat_VTB.pdf",
    "Mandat_VTB.pptx": ROOT / "submission" / "slides" / "Mandat_VTB.pptx",
    "Mandat_analytics_all.pdf": ROOT / "submission" / "analytics" / "out" / "Mandat_analytics_all.pdf",
    "Mandat_financial_model.xlsx": ROOT / "submission" / "analytics" / "out" / "Mandat_financial_model.xlsx",
    "Mandat_demo.mp4": ROOT / "submission" / "video" / "out" / "Mandat_demo.mp4",
}
VERDICTS = {
    "confirmed": ("подтверждено", "ok"),
    "refuted": ("опровергнуто", "no"),
    "needs_review": ("на разбор", "review"),
    "insufficient_data": ("недостаточно данных", "gap"),
}
REPO = "https://github.com/KonkovDV/Mandat"
# Адрес страницы на GitHub Pages: нужен метаданным Open Graph и ссылкам в колоде, браузер по нему ничего не грузит.
SITE_URL = "https://konkovdv.github.io/Mandat/"
# Страницы в порядке меню. Главная в меню не входит: на неё ведёт знак в шапке.
PAGES = (
    ("index", "Главная"),
    ("cases", "Кейсы"),
    ("showcase", "Захват"),
    ("verify", "Проверка"),
    ("economics", "Экономика"),
    ("pilot", "Пилот"),
    ("law", "Закон"),
    ("criteria", "Критерии"),
    ("pitch", "Слайды"),
)
# Политика страниц: скрипты, стили, шрифты и картинки — только со своего адреса; встроенных скриптов и стилей нет.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; media-src 'self'; object-src 'none'; base-uri 'self'; form-action 'none'"
)
# Сценарий показа: события захвата для mandat monitor и кейс, удостоверение которого проверяется в браузере.
TAKEOVER = ROOT / "examples" / "events" / "takeover.json"
VERIFY_CASE = "14_old_director_charter_cleared"
EFFECT_ORDER = {"refutes": 0, "needs_review": 1, "not_verified": 2, "info": 3, "supports": 4}
CHECK_MARK = {
    "ok": "ok",
    "refuted": "no",
    "review": "review",
    "not_verified": "gap",
    "info_only": "skip",
    "not_applicable": "skip",
    "waived": "skip",
}
CHECK_WORD = {
    "ok": "сходится",
    "refuted": "опровергает",
    "review": "нужен человек",
    "not_verified": "не проверено",
    "info_only": "справка",
    "not_applicable": "не применимо",
    "waived": "не требуется на стенде",
}


def _num(value: float, digits: int = 0) -> str:
    """Число по-русски: узкий неразрывный пробел между разрядами и запятая."""
    text = f"{value:,.{digits}f}".replace(",", "\u202f").replace(".", ",")
    return text


def _plural(n: int, one: str, few: str, many: str) -> str:
    tail = n % 100
    if 11 <= tail <= 14:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(n % 10, many)


def _day(value: date | None) -> str:
    return f"{value:%d.%m.%Y}" if value is not None else "—"


def _ru(iso: str | None) -> str:
    return "—" if not iso else f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}"


def _esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _json_block(payload: Any) -> str:
    """JSON для <script type="application/json">: «<», «>» и «&» экранированы, блок нельзя закрыть данными."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    for char, code in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), (" ", "\\u2028"), (" ", "\\u2029")):
        text = text.replace(char, code)
    return text


def _cli(*args: str) -> str:
    """Команда mandat в этом же процессе; вывод перехватывается, ненулевой код — ошибка сборки."""
    from mandat.cli import main

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(list(args))
    if code != 0:
        raise SystemExit(f"mandat {' '.join(args)} завершился с кодом {code}")
    return buffer.getvalue()


# ---------- Стенд: демо, событие захвата, сценарий ----------


def _stand(tmp: Path, demo_dir: Path | None) -> dict[str, Path]:
    """Каталоги прогона: демо, его копия после события захвата и сценарий mandat showcase."""
    demo = tmp / "demo"
    if demo_dir is None:
        _cli("demo", "--out", str(demo))
    else:
        shutil.copytree(demo_dir, demo)
    after = tmp / "after"
    shutil.copytree(demo, after)
    _cli("monitor", str(TAKEOVER), "--status-dir", str(after / "status"))
    showcase = tmp / "showcase"
    _cli("showcase", "--out", str(showcase))
    return {"demo": demo, "after": after, "showcase": showcase}


# ---------- Данные страниц ----------


def _cases(evidence: dict, demo: Path) -> list[dict[str, Any]]:
    """Выдержка из отчётов демо: вердикт, проверки, находки с нормой и даты для оси времени."""
    out = []
    for case_id, result in evidence["demo_cases"]["results"].items():
        folder = ROOT / "examples" / "cases" / case_id
        manifest = json.loads((folder / "case.json").read_text(encoding="utf-8"))
        egrul = json.loads((folder / "egrul.json").read_text(encoding="utf-8"))
        report = json.loads((demo / f"{case_id}.json").read_text(encoding="utf-8"))
        label, css = VERDICTS[report["verdict"]]
        if report["verdict"] != result["verdict"]:
            raise SystemExit(f"{case_id}: в evidence.json {result['verdict']}, а демо дало {report['verdict']}")
        findings = sorted(report["findings"], key=lambda f: (EFFECT_ORDER.get(f["effect"], 9), f["rule_id"]))
        registry = manifest.get("registry", {})
        out.append(
            {
                "id": case_id,
                "title": manifest.get("title", case_id),
                "verdict": report["verdict"],
                "label": label,
                "css": css,
                "operation": {
                    "name": report["operation"]["name"],
                    "date": report["operation"]["date"],
                    "amount": report["operation"].get("amount_rub"),
                },
                "org": report["organization"]["name"],
                "requester": report["requester"]["full_name"],
                "objection": report.get("objection") or "",
                "checks": [
                    {
                        "label": check["label"],
                        "status": check["status"],
                        "mark": CHECK_MARK.get(check["status"], "skip"),
                        "word": CHECK_WORD.get(check["status"], check["status"]),
                    }
                    for check in report["checks"].values()
                ],
                "findings": [
                    {
                        "rule": f["rule_id"],
                        "effect": f["effect"],
                        "message": f["message"],
                        "norm": f.get("norm_ref") or "",
                        "step": f.get("next_step") or "",
                        "unit": f.get("unit") or "",
                        "loc": "; ".join(ref.get("locator") or "" for ref in f.get("refs", []) if ref.get("locator")),
                    }
                    for f in findings
                ],
                "next_steps": [{"action": s["action"], "unit": s.get("unit") or ""} for s in report["next_steps"]],
                "routing": report["routing"],
                "chain_kind": report["chain"]["kind"],
                "timeline": {
                    "executives": [
                        {
                            "name": e["full_name"],
                            "from": e.get("valid_from"),
                            "to": e.get("valid_to"),
                            "unreliable": bool(e.get("unreliable")),
                        }
                        for e in egrul.get("executives", [])
                    ],
                    "documents": [
                        {
                            "number": d["number"],
                            "issue": d.get("issue_date"),
                            "until": d.get("effective_until") or d.get("valid_until"),
                            "by_law": bool(d.get("term_by_law")),
                            "signer": d.get("signer") or "",
                            "to": ", ".join(d.get("representatives") or []),
                            "parent": d.get("parent_number"),
                            "status": registry.get(d["number"], "no_answer"),
                        }
                        for d in report["chain"].get("documents", [])
                    ],
                    "op": report["operation"]["date"],
                    "extracted": egrul.get("extracted_at"),
                },
                "report": f"reports/{case_id}.html",
            }
        )
    return out


# ---------- Ядро на сборке: ответ на каждый день и на каждое сочетание фактов ----------

LAB_CASE = "14_old_director_charter_cleared"
DECIDES = {
    "refuted": "refutes",
    "needs_review": "needs_review",
    "insufficient_data": "not_verified",
    "confirmed": "info",
}
# Звено цепочки, которое называет правило: по нему страница подсвечивает схему.
LINK_OF = {"R-EIO": "eio", "R-BNK": "org", "R-ID": "org", "R-SUB": "sub", "R-CHR": "charter", "R-JNT": "poa"}


def _lab_case() -> tuple[dict[str, Any], dict[str, Any], dict[str, bytes]]:
    folder = ROOT / "examples" / "cases" / LAB_CASE
    manifest = json.loads((folder / "case.json").read_text(encoding="utf-8"))
    egrul = json.loads((folder / "egrul.json").read_text(encoding="utf-8"))
    files = {d["id"]: (folder / d["path"]).read_bytes() for d in manifest["documents"]}
    return manifest, egrul, files


def _answer(manifest: dict[str, Any], egrul: dict[str, Any], files: dict[str, bytes]) -> dict[str, Any]:
    """Вердикт ядра и находки, которые его решили. Проверки, отключённые на стенде (waived), не решают."""
    from mandat.intake.case_loader import load_case_from_manifest
    from mandat.services.verify import verify_case

    docs = {**files, "egrul": json.dumps(egrul, ensure_ascii=False).encode("utf-8")}
    report = verify_case(load_case_from_manifest(manifest, lambda d: docs[d["id"]]), profile="demo").as_dict()
    waived = {k for k, v in report["checks"].items() if v["status"] == "waived"}
    want = DECIDES[report["verdict"]]
    deciding = [f for f in report["findings"] if f["effect"] == want and f["check"] not in waived]
    label, css = VERDICTS[report["verdict"]]
    return {
        "verdict": report["verdict"],
        "label": label,
        "css": css,
        "why": [
            {
                "rule": f["rule_id"],
                "message": f["message"],
                "norm": f.get("norm_ref") or "",
                "step": f.get("next_step") or "",
                "link": LINK_OF.get(f["rule_id"].rsplit("-", 1)[0], "poa"),
            }
            for f in deciding[:3]
        ],
    }


def _scrub() -> dict[str, Any]:
    """Кейс 14 на каждый день: тот же пакет документов, меняется только дата операции."""
    from datetime import timedelta

    manifest, egrul, files = _lab_case()
    start, end = date(2026, 1, 15), date(2027, 4, 15)
    segments: list[dict[str, Any]] = []
    day = start
    while day <= end:
        probe = json.loads(json.dumps(manifest))
        probe["operation"]["date"] = day.isoformat()
        answer = _answer(probe, egrul, files)
        key = (answer["verdict"], tuple(w["rule"] for w in answer["why"]))
        if segments and segments[-1]["key"] == key:
            segments[-1]["to"] = day.isoformat()
        else:
            segments.append({"key": key, "from": day.isoformat(), "to": day.isoformat(), **answer})
        day += timedelta(days=1)
    for s in segments:
        del s["key"]
    root = (ROOT / "examples" / "cases" / LAB_CASE / "poa-root.xml").read_text(encoding="utf-8")
    issued = re.search(r'ДатаВыдДовер="([^"]+)"', root)
    until = re.search(r'СрокДейст="([^"]+)"', root)
    executives = egrul["executives"]
    marks = [
        {"date": issued.group(1) if issued else None, "text": "выдана МЧД"},
        {"date": executives[1]["valid_from"], "text": "новый директор в ЕГРЮЛ"},
        {"date": egrul.get("extracted_at"), "text": "выписка ЕГРЮЛ"},
        {"date": until.group(1) if until else None, "text": "срок МЧД"},
    ]
    return {
        "case": LAB_CASE,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "op": manifest["operation"]["date"],
        "amount": manifest["operation"]["amount_rub"],
        "segments": segments,
        "marks": [m for m in marks if m["date"]],
    }


def _whatif() -> dict[str, Any]:
    """Кейс 14 и сочетания фактов: реестр МЧД (действует, отменена, молчит) и четыре переключателя."""
    from datetime import timedelta
    from itertools import product

    manifest, egrul, files = _lab_case()
    op = date.fromisoformat(manifest["operation"]["date"])

    def fresh(m: dict[str, Any], e: dict[str, Any]) -> None:
        e["executives"][0]["valid_to"] = (op - timedelta(days=11)).isoformat()
        e["executives"][1]["valid_from"] = e["executives"][1]["record_date"] = (op - timedelta(days=10)).isoformat()

    def charter(m: dict[str, Any], e: dict[str, Any]) -> None:
        m.pop("charter_disposition", None)
        m.pop("charter_disposition_note", None)

    def bankrupt(m: dict[str, Any], e: dict[str, Any]) -> None:
        m["bankruptcy"] = {m["organization"]["inn"]: "bankruptcy_proceedings"}

    def stale(m: dict[str, Any], e: dict[str, Any]) -> None:
        e["extracted_at"] = (op - timedelta(days=45)).isoformat()

    toggles = (("fresh", fresh), ("charter", charter), ("bankrupt", bankrupt), ("stale", stale))
    answers: dict[str, Any] = {}
    for registry in ("active", "revoked", "silent"):
        for bits in product((0, 1), repeat=len(toggles)):
            m, e = json.loads(json.dumps(manifest)), json.loads(json.dumps(egrul))
            m["registry"] = {} if registry == "silent" else {k: registry for k in m["registry"]}
            for on, (_name, change) in zip(bits, toggles, strict=True):
                if on:
                    change(m, e)
            answers[f"{registry}-{''.join(map(str, bits))}"] = _answer(m, e, files)
    if answers["active-0000"]["verdict"] != "confirmed":
        raise SystemExit("кейс 14 без изменений должен давать «подтверждено»")
    return {"case": LAB_CASE, "toggles": [name for name, _ in toggles], "answers": answers}


def _verify(stand: dict[str, Path]) -> dict[str, Any]:
    """Удостоверение кейса 14, списки статусов до и после события захвата, ключ стенда и запись журнала."""
    demo, after = stand["demo"], stand["after"]
    attestation = json.loads((demo / "attestations" / f"{VERIFY_CASE}.json").read_text(encoding="utf-8"))
    lists = {
        moment: {
            purpose: json.loads((folder / "status" / f"{purpose}.json").read_text(encoding="utf-8"))
            for purpose in ("revocation", "suspension")
        }
        for moment, folder in (("before", demo), ("after", after))
    }
    records = journal.read_journal(demo / "journal.jsonl")
    leaves = merkle.journal_leaves(records)
    index = next(i for i, r in enumerate(records) if r.get("case_id") == VERIFY_CASE)
    proof = merkle.inclusion_proof(index, leaves)
    root = merkle.root(leaves)
    if not merkle.verify_inclusion(leaves[index], index, len(leaves), proof, root):
        raise SystemExit("доказательство включения записи журнала не сходится")
    return {
        "case": VERIFY_CASE,
        "attestation": attestation,
        "public_key": (demo / "public_key.txt").read_text(encoding="utf-8").strip(),
        "lists": lists,
        "journal": {
            "seq": index + 1,
            "size": len(leaves),
            "record": records[index],
            "leaf": leaves[index].hex(),
            "proof": [h.hex() for h in proof],
            "root": root.hex(),
        },
    }


def _showcase(stand: dict[str, Path]) -> dict[str, Any]:
    """Шаги mandat showcase и записи журнала, которые оставил каждый шаг."""
    data = json.loads((stand["showcase"] / "showcase.json").read_text(encoding="utf-8"))
    want = "ПОДТВЕРЖДЕНО → ВЫПУЩЕНО → ДОПУЩЕНО → ОТКАЗ → ОТКАЗ → ПРИЗНАКИ ЗАХВАТА → ОТКАЗ"
    got = " → ".join(step["outcome"] for step in data["steps"])
    if got != want or not data["journal"]["intact"] or not data["refusal_inclusion_proof_verified"]:
        raise SystemExit(f"mandat showcase дал «{got}», а страница рассчитана на «{want}»")
    records = journal.read_journal(stand["showcase"] / "journal.jsonl")
    # Проверка кейса пишет verify, выпуск удостоверения журнал не трогает, каждый запрос ДБО — authorize_operation,
    # событие захвата — status_change на каждое приостановленное удостоверение.
    action = {"ПОДТВЕРЖДЕНО": "verify", "ДОПУЩЕНО": "authorize_operation", "ОТКАЗ": "authorize_operation"}
    queue = list(records)
    for step in data["steps"]:
        mine: list[dict[str, Any]] = []
        if step["outcome"] == "ПРИЗНАКИ ЗАХВАТА":
            while queue and queue[0]["action"] == "status_change":
                mine.append(queue.pop(0))
        elif step["outcome"] in action:
            if not queue or queue[0]["action"] != action[step["outcome"]]:
                raise SystemExit(f"журнал mandat showcase не совпал с шагом «{step['title']}»")
            mine.append(queue.pop(0))
        step["records"] = [
            {"seq": r["seq"], "action": r["action"], "role": r.get("role"), "details": r.get("details") or {}}
            for r in mine
        ]
    if queue or len(records) != data["journal"]["records"]:
        raise SystemExit("в журнале mandat showcase остались записи без шага")
    return data


def _months(value: float) -> str:
    """Срок окупаемости, как в колоде и пакете аналитики: до года — с десятыми («7,5 месяца»)."""
    rounded = round(value) if value >= 12 else round(value, 1)
    if float(rounded).is_integer():
        n = int(rounded)
        return f"{n} {_plural(n, 'месяц', 'месяца', 'месяцев')}"
    return f"{_num(rounded, 1)} месяца"


def _model() -> dict[str, Any]:
    return json.loads((ROOT / "submission" / "analytics" / "out" / "model_values.json").read_text(encoding="utf-8"))


# Входы модели банка, которые калькулятор читает из пакета аналитики (лист «Банк», три сценария).
ECON_INPUTS = (
    "clients",
    "event_share",
    "minutes",
    "hours_fte",
    "incidents_q",
    "loss_q",
    "vtb_share",
    "authority_share",
    "salary_ops",
    "bank_contrib",
    "overhead",
    "realization",
    "bank_loss_share",
    "ramp",
    "fixed_t1",
    "price_client",
    "impl_t1",
    "vat",
    "bank_it_cost",
    "bank_integration_py",
    "bank_infra",
    "bank_security",
    "bank_support_py",
)
# Выходы, по которым калькулятор при загрузке сверяет себя с моделью: расхождение — ошибка в консоли.
ECON_OUTPUTS = (
    "events",
    "fte",
    "cost_fte",
    "capacity",
    "cash_saving",
    "prevented",
    "bank_loss",
    "effect_cash",
    "sub_vtb",
    "one_off",
    "annual_cost",
    "y1_cash",
    "y2_cash",
    "roi_cash",
    "payback_cash",
)


def _economics(model: dict[str, Any]) -> dict[str, Any]:
    bank = model["bank"]
    unit = effect_model.unit_economics()
    return {
        "scenarios": model["scenarios"],
        "inputs": {key: bank[key] for key in ECON_INPUTS},
        "outputs": {key: bank[key] for key in ECON_OUTPUTS},
        "unit": [
            {
                "manual": float(u["manual_check_rub"]),
                "mandat": float(u["mandat_cost_per_check_rub"]),
            }
            for u in unit
        ],
        "as_of": model["as_of"],
        "version": model["version"],
    }


def economics() -> dict[str, str]:
    """Ценность для банка в базовом сценарии: те же значения, что в колоде, форме спринта и пакете аналитики.

    Юнит-экономика — ``scripts/effect_model.py --unit`` (C-21), окупаемость, эффект и ставки — модель пакета
    аналитики, операции без согласия у юрлиц — обзоры Банка России в ``effect_scenarios.json`` (M-07, M-15).
    """
    unit = effect_model.unit_economics()[1]
    bank = _model()["bank"]
    effect = load_json("effect_scenarios.json")
    fte = int(bank["fte_round"][1])
    return {
        "unit_manual": _num(round(float(unit["manual_check_rub"]) / 10) * 10),
        "unit_mandat": _num(float(unit["mandat_cost_per_check_rub"])),
        "unit_ratio": _num(float(unit["manual_to_mandat"]), 1),
        "payback": _months(float(bank["payback_cash"][1])),
        "prevented": _num(float(bank["prevented_round"][1])),
        "fte": str(fte),
        "fte_word": _plural(fte, "ставка", "ставки", "ставок"),
        "incidents_h1": _num(2 * float(effect["incidentsPerQuarter"])),
        "loss_h1_bln": _num(2 * float(effect["lossPerQuarterMln"]) / 1000, 1),
    }


def _scale(model: dict[str, Any]) -> dict[str, str]:
    """Масштаб в базовом сценарии модели: банки с договором, годовая подписка, команда и потребность в деньгах."""
    base = model["calc"][1]
    series, const = base["series"], base["const"]
    years = model["years"]
    return {
        "scale_banks_first": str(series["banks"][0]),
        "scale_banks_last": str(int(const["banks_2031"])),
        "scale_year_first": str(years[0]),
        "scale_year_last": str(years[-1]),
        "scale_arr_last": _num(round(float(const["arr_2031"]))),
        "scale_team_first": str(series["hc"][0]),
        "scale_team_last": str(int(const["hc_2031"])),
        "scale_funding": _num(round(float(const["funding"]))),
        "scale_ebitda_year": str(const["year_ebitda"]),
    }


def _econ_rows(model: dict[str, Any]) -> str:
    """Таблица трёх сценариев: она видна и без скриптов, и рядом с калькулятором как его табличный двойник."""
    bank = model["bank"]
    rows = (
        ("Высвобождается ставок в год", [_num(v) for v in bank["fte_round"]]),
        ("Предотвращённый ущерб, млн ₽ в год", [_num(v) for v in bank["prevented_round"]]),
        ("Разовые затраты банка, млн ₽", [_num(v, 1) for v in bank["one_off"]]),
        ("Ежегодные затраты банка, млн ₽", [_num(v, 1) for v in bank["annual_cost"]]),
        ("Окупаемость разовых затрат", [_months(float(v)) for v in bank["payback_cash"]]),
        ("Отдача за три года на рубль затрат", [f"{_num(v, 1)}×" for v in bank["roi_cash"]]),
    )
    lines = []
    for name, vals in rows:
        cells = "".join(f'<td class="n">{_esc(v)}</td>' for v in vals)
        lines.append(f'<tr><th scope="row">{_esc(name)}</th>{cells}</tr>')
    return "\n".join(lines)


def _law() -> list[dict[str, Any]]:
    return [
        {
            "title": norm.title,
            "url": norm.url,
            "from": norm.in_force_from.isoformat() if norm.in_force_from else None,
            "until": norm.in_force_until.isoformat() if norm.in_force_until else None,
            "rules": list(norm.rules),
        }
        for norm in default_pack().norms
    ]


def _law_rows() -> str:
    rows = []
    for norm in default_pack().norms:
        rules = ", ".join(f'<span class="rule">{html.escape(rule)}</span>' for rule in norm.rules)
        rows.append(
            f'<tr data-rules="{_esc(" ".join(norm.rules))}"><td><a href="{html.escape(norm.url)}">'
            f"{html.escape(norm.title)}</a></td>"
            f"<td>{_day(norm.in_force_from)}</td><td>{_day(norm.in_force_until)}</td><td>{rules}</td></tr>"
        )
    return "\n".join(rows)


# ---------- Критерии: таблицы README — один источник для сайта и плана сдачи ----------

LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
CODE = re.compile(r"`([^`]+)`")


def _href(target: str, base: Path) -> str:
    """Ссылка из Markdown репозитория — на файл в GitHub; внешние адреса как есть."""
    if target.startswith(("http://", "https://", "mailto:")):
        return target
    path, _, anchor = target.partition("#")
    if not path:
        return f"{REPO}/blob/main/{base.as_posix()}" + (f"#{anchor}" if anchor else "")
    resolved = (ROOT / base.parent / path).resolve().relative_to(ROOT).as_posix()
    kind = "tree" if (ROOT / resolved).is_dir() else "blob"
    return f"{REPO}/{kind}/main/{resolved}" + (f"#{anchor}" if anchor else "")


def _inline(text: str, base: Path) -> str:
    """Строчная разметка Markdown → HTML: ссылки и код; остальной текст экранируется."""
    out, last = [], 0
    for m in LINK.finditer(text):
        out.append(_code(text[last : m.start()]))
        out.append(f'<a href="{_esc(_href(m.group(2), base))}">{_code(m.group(1))}</a>')
        last = m.end()
    out.append(_code(text[last:]))
    return "".join(out)


def _code(text: str) -> str:
    parts, last = [], 0
    for m in CODE.finditer(text):
        parts.append(html.escape(text[last : m.start()]))
        parts.append(f"<code>{html.escape(m.group(1))}</code>")
        last = m.end()
    parts.append(html.escape(text[last:]))
    return "".join(parts)


def _table(markdown: str, heading: str) -> list[list[str]]:
    """Строки первой таблицы после заголовка; ячейки без внешних пробелов, шапка и разделитель отброшены."""
    lines = markdown.splitlines()
    try:
        start = lines.index(heading)
    except ValueError as exc:
        raise SystemExit(f"нет раздела «{heading}»") from exc
    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        if line.startswith("|"):
            rows.append([cell.strip() for cell in line.strip().strip("|").split(" | ")])
        elif rows:
            break
    if len(rows) < 3:
        raise SystemExit(f"в разделе «{heading}» нет таблицы")
    return rows[2:]


def _criteria() -> dict[str, Any]:
    plan = ROOT / "submission" / "README.md"
    text = plan.read_text(encoding="utf-8")
    base = plan.relative_to(ROOT)

    def scored(heading: str) -> list[dict[str, Any]]:
        out = []
        for name, points, answer, lack in _table(text, heading):
            low, _, high = points.partition("–")
            out.append(
                {
                    "name": name,
                    "points": points,
                    "max": int(high or low),
                    "answer": _inline(answer, base),
                    "lack": _inline(lack, base) if lack.strip("— ") else "",
                }
            )
        return out

    readme = ROOT / "README.md"
    conditions = [
        {"name": _inline(cond, readme.relative_to(ROOT)), "answer": _inline(how, readme.relative_to(ROOT))}
        for cond, how in _table(readme.read_text(encoding="utf-8"), "## Задача заказчика")
    ]
    final = [
        {"name": _inline(want, base), "ready": _inline(ready, base), "next": _inline(nxt, base)}
        for want, ready, nxt in _table(text, "## Спринт 3 и финал")
    ]
    return {
        "sprint1": scored("## Критерии спринта 1"),
        "sprint2": scored("## Критерии спринта 2"),
        "final": final,
        "conditions": conditions,
    }


def _criteria_html(criteria: dict[str, Any]) -> dict[str, str]:
    def scored(rows: list[dict[str, Any]]) -> str:
        items = []
        for row in rows:
            lack = f'<p class="lack"><b>Чего нет:</b> {row["lack"]}</p>' if row["lack"] else ""
            head = f'<b>{_esc(row["name"])}</b><span class="pts">{_esc(row["points"])} баллов</span>'
            items.append(f'<li><div class="head">{head}</div><p class="where">{row["answer"]}</p>{lack}</li>')
        return "\n".join(items)

    final = "\n".join(
        f'<li><div class="head"><b>{row["name"]}</b></div><p class="where"><b>Готово:</b> {row["ready"]}</p>'
        f'<p class="lack"><b>К 08.12:</b> {row["next"]}</p></li>'
        for row in criteria["final"]
    )
    conditions = "\n".join(
        f'<li><div class="head"><b>{row["name"]}</b></div><p class="where">{row["answer"]}</p></li>'
        for row in criteria["conditions"]
    )
    return {
        "crit_sprint1": scored(criteria["sprint1"]),
        "crit_sprint2": scored(criteria["sprint2"]),
        "crit_final": final,
        "crit_conditions": conditions,
        "sprint1_total": str(sum(r["max"] for r in criteria["sprint1"])),
        "sprint2_total": str(sum(r["max"] for r in criteria["sprint2"])),
    }


# ---------- Слайды ----------


def _slides() -> dict[str, Any]:
    path = SITE / "slides" / "slides.json"
    if not path.exists():
        raise SystemExit("нет site/slides/slides.json: соберите колоду и выполните submission/deck/export_slides.py")
    slides = json.loads(path.read_text(encoding="utf-8"))
    for slide in slides["slides"]:
        if not (SITE / "slides" / slide["image"]).is_file():
            raise SystemExit(f"нет картинки слайда {slide['image']}")
        # Слайды при пересборке колоды меняются под теми же именами: версия в адресе, как у стилей и скриптов.
        slide["src"] = f"{slide['image']}?v={_version(SITE / 'slides' / slide['image'])}"
    return slides


def _slide_items(slides: dict[str, Any]) -> str:
    return "\n".join(
        f'<li id="slide-{s["n"]}"><figure><a href="slides/{_esc(s["src"])}"><img src="slides/{_esc(s["src"])}" '
        f'alt="Слайд {s["n"]}: {_esc(s["title"])}" width="{s["width"]}" height="{s["height"]}" loading="lazy" '
        f'decoding="async"></a><figcaption>{s["n"]}. {_esc(s["title"])}</figcaption></figure></li>'
        for s in slides["slides"]
    )


# ---------- Шапка, подвал, сборка ----------

THEME_ICON = (
    '<svg class="sun" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.2" fill="none" '
    'stroke="currentColor" stroke-width="1.8"/><path d="M12 2.5v2.2M12 19.3v2.2M4.6 4.6l1.6 1.6M17.8 17.8l1.6 1.6'
    'M2.5 12h2.2M19.3 12h2.2M4.6 19.4l1.6-1.6M17.8 6.2l1.6-1.6" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round"/></svg><svg class="moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.6A8.2 '
    '8.2 0 0 1 9.4 4a8.2 8.2 0 1 0 10.6 10.6z" fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linejoin="round"/></svg>'
)


def _logo() -> str:
    """Логотип из README (docs/assets/logo.svg) прямо в разметке: цвета задаёт CSS, в тёмной теме он светлый."""
    svg = (ROOT / "docs" / "assets" / "logo.svg").read_text(encoding="utf-8")
    view = re.search(r'viewBox="([^"]+)"', svg)
    paths = re.findall(r'<path[^>]*?\sd="([^"]+)"', svg)
    if view is None or len(paths) != 4:
        raise SystemExit(
            "docs/assets/logo.svg: ожидались viewBox и четыре контура (грани, нижняя грань, диагональ, надпись)"
        )
    navy, deep, blue, word = paths
    return (
        f'<svg class="logo" viewBox="{view.group(1)}" width="130" height="26" role="img" aria-label="Мандат">'
        f'<path class="lg-n" d="{navy}"/><path class="lg-d" d="{deep}"/><path class="lg-b" d="{blue}"/>'
        f'<path class="lg-w" fill-rule="evenodd" d="{word}"/></svg>'
    )


def _header(active: str) -> str:
    current = ' aria-current="page"'
    links = "\n      ".join(
        f'<a href="{name}.html"{current if name == active else ""}>{title}</a>' for name, title in PAGES[1:]
    )
    home = current if active == "index" else ""
    return f"""<a class="skip-link" href="#main">К содержанию</a>
<header class="top" id="top">
  <div class="wrap bar">
    <a class="brand" href="index.html"{home}>{_logo()}</a>
    <nav class="nav" aria-label="Страницы сайта">
      {links}
    </nav>
    <button class="theme" type="button" aria-label="Сменить тему">{THEME_ICON}</button>
  </div>
</header>"""


TEXT_NODE = re.compile(r"(<script\b.*?</script|<code>.*?</code)|>([^<]+)(?=<)", re.S | re.I)


def _typo(page: str) -> str:
    """Русская типографика в тексте страницы, как в колоде: «315‑ФЗ» не рвётся на дефисе,
    «№ 856» — на пробеле, тире не начинает строку, предлог и союз из одной-двух букв не висят в конце строки,
    число не отрывается от единицы. Скрипты, данные, код и атрибуты не трогаются."""
    nb = "\u00a0"

    def text(m: re.Match[str]) -> str:
        if m.group(1):
            return m.group(1)
        t = re.sub(r"(\d)-(ФЗ|П|У|И)\b", "\\1\u2011\\2", m.group(2))
        t = re.sub(r"№ (?=\d)", "№\u00a0", t)
        t = re.sub(r" +—", nb + "—", t)
        t = re.sub(r"(?<![\w&;-])([А-ЯЁа-яё]{1,2}) +(?=[\w«„(№])", "\\1" + nb, t)
        # Число держится за единицу («5 рабочих», «95 %»), но не за предлог: «№ 857 о классификаторе» иначе
        # склеится в цепочку шире узкого экрана.
        t = re.sub(r"(?<![\d.,])(\d+(?:,\d+)?) +(?=[а-яё]{3}|[₽%])", "\\1" + nb, t)
        t = re.sub(r"(?<=[≈≤≥×]) +(?=\d)", nb, t)
        return f">{t}"

    return TEXT_NODE.sub(text, page)


def _commit() -> tuple[str, str]:
    """Коммит сборки: в CI — GITHUB_SHA и номер прогона, локально — HEAD из git."""
    sha = os.environ.get("GITHUB_SHA", "")
    if not sha:
        try:
            sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            sha = ""
    return sha, os.environ.get("GITHUB_RUN_ID", "")


def _footer(as_of: str, engine: str) -> str:
    sha, run = _commit()
    built = (
        f'из коммита <a href="{REPO}/commit/{_esc(sha)}"><code>{_esc(sha[:7])}</code></a>'
        if sha
        else "из рабочей копии"
    )
    ci = f', <a href="{REPO}/actions/runs/{_esc(run)}">прогон CI</a>' if run else ""
    note = (
        "«Мандат» — стенд, а не юридическое заключение. Все правила — черновики, пока юрист банка не утвердит "
        "их подписью. Организации, люди и документы на страницах и в отчётах вымышлены."
    )
    links = f'<a href="{REPO}">Код на GitHub</a> · <a href="#top">Наверх</a>'
    return f"""<footer class="foot">
  <div class="wrap">
    <p>{note}</p>
    <div class="row"><span>Сборка {as_of} {built}{ci}, {engine}</span><span>{links}</span></div>
  </div>
</footer>"""


def _stages(today: date) -> str:
    """Этапы соревнования и где команда на дату сборки (submission/README.md, fintechacademy.ru/task-1)."""
    stages = (
        ("Спринт 1", "до 01.11.2026", date(2026, 11, 1), "сдан"),
        ("Онлайн-питч", "10–15.11.2026", date(2026, 11, 15), "полуфинал"),
        ("Спринт 2", "16–29.11.2026", date(2026, 11, 29), "сдаём до 29.11"),
        ("Спринт 3", "03–08.12.2026", date(2026, 12, 8), "пилот и масштаб"),
        ("Финал", "11.12.2026", date(2026, 12, 11), "живое демо"),
    )
    items, current_set = [], False
    for name, when, end, word in stages:
        css = "done" if today > end else ("now" if not current_set else "")
        if css == "now":
            current_set = True
        state = "пройден" if css == "done" else ("идёт сейчас" if css == "now" else "впереди")
        items.append(
            f'<li class="{css}"><b>{name}</b><span>{when}</span><small>{state}: {word}</small></li>'
            if css
            else f"<li><b>{name}</b><span>{when}</span><small>{state}: {word}</small></li>"
        )
    return "\n".join(items)


def values(evidence: dict) -> dict[str, str]:
    corpus = evidence["synthetic_corpus"]
    confirmed = corpus["confusion"].get("confirmed->confirmed", 0)
    negatives = corpus["cases"] - confirmed
    # Пока build_evidence прогоняет тесты, в evidence.json ещё нет их итога («tests»: null): страница
    # собирается с прочерком, а не падает.
    run = evidence.get("tests") or {}
    tests = run.get("passed")
    rules = evidence["rule_pack"]["rules"]
    as_of = evidence["generated_at"][:10]
    return {
        "rules": str(rules),
        "rules_word": _plural(rules, "правило", "правила", "правил"),
        "tests": _num(tests) if tests is not None else "—",
        "tests_word": _plural(tests, "автотест", "автотеста", "автотестов") if tests is not None else "автотестов",
        "coverage": _num(run["coverage_percent"], 1) if "coverage_percent" in run else "—",
        "cases": _num(corpus["cases"]),
        "cases_word": _plural(corpus["cases"], "проверочный кейс", "проверочных кейса", "проверочных кейсов"),
        "defect_types": str(corpus["defect_types"]),
        "defect_word": _plural(corpus["defect_types"], "вида", "видов", "видов"),
        "negatives": _num(negatives),
        "false_confirmed": str(corpus["false_confirmed"]),
        "upper95": _num(corpus["false_confirmed_rate_upper95"] * 100, 1),
        "refute_recall": _num(corpus["refute_recall"] * 100, 0),
        "auto_share": _num(corpus["coverage"] * 100, 1),
        "chains": _num(evidence["subdelegation_property"]["chains"]),
        "chains_word": _plural(evidence["subdelegation_property"]["chains"], "цепочка", "цепочки", "цепочек"),
        "median_ms": _num(evidence["latency"]["median_ms"], 1),
        "p95_ms": _num(evidence["latency"]["p95_ms"], 1),
        "demo_count": str(len(evidence["demo_cases"]["results"])),
        "engine": html.escape(evidence["engine"]),
        "pack_version": html.escape(evidence["rule_pack"]["version"]),
        "as_of": as_of,
        "as_of_ru": _ru(as_of),
        "repo": REPO,
        "site_url": SITE_URL,
        "csp": CSP,
        "stages": _stages(date.fromisoformat(as_of)),
        "law_rows": _law_rows(),
        **economics(),
    }


def _case_rows(cases: list[dict[str, Any]]) -> str:
    """Список кейсов без скриптов: ссылки на полные карточки сотрудника."""
    rows = []
    for c in cases:
        inner = (
            f'<span class="id">{_esc(c["id"])}</span><b>{_esc(c["title"])}</b>'
            f'<span class="badge {c["css"]}">{_esc(c["label"])}</span>'
        )
        rows.append(
            f'<li data-verdict="{_esc(c["verdict"])}"><a class="case" href="{_esc(c["report"])}">{inner}</a></li>'
        )
    return "\n".join(rows)


def _scrub_rows(scrub: dict[str, Any]) -> str:
    """Ответы ядра по датам таблицей: её видно без скриптов, и она же — табличный двойник шкалы."""
    rows = []
    for s in scrub["segments"]:
        why = "; ".join(f"{w['rule']}: {w['message']}" for w in s["why"]) or "возражений нет"
        rows.append(
            f'<tr><td class="nowrap">{_ru(s["from"])} — {_ru(s["to"])}</td>'
            f'<td><span class="badge {s["css"]}">{_esc(s["label"])}</span></td><td>{_esc(why)}</td></tr>'
        )
    return "\n".join(rows)


def _showcase_rows(show: dict[str, Any]) -> str:
    css = {"ПОДТВЕРЖДЕНО": "ok", "ВЫПУЩЕНО": "info", "ДОПУЩЕНО": "ok", "ОТКАЗ": "no", "ПРИЗНАКИ ЗАХВАТА": "review"}
    items = []
    for i, step in enumerate(show["steps"], 1):
        kind = css[step["outcome"]]
        detail = f"<p>{_esc(step['detail'])}</p>" if step.get("detail") else ""
        body = f'<b>{_esc(step["title"])}</b><span class="badge {kind}">{_esc(step["outcome"])}</span>{detail}'
        items.append(f'<li class="{kind}"><span class="n">{i}</span><div class="body">{body}</div></li>')
    return "\n".join(items)


def build(out: Path, demo_dir: Path | None = None) -> Path:
    evidence = json.loads((ROOT / "evidence.json").read_text(encoding="utf-8"))
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    model = _model()
    base = values(evidence)
    footer = _footer(base["as_of_ru"], base["engine"])
    with tempfile.TemporaryDirectory() as tmp:
        stand = _stand(Path(tmp), demo_dir)
        cases = _cases(evidence, stand["demo"])
        show = _showcase(stand)
        verify = _verify(stand)
        reports = out / "reports"
        reports.mkdir()
        for report in sorted(stand["demo"].glob("*.html")):
            shutil.copy2(report, reports / report.name)
    criteria = _criteria()
    slides = _slides()
    scrub = _scrub()
    page_data: dict[str, Any] = {
        "index": scrub,
        "cases": {"cases": cases, "whatif": _whatif()},
        "showcase": show,
        "verify": verify,
        "economics": _economics(model),
        "pilot": None,
        "law": {"norms": _law(), "today": base["as_of"]},
        "criteria": None,
        "pitch": slides,
    }
    extra = {
        "case_rows": _case_rows(cases),
        "scrub_rows": _scrub_rows(scrub),
        "scrub_amount": _num(scrub["amount"]),
        "showcase_rows": _showcase_rows(show),
        "showcase_root": _esc(show["journal"]["merkle_root"][:16]),
        "showcase_records": str(show["journal"]["records"]),
        "showcase_seconds": _num(show["seconds"], 2),
        "econ_rows": _econ_rows(model),
        "econ_as_of": _esc(model["as_of"]),
        "slide_items": _slide_items(slides),
        "slide_count": str(len(slides["slides"])),
        "pitch_count": str(slides["pitch"]),
        "verify_seq": str(verify["journal"]["seq"]),
        "verify_size": str(verify["journal"]["size"]),
        "verify_root": verify["journal"]["root"],
        **_scale(model),
        **_criteria_html(criteria),
    }
    for name, _title in PAGES:
        mapping = {
            **base,
            **extra,
            "header": _header(name),
            "footer": footer,
            "data": _json_block(page_data[name]) if page_data[name] is not None else "null",
        }
        page = Template((SITE / f"{name}.html").read_text(encoding="utf-8")).substitute(mapping)
        (out / f"{name}.html").write_text(_typo(page), encoding="utf-8")
    page404 = Template((SITE / "404.html").read_text(encoding="utf-8")).substitute(
        {**base, "header": _header(""), "footer": footer, "data": "null"}
    )
    (out / "404.html").write_text(page404, encoding="utf-8")
    for name in STATIC:
        shutil.copy2(SITE / name, out / name)
    shutil.copytree(SITE / "js", out / "js")
    shutil.copytree(SITE / "slides", out / "slides")
    shutil.copytree(SITE / "img", out / "img")
    fonts = out / "assets" / "fonts"
    fonts.mkdir(parents=True)
    for name in FONTS:
        shutil.copy2(SITE / "fonts" / name, fonts / name)
    for name in LICENSES:
        shutil.copy2(ROOT / "submission" / "deck" / "fonts" / name, fonts / name)
    materials = out / "materials"
    materials.mkdir()
    for name, source in MATERIALS.items():
        shutil.copy2(source, materials / name)
    (out / ".nojekyll").write_text("", encoding="utf-8")
    _stamp(out)
    return out / "index.html"


IMPORT = re.compile(r"""(\bfrom\s+|\bimport\s+)(["'])\./([\w-]+\.js)\2""")
ASSET = re.compile(r'((?:href|src)=")((?:/Mandat/)?(?:style\.css|js/[\w-]+\.js))(")')
# Картинки, которые меняются при пересборке под тем же именем: фото и рисунки страниц, значки, превью ссылки.
PICTURE = re.compile(
    r'((?:href|src|content)=")((?:/Mandat/|' + re.escape(SITE_URL) + r")?"
    r'(?:og\.jpg|favicon\.svg|apple-touch-icon\.png|img/[\w.-]+\.(?:webp|png|jpg|svg)))(")'
)
SRCSET = re.compile(r'(srcset=")([^"]+)(")')
CSS_URL = re.compile(r'(url\(")(img/[\w.-]+\.(?:webp|png|jpg|svg))("\))')


def _version(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def _stamp(out: Path) -> None:
    """Версия в адресах стилей, скриптов и сменяемых картинок: ?v= и 8 знаков SHA-256 файла. Браузер со старым
    кэшем не покажет новую страницу со старыми стилями, а мессенджер обновит превью ссылки. Сначала версии получают
    импорты модулей и картинки в стилях, потом сами страницы."""
    scripts = sorted((out / "js").glob("*.js"))
    # Переводы строк — \n до подсчёта версий: на Windows запись в текстовом режиме дала бы \r\n,
    # и версия в импорте разошлась бы с байтами файла.
    for script in scripts:
        script.write_text(script.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    leaf = {p.name: _version(p) for p in scripts}
    for script in scripts:
        text = script.read_text(encoding="utf-8")
        script.write_text(
            IMPORT.sub(lambda m: f"{m.group(1)}{m.group(2)}./{m.group(3)}?v={leaf[m.group(3)]}{m.group(2)}", text),
            encoding="utf-8",
            newline="\n",
        )
    # Картинки в стилях — до версии самого style.css: она считается уже по новым адресам.
    css = out / "style.css"
    styles = css.read_text(encoding="utf-8")
    styles = CSS_URL.sub(lambda m: f"{m.group(1)}{m.group(2)}?v={_version(out / m.group(2))}{m.group(3)}", styles)
    css.write_text(styles, encoding="utf-8", newline="\n")

    def versioned(m: re.Match[str]) -> str:
        target = out / m.group(2).removeprefix(SITE_URL).removeprefix("/Mandat/")
        return f"{m.group(1)}{m.group(2)}?v={_version(target)}{m.group(3)}"

    def versioned_set(m: re.Match[str]) -> str:
        items = []
        for item in m.group(2).split(","):
            url, _, size = item.strip().partition(" ")
            if re.fullmatch(r"img/[\w.-]+\.(?:webp|png|jpg)", url):
                url = f"{url}?v={_version(out / url)}"
            items.append(f"{url} {size}".strip())
        return f"{m.group(1)}{', '.join(items)}{m.group(3)}"

    for page in out.glob("*.html"):
        text = page.read_text(encoding="utf-8")
        text = PICTURE.sub(versioned, ASSET.sub(versioned, text))
        page.write_text(SRCSET.sub(versioned_set, text), encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="_site", help="каталог сайта (по умолчанию _site)")
    parser.add_argument("--demo", help="готовый каталог mandat demo с ключами стенда; без него демо собирается заново")
    parser.add_argument("--scrub", help="только записать ответы ядра по датам для кейса 14 в этот JSON (для колоды)")
    args = parser.parse_args(argv)
    if args.scrub:
        target = Path(args.scrub)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(_scrub(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"ответы по датам: {target}")
        return 0
    index = build(Path(args.out), Path(args.demo) if args.demo else None)
    print(f"сайт: {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
