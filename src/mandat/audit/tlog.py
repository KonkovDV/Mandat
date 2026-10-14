"""Клиент прозрачного журнала: checkpoint, доказательства согласованности и тайлы.

* Checkpoint — подписанная заметка (C2SP signed-note, tlog-checkpoint): origin, размер, корень,
  затем строки подписей ``— <имя> <base64(id ключа || подпись)>``. Чужие подписи (свидетели) пропускаются.
  ECDSA: id = SHA-256(SPKI DER)[:4], подпись ECDSA-SHA256. Ed25519: id = SHA-256(имя || 0x0A || 0x01 ||
  ключ)[:4]. Так подписывают Rekor v1 (ECDSA) и Rekor v2 / rekor-tiles (Ed25519).
* Доказательство согласованности (RFC 6962, разд. 2.1.2; проверка — RFC 9162, разд. 2.1.4.2): дерево
  размера n2 продолжает дерево размера n1, ничего не переписано. ``CheckpointState`` помнит последний
  проверенный checkpoint каждого журнала и требует доказательство при каждом новом; расхождение — признак
  раздвоения журнала (split view).
* Тайлы (C2SP tlog-tiles): Rekor v2 не выдаёт доказательств по API, их считают по ``/tile/<L>/<N>``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from mandat.audit import merkle

RangeHash = Callable[[int, int], bytes]
Prover = Callable[[int, int], list[bytes]]


# --- checkpoint -------------------------------------------------------------------------------------


def _key_id_and_verify(key_pem: bytes, name: str) -> tuple[bytes, Callable[[bytes, bytes], None]]:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519

    key = serialization.load_pem_public_key(key_pem)
    if isinstance(key, ec.EllipticCurvePublicKey):
        spki = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        ec_key = key

        def verify_ec(sig: bytes, data: bytes) -> None:
            ec_key.verify(sig, data, ec.ECDSA(hashes.SHA256()))

        return hashlib.sha256(spki).digest()[:4], verify_ec
    if isinstance(key, ed25519.Ed25519PublicKey):
        raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        ed_key = key

        def verify_ed(sig: bytes, data: bytes) -> None:
            ed_key.verify(sig, data)

        return hashlib.sha256(name.encode() + b"\n\x01" + raw).digest()[:4], verify_ed
    raise ValueError("ключ журнала — ECDSA или Ed25519")


def verify_checkpoint(text: str, log_key_pem: bytes, origin: str | None = None) -> tuple[str, int, bytes]:
    """Проверить checkpoint ключом журнала. Возвращает (origin, размер, корень).

    Имя ключа в строке подписи — origin целиком (C2SP, Rekor v2) или его первое слово (Rekor v1:
    ``rekor.sigstore.dev - <treeID>``). ``origin`` задан — первая строка обязана с ним совпасть.
    """
    body, sep, signatures = text.partition("\n\n")
    lines = body.split("\n")
    if not sep or len(lines) < 3:
        raise ValueError("checkpoint не в формате signed note")
    first, size_text = lines[0], lines[1]
    if not size_text.isdigit() or (size_text != "0" and size_text.startswith("0")):
        raise ValueError("размер дерева в checkpoint не десятичное число")
    root = base64.b64decode(lines[2], validate=True)
    if len(root) != 32:
        raise ValueError("корень в checkpoint не SHA-256")
    if origin is not None and first != origin:
        raise ValueError(f"checkpoint другого журнала: {first!r}")
    names = {first, first.split(" ", 1)[0]}
    keys = {name: _key_id_and_verify(log_key_pem, name) for name in names}
    for line in signatures.splitlines():
        if not line.startswith("\u2014 "):
            continue
        signer, _, encoded = line[2:].rpartition(" ")
        if signer not in keys:
            continue
        raw = base64.b64decode(encoded, validate=True)
        key_id, verify = keys[signer]
        if raw[:4] != key_id:
            continue
        verify(raw[4:], (body + "\n").encode("utf-8"))
        return first, int(size_text), root
    raise ValueError("в checkpoint нет подписи заданного ключа журнала")


# --- доказательства по RFC 6962 над произвольным источником хешей поддеревьев ------------------------


def _pow2(n: int) -> bool:
    return n > 0 and n & (n - 1) == 0


def range_hash(a: int, b: int, node_at: Callable[[int, int], bytes]) -> bytes:
    """MTH(D[a:b]) для поддерева из разбиения RFC 6962; ``node_at(h, i)`` — полный узел высоты h."""
    n = b - a
    if _pow2(n) and a % n == 0:
        return node_at(n.bit_length() - 1, a >> (n.bit_length() - 1))
    k = merkle._split(n)
    return merkle.node_hash(range_hash(a, a + k, node_at), range_hash(a + k, b, node_at))


def inclusion_path(m: int, a: int, b: int, h: RangeHash) -> list[bytes]:
    """PATH(m, D[a:b]), RFC 6962, разд. 2.1.1."""
    n = b - a
    if n == 1:
        return []
    k = merkle._split(n)
    if m < k:
        return [*inclusion_path(m, a, a + k, h), h(a + k, b)]
    return [*inclusion_path(m - k, a + k, b, h), h(a, a + k)]


def _subproof(m: int, a: int, b: int, complete: bool, h: RangeHash) -> list[bytes]:
    n = b - a
    if m == n:
        return [] if complete else [h(a, b)]
    k = merkle._split(n)
    if m <= k:
        return [*_subproof(m, a, a + k, complete, h), h(a + k, b)]
    return [*_subproof(m - k, a + k, b, False, h), h(a, a + k)]


def consistency_proof(size1: int, size2: int, h: RangeHash) -> list[bytes]:
    """PROOF(m, D[n]), RFC 6962, разд. 2.1.2."""
    if not 0 < size1 <= size2:
        raise ValueError("размеры деревьев для доказательства согласованности")
    return [] if size1 == size2 else _subproof(size1, 0, size2, True, h)


def leaves_hasher(leaves: Sequence[bytes]) -> RangeHash:
    return lambda a, b: merkle.root(leaves[a:b])


def verify_consistency(size1: int, size2: int, root1: bytes, root2: bytes, proof: Sequence[bytes]) -> bool:
    """Проверка доказательства согласованности, RFC 9162, разд. 2.1.4.2."""
    if size1 < 0 or size2 < size1:
        return False
    if size1 == size2:
        return not proof and root1 == root2
    if size1 == 0:
        return not proof
    path = list(proof)
    if _pow2(size1):
        path = [root1, *path]
    if not path:
        return False
    fn, sn = size1 - 1, size2 - 1
    while fn & 1:
        fn >>= 1
        sn >>= 1
    fr = sr = path[0]
    for c in path[1:]:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            fr, sr = merkle.node_hash(c, fr), merkle.node_hash(c, sr)
            if not fn & 1:
                while not fn & 1 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            sr = merkle.node_hash(sr, c)
        fn >>= 1
        sn >>= 1
    return sn == 0 and fr == root1 and sr == root2


# --- тайлы (C2SP tlog-tiles) -------------------------------------------------------------------------

TILE_HEIGHT = 8
TILE_WIDTH = 1 << TILE_HEIGHT


def tile_path(level: int, index: int, width: int = TILE_WIDTH) -> str:
    """``tile/<L>/<N>[.p/<W>]``: N тройками цифр, все элементы, кроме последнего, с ``x``."""
    digits = f"{index:d}"
    digits = "0" * (-len(digits) % 3) + digits
    groups = [digits[i : i + 3] for i in range(0, len(digits), 3)]
    encoded = "/".join([*("x" + g for g in groups[:-1]), groups[-1]])
    suffix = "" if width == TILE_WIDTH else f".p/{width}"
    return f"tile/{level}/{encoded}{suffix}"


class TileReader:
    """Хеши поддеревьев журнала размера ``size`` из тайлов; тайлы кешируются на время одного чтения."""

    def __init__(self, fetch: Callable[[str], bytes], size: int) -> None:
        self.fetch, self.size = fetch, size
        self._tiles: dict[tuple[int, int], bytes] = {}

    def _tile(self, level: int, index: int) -> bytes:
        if (level, index) not in self._tiles:
            count = self.size >> (TILE_HEIGHT * level)  # полных узлов на высоте 8·level
            width = min(TILE_WIDTH, count - index * TILE_WIDTH)
            if width <= 0:
                raise ValueError("тайл за пределами дерева")
            try:
                data = self.fetch(tile_path(level, index, width))
            except Exception:
                if width == TILE_WIDTH:
                    raise
                # Журнал вправе удалить частичный тайл, когда готов полный: его префикс — те же хеши.
                data = self.fetch(tile_path(level, index))
            if len(data) < 32 * width:
                raise ValueError("тайл короче заявленной ширины")
            self._tiles[(level, index)] = data[: 32 * width]
        return self._tiles[(level, index)]

    def node_at(self, height: int, index: int) -> bytes:
        level, rest = divmod(height, TILE_HEIGHT)
        first, count = index << rest, 1 << rest
        tile = self._tile(level, first // TILE_WIDTH)
        offset = first % TILE_WIDTH
        hashes = [tile[32 * i : 32 * i + 32] for i in range(offset, offset + count)]
        if len(hashes) != count or any(len(x) != 32 for x in hashes):
            raise ValueError("в тайле нет нужного узла")
        return merkle.root(hashes)

    def hasher(self) -> RangeHash:
        return lambda a, b: range_hash(a, b, self.node_at)


def tile_prover(fetch: Callable[[str], bytes]) -> Prover:
    """Доказательство согласованности (size1 → size2) по тайлам дерева размера size2."""

    def prove(size1: int, size2: int) -> list[bytes]:
        return consistency_proof(size1, size2, TileReader(fetch, size2).hasher())

    return prove


# --- состояние: последний проверенный checkpoint каждого журнала ---------------------------------------


class CheckpointState:
    """Последний проверенный (origin → размер, корень). Файл — JSON, запись через os.replace."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._memory: dict[str, dict[str, Any]] = {}

    def _load(self) -> dict[str, dict[str, Any]]:
        if self.path is None:
            return dict(self._memory)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        if self.path is None:
            self._memory = data
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, origin: str) -> tuple[int, bytes] | None:
        row = self._load().get(origin)
        return None if row is None else (int(row["size"]), bytes.fromhex(row["root"]))

    def observe(self, origin: str, size: int, root: bytes, prove: Prover) -> str | None:
        """Принять проверенный checkpoint. Новый больше — нужен proof от старого; меньше — от нового к старому."""
        known = self.get(origin)
        if known is not None:
            old_size, old_root = known
            if size == old_size:
                if root != old_root:
                    return f"журнал {origin}: два разных корня при размере {size} (раздвоение)"
                return None
            small, big = (old_size, old_root), (size, root)
            if size < old_size:
                small, big = big, small
            try:
                proof = prove(small[0], big[0])
            except Exception as exc:  # noqa: BLE001 - недоступное доказательство — отказ, а не исключение
                return f"журнал {origin}: нет доказательства согласованности ({type(exc).__name__})"
            if not verify_consistency(small[0], big[0], small[1], big[1], proof):
                return f"журнал {origin}: checkpoint {size} не согласован с {old_size} (RFC 9162, 2.1.4)"
            if size < old_size:
                return None
        data = self._load()
        data[origin] = {"size": size, "root": root.hex()}
        self._save(data)
        return None


def read_url(open_: Callable[[urllib.request.Request], Any], url: str, accept: str = "*/*") -> bytes:
    request = urllib.request.Request(url, headers={"accept": accept})  # noqa: S310 - адрес задаёт администратор
    with open_(request) as response:
        return bytes(response.read())
