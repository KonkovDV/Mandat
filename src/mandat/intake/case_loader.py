"""Сборка ``Case`` из манифеста: каталог с case.json (CLI) или JSON с содержимым (API).

Ошибки разбора не роняют проверку: они попадают в ``intake_errors``, а правило
R-FMT-001 превращает их в «недостаточно данных» (при сомнении — не «да»).
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from mandat.domain.identifiers import normalize_poa_number
from mandat.domain.model import Case, IntakeError, Operation, Organization, Person, TextDocument
from mandat.intake.egrul import EgrulError, parse_egrul
from mandat.intake.emchd import EmchdError
from mandat.intake.formats import parse_poa
from mandat.intake.xml_safety import XmlRejected

KINDS = {"emchd_xml", "egrul_json", "charter_text", "paper_poa_text"}
MAX_DOCUMENTS = 500  # длинные цепочки до 300 звеньев проверяет test_long_expired_chain_is_bounded
MAX_DOC_BYTES = 4 * 1024 * 1024


class CaseError(ValueError):
    pass


def _object(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CaseError(f"{name} должен быть объектом JSON")
    return value


def _registry(value: Any, field_name: str = "registry") -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CaseError(f"{field_name} должен быть объектом")
    return {str(k): str(v) for k, v in value.items()}


def _iso_day(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


_REGISTRY_KEYS = {"status", "event_date", "observed_at"}


def _registry_entries(value: Any) -> dict[str, str | dict[str, str]]:
    """Реестр МЧД в манифесте: номер -> статус или {status, event_date?, observed_at?}.

    ``event_date`` — дата отмены или истечения по реестру (ГГГГ-ММ-ДД), ``observed_at`` — момент запроса
    с часовым поясом. Неразборчивое значение — ошибка кейса, а не «нет сведений».
    """
    out: dict[str, str | dict[str, str]] = {}
    if value is None:
        return out
    if not isinstance(value, dict):
        raise CaseError("registry должен быть объектом")
    for number, entry in value.items():
        if not isinstance(entry, dict):
            out[str(number)] = str(entry)
            continue
        if set(entry) - _REGISTRY_KEYS or not isinstance(entry.get("status"), str):
            raise CaseError(f"registry[{number}]: поля status, event_date, observed_at")
        clean = {"status": entry["status"]}
        event = entry.get("event_date")
        if event is not None:
            if not _iso_day(event):
                raise CaseError(f"registry[{number}].event_date: ГГГГ-ММ-ДД")
            clean["event_date"] = event
        observed = entry.get("observed_at")
        if observed is not None:
            try:
                stamp = datetime.fromisoformat(observed) if isinstance(observed, str) else None
            except ValueError:
                stamp = None
            if stamp is None or stamp.tzinfo is None:
                raise CaseError(f"registry[{number}].observed_at: дата и время с часовым поясом")
            clean["observed_at"] = observed
        out[str(number)] = clean
    return out


def _disposition(value: Any) -> str | None:
    if value is None:
        return None
    if value != "no_applicable_restriction":
        raise CaseError("charter_disposition: no_applicable_restriction или пусто")
    return "no_applicable_restriction"


SUBDELEGATION_FORMS = ("notarized", "legal_entity", "branch_head")


def _subdelegation_forms(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CaseError("subdelegation_forms должен быть объектом: номер МЧД -> форма")
    out: dict[str, str] = {}
    for number, form in value.items():
        key = normalize_poa_number(number)
        if key is None or form not in SUBDELEGATION_FORMS:
            raise CaseError("subdelegation_forms: форма " + ", ".join(SUBDELEGATION_FORMS))
        out[key] = form
    return out


def _rubles(value: Any) -> int | None:
    """Целые рубли. Дробь не усекается, boolean не считается деньгами."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CaseError("amount_rub — целое число рублей; дробные значения и boolean не принимаются")
    if value <= 0:
        raise CaseError("amount_rub должен быть больше нуля")
    return value


def _text(raw: dict[str, Any], key: str, where: str) -> str | None:
    """Реквизит строкой или пусто. Число вместо ИНН теряет ведущие нули, поэтому не принимается."""
    value = raw.get(key)
    if value is None or isinstance(value, str):
        return value
    raise CaseError(f"{where}.{key} должен быть строкой")


def _person(raw: dict[str, Any] | None) -> Person:
    raw = _object(raw, "person")
    return Person(**{k: _text(raw, k, "requester") for k in ("full_name", "inn", "snils", "position")})


def load_case_from_manifest(manifest: dict[str, Any], read: Callable[[dict[str, Any]], bytes]) -> Case:
    if not isinstance(manifest, dict):
        raise CaseError("манифест должен быть объектом JSON")
    try:
        op = _object(manifest.get("operation"), "operation")
        operation = Operation(
            code=str(op["code"]),
            on_date=date.fromisoformat(str(op["date"])),
            amount_rub=_rubles(op.get("amount_rub")),
            counterparty_inn=_text(op, "counterparty_inn", "operation"),
        )
        org = _object(manifest.get("organization"), "organization")
        organization = Organization(**{k: _text(org, k, "organization") for k in ("inn", "ogrn", "kpp", "name")})
    except (KeyError, ValueError, TypeError) as exc:
        raise CaseError(f"манифест кейса неполный: {exc}") from exc

    poas, papers, errors, inputs = [], [], [], []
    duplicates: list[tuple[str, str]] = []
    egrul = charter = None
    documents = manifest.get("documents") or []
    if not isinstance(documents, list):
        raise CaseError("documents должен быть списком")
    if len(documents) > MAX_DOCUMENTS:
        raise CaseError(f"в кейсе больше {MAX_DOCUMENTS} документов")
    seen_ids: set[str] = set()
    for doc in documents:
        if not isinstance(doc, dict):
            raise CaseError("документ в списке должен быть объектом")
        raw_id = doc.get("id")
        # Идентификатор — локатор доказательства: без него или при повторе ссылки в отчёте неоднозначны.
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise CaseError("у каждого документа должен быть непустой строковый id")
        if raw_id in seen_ids:
            raise CaseError(f"повторный id документа: {raw_id!r}")
        seen_ids.add(raw_id)
        doc_id, kind = raw_id, str(doc.get("kind"))
        if kind not in KINDS:
            errors.append(IntakeError(doc_id, kind, f"неизвестный вид документа {kind!r}"))
            continue
        try:
            raw = read(doc)
        except (OSError, ValueError) as exc:
            errors.append(IntakeError(doc_id, kind, f"документ не читается: {exc}"))
            continue
        if len(raw) > MAX_DOC_BYTES:
            errors.append(IntakeError(doc_id, kind, "документ больше допустимого размера"))
            continue
        sha = hashlib.sha256(raw).hexdigest()
        inputs.append({"doc_id": doc_id, "kind": kind, "sha256": sha})
        try:
            if kind == "emchd_xml":
                poas.append(parse_poa(raw, doc_id=doc_id))
            elif kind == "egrul_json":
                parsed = parse_egrul(raw, doc_id=doc_id)
                if egrul is not None and egrul.sha256 != parsed.sha256:
                    duplicates.append((doc_id, kind))
                egrul = egrul or parsed
            elif kind == "charter_text":
                text_doc = TextDocument(doc_id, sha, kind, raw.decode("utf-8"))
                if charter is not None and charter.sha256 != sha:
                    duplicates.append((doc_id, kind))
                charter = charter or text_doc
            else:
                papers.append(TextDocument(doc_id, sha, kind, raw.decode("utf-8")))
        except (EmchdError, EgrulError, XmlRejected, UnicodeDecodeError) as exc:
            errors.append(IntakeError(doc_id, kind, str(exc)))

    # Два разных экземпляра одного документа: ни один не используется, R-FMT-005 называет причину.
    if any(kind == "egrul_json" for _, kind in duplicates):
        egrul = None
    if any(kind == "charter_text" for _, kind in duplicates):
        charter = None
    return Case(
        case_id=str(manifest.get("case_id") or "case"),
        organization=organization,
        requester=_person(manifest.get("requester")),
        operation=operation,
        poas=tuple(poas),
        egrul=egrul,
        charter=charter,
        paper_poas=tuple(papers),
        registry=_registry_entries(manifest.get("registry")),
        bankruptcy=_registry(manifest.get("bankruptcy"), "bankruptcy"),
        intake_errors=tuple(errors),
        inputs=tuple(inputs),
        charter_disposition=_disposition(manifest.get("charter_disposition")),
        subdelegation_forms=_subdelegation_forms(manifest.get("subdelegation_forms")),
        duplicates=tuple(duplicates),
    )


def _read_regular(target: Path, limit: int) -> bytes:
    """Чтение обычного файла без гонки «проверил — открыл» (S-4, CWE-367).

    Файл открывается с ``O_NOFOLLOW`` (подменённая после проверки символическая ссылка не открывается)
    и ``O_NONBLOCK`` (FIFO не вешает процесс на открытии). Тип и размер проверяются ``fstat`` по уже
    открытому дескриптору, а не по имени, и чтение ограничено ``limit + 1`` байтами: файл, выросший
    после ``fstat``, всё равно отвергается.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(target, flags)
    except OSError as exc:
        raise ValueError(f"документ не открывается: {exc.strerror}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("документ не является обычным файлом")
        if info.st_size > limit:
            raise ValueError("документ больше допустимого размера")
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "rb", buffering=0) as handle:
        chunks, total = [], 0
        while total <= limit:
            chunk = handle.read(min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    if total > limit:
        raise ValueError("документ больше допустимого размера")
    return b"".join(chunks)


def load_case_dir(path: Path) -> Case:
    manifest_path = path / "case.json"
    try:
        raw_manifest = _read_regular(manifest_path, MAX_DOC_BYTES)
    except ValueError as exc:
        raise CaseError(f"case.json: {exc}") from exc
    manifest = json.loads(raw_manifest.decode("utf-8"))
    base = path.resolve()

    def read(doc: dict[str, Any]) -> bytes:
        target = (base / str(doc["path"])).resolve()
        if base not in target.parents:
            raise ValueError("путь документа выходит за каталог кейса")
        return _read_regular(target, MAX_DOC_BYTES)

    return load_case_from_manifest(manifest, read)


def load_case_inline(manifest: dict[str, Any]) -> Case:
    """Для API: содержимое документов передаётся строкой (XML, JSON или текст)."""

    def read(doc: dict[str, Any]) -> bytes:
        content = doc.get("content")
        if isinstance(content, (dict, list)):
            return json.dumps(content, ensure_ascii=False).encode("utf-8")
        if not isinstance(content, str):
            raise ValueError("content должен быть строкой или JSON-объектом")
        return content.encode("utf-8")

    return load_case_from_manifest(manifest, read)
