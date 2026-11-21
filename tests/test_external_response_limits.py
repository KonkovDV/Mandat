"""Ответы TSA, Rekor, тайлов журнала и Vault читаются с пределом размера.

Ответ read-back вебхука и OCSP/CRL уже читались с пределом, а ответы метки времени (RFC 3161), Rekor v1 и v2,
тайлы и checkpoint прозрачного журнала и Vault Transit — целиком в память. Адреса задаёт администратор, но
подменённый или сломанный узел на этом стыке (прокси, ошибка балансировщика, взломанный сервис) отдавал
ответ любого размера, и процесс журнала или подписи падал по памяти. Теперь сверх предела — ``OSError``,
как у OCSP: якорь не подтверждён, ключ не загружен, но процесс жив.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from mandat.audit import anchor, signing, tlog
from mandat.audit.bounded import MAX_EXTERNAL_RESPONSE, read_bounded

STMT = {"segment": 1, "root": "a" * 64, "records": 3}


class Huge:
    """Ответ без конца: читает столько, сколько попросили, а без предела — больше допустимого."""

    status = 200
    headers: dict[str, str] = {}
    asked: list[int] = []

    def __enter__(self) -> Huge:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self, n: int = -1) -> bytes:
        Huge.asked.append(n)
        size = MAX_EXTERNAL_RESPONSE + 1 if n is None or n < 0 else n
        return b"0" * size


def _huge(*_args: object) -> Huge:
    return Huge()


def _ec_pem() -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    pkcs8 = serialization.PrivateFormat.PKCS8
    return key.private_bytes(serialization.Encoding.PEM, pkcs8, serialization.NoEncryption())


def test_read_bounded_keeps_small_answers_and_refuses_large_ones():
    class Small(Huge):
        def read(self, n: int = -1) -> bytes:
            return b"ok"

    assert read_bounded(Small()) == b"ok"
    with pytest.raises(OSError, match="больше"):
        read_bounded(Huge(), limit=10)


def test_timestamp_authority_answer_is_bounded():
    pytest.importorskip("asn1crypto")
    with pytest.raises(OSError, match="больше"):
        anchor.TsaSink("https://tsa.bank", opener=_huge).publish(STMT)


def test_rekor_v1_answer_is_bounded():
    with pytest.raises(OSError, match="больше"):
        anchor.RekorSink("https://rekor.bank", _ec_pem(), opener=_huge).publish(STMT)


def test_rekor_v2_answer_is_bounded():
    with pytest.raises(OSError, match="больше"):
        anchor.RekorV2Sink("https://rekor.bank", _ec_pem(), opener=_huge).publish(STMT)


def test_log_tiles_and_checkpoints_are_bounded():
    with pytest.raises(OSError, match="больше"):
        tlog.read_url(_huge, "https://rekor.bank/checkpoint")


def test_vault_answer_is_bounded():
    with pytest.raises(OSError, match="больше"):
        signing.VaultTransitSigner("https://vault.bank:8200", "mandat", "s.token", opener=_huge)


def test_no_call_site_reads_without_a_limit():
    Huge.asked.clear()
    for call in (
        lambda: anchor.RekorSink("https://rekor.bank", _ec_pem(), opener=_huge).publish(STMT),
        lambda: tlog.read_url(_huge, "https://rekor.bank/checkpoint"),
        lambda: signing.VaultTransitSigner("https://vault.bank:8200", "mandat", "s.token", opener=_huge),
    ):
        with pytest.raises(OSError):
            call()
    assert Huge.asked and all(n == MAX_EXTERNAL_RESPONSE + 1 for n in Huge.asked)
