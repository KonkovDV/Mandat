"""Дифференциальная сверка JCS и base58btc «Мандата» с независимыми реализациями (аудит 27.10).

Эксперимент, а не тест пакета: эталоны ``rfc8785`` и ``base58`` в lock-файлах не закреплены.

    python -m venv .venv-jcs && .venv-jcs/bin/pip install -e . rfc8785==0.1.4 base58==2.1.1 hypothesis
    .venv-jcs/bin/python tools/experiments/jcs_differential/run.py
"""

from __future__ import annotations

import base58
import rfc8785
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mandat.audit.attestation import b58decode, b58encode
from mandat.audit.jcs import MAX_SAFE, JcsError, canonicalize

FINITE = st.floats(allow_nan=False, allow_infinity=False)
SAFE_INTS = st.integers(min_value=-MAX_SAFE, max_value=MAX_SAFE)
TEXT = st.text(st.characters(blacklist_categories=("Cs",)), max_size=20)
VALUES = st.recursive(
    st.none() | st.booleans() | SAFE_INTS | FINITE | TEXT,
    lambda children: st.lists(children, max_size=4) | st.dictionaries(TEXT, children, max_size=4),
    max_leaves=25,
)


@settings(max_examples=20_000, deadline=None, suppress_health_check=list(HealthCheck))
@given(VALUES)
def documents_match(value: object) -> None:
    assert canonicalize(value).encode("utf-8") == rfc8785.dumps(value), value


@settings(max_examples=20_000, deadline=None)
@given(FINITE)
def numbers_match(value: float) -> None:
    assert canonicalize(value).encode("utf-8") == rfc8785.dumps(value), value


@settings(max_examples=5_000, deadline=None)
@given(st.binary(max_size=64))
def base58_matches(data: bytes) -> None:
    assert b58encode(data) == base58.b58encode(data).decode("ascii")
    assert b58decode(b58encode(data)) == data


def edges_match() -> None:
    """Границы целых (RFC 7493, разд. 2.2): обе реализации отвергают 2**53, принимают 2**53 − 1."""
    for value in (MAX_SAFE, -MAX_SAFE, -0.0, 5e-324, 1e21, 1e-7, 0.000001):
        assert canonicalize(value).encode("utf-8") == rfc8785.dumps(value), value
    for value in (MAX_SAFE + 1, -(MAX_SAFE + 1)):
        try:
            canonicalize(value)
        except JcsError:
            pass
        else:
            raise AssertionError(value)
        try:
            rfc8785.dumps(value)
        except rfc8785.IntegerDomainError:
            pass
        else:
            raise AssertionError(value)


if __name__ == "__main__":
    numbers_match()
    documents_match()
    base58_matches()
    edges_match()
    print("JCS: 40 000 случайных документов и чисел совпали с rfc8785; base58btc: 5000 строк совпали с base58")
