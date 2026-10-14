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
  ``MANDAT_JOURNAL_ANCHOR_SECRET``;
* ``tsa:https://…`` — метка времени RFC 3161; разбор и проверка ответа — extra ``[tsa]`` (asn1crypto),
  импорт ленивый; ``MANDAT_JOURNAL_ANCHOR_TSA_CERT`` — SHA-256 сертификата TSA для закрепления;
* ``rekor:https://…`` — запись hashedrekord в Sigstore Rekor, ключ ECDSA P-256 в PEM по пути
  ``MANDAT_JOURNAL_ANCHOR_REKOR_KEY``.

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

from mandat.audit import merkle

log = logging.getLogger("mandat.anchor")
STATEMENT_TYPE = "mandat.journal.anchor/v1"
Opener = Callable[[urllib.request.Request], Any]


def _urlopen(request: urllib.request.Request) -> Any:
    return urllib.request.urlopen(request, timeout=10)  # noqa: S310 - адрес задаёт администратор, схема проверена


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
    """POST утверждения с подписью Standard Webhooks. Копия — у получателя; локально сверяется квитанция."""

    def __init__(self, url: str, secret: str, opener: Opener | None = None) -> None:
        self.url, self.secret = _https(url), secret
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

    def check(self, stmt: dict[str, Any], receipt: dict[str, Any]) -> bool:
        return receipt.get("sha256") == digest(stmt) and isinstance(receipt.get("delivery_id"), str)


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


def verify_tsa_token(token: bytes, message_digest: bytes, nonce: int | None, pinned_cert: str | None) -> str | None:
    """Проверить метку времени: imprint, nonce, подпись CMS сертификатом из ответа, закрепление сертификата.

    Возвращает причину отказа или ``None``. Цепочка до корня УЦ здесь не строится: доверие — через
    закреплённый SHA-256 сертификата TSA (``MANDAT_JOURNAL_ANCHOR_TSA_CERT``).
    """
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
    cert_der = certs[0].dump()
    if pinned_cert and hashlib.sha256(cert_der).hexdigest() != pinned_cert.lower():
        return "сертификат TSA не совпадает с закреплённым"
    signer = signed["signer_infos"][0]
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
    return None


class TsaSink:
    """Метка времени RFC 3161 на хеш утверждения. Квитанция — сам токен, проверяется офлайн."""

    def __init__(self, url: str, pinned_cert: str | None = None, opener: Opener | None = None) -> None:
        self.url, self.pinned = _https(url), pinned_cert
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
        problem = verify_tsa_token(token, imprint, nonce, self.pinned)
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
        return verify_tsa_token(token, bytes.fromhex(digest(stmt)), receipt.get("nonce"), self.pinned) is None


class RekorSink:
    """Запись hashedrekord v0.0.1 в Sigstore Rekor. ``check`` читает запись из журнала Rekor по uuid."""

    def __init__(self, url: str, key_pem: bytes, opener: Opener | None = None) -> None:
        self.url, self._key_pem = _https(url), key_pem
        self.name = f"rekor:{self.url}"
        self._open = opener or _urlopen

    def _key(self) -> Any:
        from cryptography.hazmat.primitives.asymmetric import ec  # lazy
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        key = load_pem_private_key(self._key_pem, password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError("ключ Rekor — ECDSA P-256 в PEM")
        return key

    def _json(self, request: urllib.request.Request) -> Any:
        with self._open(request) as response:
            return json.loads(response.read().decode("utf-8"))

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

    def check(self, stmt: dict[str, Any], receipt: dict[str, Any]) -> bool:
        if receipt.get("sha256") != digest(stmt) or not isinstance(receipt.get("uuid"), str):
            return False
        request = urllib.request.Request(  # noqa: S310
            f"{self.url}/api/v1/log/entries/{receipt['uuid']}", headers={"accept": "application/json"}
        )
        try:
            answer = self._json(request)
            body = json.loads(base64.b64decode(answer[receipt["uuid"]]["body"]))
            return bool(body["spec"]["data"]["hash"]["value"] == digest(stmt))
        except (OSError, KeyError, ValueError, TypeError):
            return False


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
            sinks.append(WebhookSink(target, secret))
        elif kind == "tsa":
            sinks.append(TsaSink(target, os.environ.get("MANDAT_JOURNAL_ANCHOR_TSA_CERT") or None))
        elif kind == "rekor":
            key_path = os.environ.get("MANDAT_JOURNAL_ANCHOR_REKOR_KEY", "")
            if not key_path:
                raise ValueError("rekor-якорь требует MANDAT_JOURNAL_ANCHOR_REKOR_KEY (ECDSA P-256, PEM)")
            sinks.append(RekorSink(target, Path(key_path).read_bytes()))
        else:
            raise ValueError(f"неизвестный якорь журнала: {spec}")
    return sinks


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
    from mandat.audit.journal import strict_loads

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
