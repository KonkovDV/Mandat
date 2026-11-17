"""Дерево Меркла над журналом решений, как в прозрачных журналах (RFC 6962 / RFC 9162).

Хеш-цепочка доказывает, что журнал не правили, но чтобы показать аудитору одну
запись, приходится отдавать весь журнал. Дерево Меркла даёт короткое доказательство
включения: запись, её номер и log2(n) хешей. Корень дерева можно публиковать во
внешнем якоре (система банка, бумажный реестр) — так делают Certificate Transparency,
Trillian/Tessera и Sigstore Rekor. Хеши листьев и узлов разделены префиксами 0x00 и 0x01.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from mandat.data import canonical_json


def leaf_hash(data: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def _split(n: int) -> int:
    """Наибольшая степень двойки, строго меньшая n."""
    k = 1
    while k << 1 < n:
        k <<= 1
    return k


def root(leaves: Sequence[bytes]) -> bytes:
    """MTH из RFC 6962 по хешам листьев."""
    n = len(leaves)
    if n == 0:
        return hashlib.sha256(b"").digest()
    if n == 1:
        return leaves[0]
    k = _split(n)
    return node_hash(root(leaves[:k]), root(leaves[k:]))


def inclusion_proof(index: int, leaves: Sequence[bytes]) -> list[bytes]:
    """PATH(m, D[n]) из RFC 6962, раздел 2.1.1."""
    n = len(leaves)
    if not 0 <= index < n:
        raise IndexError("номер записи вне журнала")
    if n == 1:
        return []
    k = _split(n)
    if index < k:
        return [*inclusion_proof(index, leaves[:k]), root(leaves[k:])]
    return [*inclusion_proof(index - k, leaves[k:]), root(leaves[:k])]


def verify_inclusion(leaf: bytes, index: int, size: int, proof: Sequence[bytes], expected_root: bytes) -> bool:
    """Проверка доказательства включения по алгоритму RFC 9162, раздел 2.1.3.2."""
    if not 0 <= index < size:
        return False
    fn, sn, r = index, size - 1, leaf
    for p in proof:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            r = node_hash(p, r)
            if not fn & 1:
                while fn & 1 == 0 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            r = node_hash(r, p)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == expected_root


def journal_leaves(records: Sequence[dict[str, Any]]) -> list[bytes]:
    """Лист — каноническая запись журнала целиком, вместе с её хешем цепочки и HMAC."""
    return [leaf_hash(canonical_json(record).encode("utf-8")) for record in records]
