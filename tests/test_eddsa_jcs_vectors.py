"""Векторы eddsa-jcs-2022 и крайние случаи Ed25519.

Векторы — из w3c/vc-di-eddsa, коммит 13e70705, каталог ``tests/vectors/eddsa-jcs-2022``.
Крайние случаи — проверки, которые спецификация (раздел Security Properties of Ed25519
Implementations) просит сверить с RFC 8032 и с точками малого порядка.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from mandat.audit import attestation
from mandat.audit.jcs import canonicalize
from mandat.audit.signing import as_signer, checked_sign

VECTORS = Path(__file__).parent / "vectors" / "eddsa-jcs-2022"
# Порядок скаляра Ed25519. Подпись с S >= L неканоническая: RFC 8032 её отвергает.
_SCALAR_L = 2**252 + 27742317777372353535851937790883648493
# Восемь точек порядка, делящего кофактор 8, в сжатой записи. На них слабая проверка
# принимает чужую подпись.
_SMALL_ORDER = (
    "0100000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000080",
    "0100000000000000000000000000000000000000000000000000000000000080",
    "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",
    "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc85",
    "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a",
    "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac03fa",
    "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
)


def _text(name: str) -> str:
    return (VECTORS / name).read_text(encoding="utf-8").strip()


def _json(name: str) -> dict:
    return json.loads(_text(name))


def _seed() -> bytes:
    raw = attestation.b58decode(_json("keyPair.json")["privateKeyMultibase"][1:])
    assert raw.startswith(b"\x80\x26") and len(raw) == 34  # multicodec ed25519-priv
    return raw[2:]


def _public() -> Ed25519PublicKey:
    return attestation.public_key_from_multibase(_json("keyPair.json")["publicKeyMultibase"])


def test_canonical_form_and_hash_match_the_w3c_vector() -> None:
    document = _json("unsigned.json")
    config = _json("proofConfigJCS.json")
    assert canonicalize(document) == _text("canonDocJCS.txt")
    assert canonicalize(config) == _text("proofCanonJCS.txt")
    assert attestation._hash_data(document, config).hex() == _text("combinedHashJCS.txt")


def test_signature_matches_the_w3c_proof_value() -> None:
    key = Ed25519PrivateKey.from_private_bytes(_seed())
    signed = checked_sign(as_signer(key), attestation._hash_data(_json("unsigned.json"), _json("proofConfigJCS.json")))
    assert "z" + attestation.b58encode(signed) == _text("sigBTC58JCS.txt")
    assert signed.hex() == _text("sigHexJCS.txt")


def test_signed_vector_verifies_and_one_changed_byte_fails() -> None:
    signed = _json("signedJCS.json")
    public = _public()
    assert attestation.verify_signature(signed, public)
    changed = json.loads(json.dumps(signed))
    changed["credentialSubject"]["alumniOf"] = "The School of Examplez"
    assert not attestation.verify_signature(changed, public)
    proof = dict(signed["proof"])
    raw = bytearray(attestation.b58decode(proof.pop("proofValue")[1:]))
    raw[0] ^= 0x01
    signed["proof"]["proofValue"] = "z" + attestation.b58encode(bytes(raw))
    assert not attestation.verify_signature(signed, public)


def test_noncanonical_scalar_and_small_order_points_are_rejected() -> None:
    digest = attestation._hash_data(_json("unsigned.json"), _json("proofConfigJCS.json"))
    signature = attestation.b58decode(_text("sigBTC58JCS.txt")[1:])
    public = _public()
    scalar = int.from_bytes(signature[32:], "little")
    malleable = signature[:32] + (scalar + _SCALAR_L).to_bytes(32, "little")
    with pytest.raises(InvalidSignature):
        public.verify(malleable, digest)
    for point in _SMALL_ORDER:
        low = Ed25519PublicKey.from_public_bytes(bytes.fromhex(point))
        with pytest.raises(InvalidSignature):
            low.verify(signature, digest)
    # y = p неканоничен (сравним с 0 по модулю поля). Объект ключа создаётся, подпись не принимается.
    field = (2**255 - 19).to_bytes(32, "little")
    assert Ed25519PublicKey.from_public_bytes(field) is not None
    with pytest.raises(InvalidSignature):
        Ed25519PublicKey.from_public_bytes(field).verify(signature, digest)
