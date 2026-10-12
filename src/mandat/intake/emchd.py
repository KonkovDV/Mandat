"""Разбор единой МЧД (формат 003, EMCHD_1) по файлу соответствия ``data/emchd_003_mapping.json``.

Код не знает имён тегов: они лежат в файле соответствия. Когда появится официальная
XSD, правится только JSON. Писатель ``build_emchd_xml`` использует тот же файл и
нужен синтетическому корпусу и тестам.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any
from xml.etree.ElementTree import Element, tostring

from mandat.data import load_json
from mandat.domain.model import Authority, EvidenceRef, Organization, Person, PowerOfAttorney, Subdelegation
from mandat.intake import xpath
from mandat.intake.xml_safety import safe_fromstring

REQUIRED = ("number", "issue_date", "principal.inn", "signer.inn")


class EmchdError(ValueError):
    """МЧД не разбирается или в ней нет обязательных полей."""


def mapping() -> dict[str, Any]:
    return load_json("emchd_003_mapping.json")


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        if "." in value:
            dd, mm, yyyy = value.split(".")
            return date(int(yyyy), int(mm), int(dd))
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise EmchdError(f"дата не распознана: {value!r}") from exc


def _fio(values: dict[str, str | None]) -> str | None:
    parts = [values.get("last_name"), values.get("first_name"), values.get("middle_name")]
    joined = " ".join(p for p in parts if p)
    return joined or None


def parse_emchd(data: bytes | str, *, doc_id: str) -> PowerOfAttorney:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    sha = hashlib.sha256(raw).hexdigest()
    root = safe_fromstring(raw)
    spec = mapping()
    root_tag = spec["root"]
    if root.tag != root_tag:
        raise EmchdError(f"корневой элемент {root.tag!r}, ожидался {root_tag!r}")

    values: dict[str, str | None] = {}
    refs: dict[str, EvidenceRef] = {}
    for key, field in spec["fields"].items():
        value = xpath.read(root, field["path"])
        values[key] = value
        if value is not None:
            refs[key] = EvidenceRef(doc_id, sha, xpath.locator(root_tag, field["path"]), quote=value)

    missing = [key for key in REQUIRED if not values.get(key)]
    if missing:
        raise EmchdError("нет обязательных полей: " + ", ".join(missing))

    reps: list[Person] = []
    rep_spec = spec["lists"]["representatives"]
    for index, node in enumerate(root.findall(rep_spec["path"]), start=1):
        fields = {name: xpath.read(node, sub) for name, sub in rep_spec["fields"].items()}
        reps.append(Person(full_name=_fio(fields), inn=fields.get("inn"), snils=fields.get("snils")))
        refs[f"representative.{index}"] = EvidenceRef(
            doc_id, sha, xpath.locator(root_tag, rep_spec["path"], index), quote=_fio(fields)
        )

    authorities: list[Authority] = []
    machine = spec["lists"]["authorities_machine"]
    for index, node in enumerate(root.findall(machine["path"]), start=1):
        fields = {name: xpath.read(node, sub) for name, sub in machine["fields"].items()}
        ref = EvidenceRef(doc_id, sha, xpath.locator(root_tag, machine["path"], index), quote=fields.get("name"))
        authorities.append(
            Authority(
                kind="machine",
                code=fields.get("code"),
                mnemonic=fields.get("mnemonic"),
                name=fields.get("name"),
                ref=ref,
            )
        )
    text_spec = spec["lists"]["authorities_text"]
    for index, node in enumerate(root.findall(text_spec["path"]), start=1):
        text = (node.text or "").strip()
        if text:
            ref = EvidenceRef(doc_id, sha, xpath.locator(root_tag, text_spec["path"], index), quote=text[:300])
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
        number=values.get("number"),
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
        parent_number=values.get("parent_number"),
        refs=refs,
        loses_authority=loses,
        joint=joint,
        original_number=values.get("original_number"),
        irrevocable_subdelegation=values.get("irrevocable_subdelegation"),
        kind_code=values.get("kind"),
    )


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
    kind_code: str | None = None,
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
                code, mnemonic, name = item
                for fname, value in (("code", code), ("mnemonic", mnemonic), ("name", name or None)):
                    xpath.write(node, list_spec["fields"][fname], value)
            else:
                node.text = str(item)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode")


def _sub(parent: Element, tag: str) -> Element:
    from xml.etree.ElementTree import SubElement

    return SubElement(parent, tag)
