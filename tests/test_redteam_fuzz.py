"""Фазз из tools/redteam на базе с отметкой об уставе: ни одного ложного «подтверждено».

Без отметок базовые сценарии сами уходят на разбор из-за устава, и фазз ничего не проверяет.
Поэтому база здесь — зелёный сценарий с отметками ``charter_disposition`` и ``subdelegation_forms``
в профиле demo (в strict отметки не принимаются, и подтверждения нет вовсе).

Ожидания стенда исправлены в трёх местах, причины в ``EXPECTATION_FIXES``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "redteam"))

import common  # noqa: E402
import fuzz_mutations as fz  # noqa: E402

from mandat.eval.synth import World  # noqa: E402

_plain_base = common.base


def annotated_base(defect: str, seed: int = 1) -> dict:
    m = _plain_base(defect, seed)
    m["charter_disposition"] = "no_applicable_restriction"
    children = [
        common.xget(common.doc(m, i), "number")
        for i in common.poa_ids(m)
        if common.xget(common.doc(m, i), "parent_number")
    ]
    if children:
        # Форма передоверия (п. 3 ст. 187 ГК РФ) по отметке кейса; без неё цепочки уходят на разбор.
        m["subdelegation_forms"] = {n: "notarized" for n in children}
    if defect == "ok_director":
        # СНИЛС руководителя есть в выписке, иначе СНИЛС заявителя сверить не с чем и путь уходит на разбор.
        record = common.egrul(m)["executives"][0]
        if record.get("inn") == m["requester"].get("inn"):
            record["snils"] = m["requester"]["snils"]
    return m


def _expectation(name: str, expect: str, base_manifest: dict) -> str:
    if name in EXPECTATION_FIXES:
        return "ANY"
    is_director = not common.poa_ids(base_manifest)
    if is_director and (name.startswith("G.registry_") or name == "F.dir_valid_from_after_root_issue"):
        # Путь руководителя без МЧД: реестр МЧД пуст, первоначальной МЧД нет — мутация ничего не меняет.
        return "ANY"
    if name in OPEN_QUESTIONS and common.doc(base_manifest, "poa-child") is not None:
        # Открытый вопрос не проходит молча: до ответа юриста такая МЧД уходит на разбор.
        return "NC"
    if name.startswith(("A.remove.leaf.parent_number", "A.empty.leaf.parent_number", "A.spaces.leaf.parent_number")):
        leaf = common.doc(base_manifest, common.leaf_id(base_manifest))
        if leaf is not None and common.xget(leaf, "parent_number") is None:
            # У единственной МЧД нет номера родителя: мутация ничего не меняет, «подтверждено» верно.
            return "ANY"
    return expect


EXPECTATION_FIXES = {
    # RFC 9562, разд. 4: шестнадцатеричные символы UUID могут быть в любом регистре, значение то же.
    # Номер МЧД — GUID, движок сравнивает его без учёта регистра (identifiers.normalize_poa_number).
    "E.child_parent_number_uppercase": "RFC 9562, разд. 4",
    "G.registry_keys_upper": "RFC 9562, разд. 4",
    # Пробелы по краям ИНН в выписке — артефакт выгрузки, а не другой ИНН: входные строки обрезаются при разборе,
    # как и атрибуты XML МЧД. Неправильно сформированный ИНН после обрезки не сравнивается (comparable_inn).
    "F.exec_inn_whitespace": "нормализация пробелов",
}

# Должность подписанта обязательна для первоначальной МЧД (подписывает руководитель). Для МЧД в порядке
# передоверия подписывает представитель, и обязательность должности по XSD формата 003 не сверена:
# вопрос к юристу и владельцу формата. До ответа такая МЧД уходит на разбор (R-FMT-001), а не подтверждается.
OPEN_QUESTIONS = {f"A.{how}.leaf.signer.position": "должность передоверителя" for how in ("remove", "empty", "spaces")}


def _stand_expectation(name: str, expect: str, base_manifest: dict) -> str:
    return expect


def _plan(base_fn, expectation, seeds=(1, 2, 3)):
    for b in common.GREEN:
        for s in seeds:
            m0 = base_fn(b, s)
            for name, expect, applies, _ in fz.MUTS:
                if applies(b, m0):
                    yield b, s, (name,), expectation(name, expect, m0)
            for combo in fz.COMBOS:
                rows = {n: (e, a) for n, e, a, _ in fz.MUTS if n in combo}
                if len(rows) == len(combo) and all(a(b, m0) for _, a in rows.values()):
                    exps = [expectation(n, rows[n][0], m0) for n in combo]
                    yield b, s, combo, "NC" if "NC" in exps else ("NC-fmt" if "NC-fmt" in exps else "ANY")


def charter_only_base(defect: str, seed: int = 1) -> dict:
    m = _plain_base(defect, seed)
    m["charter_disposition"] = "no_applicable_restriction"
    return m


def _run_fuzz(base_fn, expectation) -> list[tuple]:
    fz.base = base_fn
    worlds = {s: World.make(s) for s in (1, 2, 3)}
    rows = []
    for b, s, names, expect in _plan(base_fn, expectation):
        m = base_fn(b, s)
        # Исключение мутации роняет тест: молча выпавший случай ничего не проверяет.
        for n in names:
            fz.MUTS_BY_NAME[n](m, {"world": worlds[s], "base": b})
        for profile in ("demo", "strict"):
            verdict, _ = common.run(m, profile)
            rows.append((b, s, "+".join(names), expect, profile, verdict))
    return rows


def test_baseline_with_charter_mark_is_confirmed():
    for b in common.GREEN:
        verdict, _ = common.run(annotated_base(b), "demo")
        assert verdict == "confirmed", b


@pytest.fixture(autouse=True, scope="module")
def _demo_env():
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("MANDAT_ENV", raising=False)
        yield


@pytest.fixture(scope="module")
def fuzz_rows():
    return _run_fuzz(annotated_base, _expectation)


def test_fuzz_has_no_false_confirmed(fuzz_rows):
    bad = sorted({(r[2], r[0], r[4]) for r in fuzz_rows if r[5] == "confirmed" and r[3] in ("NC", "NC-fmt")})
    assert len(fuzz_rows) == 8814
    print(*bad, sep="\n")
    assert bad == []


def test_fuzz_has_no_crashes(fuzz_rows):
    assert [r for r in fuzz_rows if r[5].startswith("CRASH")] == []


# База только с отметкой об уставе, ожидания стенда без поправок. «Подтверждено» при ожидании NC здесь
# получают ровно эти случаи, и все они нейтральны:
# - у единственной МЧД нет номера родителя, удалять или портить нечего (базы ok и director_changed_long_ago);
# - ключи реестра в верхнем регистре: GUID сравнивается без учёта регистра (RFC 9562, разд. 4).
# В strict ни один не подтверждается: отметка об уставе там не принимается.
NEUTRAL_UNDER_STAND_EXPECTATIONS = {
    (mutation, base, seed, "demo")
    for mutation in (
        "A.remove.leaf.parent_number",
        "A.empty.leaf.parent_number",
        "A.spaces.leaf.parent_number",
        "G.registry_keys_upper",
    )
    for base in ("ok", "director_changed_long_ago")
    for seed in (1, 2, 3)
}


def test_charter_only_baseline_confirms_only_the_neutral_cases():
    rows = _run_fuzz(charter_only_base, _stand_expectation)
    confirmed_nc = {(r[2], r[0], r[1], r[4]) for r in rows if r[5] == "confirmed" and r[3] in ("NC", "NC-fmt")}
    assert len(rows) == 8814
    assert len(NEUTRAL_UNDER_STAND_EXPECTATIONS) == 24
    assert confirmed_nc == NEUTRAL_UNDER_STAND_EXPECTATIONS


def test_open_questions_are_never_confirmed(fuzz_rows):
    rows = [r for r in fuzz_rows if r[2] in OPEN_QUESTIONS and r[3] == "NC"]
    assert len({(r[0], r[1], r[2]) for r in rows}) == 18
    assert [r for r in rows if r[5] == "confirmed"] == []
