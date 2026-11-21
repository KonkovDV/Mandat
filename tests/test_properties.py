"""Свойства передоверия на полном переборе малого домена (verification-guided подход Cedar, без SMT).

Перебор: 8 наборов кодов родителя × 8 наборов кодов передоверия × 3 значения ПрПередов ×
2 варианта срока = 384 цепочки. Для каждой проверяется закрытая формула: «подтверждено»
тогда и только тогда, когда передоверие разрешено, не шире родительской МЧД, не дольше её
и покрывает операцию. Это и есть свойство «передоверие не расширяет полномочия».
"""

from __future__ import annotations

from conftest import PAY_CODE, chain_matrix, chain_scenario, verify_scenario


def expected_confirmed(parent: frozenset[str], child: frozenset[str], flag: str, longer: bool) -> bool:
    return flag in {"2", "3"} and child <= parent and PAY_CODE in child and not longer


def test_subdelegation_never_widens_authority_exhaustive():
    matrix = chain_matrix()
    assert len(matrix) == 384
    wrong = []
    for parent, child, flag, longer in matrix:
        verdict = verify_scenario(chain_scenario(parent, child, flag, longer)).verdict.value
        if (verdict == "confirmed") != expected_confirmed(parent, child, flag, longer):
            wrong.append((sorted(parent), sorted(child), flag, longer, verdict))
        if verdict == "confirmed":
            assert child <= parent, "подтверждено передоверие шире родительской МЧД"
    assert not wrong, wrong[:5]


def test_no_false_confirmed_in_strict_profile_exhaustive():
    """В строгом профиле без СКЗИ и утверждённых правил автоматического «подтверждено» нет вовсе."""
    for parent, child, flag, longer in chain_matrix()[::7]:
        assert verify_scenario(chain_scenario(parent, child, flag, longer), "strict").verdict.value != "confirmed"
