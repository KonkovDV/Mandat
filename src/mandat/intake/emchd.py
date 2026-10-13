"""Разбор единой МЧД (формат 003, EMCHD_1) по файлу соответствия ``data/emchd_003_mapping.json``.

Код не знает имён тегов: они лежат в файле соответствия, сверенном с XSD EMCHD_1. Разбираются обе ветви
схемы: ``Документ/Довер`` (первичная доверенность) и ``Документ/Передов`` (в порядке передоверия).
Документ может быть в пространстве имён EMCHD_1 или без него; другое пространство имён — ошибка.

Писатель ``build_emchd_xml`` использует тот же файл и нужен синтетическому корпусу и тестам. Он пишет
только ветвь ``Довер`` без пространства имён и без реквизитов, которые проверки не читают, поэтому его
вывод схему XSD не проходит: совпадение писателя и разборщика соответствия формату не доказывает.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any, cast
from xml.etree.ElementTree import Element, tostring

from mandat.data import load_json
from mandat.domain.identifiers import normalize_poa_number
from mandat.domain.model import Authority, EvidenceRef, Organization, Person, PowerOfAttorney, Subdelegation
from mandat.intake import xpath
from mandat.intake.xml_safety import safe_fromstring

REQUIRED = ("number", "issue_date", "principal.inn", "signer.inn")
# Без этих полей документ разбирается, но формат не соблюдён: R-FMT-001 не даёт по нему «подтверждено».
FORMAT_REQUIRED = (
    "subdelegation",
    "kind",
    "principal_type",
    "principal.name",
    "signer.last_name",
    "signer.first_name",
)
# СрокДейст и ВерсФорм по XSD тоже обязательны, их отсутствие разбирают R-TERM-001 и R-FMT-002.
# ПрСовмПолн обязателен, без него R-SUB-009 и проверка нескольких представителей не дают подтверждения.

# По XSD необязательны, но без них проверки цепочки и реестра не провести: МЧД без них не подтверждается.
POLICY_REQUIRED = ("principal.ogrn",)


class EmchdError(ValueError):
    """МЧД не разбирается или в ней нет обязательных полей."""


def mapping() -> dict[str, Any]:
    return load_json("emchd_003_mapping.json")


def _date(value: str | None) -> date | None:
    """Только ``ГГГГ-ММ-ДД`` целиком (xs:date без зоны). ``2026-03-01garbage`` и ``1.3.2026`` — ошибка формата."""
    if not value:
        return None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise EmchdError(f"дата не распознана: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise EmchdError(f"дата не распознана: {value!r}") from exc


def _fio(values: dict[str, str | None]) -> str | None:
    parts = [values.get("last_name"), values.get("first_name"), values.get("middle_name")]
    joined = " ".join(p for p in parts if p)
    return joined or None


def _strip_namespace(root: Element, namespace: str) -> None:
    """Снимает пространство имён EMCHD_1 с элементов. Элементы чужого пространства имён остаются как есть."""
    prefix = "{" + namespace + "}"
    for node in root.iter():
        if isinstance(node.tag, str) and node.tag.startswith(prefix):
            node.tag = node.tag[len(prefix) :]


def _read_any(root: Element, paths: str | list[str] | None) -> tuple[str | None, str | None]:
    """Значение по одному из путей. Если значение есть по двум путям сразу, какое верное — не выбираем."""
    if paths is None:
        return None, None
    found = [(xpath.read(root, p), p) for p in ([paths] if isinstance(paths, str) else paths)]
    found = [(v, p) for v, p in found if v is not None]
    if len(found) > 1:
        raise xpath.AmbiguousField("значение указано в нескольких местах")
    return found[0] if found else (None, None)


def _layout(root: Element, spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str], str | None]:
    """Пути полей и списков для ветви документа. Возвращает поля, списки и признак ветви ``Передов``."""
    fields: dict[str, Any] = {key: field["path"] for key, field in spec["fields"].items()}
    lists = {key: item["path"] for key, item in spec["lists"].items()}
    transfer = spec["transfer"]
    if root.find(transfer["marker"]) is None:
        return fields, lists, None
    if root.find(spec["direct_marker"]) is not None:
        raise EmchdError("в документе одновременно ветви Довер и Передов")
    fields.update(transfer["fields"])
    lists.update(transfer["lists"])
    return fields, lists, transfer["marker"]


def parse_emchd(data: bytes | str, *, doc_id: str) -> PowerOfAttorney:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    sha = hashlib.sha256(raw).hexdigest()
    root = safe_fromstring(raw)
    spec = mapping()
    root_tag = spec["root"]
    if root.tag == "{" + spec["namespace"] + "}" + root_tag:
        _strip_namespace(root, spec["namespace"])
    if root.tag != root_tag:
        namespace = spec["namespace"]
        raise EmchdError(f"корневой элемент {root.tag!r}, ожидался {root_tag!r} в пространстве имён {namespace}")

    paths, list_paths, transfer = _layout(root, spec)
    values: dict[str, str | None] = {}
    refs: dict[str, EvidenceRef] = {}
    for key, field_paths in paths.items():
        try:
            value, used = _read_any(root, field_paths)
        except xpath.AmbiguousField as exc:
            raise EmchdError(f"поле {key}: {exc}") from exc
        values[key] = value
        if value is not None and used is not None:
            refs[key] = EvidenceRef(doc_id, sha, xpath.locator(root_tag, used), quote=value)
    if transfer and not values.get("parent_number"):
        # Передоверие первичной доверенности: номер родителя совпадает с номером первоначальной.
        values["parent_number"] = values.get("original_number")
        if "original_number" in refs:
            refs["parent_number"] = refs["original_number"]

    missing = [key for key in REQUIRED if not values.get(key)]
    if missing:
        raise EmchdError("нет обязательных полей: " + ", ".join(missing))

    gaps = [key for key in FORMAT_REQUIRED if not values.get(key)]
    for key, allowed in spec["enums"].items():
        if values.get(key) is not None and values[key] not in allowed:
            gaps.append(f"{key}.value")
    rep_type_path = spec["transfer"]["representative_type_path"] if transfer else spec["representative_type_path"]
    holder_path, rep_attr = rep_type_path.rsplit("/@", 1)
    for index, holder in enumerate(root.findall(holder_path), start=1):
        code = holder.get(rep_attr)
        if code not in spec["enums"]["representative_type"]:
            gaps.append(f"representative_type.{index}" + ("" if code is None else ".value"))
    subdelegator_kind = None
    if transfer:
        sub_spec = spec["transfer"]
        type_holder, type_attr = sub_spec["subdelegator_type_path"].rsplit("/@", 1)
        sub_type = root.findall(type_holder)
        sub_code = sub_type[0].get(type_attr) if len(sub_type) == 1 else None
        if sub_code not in spec["enums"]["subdelegator_type"]:
            gaps.append("subdelegator_type" + ("" if sub_code is None else ".value"))
        sub_nodes = root.findall(sub_spec["subdelegator_path"])
        subdelegator_kind = (
            _representative_kind(sub_nodes[0], sub_spec["subdelegator_kinds"]) if len(sub_nodes) == 1 else "unknown"
        )
    # Нотариальное удостоверение принимается только с реквизитами, обязательными по XSD.
    # Пустой элемент или элемент без реквизитов — пробел формата.
    notary_nodes = root.findall(spec["transfer"]["notarization_path"] if transfer else spec["notarization_path"])
    notarized = False
    if len(notary_nodes) > 1:
        raise EmchdError("сведения о нотариальном удостоверении указаны несколько раз")
    if notary_nodes:
        try:
            details = [xpath.read(notary_nodes[0], path) for path in spec["notarization_details"]]
        except xpath.AmbiguousField as exc:
            raise EmchdError(f"нотариальное удостоверение: {exc}") from exc
        notarized = all(details)
        if not notarized:
            gaps.append("notarization.details")
    policy = [key for key in POLICY_REQUIRED if not values.get(key)]
    questions: list[str] = []
    if not values.get("signer.position"):
        # По XSD должность подписанта необязательна в обеих ветвях. При передоверии подписывает представитель:
        # нужна ли тогда должность, решает юрист, до ответа МЧД уходит на разбор. В первичной МЧД без должности
        # не проверить, что подписал руководитель, поэтому подтверждения нет.
        if values.get("parent_number"):
            questions.append("signer.position")
        else:
            policy.append("signer.position")
    reps: list[Person] = []
    kinds: list[str] = []
    rep_spec = spec["lists"]["representatives"]
    for index, node in enumerate(root.findall(list_paths["representatives"]), start=1):
        kinds.append(_representative_kind(node, spec["representative_kinds"]))
        fields = {name: xpath.read(node, sub) for name, sub in rep_spec["fields"].items()}
        reps.append(Person(full_name=_fio(fields), inn=fields.get("inn"), snils=fields.get("snils")))
        if not fields.get("last_name") or not fields.get("first_name"):
            gaps.append(f"representative.{index}.fio")
        refs[f"representative.{index}"] = EvidenceRef(
            doc_id, sha, xpath.locator(root_tag, list_paths["representatives"], index), quote=_fio(fields)
        )

    authorities: list[Authority] = []
    machine = spec["lists"]["authorities_machine"]
    for index, node in enumerate(root.findall(list_paths["authorities_machine"]), start=1):
        fields = {name: xpath.read(node, sub) for name, sub in machine["fields"].items()}
        locator = xpath.locator(root_tag, list_paths["authorities_machine"], index)
        ref = EvidenceRef(doc_id, sha, locator, quote=fields.get("name"))
        authorities.append(
            Authority(
                kind="machine",
                code=fields.get("code"),
                mnemonic=fields.get("mnemonic"),
                name=fields.get("name"),
                ref=ref,
            )
        )
    text_path = list_paths["authorities_text"]
    for index, node in enumerate(root.findall(text_path), start=1):
        text = (node.text or "").strip()
        if text:
            ref = EvidenceRef(doc_id, sha, xpath.locator(root_tag, text_path, index), quote=text[:300])
            authorities.append(Authority(kind="text", text=text, ref=ref))

    sub_code = values.get("subdelegation")
    sub_value = spec["subdelegation_values"].get(sub_code or "", "unknown")
    loses = spec["loses_authority_values"].get(values.get("loses_authority") or "")
    joint = spec["joint_values"].get(values.get("joint") or "")
    signer = Person(
        full_name=_fio(
            {
                "last_name": values.get("signer.last_name"),
                "first_name": values.get("signer.first_name"),
                "middle_name": values.get("signer.middle_name"),
            }
        ),
        inn=values.get("signer.inn"),
        snils=values.get("signer.snils"),
        position=values.get("signer.position"),
    )
    refs.setdefault("document", EvidenceRef(doc_id, sha, f"/{root_tag}"))
    return PowerOfAttorney(
        doc_id=doc_id,
        sha256=sha,
        format_version=values.get("format_version"),
        number=normalize_poa_number(values.get("number")),
        internal_number=values.get("internal_number"),
        issue_date=_date(values.get("issue_date")),
        valid_until=_date(values.get("valid_until")),
        subdelegation=Subdelegation(sub_value),
        principal=Organization(
            inn=values.get("principal.inn"),
            ogrn=values.get("principal.ogrn"),
            kpp=values.get("principal.kpp"),
            name=values.get("principal.name"),
        ),
        signer=signer,
        representatives=tuple(reps),
        authorities=tuple(authorities),
        parent_number=normalize_poa_number(values.get("parent_number")),
        refs=refs,
        loses_authority=loses,
        joint=joint,
        original_number=normalize_poa_number(values.get("original_number")),
        irrevocable_subdelegation=values.get("irrevocable_subdelegation"),
        kind_code=values.get("kind"),
        format_gaps=tuple(gaps),
        format_questions=tuple(questions),
        representative_kinds=tuple(kinds),
        notarized=notarized,
        policy_gaps=tuple(policy),
        subdelegator_kind=subdelegator_kind,
    )


def _representative_kind(node: Element, kinds: dict[str, str]) -> str:
    """Вид представителя по элементу внутри ``Пред``; если элементов вида несколько или нет ни одного — ``unknown``."""
    found = {kinds[child.tag] for child in node if child.tag in kinds}
    return found.pop() if len(found) == 1 else "unknown"


def _split_fio(full_name: str | None) -> dict[str, str | None]:
    parts = (full_name or "").split()
    return {
        "last_name": parts[0] if len(parts) > 0 else None,
        "first_name": parts[1] if len(parts) > 1 else None,
        "middle_name": " ".join(parts[2:]) if len(parts) > 2 else None,
    }


def build_emchd_xml(
    *,
    number: str,
    issue_date: date,
    valid_until: date | None,
    principal: Organization,
    signer: Person,
    representatives: list[Person],
    machine_codes: list[tuple[str, str, str]] | None = None,
    text_authorities: list[str] | None = None,
    subdelegation_code: str = "1",
    parent_number: str | None = None,
    format_version: str = "EMCHD_1",
    internal_number: str | None = None,
    loses_authority_code: str | None = None,
    joint_code: str | None = "1",
    original_number: str | None = None,
    irrevocable_subdelegation_code: str | None = None,
    kind_code: str | None = "1",
) -> str:
    """Синтетическая МЧД по тому же файлу соответствия. Только для тестов и корпуса."""
    spec = mapping()
    root = Element(spec["root"])
    fields = spec["fields"]

    def put(key: str, value: str | None) -> None:
        xpath.write(root, fields[key]["path"], value)

    put("format_version", format_version)
    put("number", number)
    put("internal_number", internal_number)
    put("issue_date", issue_date.isoformat())
    put("valid_until", valid_until.isoformat() if valid_until else None)
    put("kind", kind_code)
    put("subdelegation", subdelegation_code)
    put("irrevocable_subdelegation", irrevocable_subdelegation_code)
    put("loses_authority", loses_authority_code)
    put("joint", joint_code)
    put("parent_number", parent_number)
    put("original_number", original_number)
    put("principal_type", "1")
    put("principal.name", principal.name)
    put("principal.inn", principal.inn)
    put("principal.kpp", principal.kpp)
    put("principal.ogrn", principal.ogrn)
    put("signer.inn", signer.inn)
    put("signer.snils", signer.snils)
    put("signer.position", signer.position)
    for key, value in _split_fio(signer.full_name).items():
        put(f"signer.{key}", value)

    rep_spec = spec["lists"]["representatives"]
    parent_path, leaf = rep_spec["path"].rsplit("/", 1)
    for person in representatives:
        holder = root
        for part in parent_path.split("/"):
            found = holder.find(part)
            holder = found if found is not None else _sub(holder, part)
        holder.set(spec["representative_type_path"].rsplit("@", 1)[1], "3")
        node = _sub(holder, leaf)
        values = {"inn": person.inn, "snils": person.snils, **_split_fio(person.full_name)}
        for name, sub in rep_spec["fields"].items():
            xpath.write(node, sub, values.get(name))

    for list_key, items in (
        ("authorities_machine", machine_codes or []),
        ("authorities_text", text_authorities or []),
    ):
        list_spec = spec["lists"][list_key]
        parent_path, leaf = list_spec["path"].rsplit("/", 1)
        for item in items:
            holder = root
            for part in parent_path.split("/"):
                found = holder.find(part)
                holder = found if found is not None else _sub(holder, part)
            node = _sub(holder, leaf)
            if list_key == "authorities_machine":
                code, mnemonic, name = cast(tuple[str, str, str], item)
                for fname, value in (("code", code), ("mnemonic", mnemonic), ("name", name or None)):
                    xpath.write(node, list_spec["fields"][fname], value)
            else:
                node.text = str(item)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode")


def _sub(parent: Element, tag: str) -> Element:
    from xml.etree.ElementTree import SubElement

    return SubElement(parent, tag)
