"""Mutation harness: start from green scenarios and apply single/combined mutations.

expect:
  NC      - by law / engine's own stated policy the result must NOT be "confirmed"
  NC-fmt  - a format-mandatory field is missing/invalid (lower severity if confirmed)
  ANY     - benign mutation; any verdict acceptable (used to find crashes / sanity)
"""

from __future__ import annotations

import copy
import json
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date, timedelta

from common import (
    GREEN,
    MAP,
    OP_DATE,
    base,
    doc,
    egrul,
    leaf_id,
    poa_ids,
    run,
    xget,
    xroot,
    xset,
    xstore,
)

from mandat.eval.synth import World

MUTS: list[tuple[str, str, callable, callable]] = []  # (name, expect, applies(base, m), fn(m, ctx))


def mut(name, expect, applies=lambda b, m: True):
    def deco(fn):
        MUTS.append((name, expect, applies, fn))
        return fn

    return deco


def is_chain(b, m):
    return "poa-child" in poa_ids(m)


def has_poa(b, m):
    return bool(poa_ids(m))


def is_dir(b, m):
    return not poa_ids(m)


# ---------------------------------------------------------------- A. attribute removal / emptying
REMOVE_EXPECT = {
    "number": "NC",
    "issue_date": "NC",
    "principal.inn": "NC",
    "signer.inn": "NC",
    "valid_until": "NC",
    "principal.kpp": "NC",
    "signer.snils": "NC",
    "format_version": "NC",
    "subdelegation": "NC-fmt",
    "principal.ogrn": "NC-fmt",
    "principal.name": "NC-fmt",
    "signer.position": "NC-fmt",
    "signer.last_name": "NC-fmt",
    "signer.first_name": "NC-fmt",
    "joint": "ANY",
    "loses_authority": "ANY",
    "internal_number": "ANY",
    "kind": "ANY",
    "irrevocable_subdelegation": "ANY",
    "original_number": "ANY",
    "parent_number": "ANY",
    "signer.middle_name": "ANY",
}
for _key, _exp in REMOVE_EXPECT.items():
    for _target in ("leaf", "root"):
        for _how in ("remove", "empty", "spaces"):

            def _f(m, ctx, key=_key, target=_target, how=_how):
                d = doc(m, leaf_id(m) if target == "leaf" else "poa-root")
                xset(d, key, None if how == "remove" else ("" if how == "empty" else "   "))

            exp = _exp
            # removing parent_number of a child turns it into a root signed by a non-director -> must not confirm
            if _key == "parent_number" and _target == "leaf":
                exp = "NC"
            mut(
                f"A.{_how}.{_target}.{_key}",
                exp,
                (lambda b, m, t=_target: has_poa(b, m) and (t == "leaf" or doc(m, "poa-root") is not None)),
            )(_f)


@mut("A.remove.leaf.rep.inn", "ANY", has_poa)
def _(m, ctx):
    d = doc(m, leaf_id(m))
    r = xroot(d)
    for n in r.findall(MAP["lists"]["representatives"]["path"] + "/СведФизЛ"):
        n.attrib.pop("ИННФЛ", None)
    xstore(d, r)


@mut("A.remove.leaf.rep.snils", "ANY", has_poa)
def _(m, ctx):
    d = doc(m, leaf_id(m))
    r = xroot(d)
    for n in r.findall(MAP["lists"]["representatives"]["path"] + "/СведФизЛ"):
        n.attrib.pop("СНИЛС", None)
    xstore(d, r)


@mut("A.remove.leaf.rep.fio", "NC-fmt", has_poa)
def _(m, ctx):
    d = doc(m, leaf_id(m))
    r = xroot(d)
    for n in r.findall(MAP["lists"]["representatives"]["path"] + "/СведФизЛ"):
        for c in list(n):
            n.remove(c)
    xstore(d, r)


@mut("A.remove.leaf.auth.name", "NC", has_poa)
def _(m, ctx):
    d = doc(m, leaf_id(m))
    r = xroot(d)
    for n in r.findall(MAP["lists"]["authorities_machine"]["path"]):
        n.attrib.pop("НаимПолн", None)
    xstore(d, r)


@mut("A.remove.leaf.auth.mnemonic", "ANY", has_poa)
def _(m, ctx):
    d = doc(m, leaf_id(m))
    r = xroot(d)
    for n in r.findall(MAP["lists"]["authorities_machine"]["path"]):
        n.attrib.pop("МнемКодПолн", None)
    xstore(d, r)


# ---------------------------------------------------------------- A2. duplicated / conflicting elements
@mut("A2.dup_SvDov_conflicting_number_first_wins", "NC", has_poa)
def _(m, ctx):
    """Second СвДов with different number/dates: XSD forbids; parser silently takes the first."""
    d = doc(m, leaf_id(m))
    r = xroot(d)
    dov = r.find("Документ/Довер")
    sv = dov.find("СвДов")
    clone = copy.deepcopy(sv)
    clone.set("НомДовер", "00000000-0000-0000-0000-000000000000")
    clone.set("СрокДейст", "2026-01-01")
    dov.insert(1, clone)
    xstore(d, r)


@mut("A2.dup_principal_conflicting_inn", "NC", has_poa)
def _(m, ctx):
    d = doc(m, leaf_id(m))
    r = xroot(d)
    ros = r.find("Документ/Довер/СвДоверит/Доверит/РосОргДовер")
    sv = ros.find("СвРосОрг")
    clone = copy.deepcopy(sv)
    clone.set("ИННЮЛ", "7700000013")
    ros.append(clone)
    xstore(d, r)


@mut("A2.attr_and_child_element_conflict", "NC", has_poa)
def _(m, ctx):
    """СрокДейст both as attribute (valid) and as child element (expired): attribute silently wins."""
    d = doc(m, leaf_id(m))
    r = xroot(d)
    sv = r.find("Документ/Довер/СвДов")
    ET.SubElement(sv, "СрокДейст").text = "2026-01-01"
    xstore(d, r)


@mut("A2.dup_root_doc_same_number_conflicting_subdeleg", "NC", is_chain)
def _(m, ctx):
    """Two root documents with the SAME number: one forbids subdelegation (ПрПередов=1), one allows (3)."""
    root = doc(m, "poa-root")
    clone = json.loads(json.dumps(root))
    clone["id"] = "poa-root-dup"
    xset(root, "subdelegation", "1")  # the first copy forbids subdelegation
    m["documents"].append(clone)  # the later copy (wins in by_number) allows it


@mut("A2.dup_root_doc_same_number_conflicting_subdeleg_order2", "NC", is_chain)
def _(m, ctx):
    root = doc(m, "poa-root")
    clone = json.loads(json.dumps(root))
    clone["id"] = "poa-root-dup"
    xset(clone, "subdelegation", "1")
    m["documents"].insert(0, clone)


@mut("A2.dup_root_doc_same_number_other_principal", "NC", is_chain)
def _(m, ctx):
    root = doc(m, "poa-root")
    clone = json.loads(json.dumps(root))
    clone["id"] = "poa-root-dup"
    xset(clone, "valid_until", "2026-04-15")
    m["documents"].insert(0, clone)


@mut("A2.duplicate_egrul_docs_last_wins_liquidated_first", "NC")
def _(m, ctx):
    e = doc(m, "egrul")
    first = json.loads(json.dumps(e))
    first["id"] = "egrul-0"
    first["content"]["status"] = "liquidated"
    m["documents"].insert(0, first)


@mut("A2.duplicate_charter_docs_last_wins", "NC")
def _(m, ctx):
    c = doc(m, "charter")
    first = json.loads(json.dumps(c))
    first["id"] = "charter-0"
    first["content"] += (
        " Сделки на сумму свыше 300 000 рублей совершаются генеральным директором только после "
        "одобрения общим собранием участников."
    )
    m["documents"].insert(0, first)


# ---------------------------------------------------------------- B. identity tricks
def _req(m):
    return m.setdefault("requester", {})


def _set_req_inn(f):
    def g(m, ctx):
        _req(m)["inn"] = f(_req(m)["inn"])

    return g


def _ws(s):
    return s[:4] + " " + s[4:]


FULLW = str.maketrans("0123456789", "０１２３４５６７８９")
ARAB = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
for _n, _f, _e in [
    ("nbsp_inside", _ws, "ANY"),
    ("leading_space", lambda s: " " + s, "ANY"),
    ("fullwidth", lambda s: s.translate(FULLW), "ANY"),
    ("arabic_indic", lambda s: s.translate(ARAB), "ANY"),
    ("leading_zero", lambda s: "0" + s, "ANY"),
    ("dash", lambda s: s[:4] + "-" + s[4:], "ANY"),
]:
    mut(f"B.requester_inn.{_n}", _e)(_set_req_inn(_f))


def _set_leaf_rep_attr(attr, f):
    def g(m, ctx):
        d = doc(m, leaf_id(m))
        r = xroot(d)
        for n in r.findall(MAP["lists"]["representatives"]["path"] + "/СведФизЛ"):
            if n.get(attr):
                n.set(attr, f(n.get(attr)))
        xstore(d, r)

    return g


for _n, _f in [
    ("fullwidth", lambda s: s.translate(FULLW)),
    ("arabic_indic", lambda s: s.translate(ARAB)),
    ("nbsp", _ws),
]:
    mut(f"B.leaf_rep_inn.{_n}", "ANY", has_poa)(_set_leaf_rep_attr("ИННФЛ", _f))
    mut(f"B.leaf_rep_snils.{_n}", "ANY", has_poa)(_set_leaf_rep_attr("СНИЛС", _f))


@mut("B.both_req_and_rep_inn_fullwidth", "NC-fmt", has_poa)
def _(m, ctx):
    _set_leaf_rep_attr("ИННФЛ", lambda s: s.translate(FULLW))(m, ctx)
    _req(m)["inn"] = _req(m)["inn"].translate(FULLW)
    # also remove SNILS so the INN path is used for matching
    _set_leaf_rep_attr("СНИЛС", lambda s: s)(m, ctx)


@mut("B.requester_inn_match_snils_of_other_person", "NC")
def _(m, ctx):
    _req(m)["snils"] = ctx["world"].stranger.snils


@mut("B.requester_inn_of_other_snils_match", "NC")
def _(m, ctx):
    _req(m)["inn"] = ctx["world"].stranger.inn


@mut("B.requester_snils_only", "ANY")
def _(m, ctx):
    _req(m).pop("inn", None)


@mut("B.requester_inn_only", "ANY")
def _(m, ctx):
    _req(m).pop("snils", None)


@mut("B.requester_name_differs", "ANY")
def _(m, ctx):
    _req(m)["full_name"] = "Совсем Другой Человек"


@mut("B.requester_snils_unformatted", "ANY")
def _(m, ctx):
    if _req(m).get("snils"):
        _req(m)["snils"] = "".join(c for c in _req(m)["snils"] if c.isdigit())


@mut("B.requester_empty", "NC")
def _(m, ctx):
    m["requester"] = {}


@mut("B.org_inn_fullwidth", "NC")
def _(m, ctx):
    m["organization"]["inn"] = m["organization"]["inn"].translate(FULLW)


@mut("B.org_ogrn_differs_from_egrul_and_poa", "NC")
def _(m, ctx):
    m["organization"].pop("ogrn", None)
    egrul(m)["ogrn"] = "1027700132195"  # valid checksum, other company


@mut("B.egrul_ogrn_differs", "NC")
def _(m, ctx):
    egrul(m)["ogrn"] = "1027700132195"


# ---------------------------------------------------------------- C. multiple representatives
def _add_rep(m, person, joint=None):
    d = doc(m, leaf_id(m))
    r = xroot(d)
    holder = r.find("Документ/Довер/СвУпПред")
    pred = ET.SubElement(holder, "Пред")
    sv = ET.SubElement(pred, "СведФизЛ")
    if person.inn:
        sv.set("ИННФЛ", person.inn)
    if person.snils:
        sv.set("СНИЛС", person.snils)
    fio = ET.SubElement(ET.SubElement(sv, "СведФЛ"), "ФИО")
    parts = (person.full_name or "А Б В").split()
    fio.set("Фамилия", parts[0])
    fio.set("Имя", parts[1])
    fio.set("Отчество", parts[2])
    xstore(d, r)
    if joint is not None:
        xset(d, "joint", joint)


@mut("C.second_rep_individual", "ANY", has_poa)
def _(m, ctx):
    _add_rep(m, ctx["world"].stranger, "1")


@mut("C.second_rep_joint_flag_missing", "NC", has_poa)
def _(m, ctx):
    _add_rep(m, ctx["world"].stranger, None)
    xset(doc(m, leaf_id(m)), "joint", None)


@mut("C.second_rep_joint_2", "NC", has_poa)
def _(m, ctx):
    _add_rep(m, ctx["world"].stranger, "2")


@mut("C.second_rep_joint_garbage", "NC", has_poa)
def _(m, ctx):
    _add_rep(m, ctx["world"].stranger, "X")


# ---------------------------------------------------------------- D. dates
def _set_op_date(m, d):
    m["operation"]["date"] = d.isoformat()


def _leaf_date(m, key):
    v = xget(doc(m, leaf_id(m)), key)
    return date.fromisoformat(v) if v else None


@mut("D.op_eq_leaf_issue", "ANY", has_poa)
def _(m, ctx):
    _set_op_date(m, _leaf_date(m, "issue_date"))


@mut("D.op_eq_leaf_valid_until", "ANY", has_poa)
def _(m, ctx):
    _set_op_date(m, _leaf_date(m, "valid_until"))


@mut("D.op_day_after_valid_until", "NC", has_poa)
def _(m, ctx):
    _set_op_date(m, _leaf_date(m, "valid_until") + timedelta(days=1))


@mut("D.op_day_before_issue", "NC", has_poa)
def _(m, ctx):
    _set_op_date(m, _leaf_date(m, "issue_date") - timedelta(days=1))


@mut("D.valid_until_before_issue", "NC", has_poa)
def _(m, ctx):
    d = doc(m, leaf_id(m))
    xset(d, "valid_until", (_leaf_date(m, "issue_date") - timedelta(days=1)).isoformat())


for _v, _e in [
    ("2026-02-30", "NC"),
    ("2026-13-01", "NC"),
    ("01.03.2026", "ANY"),
    ("2026-03-01T00:00:00", "ANY"),
    ("2026-03-01garbage", "NC-fmt"),
    ("20260301", "NC-fmt"),
    ("2026-W09-7", "NC-fmt"),
    ("1.3.2026", "NC-fmt"),
    ("", "NC"),
    ("29.02.2026", "NC"),
]:

    def _f(m, ctx, v=_v):
        xset(doc(m, "poa-root"), "issue_date", v)

    mut(f"D.root_issue_date={_v!r}", _e, lambda b, m: doc(m, "poa-root") is not None)(_f)

    def _g(m, ctx, v=_v):
        xset(doc(m, leaf_id(m)), "valid_until", v)

    mut(f"D.leaf_valid_until={_v!r}", "NC" if _e == "ANY" and False else ("ANY" if _e == "ANY" else _e), has_poa)(_g)


@mut("D.leap_issue_no_term_op_on_feb28", "NC", lambda b, m: doc(m, "poa-root") is not None and not is_chain(b, m))
def _(m, ctx):
    d = doc(m, "poa-root")
    xset(d, "issue_date", "2028-02-29")
    xset(d, "valid_until", None)
    _set_op_date(m, date(2029, 2, 28))
    egrul(m)["executives"][0]["valid_to"] = None


@mut("D.issue_year_9999_no_term", "NC", lambda b, m: doc(m, "poa-root") is not None)
def _(m, ctx):
    d = doc(m, "poa-root")
    xset(d, "issue_date", "9999-06-01")
    xset(d, "valid_until", None)


@mut("D.child_issued_before_parent", "NC", is_chain)
def _(m, ctx):
    root_issue = date.fromisoformat(xget(doc(m, "poa-root"), "issue_date"))
    xset(doc(m, "poa-child"), "issue_date", (root_issue - timedelta(days=20)).isoformat())
    # keep the root issue date (op date still inside all terms)


@mut("D.child_issued_before_parent_long_chain_mid", "NC", lambda b, m: doc(m, "poa-link1") is not None)
def _(m, ctx):
    root_issue = date.fromisoformat(xget(doc(m, "poa-root"), "issue_date"))
    xset(doc(m, "poa-link1"), "issue_date", (root_issue - timedelta(days=60)).isoformat())


@mut("D.op_far_future", "NC", has_poa)
def _(m, ctx):
    _set_op_date(m, date(2099, 1, 1))


# ---------------------------------------------------------------- E. subdelegation
for _v in ["0", "4", "01", "３", "three", "1 ", "2"]:

    def _f(m, ctx, v=_v):
        xset(doc(m, "poa-root"), "subdelegation", v)

    mut(f"E.root_subdeleg={_v!r}", "ANY" if _v in ("1 ", "2") else "NC", is_chain)(_f)
    # "1 " is stripped -> "1" -> forbids -> refuted; "2" once -> allowed for 2 links


@mut("E.child_signer_is_stranger", "NC", is_chain)
def _(m, ctx):
    s = ctx["world"].stranger
    d = doc(m, "poa-child")
    xset(d, "signer.inn", s.inn)
    xset(d, "signer.snils", s.snils)
    last, first, middle = s.full_name.split()
    xset(d, "signer.last_name", last)
    xset(d, "signer.first_name", first)
    xset(d, "signer.middle_name", middle)


@mut("E.child_signer_is_requester_self", "NC", is_chain)
def _(m, ctx):
    req = m["requester"]
    d = doc(m, "poa-child")
    xset(d, "signer.inn", req["inn"])
    xset(d, "signer.snils", req["snils"])


@mut("E.mid_link_signer_is_stranger", "NC", lambda b, m: doc(m, "poa-link1") is not None)
def _(m, ctx):
    s = ctx["world"].stranger
    d = doc(m, "poa-link1")
    xset(d, "signer.inn", s.inn)
    xset(d, "signer.snils", s.snils)


@mut("E.child_original_number_mismatch", "NC", is_chain)
def _(m, ctx):
    xset(doc(m, "poa-child"), "original_number", "11111111-2222-3333-4444-555555555555")


@mut("E.parent_self", "NC", is_chain)
def _(m, ctx):
    d = doc(m, "poa-child")
    xset(d, "parent_number", xget(d, "number"))


@mut("E.cycle_root_points_to_child", "NC", is_chain)
def _(m, ctx):
    xset(doc(m, "poa-root"), "parent_number", xget(doc(m, "poa-child"), "number"))


@mut("E.child_parent_number_uppercase", "NC", is_chain)
def _(m, ctx):
    d = doc(m, "poa-child")
    xset(d, "parent_number", xget(d, "parent_number").upper())


@mut("E.child_principal_other_org", "NC", is_chain)
def _(m, ctx):
    xset(doc(m, "poa-child"), "principal.inn", "7700000013")


@mut("E.second_root_with_requester_issued_later_revoked", "ANY", lambda b, m: has_poa(b, m))
def _(m, ctx):
    pass


# ---------------------------------------------------------------- F. EGRUL
def _dir_rec(m):
    return egrul(m)["executives"][0]


@mut("F.dir_valid_from_removed", "ANY")
def _(m, ctx):
    _dir_rec(m)["valid_from"] = None


@mut("F.dir_valid_from_removed_record_date_after_issue", "NC")
def _(m, ctx):
    rec = _dir_rec(m)
    rec["valid_from"] = None
    rec["record_date"] = (OP_DATE + timedelta(days=5)).isoformat()  # appointed AFTER both POA issue and op date


@mut("F.dir_all_dates_removed", "NC")
def _(m, ctx):
    rec = _dir_rec(m)
    rec["valid_from"] = None
    rec["record_date"] = None
    rec["valid_to"] = None
    for r in egrul(m)["executives"][1:]:
        r["valid_from"] = None
        r["record_date"] = None


@mut("F.dir_valid_from_after_root_issue", "NC")
def _(m, ctx):
    _dir_rec(m)["valid_from"] = "2026-03-02"
    _dir_rec(m)["record_date"] = "2026-03-02"


@mut("F.overlapping_second_executive", "ANY")
def _(m, ctx):
    w = ctx["world"]
    egrul(m)["executives"].append(
        {
            "full_name": w.stranger.full_name,
            "inn": w.stranger.inn,
            "grn": "2267700000001",
            "record_date": "2024-05-01",
            "valid_from": "2024-05-01",
            "valid_to": None,
        }
    )


@mut("F.fresh_successor_old_record_not_closed", "NC")
def _(m, ctx):
    w = ctx["world"]
    for r in egrul(m)["executives"]:
        r["valid_to"] = None
    egrul(m)["executives"].append(
        {
            "full_name": w.clerk2.full_name,
            "inn": w.clerk2.inn,
            "grn": "2267700999999",
            "record_date": (OP_DATE - timedelta(days=3)).isoformat(),
            "valid_from": (OP_DATE - timedelta(days=3)).isoformat(),
            "valid_to": None,
        }
    )


@mut("F.status_missing", "NC")
def _(m, ctx):
    egrul(m).pop("status", None)


for _s, _e in [
    ("in_liquidation", "NC"),
    ("liquidated", "NC"),
    ("LIQUIDATED", "NC"),
    ("Active", "NC"),
    ("reorganizing", "NC"),
    ("", "NC"),
]:

    def _f(m, ctx, s=_s):
        egrul(m)["status"] = s

    mut(f"F.status={_s!r}", _e)(_f)


for _u in [True, 1, "true", "false", "yes", [1]]:

    def _f(m, ctx, u=_u):
        _dir_rec(m)["unreliable"] = u

    mut(f"F.unreliable={_u!r}", "NC" if _u not in ("false",) else "ANY")(_f)


@mut("F.extracted_at_old_2024", "NC")
def _(m, ctx):
    egrul(m)["extracted_at"] = "2024-06-01"


@mut("F.extracted_at_missing", "NC")
def _(m, ctx):
    egrul(m).pop("extracted_at", None)


@mut("F.extracted_at_after_op_far", "ANY")
def _(m, ctx):
    egrul(m)["extracted_at"] = "2030-01-01"


@mut("F.egrul_inn_missing", "NC")
def _(m, ctx):
    egrul(m).pop("inn", None)


@mut("F.executives_empty", "NC")
def _(m, ctx):
    egrul(m)["executives"] = []


@mut("F.exec_inn_whitespace", "NC")
def _(m, ctx):
    _dir_rec(m)["inn"] = " " + _dir_rec(m)["inn"]


@mut("F.exec_inn_none_snils_match", "ANY")
def _(m, ctx):
    rec = _dir_rec(m)
    rec["snils"] = ctx["world"].director.snils
    rec["inn"] = None


@mut("F.dir_valid_only_on_op_date", "ANY")
def _(m, ctx):
    rec = _dir_rec(m)
    rec["valid_from"] = rec["valid_to"] = rec["record_date"] = OP_DATE.isoformat()


@mut("F.dir_valid_to_eq_op", "ANY")
def _(m, ctx):
    _dir_rec(m)["valid_to"] = OP_DATE.isoformat()


@mut("F.dir_valid_to_day_before_op_no_successor", "ANY")
def _(m, ctx):
    _dir_rec(m)["valid_to"] = (OP_DATE - timedelta(days=1)).isoformat()


# crash-oriented EGRUL mutations (any verdict is fine, crash is not)
for _n, _fn in [
    ("executives_str", lambda e: e.__setitem__("executives", "x")),
    ("executives_list_of_str", lambda e: e.__setitem__("executives", ["x"])),
    ("executives_dict", lambda e: e.__setitem__("executives", {"a": 1})),
    ("executives_int", lambda e: e.__setitem__("executives", 5)),
    ("exec_inn_int", lambda e: e["executives"][0].__setitem__("inn", int(e["executives"][0]["inn"]))),
    ("org_inn_int", lambda e: e.__setitem__("inn", int(e["inn"]))),
    ("full_name_int", lambda e: e.__setitem__("full_name", 123)),
    ("full_name_list", lambda e: e.__setitem__("full_name", ["x"])),
    ("status_null", lambda e: e.__setitem__("status", None)),
    ("valid_from_int", lambda e: e["executives"][0].__setitem__("valid_from", 20240501)),
    ("valid_from_bad", lambda e: e["executives"][0].__setitem__("valid_from", "2024-13-01")),
    ("exec_full_name_int", lambda e: e["executives"][0].__setitem__("full_name", 5)),
    ("grn_dict", lambda e: e["executives"][0].__setitem__("grn", {"x": 1})),
]:

    def _f(m, ctx, fn=_fn):
        fn(egrul(m))

    mut(f"F.crash.{_n}", "ANY")(_f)


@mut("F.crash.egrul_top_list", "ANY")
def _(m, ctx):
    doc(m, "egrul")["content"] = [1, 2]


@mut("F.crash.egrul_content_int", "ANY")
def _(m, ctx):
    doc(m, "egrul")["content"] = 5


@mut("F.crash.egrul_content_bad_json_str", "ANY")
def _(m, ctx):
    doc(m, "egrul")["content"] = "{not json"


# ---------------------------------------------------------------- G. operation / manifest
for _c, _e in [
    ("payment_sign", "NC"),
    ("PAYMENT_SIGN ", "NC"),
    ("UNKNOWN_OP", "NC"),
    ("USER_ADMIN", "ANY"),
    ("REQUISITES_CHANGE", "ANY"),
]:

    def _f(m, ctx, c=_c):
        m["operation"]["code"] = c

    mut(f"G.op_code={_c!r}", _e)(_f)


for _a, _e in [(None, "ANY"), (-5, "NC-fmt"), (0, "ANY"), (10**15, "ANY"), ("480000", "ANY"), (1.9, "NC-fmt")]:

    def _f(m, ctx, a=_a):
        if a is None:
            m["operation"].pop("amount_rub", None)
        else:
            m["operation"]["amount_rub"] = a

    mut(f"G.amount={_a!r}", _e)(_f)


for _n, _fn, _e in [
    ("org_inn_missing", lambda m: m["organization"].pop("inn"), "NC"),
    ("org_missing", lambda m: m.pop("organization"), "NC"),
    ("registry_ACTIVE_upper", lambda m: m.__setitem__("registry", {k: "ACTIVE" for k in m.get("registry", {})}), "NC"),
    ("registry_Active", lambda m: m.__setitem__("registry", {k: "Active" for k in m.get("registry", {})}), "NC"),
    (
        "registry_keys_upper",
        lambda m: m.__setitem__("registry", {k.upper(): v for k, v in m.get("registry", {}).items()}),
        "NC",
    ),
    ("registry_value_true", lambda m: m.__setitem__("registry", {k: True for k in m.get("registry", {})}), "NC"),
    ("registry_empty", lambda m: m.__setitem__("registry", {}), "NC"),
    ("doc_unknown_kind", lambda m: m["documents"].append({"id": "x", "kind": "pdf", "content": "x"}), "NC"),
    ("crash.org_list", lambda m: m.__setitem__("organization", ["x"]), "ANY"),
    ("crash.requester_list", lambda m: m.__setitem__("requester", ["x"]), "ANY"),
    ("crash.requester_str", lambda m: m.__setitem__("requester", "x"), "ANY"),
    (
        "crash.requester_inn_int",
        lambda m: m["requester"].__setitem__("inn", int(m["requester"]["inn"]) if m["requester"].get("inn") else 1),
        "ANY",
    ),
    ("crash.requester_snils_int", lambda m: m["requester"].__setitem__("snils", 12345678901), "ANY"),
    ("crash.requester_inn_list", lambda m: m["requester"].__setitem__("inn", ["1"]), "ANY"),
    ("crash.org_inn_int", lambda m: m["organization"].__setitem__("inn", int(m["organization"]["inn"])), "ANY"),
    ("crash.org_name_int", lambda m: m["organization"].__setitem__("name", 5), "ANY"),
    ("crash.documents_dict", lambda m: m.__setitem__("documents", {"a": 1}), "ANY"),
    ("crash.documents_str_items", lambda m: m.__setitem__("documents", ["x"]), "ANY"),
    ("crash.doc_content_int", lambda m: m["documents"][0].__setitem__("content", 5), "ANY"),
    ("crash.doc_content_none", lambda m: m["documents"][0].__setitem__("content", None), "ANY"),
    ("crash.registry_list", lambda m: m.__setitem__("registry", ["x"]), "ANY"),
    ("crash.operation_str", lambda m: m.__setitem__("operation", "x"), "ANY"),
    ("crash.amount_inf", lambda m: m["operation"].__setitem__("amount_rub", float("inf")), "ANY"),
    ("crash.amount_nan", lambda m: m["operation"].__setitem__("amount_rub", float("nan")), "ANY"),
    ("crash.op_date_int", lambda m: m["operation"].__setitem__("date", 20261020), "ANY"),
    ("crash.op_code_dict", lambda m: m["operation"].__setitem__("code", {"a": 1}), "ANY"),
    ("crash.case_id_dict", lambda m: m.__setitem__("case_id", {"a": 1}), "ANY"),
    ("crash.counterparty_list", lambda m: m["operation"].__setitem__("counterparty_inn", [1]), "ANY"),
    ("crash.op_date_year_9999", lambda m: m["operation"].__setitem__("date", "9999-12-31"), "ANY"),
    ("crash.op_date_year_1", lambda m: m["operation"].__setitem__("date", "0001-01-01"), "ANY"),
]:

    def _f(m, ctx, fn=_fn):
        fn(m)

    mut(f"G.{_n}", _e)(_f)


# ---------------------------------------------------------------- H. authority codes
def _codes(m, f):
    d = doc(m, leaf_id(m))
    r = xroot(d)
    for n in r.findall(MAP["lists"]["authorities_machine"]["path"]):
        f(n)
    xstore(d, r)


for _n, _fn, _e in [
    ("lower", lambda n: n.set("КодПолн", n.get("КодПолн").lower()), "NC"),
    ("inner_space", lambda n: n.set("КодПолн", n.get("КодПолн").replace("-", " - ")), "NC"),
    ("outer_space", lambda n: n.set("КодПолн", " " + n.get("КодПолн") + " "), "ANY"),
    ("homoglyph_cyr_A", lambda n: n.set("КодПолн", n.get("КодПолн").replace("A", "А")), "NC"),
    ("code_as_child_elem", lambda n: ET.SubElement(n, "КодПолн").__setattr__("text", n.attrib.pop("КодПолн")), "ANY"),
    ("code_only_in_mnemonic", lambda n: n.attrib.pop("КодПолн"), "NC"),
    ("revoked_code", lambda n: n.set("КодПолн", "SYN-BANK-PAY-SIGN-OLD") if "PAY" in n.get("КодПолн") else None, "NC"),
    (
        "account_dispose_code",
        lambda n: n.set("КодПолн", "SYN-BANK-ACCOUNT-DISPOSE") if "PAY" in n.get("КодПолн") else None,
        "ANY",
    ),
]:

    def _f(m, ctx, fn=_fn):
        _codes(m, fn)

    mut(f"H.auth_code.{_n}", _e, has_poa)(_f)


@mut("H.text_authority_mixed_with_machine", "NC", has_poa)
def _(m, ctx):
    d = doc(m, leaf_id(m))
    r = xroot(d)
    ET.SubElement(
        r.find("Документ/Довер/СвПолн"), "ТекстПолн"
    ).text = "Только просмотр выписок; подписывать платежи запрещено"
    xstore(d, r)


@mut("H.paper_poa_added_alongside", "NC", has_poa)
def _(m, ctx):
    m["documents"].append({"id": "paper", "kind": "paper_poa_text", "content": "Доверенность отменена 01.10.2026"})


# ------------------------------------------------ I. charter thresholds (amount 480 000 > threshold 300 000)
CHARTER_VARIANTS = {
    "baseline_rublej": "Сделки на сумму свыше 300 000 рублей "
    "совершаются генеральным директором только после одобрения общим собранием участников.",
    "rub_dot_abbrev": "Сделки на сумму свыше 300 000 руб. "
    "совершаются генеральным директором только после одобрения общим собранием участников.",
    "tys_rub": "Сделки на сумму свыше 300 тыс. рублей требуют одобрения общего собрания участников.",
    "mln": "Сделки на сумму свыше 0,3 млн рублей требуют одобрения общего собрания участников.",
    "dots_thousands": "Сделки на сумму свыше 300.000 рублей требуют одобрения общего собрания участников.",
    "comma_thousands": "Сделки на сумму свыше 300,000 рублей требуют одобрения общего собрания участников.",
    "kopecks": "Сделки на сумму свыше 300 000,00 рублей требуют одобрения общего собрания участников.",
    "words": "Сделки на сумму более трехсот тысяч рублей требуют одобрения общего собрания участников.",
    "and_more": "Сделки на сумму 300 000 рублей и более требуют одобрения общего собрания участников.",
    "soglasie": "Сделки на сумму свыше 300 000 рублей совершаются только с согласия общего собрания участников.",
    "sole_member": "Сделки на сумму свыше 300 000 рублей требуют решения единственного участника общества.",
    "RUB": "Сделки на сумму свыше 300 000 RUB требуют одобрения общего собрания участников.",
    "rubley_genitive_r": "Сделки на сумму свыше 300 000 (трехсот тысяч) рублей "
    "требуют одобрения общего собрания участников.",
    "two_sentences": "Сделки на сумму свыше 300 000 рублей. Такие сделки требуют одобрения общего собрания участников.",
    "semicolon": "Генеральный директор совершает сделки на сумму свыше 300 000 рублей; "
    "на это требуется одобрение общего собрания.",
    "joint_sovmestno_s": "Платёжные документы генеральный директор подписывает совместно с главным бухгалтером.",
    "joint_vtoraya_podpis": "Для платёжных документов обязательна вторая подпись главного бухгалтера.",
    "nbsp": "Сделки на сумму свыше 300 000 рублей требуют одобрения общего собрания участников.",
    "narrow_nbsp_rub_sign": "Сделки на сумму свыше 300 000 ₽ требуют одобрения общего собрания участников.",
}
for _n, _t in CHARTER_VARIANTS.items():

    def _f(m, ctx, t=_t):
        doc(m, "charter")["content"] += " " + t

    mut(f"I.charter.{_n}", "NC")(_f)


# ---------------------------------------------------------------- K. leaf choice
def _poa_xml(world, number, issue, until, reps, signer, codes=True, sub="1", principal=None, parent=None):
    from mandat.domain.model import Organization
    from mandat.eval.synth import PAY, VIEW
    from mandat.intake.emchd import build_emchd_xml

    p = principal or Organization(inn=world.org.inn, ogrn=world.org.ogrn, kpp=world.org.kpp, name=world.org_full_name)
    return build_emchd_xml(
        number=number,
        issue_date=issue,
        valid_until=until,
        principal=p,
        signer=signer,
        representatives=reps,
        machine_codes=[PAY, VIEW] if codes else [],
        subdelegation_code=sub,
        parent_number=parent,
    )


def _req_person(m, world):
    for p in (world.accountant, world.deputy, world.director, world.stranger, world.clerk1, world.clerk2):
        if p.inn == m["requester"].get("inn"):
            return p
    return world.accountant


@mut("K.extra_older_leaf_revoked", "ANY", has_poa)
def _(m, ctx):
    w = ctx["world"]
    num = "aaaaaaaa-0000-0000-0000-000000000001"
    m["documents"].append(
        {
            "id": "extra",
            "kind": "emchd_xml",
            "content": _poa_xml(w, num, date(2025, 1, 1), date(2027, 1, 1), [_req_person(m, w)], w.director),
        }
    )
    m["registry"][num] = "revoked"


@mut("K.extra_newer_leaf_revoked", "NC", has_poa)
def _(m, ctx):
    w = ctx["world"]
    num = "aaaaaaaa-0000-0000-0000-000000000002"
    m["documents"].append(
        {
            "id": "extra",
            "kind": "emchd_xml",
            "content": _poa_xml(w, num, date(2026, 9, 1), date(2027, 1, 1), [_req_person(m, w)], w.director),
        }
    )
    m["registry"][num] = "revoked"


@mut("K.extra_same_issue_date_leaf_revoked_inserted_first", "NC", has_poa)
def _(m, ctx):
    """Two leaves with the SAME issue date; the revoked one comes first in the package."""
    w = ctx["world"]
    num = "aaaaaaaa-0000-0000-0000-000000000003"
    issue = _leaf_date(m, "issue_date")
    m["documents"].insert(
        0,
        {
            "id": "extra",
            "kind": "emchd_xml",
            "content": _poa_xml(w, num, issue, date(2027, 1, 1), [_req_person(m, w)], w.director),
        },
    )
    m["registry"][num] = "revoked"


@mut("K.extra_same_issue_date_leaf_revoked_inserted_last", "NC", has_poa)
def _(m, ctx):
    w = ctx["world"]
    num = "aaaaaaaa-0000-0000-0000-000000000004"
    issue = _leaf_date(m, "issue_date")
    m["documents"].append(
        {
            "id": "extra",
            "kind": "emchd_xml",
            "content": _poa_xml(w, num, issue, date(2027, 1, 1), [_req_person(m, w)], w.director),
        }
    )
    m["registry"][num] = "revoked"


@mut(
    "K.transfer_with_loss_child_parent_uppercase",
    "NC",
    lambda b, m: doc(m, "poa-root") is not None and not is_chain(b, m),
)
def _(m, ctx):
    """Requester (rep of root) transferred powers with loss (ПрУтрПолн=2); child's parent number uses uppercase GUID."""
    w = ctx["world"]
    root_num = xget(doc(m, "poa-root"), "number")
    num = "bbbbbbbb-0000-0000-0000-000000000001"
    from mandat.domain.model import Organization
    from mandat.eval.synth import PAY, VIEW
    from mandat.intake.emchd import build_emchd_xml

    p = Organization(inn=w.org.inn, ogrn=w.org.ogrn, kpp=w.org.kpp, name=w.org_full_name)
    xml = build_emchd_xml(
        number=num,
        issue_date=date(2026, 4, 1),
        valid_until=date(2027, 1, 1),
        principal=p,
        signer=_req_person(m, w),
        representatives=[w.clerk1],
        machine_codes=[PAY, VIEW],
        subdelegation_code="1",
        parent_number=root_num.upper(),
        loses_authority_code="2",
    )
    m["documents"].append({"id": "transfer", "kind": "emchd_xml", "content": xml})
    m["registry"][num] = "active"


# ---------------------------------------------------------------- L. director (eio_direct) path specifics
@mut("L.fresh_director_takeover", "NC", is_dir)
def _(m, ctx):
    """Requester became director 2 days before the operation (classic takeover pattern)."""
    w = ctx["world"]
    e = egrul(m)
    e["executives"][0]["valid_to"] = (OP_DATE - timedelta(days=3)).isoformat()
    e["executives"].append(
        {
            "full_name": w.stranger.full_name,
            "inn": w.stranger.inn,
            "grn": "2267700777777",
            "record_date": (OP_DATE - timedelta(days=2)).isoformat(),
            "valid_from": (OP_DATE - timedelta(days=2)).isoformat(),
            "valid_to": None,
        }
    )
    m["requester"] = {"full_name": w.stranger.full_name, "inn": w.stranger.inn, "snils": w.stranger.snils}


@mut("L.fresh_director_takeover_requisites_change", "NC", is_dir)
def _(m, ctx):
    MUTS_BY_NAME["L.fresh_director_takeover"](m, ctx)
    m["operation"]["code"] = "REQUISITES_CHANGE"


@mut("L.fresh_director_takeover_user_admin", "NC", is_dir)
def _(m, ctx):
    MUTS_BY_NAME["L.fresh_director_takeover"](m, ctx)
    m["operation"]["code"] = "USER_ADMIN"


MUTS_BY_NAME = {n: f for n, _, _, f in MUTS}


# ---------------------------------------------------------------- combined mutations
COMBOS = [
    ("B.requester_inn_match_snils_of_other_person", "F.extracted_at_old_2024"),
    ("F.status_missing", "F.extracted_at_missing"),
    ("E.child_signer_is_stranger", "E.child_original_number_mismatch"),
    ("E.child_signer_is_stranger", "D.child_issued_before_parent"),
    ("F.dir_all_dates_removed", "F.extracted_at_missing"),
    ("C.second_rep_individual", "B.requester_inn_match_snils_of_other_person"),
    ("I.charter.rub_dot_abbrev", "G.op_code='USER_ADMIN'"),
    ("A.remove.leaf.principal.ogrn", "A.remove.leaf.principal.name"),
    ("A2.duplicate_egrul_docs_last_wins_liquidated_first", "F.dir_valid_from_removed"),
]


def main(seeds=(1, 2, 3), profiles=("demo", "strict")):
    results = []
    t0 = time.time()
    worlds = {s: World.make(s) for s in seeds}
    plan = []
    for b in GREEN:
        for s in seeds:
            m0 = base(b, s)
            for name, expect, applies, _fn in MUTS:
                if applies(b, m0):
                    plan.append((b, s, (name,), expect))
            for combo in COMBOS:
                exps = [e for n, e, _, _ in MUTS if n in combo]
                if len(exps) == len(combo) and all(dict((n, a) for n, _, a, _ in MUTS)[n](b, m0) for n in combo):
                    exp = "NC" if "NC" in exps else ("NC-fmt" if "NC-fmt" in exps else "ANY")
                    plan.append((b, s, combo, exp))
    for b, s, names, expect in plan:
        m = base(b, s)
        ctx = {"world": worlds[s], "base": b}
        err = None
        try:
            for n in names:
                MUTS_BY_NAME[n](m, ctx)
        except Exception as exc:  # mutation not applicable to this base
            err = f"{type(exc).__name__}: {exc}"
        for p in profiles:
            if err:
                results.append(
                    {
                        "base": b,
                        "seed": s,
                        "mut": "+".join(names),
                        "expect": expect,
                        "profile": p,
                        "verdict": "SKIP",
                        "detail": err,
                    }
                )
                continue
            t = time.time()
            v, rep = run(m, p)
            dt = time.time() - t
            row = {
                "base": b,
                "seed": s,
                "mut": "+".join(names),
                "expect": expect,
                "profile": p,
                "verdict": v,
                "secs": round(dt, 3),
            }
            if v.startswith("CRASH"):
                row["detail"] = rep
            elif v not in ("CaseError",):
                row["checks"] = rep.checks
                row["chain"] = rep.chain.kind
            else:
                row["detail"] = rep
            results.append(row)
    out = {"elapsed": round(time.time() - t0, 1), "results": results}
    with open("results_mutations.json", "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, default=str)
    # summary
    tried = [r for r in results if r["verdict"] != "SKIP"]
    fc = [r for r in tried if r["verdict"] == "confirmed" and r["expect"] == "NC"]
    fcf = [r for r in tried if r["verdict"] == "confirmed" and r["expect"] == "NC-fmt"]
    crashes = [r for r in tried if r["verdict"].startswith("CRASH")]
    print(
        f"distinct mutations: {len(MUTS)} + {len(COMBOS)} combos; executions: {len(tried)}; "
        f"skipped: {len(results) - len(tried)}; elapsed {out['elapsed']}s"
    )
    print(f"FALSE CONFIRMED (NC): {len(fc)} executions; NC-fmt confirmed: {len(fcf)}; crashes: {len(crashes)}")
    agg = defaultdict(set)
    for r in fc:
        agg[r["mut"]].add(f"{r['base']}/{r['profile']}")
    print("\n== false-confirmed mutations (NC) ==")
    for k in sorted(agg):
        print(f"  {k}: {sorted(agg[k])}")
    agg = defaultdict(set)
    for r in fcf:
        agg[r["mut"]].add(f"{r['base']}/{r['profile']}")
    print("\n== confirmed with format-mandatory defect (NC-fmt) ==")
    for k in sorted(agg):
        print(f"  {k}: {sorted(agg[k])}")
    agg = defaultdict(set)
    for r in crashes:
        agg[(r["mut"], r["verdict"])].add(r["base"])
    print("\n== crashes ==")
    for k in sorted(agg):
        print(f"  {k}: {sorted(agg[k])}")
    slow = sorted((r for r in tried if r.get("secs", 0) > 1), key=lambda r: -r["secs"])[:10]
    print("\n== slowest ==", [(r["mut"], r["secs"]) for r in slow])
    print("\nverdict distribution:", Counter(r["verdict"] for r in tried))


if __name__ == "__main__":
    main()
