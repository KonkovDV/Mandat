"""Каноническая запись по RFC 8785: примеры из текста RFC (https://www.rfc-editor.org/rfc/rfc8785)."""

from __future__ import annotations

import json
import struct

import pytest

from mandat.audit.jcs import JcsError, canonicalize
from mandat.data import canonical_json

SORTING = {
    "\u20ac": "Euro Sign",
    "\r": "Carriage Return",
    "\ufb33": "Hebrew Letter Dalet With Dagesh",
    "1": "One",
    "\U0001f600": "Emoji: Grinning Face",
    "\u0080": "Control",
    "\u00f6": "Latin Small Letter O With Diaeresis",
}


def test_sorting_by_utf16_code_units():
    """Разд. 3.2.3: порядок значений после сортировки ключей."""
    order = list(json.loads(canonicalize(SORTING)).values())
    assert order == [
        "Carriage Return",
        "One",
        "Control",
        "Latin Small Letter O With Diaeresis",
        "Euro Sign",
        "Emoji: Grinning Face",
        "Hebrew Letter Dalet With Dagesh",
    ]


def test_sort_keys_is_not_jcs_outside_bmp():
    # Сортировка по кодовым точкам ставит U+1F600 после U+FB33: это не RFC 8785.
    assert canonical_json(SORTING) != canonicalize(SORTING)


def test_primitive_sample():
    """Разд. 3.2.2 и 3.2.3: пример с числами, строкой и литералами."""
    parsed = json.loads(
        '{"numbers": [333333333.33333329, 1E30, 4.50, 2e-3, 0.000000000000000000000000001],'
        ' "string": "\\u20ac$\\u000F\\u000aA\'\\u0042\\u0022\\u005c\\\\\\"\\/",'
        ' "literals": [null, true, false]}'
    )
    expected = (
        '{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
    )
    assert canonicalize(parsed) == expected


@pytest.mark.parametrize(
    ("ieee", "text"),
    [
        ("0000000000000000", "0"),
        ("8000000000000000", "0"),
        ("0000000000000001", "5e-324"),
        ("8000000000000001", "-5e-324"),
        ("7fefffffffffffff", "1.7976931348623157e+308"),
        ("ffefffffffffffff", "-1.7976931348623157e+308"),
        ("4340000000000000", "9007199254740992"),
        ("c340000000000000", "-9007199254740992"),
        ("4430000000000000", "295147905179352830000"),
        ("44b52d02c7e14af5", "9.999999999999997e+22"),
        ("44b52d02c7e14af6", "1e+23"),
        ("44b52d02c7e14af7", "1.0000000000000001e+23"),
        ("444b1ae4d6e2ef4e", "999999999999999700000"),
        ("444b1ae4d6e2ef4f", "999999999999999900000"),
        ("444b1ae4d6e2ef50", "1e+21"),
        ("3eb0c6f7a0b5ed8c", "9.999999999999997e-7"),
        ("3eb0c6f7a0b5ed8d", "0.000001"),
        ("41b3de4355555553", "333333333.3333332"),
        ("41b3de4355555554", "333333333.33333325"),
        ("41b3de4355555555", "333333333.3333333"),
        ("41b3de4355555556", "333333333.3333334"),
        ("41b3de4355555557", "333333333.33333343"),
        ("becbf647612f3696", "-0.0000033333333333333333"),
        ("43143ff3c1cb0959", "1424953923781206.2"),
    ],
)
def test_number_table(ieee, text):
    """Разд. Appendix B, таблица 1."""
    (value,) = struct.unpack(">d", bytes.fromhex(ieee))
    assert canonicalize(value) == text


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), -float("inf"), 2**53, -(2**53), 2**53 + 1, "\ud800", {1: "x"}, {"a"}]
)
def test_rejected_values(bad):
    with pytest.raises(JcsError):
        canonicalize(bad)


def test_integer_bound_is_max_safe_integer():
    """RFC 7493 разд. 2.2: точны целые из [−(2**53) + 1, 2**53 − 1]. Число двойной точности 2**53 остаётся числом."""
    assert canonicalize(2**53 - 1) == "9007199254740991"
    assert canonicalize(-(2**53) + 1) == "-9007199254740991"
    assert canonicalize(float(2**53)) == "9007199254740992"


def test_ascii_documents_keep_their_old_bytes():
    # Для ключей ASCII, строк и малых целых старая запись совпадает с JCS: выданные удостоверения проверяются.
    doc = {"b": [1, -2, "текст", None, True], "a": {"z": "\u0001", "y": 'кавычка "'}}
    assert canonical_json(doc) == canonicalize(doc)
