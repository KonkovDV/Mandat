"""Внешний якорь журнала: хеш каждого закрытого сегмента уходит за пределы машины.

Цепочка, HMAC и свидетель ловят подмену, пока у злоумышленника нет ключа и прав на файлы. Root на
хосте может переписать сегменты, свидетель и ключ вместе. Против этого — публикация вне машины, как
в прозрачных журналах (RFC 6962 / RFC 9162, Sigstore Rekor) и метках времени (RFC 3161): переписанная
история перестаёт совпадать с тем, что уже опубликовано.

Утверждение о сегменте (``statement``) — каноническая JSON-запись: имя журнала, номер сегмента,
диапазон ``seq``, хеш последней записи (через цепочку он закрепляет всю историю) и корень Меркла
записей сегмента (RFC 6962, ``merkle.py``). Публикуется SHA-256 утверждения.

Приёмники (``MANDAT_JOURNAL_ANCHOR``, через запятую):

* ``file:/путь`` — JSON Lines в файл на другом томе или машине (по умолчанию для стенда);
* ``webhook:https://…`` — POST с подписью Standard Webhooks (``services/webhooks.py``), секрет
  ``MANDAT_JOURNAL_ANCHOR_SECRET``; сверка у получателя — ``MANDAT_JOURNAL_ANCHOR_WEBHOOK_READBACK``;
* ``tsa:https://…`` — метка времени RFC 3161; разбор и проверка ответа — extra ``[tsa]`` (asn1crypto),
  импорт ленивый; доверие — корни ``MANDAT_JOURNAL_ANCHOR_TSA_ROOTS`` (с проверкой отзыва, ``revocation.py``)
  и/или закреплённый SHA-256 сертификата ``MANDAT_JOURNAL_ANCHOR_TSA_CERT``;
* ``rekor:https://…`` — hashedrekord v0.0.1 в Rekor v1, ``rekor2:https://…`` — hashedrekord v0.0.2 в Rekor v2
  (rekor-tiles); ключ ECDSA P-256 в PEM по пути ``MANDAT_JOURNAL_ANCHOR_REKOR_KEY``, ключ журнала —
  ``MANDAT_JOURNAL_ANCHOR_REKOR_PUBKEY``, согласованность checkpoint — ``MANDAT_JOURNAL_ANCHOR_REKOR_STATE``
  (``tlog.py``).

Квитанции хранятся рядом с журналом в ``<журнал>.anchors``; это только указатели, доказательство —
у приёмника. ``verify_journal`` при заданных приёмниках требует квитанцию на каждый закрытый сегмент и
перепроверяет её у приёмника (``check``). Политика при отсутствии якоря в pilot/prod —
``MANDAT_JOURNAL_ANCHOR_POLICY``: ``warn`` (по умолчанию) или ``fail``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from mandat.audit import merkle, tlog
from mandat.audit.revocation import RevocationChecker
from mandat.audit.revocation import from_env as revocation_from_env

log = logging.getLogger("mandat.anchor")
STATEMENT_TYPE = "mandat.journal.anchor/v1"
Opener = Callable[[urllib.request.Request], Any]


def _urlopen(request: urllib.request.Request) -> Any:
    return urllib.request.urlopen(request, timeout=10)  # noqa: S310 - адрес задаёт администратор, схема проверена


def _slow_urlopen(request: urllib.request.Request) -> Any:
    """Rekor v2 отвечает на запись после выпуска checkpoint: клиентам рекомендован таймаут от 20 с."""
    return urllib.request.urlopen(request, timeout=30)  # noqa: S310 - адрес задаёт администратор, схема проверена


def canonical(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def statement(journal_name: str, segment: int, records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        raise ValueError("пустой сегмент не закрепляется")
    return {
        "type": STATEMENT_TYPE,
        "journal": journal_name,
        "segment": segment,
        "first_seq": records[0]["seq"],
        "last_seq": records[-1]["seq"],
        "head": records[-1]["hash"],
        "merkle_root": merkle.root(merkle.journal_leaves(records)).hex(),
    }


def digest(stmt: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(stmt)).hexdigest()


class Sink(Protocol):
    name: str

    def publish(self, stmt: dict[str, Any]) -> dict[str, Any]: ...

    def check(self, stmt: dict[str, Any], receipt: dict[str, Any]) -> bool: ...


def _https(url: str) -> str:
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
        raise ValueError(f"адрес якоря должен быть https: {url}")
    return url.rstrip("/")


class FileSink:
    """JSON Lines в файле вне каталога журнала. ``check`` ищет утверждение с тем же хешем."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.name = f"file:{path}"

    def publish(self, stmt: dict[str, Any]) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = canonical({"statement": stmt, "sha256": digest(stmt)}) + b"\n"
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        return {"sha256": digest(stmt)}

    def check(self, stmt: dict[str, Any], receipt: dict[str, Any]) -> bool:
        want = digest(stmt)
        if receipt.get("sha256") != want or not self.path.exists():
            return False
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("sha256") == want and digest(row.get("statement") or {}) == want:
                return True
        return False


class WebhookSink:
    """POST утверждения с подписью Standard Webhooks.

    Без ``readback`` квитанция сверяется только локально. С ``readback`` (шаблон адреса с ``{sha256}``,
    ``MANDAT_JOURNAL_ANCHOR_WEBHOOK_READBACK``) ``check`` спрашивает у получателя, что он хранит: GET с
    подписанным заголовком ``webhook-id: readback-<случайное>``; ответ 200 — JSON ``{"sha256", "statement",
    "delivery_id"}`` с подписью Standard Webhooks тем же ``webhook-id`` (привязка ответа к
    запросу, повтор старого ответа не проходит): тем же секретом или, если задан ``readback_key``
    (``MANDAT_JOURNAL_ANCHOR_WEBHOOK_READBACK_KEY``, ``whpk_``), только подписью ``v1a`` ключом получателя.
    Хеш утверждения пересчитывается; 404 или иной ответ — отказ.
    """

    def __init__(
        self,
        url: str,
        secret: str,
        opener: Opener | None = None,
        readback: str | None = None,
        readback_key: str | None = None,
    ) -> None:
        self.url, self.secret = _https(url), secret
        if readback_key is not None and not readback_key.strip().startswith("whpk_"):
            raise ValueError("ключ проверки read-back — открытый ключ получателя whpk_ (Ed25519)")
        self.readback_key = readback_key
        self.readback = _https(readback) if readback else None
        if self.readback and "{sha256}" not in self.readback:
            raise ValueError("адрес read-back вебхук-якоря должен содержать {sha256}")
        self.name = f"webhook:{self.url}"
        self._open = opener or _urlopen

    def publish(self, stmt: dict[str, Any]) -> dict[str, Any]:
        from mandat.services.webhooks import sign_headers

        body = canonical(stmt)
        delivery = f"anchor-{secrets.token_hex(8)}"
        headers = {"content-type": "application/json", **sign_headers(self.secret, delivery, body, datetime.now(UTC))}
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")  # noqa: S310
        with self._open(request) as response:
            status = int(getattr(response, "status", 200))
        if status >= 300:
            raise OSError(f"приёмник якоря ответил {status}")
        return {"sha256": digest(stmt), "delivery_id": delivery, "status": status}

    def remote_problem(self, stmt: dict[str, Any], receipt: dict[str, Any]) -> str | None:
        from mandat.services.webhooks import response_signature_ok, sign_headers

        assert self.readback is not None  # noqa: S101 - вызывается только при заданном read-back
        want = digest(stmt)
        request_id = f"readback-{secrets.token_hex(8)}"
        headers = {"accept": "application/json", **sign_headers(self.secret, request_id, b"", datetime.now(UTC))}
        headers.pop("Content-Type", None)
        request = urllib.request.Request(self.readback.replace("{sha256}", want), headers=headers)  # noqa: S310
        try:
            with self._open(request) as response:
                status = int(getattr(response, "status", 200))
                answer_headers = dict(response.headers.items()) if getattr(response, "headers", None) else {}
                body = response.read()
        except Exception as exc:  # noqa: BLE001 - 404 и сетевые ошибки — не подтверждено
            return f"read-back недоступен: {type(exc).__name__}"
        if status != 200:
            return f"read-back ответил {status}"
        # С ключом получателя (whpk_) принимается только его подпись v1a: общий секрет её не подделает.
        verify_with = self.readback_key or self.secret
        if not response_signature_ok(verify_with, answer_headers, body, datetime.now(UTC), request_id):
            return "подпись ответа read-back не сходится"
        try:
            row = json.loads(body)
            stored = row["statement"]
        except (ValueError, KeyError, TypeError):
            return "ответ read-back не разбирается"
        if row.get("sha256") != want or digest(stored) != want:
            return "получатель хранит другое утверждение"
        if row.get("delivery_id") != receipt.get("delivery_id"):
            return "получатель хранит другую доставку"
        return None

    def check(self, stmt: dict[str, Any], receipt: dict[str, Any]) -> bool:
        if receipt.get("sha256") != digest(stmt) or not isinstance(receipt.get("delivery_id"), str):
            return False
        if self.readback is None:
            return True
        problem = self.remote_problem(stmt, receipt)
        if problem:
            log.error("webhook-якорь: %s", problem)
        return problem is None


# --- RFC 3161 -------------------------------------------------------------------------------------

_SHA256_OID = bytes.fromhex("0609608648016503040201")


def _der(tag: int, body: bytes) -> bytes:
    n = len(body)
    if n < 0x80:
        size = bytes([n])
    else:
        raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
        size = bytes([0x80 | len(raw)]) + raw
    return bytes([tag]) + size + body


def _der_int(value: int) -> bytes:
    raw = value.to_bytes(value.bit_length() // 8 + 1, "big")
    return _der(0x02, raw)


def tsa_request(message_digest: bytes, nonce: int) -> bytes:
    """TimeStampReq (RFC 3161, разд. 2.4.1): v1, SHA-256, nonce, certReq=TRUE."""
    algorithm = _der(0x30, _SHA256_OID + b"\x05\x00")
    imprint = _der(0x30, algorithm + _der(0x04, message_digest))
    return _der(0x30, _der_int(1) + imprint + _der_int(nonce) + b"\x01\x01\xff")


def _asn1() -> Any:
    try:
        from asn1crypto import tsp  # type: ignore[import-untyped]  # lazy: extra [tsa]
    except ModuleNotFoundError as exc:  # pragma: no cover - проверяется в test_anchor через monkeypatch
        raise RuntimeError("для якоря RFC 3161 установите extra: pip install 'mandat[tsa]'") from exc
    return tsp


TIME_STAMPING_EKU = "1.3.6.1.5.5.7.3.8"


def load_roots(pem: bytes) -> list[Any]:
    from cryptography import x509

    roots = x509.load_pem_x509_certificates(pem)
    if not roots:
        raise ValueError("в файле корней TSA нет сертификатов")
    return roots


def tsa_chain(leaf_der: bytes, intermediates_der: Sequence[bytes], roots: Sequence[Any], at: datetime) -> list[Any]:
    """Цепочка от сертификата TSA до заданного корня на момент метки (лист первым, корень последним).

    Лист обязан иметь единственный EKU timeStamping с флагом critical (RFC 3161, разд. 2.3).
    """
    from cryptography import x509
    from cryptography.x509 import verification as v

    leaf = x509.load_der_x509_certificate(leaf_der)
    try:
        eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    except x509.ExtensionNotFound:
        raise ValueError("у сертификата TSA нет расширения ExtendedKeyUsage") from None
    if not eku.critical or [o.dotted_string for o in eku.value] != [TIME_STAMPING_EKU]:
        raise ValueError("сертификат TSA не только для меток времени (EKU timeStamping, critical)")
    builder = (
        v.PolicyBuilder()
        .store(v.Store(list(roots)))
        .time(at)
        .extension_policies(ca_policy=v.ExtensionPolicy.webpki_defaults_ca(), ee_policy=v.ExtensionPolicy.permit_all())
    )
    try:
        verified = builder.build_client_verifier().verify(
            leaf, [x509.load_der_x509_certificate(d) for d in intermediates_der]
        )
    except v.VerificationError as exc:
        raise ValueError(f"сертификат TSA не выводится из доверенного корня: {exc}") from None
    return list(verified.chain)


def tsa_chain_problem(
    leaf_der: bytes, intermediates_der: Sequence[bytes], roots: Sequence[Any], at: datetime
) -> str | None:
    try:
        tsa_chain(leaf_der, intermediates_der, roots, at)
    except ValueError as exc:
        return str(exc)
    return None


def verify_tsa_token(
    token: bytes,
    message_digest: bytes,
    nonce: int | None,
    pinned_cert: str | None,
    roots: Sequence[Any] | None = None,
    revocation: RevocationChecker | None = None,
) -> str | None:
    """Проверить метку времени: imprint, nonce, подпись CMS, доверие к сертификату TSA.

    Доверие — цепочка до корня из ``MANDAT_JOURNAL_ANCHOR_TSA_ROOTS`` на момент метки (с EKU
    timeStamping) и/или закреплённый SHA-256 сертификата (``MANDAT_JOURNAL_ANCHOR_TSA_CERT``). С корнем и
    ``revocation`` каждый сертификат цепочки, кроме корня, проверяется на отзыв (``revocation.py``). Без
    того и другого метка не принимается: подпись сертификатом из самого ответа ничего не доказывает.
    Возвращает причину отказа или ``None``.
    """
    if not pinned_cert and not roots:
        return "для TSA не задан ни доверенный корень, ни закреплённый сертификат"
    tsp = _asn1()
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

    try:
        info = tsp.ContentInfo.load(token)
        signed = info["content"]
        tst = signed["encap_content_info"]["content"].parsed
    except (ValueError, TypeError, KeyError) as exc:
        return f"метка времени не разбирается: {type(exc).__name__}"
    if tst["message_imprint"]["hashed_message"].native != message_digest:
        return "метка времени выдана на другой хеш"
    if nonce is not None and tst["nonce"].native != nonce:
        return "nonce метки времени не совпадает"
    certs = [c.chosen for c in (signed["certificates"] or [])]
    if not certs:
        return "в метке нет сертификата TSA"
    signer = signed["signer_infos"][0]
    # Сертификаты в CMS — SET OF: порядок не гарантирован, сертификат подписанта ищется по sid.
    sid = signer["sid"]
    if sid.name == "issuer_and_serial_number":
        want = (sid.chosen["issuer"].dump(), sid.chosen["serial_number"].native)
        matches = [c for c in certs if (c.issuer.dump(), c.serial_number) == want]
    else:
        matches = [c for c in certs if c.key_identifier == sid.chosen.native]
    if not matches:
        return "в метке нет сертификата подписанта"
    leaf_cert = matches[0]
    cert_der = leaf_cert.dump()
    certs = [leaf_cert, *(c for c in certs if c is not leaf_cert)]
    if pinned_cert and hashlib.sha256(cert_der).hexdigest() != pinned_cert.lower():
        return "сертификат TSA не совпадает с закреплённым"
    attrs = signer["signed_attrs"]
    content = signed["encap_content_info"]["content"].contents
    md = next((a["values"][0].native for a in attrs if a["type"].native == "message_digest"), None)
    if md != hashlib.sha256(content).digest():
        return "message-digest подписи не совпадает с содержимым метки"
    data = attrs.untag().dump()  # SET OF, как подписывается по RFC 5652, разд. 5.4
    key = x509.load_der_x509_certificate(cert_der).public_key()
    try:
        if isinstance(key, rsa.RSAPublicKey):
            key.verify(signer["signature"].native, data, padding.PKCS1v15(), hashes.SHA256())
        elif isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(signer["signature"].native, data, ec.ECDSA(hashes.SHA256()))
        else:
            return "тип ключа TSA не поддержан"
    except Exception:  # noqa: BLE001 - InvalidSignature и ошибки формата — одинаковый отказ
        return "подпись метки времени не сходится"
    if roots:
        others = [c.dump() for c in certs[1:]]
        issued = tst["gen_time"].native
        try:
            chain = tsa_chain(cert_der, others, roots, issued)
        except ValueError as exc:
            return str(exc)
        if revocation is not None:
            return revocation.problem(chain, issued)
    return None


class TsaSink:
    """Метка времени RFC 3161 на хеш утверждения. Квитанция — сам токен, проверяется офлайн."""

    def __init__(
        self,
        url: str,
        pinned_cert: str | None = None,
        opener: Opener | None = None,
        roots: Sequence[Any] | None = None,
        revocation: RevocationChecker | None = None,
    ) -> None:
        self.url, self.pinned, self.roots = _https(url), pinned_cert, list(roots or [])
        self.revocation = revocation
        self.name = f"tsa:{self.url}"
        self._open = opener or _urlopen

    def publish(self, stmt: dict[str, Any]) -> dict[str, Any]:
        tsp = _asn1()
        imprint = bytes.fromhex(digest(stmt))
        nonce = secrets.randbits(63)
        request = urllib.request.Request(  # noqa: S310
            self.url,
            data=tsa_request(imprint, nonce),
            headers={"content-type": "application/timestamp-query"},
            method="POST",
        )
        with self._open(request) as response:
            raw = response.read()
        from asn1crypto import core

        try:
            # При отказе TSA ответ без токена: статус читается отдельно, до разбора всей структуры.
            status = tsp.PKIStatusInfo.load(core.Sequence.load(raw)[0].dump())["status"].native
        except (ValueError, TypeError, KeyError) as exc:
            raise OSError(f"ответ TSA не разбирается: {type(exc).__name__}") from exc
        if status not in {"granted", "granted_with_mods"}:
            raise OSError(f"TSA отказал: {status}")
        token = tsp.TimeStampResp.load(raw)["time_stamp_token"].dump()
        problem = verify_tsa_token(token, imprint, nonce, self.pinned, self.roots, self.revocation)
        if problem:
            raise OSError(problem)
        return {"sha256": digest(stmt), "nonce": nonce, "token": base64.b64encode(token).decode()}

    def check(self, stmt: dict[str, Any], receipt: dict[str, Any]) -> bool:
        if receipt.get("sha256") != digest(stmt):
            return False
        try:
            token = base64.b64decode(receipt["token"], validate=True)
        except (KeyError, ValueError, TypeError):
            return False
        imprint = bytes.fromhex(digest(stmt))
        problem = verify_tsa_token(token, imprint, receipt.get("nonce"), self.pinned, self.roots, self.revocation)
        if problem:
            log.error("TSA: %s", problem)
        return problem is None


verify_checkpoint = tlog.verify_checkpoint


def _ecdsa_pub(pem: bytes) -> Any:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise ValueError("ключ журнала Rekor v1 — ECDSA")
    return key


def verify_set(entry: dict[str, Any], log_key_pem: bytes) -> None:
    """SignedEntryTimestamp Rekor v1: подпись журнала над {body, integratedTime, logID, logIndex}.

    Полезная нагрузка — канонический JSON (RFC 8785; ключи по алфавиту, без пробелов), logID — SHA-256
    от DER SubjectPublicKeyInfo ключа журнала. Так проверяют sigstore-go и sigstore-python.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = _ecdsa_pub(log_key_pem)
    spki = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if entry.get("logID") != hashlib.sha256(spki).hexdigest():
        raise ValueError("logID записи — не ключ заданного журнала Rekor")
    payload = {
        "body": entry["body"],
        "integratedTime": int(entry["integratedTime"]),
        "logID": entry["logID"],
        "logIndex": int(entry["logIndex"]),
    }
    sig = base64.b64decode(entry["verification"]["signedEntryTimestamp"], validate=True)
    key.verify(sig, canonical(payload), ec.ECDSA(hashes.SHA256()))


def verify_rekor_entry(uuid: str, entry: dict[str, Any], log_key_pem: bytes) -> bytes:
    """Запись Rekor v1: uuid — хеш листа, SET, включение (RFC 6962) в подписанный checkpoint. Возвращает тело."""
    return _verify_rekor_entry(uuid, entry, log_key_pem)[0]


def _verify_rekor_entry(
    uuid: str, entry: dict[str, Any], log_key_pem: bytes, witnesses: tlog.WitnessPolicy | None = None
) -> tuple[bytes, tuple[str, int, bytes]]:
    body = base64.b64decode(entry["body"], validate=True)
    leaf = merkle.leaf_hash(body)
    if uuid[-64:].lower() != leaf.hex():
        raise ValueError("uuid записи не совпадает с хешем листа")
    verify_set(entry, log_key_pem)
    proof = entry["verification"]["inclusionProof"]
    root = bytes.fromhex(proof["rootHash"])
    hashes_ = [bytes.fromhex(h) for h in proof["hashes"]]
    if not merkle.verify_inclusion(leaf, int(proof["logIndex"]), int(proof["treeSize"]), hashes_, root):
        raise ValueError("доказательство включения не сходится с корнем")
    checkpoint = verify_checkpoint(proof["checkpoint"], log_key_pem, witnesses=witnesses)
    if checkpoint[1] != int(proof["treeSize"]) or checkpoint[2] != root:
        raise ValueError("checkpoint подписан для другого дерева")
    return body, checkpoint


def _ec_signer(key_pem: bytes) -> Any:
    from cryptography.hazmat.primitives.asymmetric import ec  # lazy
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(key_pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError("ключ Rekor — ECDSA P-256 в PEM")
    return key


class RekorSink:
    """Запись hashedrekord v0.0.1 в Sigstore Rekor v1.

    ``check`` читает запись по uuid и проверяет: uuid — хеш листа, SignedEntryTimestamp подписан журналом,
    доказательство включения (RFC 6962) сходится с корнем, корень и размер подписаны ключом журнала
    (checkpoint), тело несёт хеш утверждения. Без ключа журнала (``MANDAT_JOURNAL_ANCHOR_REKOR_PUBKEY``)
    запись не принимается. С ``state`` (``MANDAT_JOURNAL_ANCHOR_REKOR_STATE``) каждый увиденный checkpoint
    — из записи и текущий ``/api/v1/log`` — сверяется с последним проверенным доказательством
    согласованности (``/api/v1/log/proof``, RFC 6962, разд. 2.1.2).
    """

    def __init__(
        self,
        url: str,
        key_pem: bytes,
        opener: Opener | None = None,
        log_key_pem: bytes | None = None,
        state: tlog.CheckpointState | None = None,
        witnesses: tlog.WitnessPolicy | None = None,
    ) -> None:
        self.url, self._key_pem, self.log_key, self.state = _https(url), key_pem, log_key_pem, state
        self.witnesses = witnesses
        self.name = f"rekor:{self.url}"
        self._open = opener or _urlopen

    def _key(self) -> Any:
        return _ec_signer(self._key_pem)

    def _json(self, request: urllib.request.Request) -> Any:
        with self._open(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def _get(self, path: str) -> Any:
        return json.loads(tlog.read_url(self._open, self.url + path, "application/json").decode("utf-8"))

    def publish(self, stmt: dict[str, Any]) -> dict[str, Any]:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        key = self._key()
        signature = key.sign(canonical(stmt), ec.ECDSA(hashes.SHA256()))
        public = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        entry = {
            "apiVersion": "0.0.1",
            "kind": "hashedrekord",
            "spec": {
                "signature": {
                    "content": base64.b64encode(signature).decode(),
                    "publicKey": {"content": base64.b64encode(public).decode()},
                },
                "data": {"hash": {"algorithm": "sha256", "value": digest(stmt)}},
            },
        }
        request = urllib.request.Request(  # noqa: S310
            self.url + "/api/v1/log/entries",
            data=canonical(entry),
            headers={"content-type": "application/json", "accept": "application/json"},
            method="POST",
        )
        answer = self._json(request)
        uuid, body = next(iter(answer.items()))
        return {"sha256": digest(stmt), "uuid": uuid, "log_index": body.get("logIndex")}

    def _prover(self, origin: str) -> tlog.Prover:
        tree = origin.rpartition(" - ")[2] if " - " in origin else ""

        def prove(size1: int, size2: int) -> list[bytes]:
            query = f"/api/v1/log/proof?firstSize={size1}&lastSize={size2}" + (f"&treeID={tree}" if tree else "")
            return [bytes.fromhex(h) for h in self._get(query)["hashes"]]

        return prove

    def consistency_problem(self, checkpoint: tuple[str, int, bytes]) -> str | None:
        """Checkpoint записи и текущий checkpoint журнала согласованы с последним проверенным."""
        if self.state is None or self.log_key is None:
            return None
        origin, size, root = checkpoint
        problem = self.state.observe(origin, size, root, self._prover(origin))
        if problem:
            return problem
        try:
            live = verify_checkpoint(self._get("/api/v1/log")["signedTreeHead"], self.log_key, witnesses=self.witnesses)
        except Exception as exc:  # noqa: BLE001 - недоступный журнал при включённом мониторинге — отказ
            return f"текущий checkpoint Rekor не получен: {type(exc).__name__}"
        return self.state.observe(live[0], live[1], live[2], self._prover(live[0]))

    def check(self, stmt: dict[str, Any], receipt: dict[str, Any]) -> bool:
        if receipt.get("sha256") != digest(stmt) or not isinstance(receipt.get("uuid"), str):
            return False
        if not self.log_key:
            return False
        request = urllib.request.Request(  # noqa: S310
            f"{self.url}/api/v1/log/entries/{receipt['uuid']}", headers={"accept": "application/json"}
        )
        try:
            answer = self._json(request)
            raw, checkpoint = _verify_rekor_entry(
                receipt["uuid"], answer[receipt["uuid"]], self.log_key, self.witnesses
            )
            body = json.loads(raw)
            if body["spec"]["data"]["hash"]["value"] != digest(stmt):
                return False
        except Exception:  # noqa: BLE001 - любая ошибка разбора или подписи — запись не подтверждена
            return False
        problem = self.consistency_problem(checkpoint)
        if problem:
            log.error("Rekor: %s", problem)
        return problem is None


V2_KIND = ("hashedrekord", "0.0.2")


def verify_rekor_v2_entry(
    tle: dict[str, Any], log_key_pem: bytes, origin: str | None, witnesses: tlog.WitnessPolicy | None = None
) -> tuple[bytes, tuple[str, int, bytes]]:
    """TransparencyLogEntry Rekor v2: тело известного вида, включение в дерево подписанного checkpoint.

    Размер и корень берутся только из проверенного checkpoint; ``inclusionProof.rootHash`` и ``treeSize``
    игнорируются, номер записи — верхний ``logIndex`` (rekor-tiles, CLIENTS.md).
    """
    kv = tle.get("kindVersion") or {}
    if (kv.get("kind"), kv.get("version")) != V2_KIND:
        raise ValueError("неизвестный вид записи Rekor v2")
    body = base64.b64decode(tle["canonicalizedBody"], validate=True)
    parsed = json.loads(body)
    if (parsed.get("kind"), parsed.get("apiVersion")) != V2_KIND:
        raise ValueError("тело записи Rekor v2 другого вида")
    proof = tle["inclusionProof"]
    checkpoint = verify_checkpoint(proof["checkpoint"]["envelope"], log_key_pem, origin=origin, witnesses=witnesses)
    hashes_ = [base64.b64decode(h, validate=True) for h in proof.get("hashes") or []]
    index = int(tle["logIndex"])
    if not merkle.verify_inclusion(merkle.leaf_hash(body), index, checkpoint[1], hashes_, checkpoint[2]):
        raise ValueError("доказательство включения не сходится с checkpoint")
    return body, checkpoint


class RekorV2Sink:
    """Запись hashedrekord v0.0.2 в Rekor v2 (rekor-tiles).

    Rekor v2 не отдаёт записи и доказательства по API: квитанция — весь TransparencyLogEntry, ``check``
    проверяет его офлайн (подпись checkpoint ключом журнала, origin = адрес без схемы, включение по RFC 6962).
    С ``state`` checkpoint записи и текущий ``/checkpoint`` сверяются с последним проверенным доказательством
    согласованности, посчитанным по тайлам (C2SP tlog-tiles). Таймаут записи — 30 с: журнал отвечает после
    выпуска checkpoint.
    """

    def __init__(
        self,
        url: str,
        key_pem: bytes,
        opener: Opener | None = None,
        log_key_pem: bytes | None = None,
        state: tlog.CheckpointState | None = None,
        witnesses: tlog.WitnessPolicy | None = None,
    ) -> None:
        self.url, self._key_pem, self.log_key, self.state = _https(url), key_pem, log_key_pem, state
        self.witnesses = witnesses
        self.origin = self.url.split("://", 1)[1]
        self.name = f"rekor2:{self.url}"
        self._open = opener or _slow_urlopen

    def publish(self, stmt: dict[str, Any]) -> dict[str, Any]:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        key = _ec_signer(self._key_pem)
        signature = key.sign(canonical(stmt), ec.ECDSA(hashes.SHA256()))
        spki = key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        request_body = {
            "hashedRekordRequestV002": {
                "digest": base64.b64encode(bytes.fromhex(digest(stmt))).decode(),
                "signature": {
                    "content": base64.b64encode(signature).decode(),
                    "verifier": {
                        "publicKey": {"rawBytes": base64.b64encode(spki).decode()},
                        "keyDetails": "PKIX_ECDSA_P256_SHA_256",
                    },
                },
            }
        }
        request = urllib.request.Request(  # noqa: S310
            self.url + "/api/v2/log/entries",
            data=canonical(request_body),
            headers={"content-type": "application/json", "accept": "application/json"},
            method="POST",
        )
        with self._open(request) as response:
            tle = json.loads(response.read().decode("utf-8"))
        receipt = {"sha256": digest(stmt), "log_index": int(tle.get("logIndex", -1)), "tle": tle}
        if self.log_key and not self._entry_ok(stmt, receipt):
            raise OSError("Rekor v2 вернул запись, которая не проверяется")
        return receipt

    def _entry_ok(self, stmt: dict[str, Any], receipt: dict[str, Any]) -> tuple[str, int, bytes] | None:
        if receipt.get("sha256") != digest(stmt) or not self.log_key:
            return None
        try:
            raw, checkpoint = verify_rekor_v2_entry(receipt["tle"], self.log_key, self.origin, self.witnesses)
            data = json.loads(raw)["spec"]["hashedRekordV002"]["data"]
            want = base64.b64encode(bytes.fromhex(digest(stmt))).decode()
            if data.get("algorithm") != "SHA2_256" or data.get("digest") != want:
                return None
        except Exception:  # noqa: BLE001 - любая ошибка разбора или подписи — запись не подтверждена
            return None
        return checkpoint

    def _fetch(self, path: str) -> bytes:
        return tlog.read_url(self._open, f"{self.url}/{path}")

    def consistency_problem(self, checkpoint: tuple[str, int, bytes]) -> str | None:
        if self.state is None or self.log_key is None:
            return None
        prove = tlog.tile_prover(self._fetch)
        problem = self.state.observe(*checkpoint, prove)
        if problem:
            return problem
        try:
            live = verify_checkpoint(
                self._fetch("checkpoint").decode("utf-8"), self.log_key, origin=self.origin, witnesses=self.witnesses
            )
        except Exception as exc:  # noqa: BLE001 - недоступный журнал при включённом мониторинге — отказ
            return f"текущий checkpoint Rekor v2 не получен: {type(exc).__name__}"
        return self.state.observe(*live, prove)

    def check(self, stmt: dict[str, Any], receipt: dict[str, Any]) -> bool:
        checkpoint = self._entry_ok(stmt, receipt)
        if checkpoint is None:
            return False
        problem = self.consistency_problem(checkpoint)
        if problem:
            log.error("Rekor v2: %s", problem)
        return problem is None


# --- настройка ------------------------------------------------------------------------------------


def configured_sinks(value: str | None = None) -> list[Sink]:
    raw = os.environ.get("MANDAT_JOURNAL_ANCHOR", "") if value is None else value
    sinks: list[Sink] = []
    for spec in (s.strip() for s in raw.split(",")):
        if not spec:
            continue
        kind, _, target = spec.partition(":")
        if kind == "file" and target:
            sinks.append(FileSink(Path(target)))
        elif kind == "webhook":
            secret = os.environ.get("MANDAT_JOURNAL_ANCHOR_SECRET", "")
            if not secret:
                raise ValueError("webhook-якорь требует MANDAT_JOURNAL_ANCHOR_SECRET")
            readback = os.environ.get("MANDAT_JOURNAL_ANCHOR_WEBHOOK_READBACK") or None
            readback_key = os.environ.get("MANDAT_JOURNAL_ANCHOR_WEBHOOK_READBACK_KEY") or None
            sinks.append(WebhookSink(target, secret, readback=readback, readback_key=readback_key))
        elif kind == "tsa":
            pinned = os.environ.get("MANDAT_JOURNAL_ANCHOR_TSA_CERT") or None
            roots_path = os.environ.get("MANDAT_JOURNAL_ANCHOR_TSA_ROOTS", "")
            roots = load_roots(Path(roots_path).read_bytes()) if roots_path else []
            if not pinned and not roots:
                raise ValueError("tsa-якорь требует MANDAT_JOURNAL_ANCHOR_TSA_ROOTS или MANDAT_JOURNAL_ANCHOR_TSA_CERT")
            sinks.append(TsaSink(target, pinned, roots=roots, revocation=revocation_from_env() if roots else None))
        elif kind in {"rekor", "rekor2", "sigstore"}:
            sinks.append(_rekor_sink(kind, target))
        else:
            raise ValueError(f"неизвестный якорь журнала: {spec}")
    return sinks


def _rekor_sink(kind: str, target: str) -> Sink:
    """Rekor v1/v2 по адресу и ключу из настроек или (``sigstore``) по TrustedRoot/SigningConfig через TUF."""
    key_path = os.environ.get("MANDAT_JOURNAL_ANCHOR_REKOR_KEY", "")
    if not key_path:
        raise ValueError("rekor-якорь требует MANDAT_JOURNAL_ANCHOR_REKOR_KEY (ECDSA P-256, PEM)")
    if kind == "sigstore":
        from mandat.audit import trust

        override = target or os.environ.get("MANDAT_SIGSTORE_REKOR_URL") or None
        target, major, log_key = trust.rekor_target(trust.load(), override)
        kind = "rekor" if major == 1 else "rekor2"
    else:
        log_key_path = os.environ.get("MANDAT_JOURNAL_ANCHOR_REKOR_PUBKEY", "")
        if not log_key_path:
            raise ValueError("rekor-якорь требует MANDAT_JOURNAL_ANCHOR_REKOR_PUBKEY (ключ журнала Rekor, PEM)")
        log_key = Path(log_key_path).read_bytes()
    state_path = os.environ.get("MANDAT_JOURNAL_ANCHOR_REKOR_STATE", "")
    state = tlog.CheckpointState(Path(state_path)) if state_path else None
    cls = RekorSink if kind == "rekor" else RekorV2Sink
    return cls(
        target,
        Path(key_path).read_bytes(),
        log_key_pem=log_key,
        state=state,
        witnesses=tlog.witness_policy_from_env(),
    )


def policy() -> str:
    value = os.environ.get("MANDAT_JOURNAL_ANCHOR_POLICY", "warn").strip().lower()
    return value if value in {"warn", "fail"} else "warn"


def missing_anchor_problem() -> str | None:
    """Для pilot/prod: якорь не задан. Возвращает текст, если политика ``fail``; при ``warn`` — пишет в лог."""
    from mandat.services.env_policy import protected

    if configured_sinks():  # заодно проверяет настройку: ошибка в ней — ValueError до старта сервиса
        return None
    if not protected():
        return None
    text = "внешний якорь журнала не задан (MANDAT_JOURNAL_ANCHOR): root на хосте может переписать историю"
    if policy() == "fail":
        return text
    log.warning(text)
    return None


def receipts_path(journal_path: Path) -> Path:
    return journal_path.with_name(journal_path.name + ".anchors")


def read_receipts(journal_path: Path) -> dict[tuple[int, str], dict[str, Any]]:
    path = receipts_path(journal_path)
    out: dict[tuple[int, str], dict[str, Any]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            out[(int(row["segment"]), str(row["sink"]))] = row
        except (ValueError, KeyError, TypeError):
            continue
    return out


def _segment_records(seg: Path) -> list[dict[str, Any]]:
    from mandat.strictjson import strict_loads

    return [strict_loads(line) for line in seg.read_text(encoding="utf-8").splitlines() if line.strip()]


def _segment_index(seg: Path) -> int:
    return int(seg.name.rsplit(".", 1)[1])


def publish_pending(journal_path: Path, sinks: Sequence[Sink]) -> list[str]:
    """Опубликовать утверждения закрытых сегментов, у которых ещё нет квитанции. Ошибки — в лог, не в запись."""
    from mandat.audit.journal import sealed_segments

    done = read_receipts(journal_path)
    failures = []
    for seg in sealed_segments(journal_path):
        index = _segment_index(seg)
        pending = [s for s in sinks if (index, s.name) not in done]
        if not pending:
            continue
        stmt = statement(journal_path.name, index, _segment_records(seg))
        for sink in pending:
            try:
                receipt = sink.publish(stmt)
            except Exception as exc:  # noqa: BLE001 - недоступный приёмник не останавливает журнал
                failures.append(f"{sink.name}: сегмент {index}: {type(exc).__name__}")
                log.error("якорь журнала не опубликован: %s сегмент %s: %s", sink.name, index, type(exc).__name__)
                continue
            row = {"segment": index, "sink": sink.name, "sha256": digest(stmt), "receipt": receipt}
            with receipts_path(journal_path).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return failures


def check_anchors(journal_path: Path, sinks: Sequence[Sink]) -> str | None:
    """Каждый закрытый сегмент закреплён у каждого приёмника. Возвращает причину отказа или ``None``."""
    from mandat.audit.journal import sealed_segments

    receipts = read_receipts(journal_path)
    for seg in sealed_segments(journal_path):
        index = _segment_index(seg)
        stmt = statement(journal_path.name, index, _segment_records(seg))
        for sink in sinks:
            row = receipts.get((index, sink.name))
            if row is None:
                return f"сегмент {index} не закреплён у {sink.name}"
            if row.get("sha256") != digest(stmt) or not sink.check(stmt, row.get("receipt") or {}):
                return f"сегмент {index} не совпадает с якорем {sink.name}"
    return None
