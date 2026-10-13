"""Shared helpers for the Mandat red-team harness (does not modify the repo)."""

from __future__ import annotations

import copy
import traceback
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any

from defusedxml.ElementTree import fromstring as safe_fromstring

from mandat.data import load_json
from mandat.eval import synth
from mandat.intake.case_loader import CaseError, load_case_inline
from mandat.services.verify import verify_case

MAP = load_json("emchd_003_mapping.json")
FIELDS = {k: v["path"] for k, v in MAP["fields"].items()}
GREEN = ["ok", "ok_subdelegation", "ok_director", "director_changed_long_ago", "long_chain"]
OP_DATE = synth.OP_DATE


def base(defect: str, seed: int = 1) -> dict[str, Any]:
    return copy.deepcopy(synth.build(defect, seed).inline_manifest())


def doc(m: dict, doc_id: str) -> dict | None:
    for d in m["documents"]:
        if d["id"] == doc_id:
            return d
    return None


def poa_ids(m: dict) -> list[str]:
    return [d["id"] for d in m["documents"] if d["kind"] == "emchd_xml"]


def leaf_id(m: dict) -> str | None:
    ids = poa_ids(m)
    if "poa-child" in ids:
        return "poa-child"
    return "poa-root" if "poa-root" in ids else None


def _split(path: str) -> tuple[str, str | None]:
    if "/@" in path:
        el, attr = path.rsplit("/@", 1)
        return el, attr
    if path.startswith("@"):
        return "", path[1:]
    return path, None


def xroot(d: dict) -> ET.Element:
    return safe_fromstring(d["content"].encode("utf-8"))


def xstore(d: dict, root: ET.Element) -> None:
    d["content"] = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def _find(root: ET.Element, el_path: str) -> ET.Element | None:
    return root if el_path == "" else root.find(el_path)


def xget(d: dict, key: str) -> str | None:
    root = xroot(d)
    el_path, attr = _split(FIELDS[key])
    el = _find(root, el_path)
    if el is None:
        return None
    return el.get(attr) if attr else el.text


def xset(d: dict, key: str, value: str | None) -> None:
    root = xroot(d)
    el_path, attr = _split(FIELDS[key])
    el = _find(root, el_path)
    if el is None:
        # create path
        el = root
        for part in el_path.split("/"):
            nxt = el.find(part)
            el = nxt if nxt is not None else ET.SubElement(el, part)
    if value is None:
        if attr and attr in el.attrib:
            del el.attrib[attr]
    else:
        el.set(attr, value)
    xstore(d, root)


def reps(d: dict) -> list[ET.Element]:
    return xroot(d).findall(MAP["lists"]["representatives"]["path"])


def egrul(m: dict) -> dict:
    return doc(m, "egrul")["content"]


def run(m: dict, profile: str = "demo") -> tuple[str, Any]:
    """Returns (verdict | 'CaseError' | 'CRASH:<exc>', report-or-traceback)."""
    try:
        case = load_case_inline(copy.deepcopy(m))
    except CaseError as exc:
        return "CaseError", str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"CRASH:{type(exc).__name__}", traceback.format_exc(limit=3)
    try:
        report = verify_case(case, profile=profile)
        report.as_dict()  # serialisation must not crash either
    except Exception as exc:  # noqa: BLE001
        return f"CRASH:{type(exc).__name__}", traceback.format_exc(limit=3)
    return report.verdict.value, report


def iso(d: date) -> str:
    return d.isoformat()


def days(n: int) -> timedelta:
    return timedelta(days=n)
