"""Контрольные суммы российских идентификаторов. Без сетевых вызовов.

ИНН: алгоритм контрольного числа ФНС (весовые коэффициенты, mod 11 mod 10).
ОГРН: первые 12 цифр mod 11 mod 10; ОГРНИП: первые 14 цифр mod 13 mod 10.
СНИЛС: сумма цифр с весами 9..1; проверяется для номеров больше 001-001-998.
КПП: только структура (4 цифры, 2 символа причины, 3 цифры), контрольного числа нет.

Генераторы ``make_*`` нужны синтетическому корпусу: они достраивают контрольный разряд.
"""

from __future__ import annotations

import re

_INN10 = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_KPP = re.compile(r"^\d{4}[\dA-Z]{2}\d{3}$")


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _weighted(digits: str, weights: tuple[int, ...]) -> int:
    return sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11 % 10


def inn_valid(value: str | None) -> bool:
    if not value or not value.isdigit():
        return False
    if len(value) == 10:
        return int(value[9]) == _weighted(value[:9], _INN10)
    if len(value) == 12:
        return int(value[10]) == _weighted(value[:10], _INN11) and int(value[11]) == _weighted(value[:11], _INN12)
    return False


def ogrn_valid(value: str | None) -> bool:
    if not value or not value.isdigit():
        return False
    if len(value) == 13:
        return int(value[:12]) % 11 % 10 == int(value[12])
    if len(value) == 15:
        return int(value[:14]) % 13 % 10 == int(value[14])
    return False


def _snils_control(nine: str) -> int:
    total = sum(int(d) * (9 - i) for i, d in enumerate(nine))
    if total < 100:
        return total
    if total in (100, 101):
        return 0
    rest = total % 101
    return 0 if rest == 100 else rest


def snils_valid(value: str | None) -> bool:
    digits = _digits(value)
    if len(digits) != 11:
        return False
    if int(digits[:9]) <= 1001998:
        return True
    return _snils_control(digits[:9]) == int(digits[9:])


def kpp_valid(value: str | None) -> bool:
    return bool(value) and bool(_KPP.match(value or ""))


def normalize_snils(value: str | None) -> str:
    return _digits(value)


def make_inn10(prefix9: str) -> str:
    return prefix9 + str(_weighted(prefix9, _INN10))


def make_inn12(prefix10: str) -> str:
    n11 = str(_weighted(prefix10, _INN11))
    n12 = str(_weighted(prefix10 + n11, _INN12))
    return prefix10 + n11 + n12


def make_ogrn(prefix12: str) -> str:
    return prefix12 + str(int(prefix12) % 11 % 10)


def make_snils(prefix9: str) -> str:
    control = _snils_control(prefix9)
    return f"{prefix9[:3]}-{prefix9[3:6]}-{prefix9[6:9]} {control:02d}"
