"""Демонстрационные кейсы: генерируются из синтетики и лежат в examples/cases.

Состав подобран под питч: зелёный кейс, руководитель без доверенности,
передоверие, сломанное передоверие, свежая и давняя смена руководителя,
текстовое полномочие, недоступный реестр, каскад отмены, срок по закону.
Тест сверяет, что файлы в репозитории совпадают с генератором.
"""

from __future__ import annotations

import json
from pathlib import Path

from mandat.eval.synth import build

DEMO = {
    "01_ok": ("ok", "Новый бухгалтер: МЧД действует, полномочие покрывает подписание платёжек"),
    "02_director_without_poa": ("ok_director", "Руководитель сам подключается к интернет-банку"),
    "03_subdelegation_ok": ("ok_subdelegation", "Передоверие в пределах полномочий и срока"),
    "04_subdelegation_forbidden": ("subdelegation_forbidden", "Передоверие при запрете в родительской МЧД"),
    "05_director_changed": ("director_changed", "Руководитель сменился после выдачи МЧД"),
    "06_text_authority": ("text_authority", "Полномочие записано только текстом"),
    "07_revoked": ("revoked", "МЧД отменена в реестре"),
    "08_registry_unavailable": ("registry_unavailable", "Реестр МЧД недоступен"),
    "09_charter_limit": ("charter_limit", "Сумма выше порога одобрения по уставу"),
    "10_old_director_poa": (
        "director_changed_long_ago",
        "МЧД прежнего руководителя не отменена: действует, смена руководителя — только сигнал",
    ),
    "11_parent_revoked": ("parent_revoked", "Основная МЧД отменена — передоверие теряет силу"),
    "12_no_term": ("no_term", "Срок в МЧД не указан — по закону год со дня совершения"),
}
SEED = 2026


def write_examples(target: Path) -> list[Path]:
    written = []
    for folder, (defect, title) in DEMO.items():
        scenario = build(defect, seed=SEED)
        case_dir = target / folder
        case_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            **scenario.manifest,
            "case_id": folder,
            "title": title,
            "expected_verdict": scenario.expected,
        }
        (case_dir / "case.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for name, raw in scenario.files.items():
            (case_dir / name).write_bytes(raw)
        written.append(case_dir)
    events = target.parent / "events"
    events.mkdir(parents=True, exist_ok=True)
    for name, payload in (("takeover.json", takeover_events()), ("poa_revoked.json", revocation_events())):
        path = events / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written


def revocation_events() -> dict:
    """Отмена основной МЧД: права по цепочке передоверия приостанавливаются (п. 3 ст. 188 ГК РФ)."""
    chain = build("ok_subdelegation", seed=SEED)
    org_inn = chain.manifest["organization"]["inn"]
    root_number, child_number = list(chain.manifest["registry"])
    return {
        "title": "Отмена основной МЧД обрывает цепочку передоверия",
        "rights": [
            {
                "right_id": "R-003",
                "org_inn": org_inn,
                "representative_inn": chain.manifest["requester"]["inn"],
                "operation": "PAYMENT_SIGN",
                "poa_numbers": [root_number, child_number],
                "eio_inn": None,
            }
        ],
        "events": [
            {
                "kind": "POA_REVOKED",
                "org_inn": org_inn,
                "at": "2026-10-22T10:00:00+03:00",
                "payload": {"number": root_number},
            }
        ],
    }


def takeover_events() -> dict:
    """Сценарий «попытка захвата»: смена руководителя, новая МЧД и смена реквизитов за три часа."""
    ok = build("ok", seed=SEED)
    org_inn = ok.manifest["organization"]["inn"]
    poa_number = next(iter(ok.manifest["registry"]))
    director = build("ok_director", seed=SEED).manifest["requester"]["inn"]
    changed = json.loads(build("director_changed", seed=SEED).files["egrul.json"])["executives"][-1]["inn"]
    return {
        "title": "Признаки захвата компании через подставного руководителя",
        "rights": [
            {
                "right_id": "R-001",
                "org_inn": org_inn,
                "representative_inn": ok.manifest["requester"]["inn"],
                "operation": "PAYMENT_SIGN",
                "poa_numbers": [poa_number],
                "eio_inn": director,
            },
            {
                "right_id": "R-002",
                "org_inn": org_inn,
                "representative_inn": director,
                "operation": "USER_ADMIN",
                "poa_numbers": [],
                "eio_inn": director,
            },
        ],
        "events": [
            {
                "kind": "EIO_CHANGED",
                "org_inn": org_inn,
                "at": "2026-10-21T09:10:00+03:00",
                "payload": {"grn": "2267700999001", "new_eio_inn": changed},
            },
            {
                "kind": "NEW_POA",
                "org_inn": org_inn,
                "at": "2026-10-21T11:40:00+03:00",
                "payload": {"number": "11111111-2222-3333-4444-555555555555"},
            },
            {
                "kind": "REQUISITES_CHANGE_REQUEST",
                "org_inn": org_inn,
                "at": "2026-10-21T12:05:00+03:00",
                "payload": {"field": "контактный телефон"},
            },
        ],
    }
