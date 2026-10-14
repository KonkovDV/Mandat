"""Свойства простых путей intake/xpath.py (Q-2): запись и чтение согласованы, неоднозначность не скрывается."""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mandat.intake import xpath

NAME = st.from_regex(r"[A-Za-zА-Яа-я][A-Za-zА-Яа-я0-9]{0,7}", fullmatch=True)
VALUE = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc")),
    min_size=1,
    max_size=40,
).filter(lambda s: s.strip() != "")
PARTS = st.lists(NAME, min_size=0, max_size=4)


@settings(max_examples=200, deadline=None)
@given(parts=PARTS, attr=NAME, value=VALUE)
def test_write_then_read_attribute(parts, attr, value):
    root = Element("Root")
    path = "/".join([*parts, f"@{attr}"])
    xpath.write(root, path, value)
    assert xpath.read(root, path) == value.strip()


@settings(max_examples=200, deadline=None)
@given(parts=st.lists(NAME, min_size=1, max_size=4), value=VALUE)
def test_write_then_read_text(parts, value):
    root = Element("Root")
    path = "/".join([*parts, "."])
    xpath.write(root, path, value)
    assert xpath.read(root, path) == value.strip()


@settings(max_examples=100, deadline=None)
@given(parts=PARTS, attr=NAME)
def test_missing_path_reads_none(parts, attr):
    assert xpath.read(Element("Root"), "/".join([*parts, f"@{attr}"])) is None


@settings(max_examples=100, deadline=None)
@given(name=NAME, count=st.integers(min_value=2, max_value=5))
def test_repeated_element_is_ambiguous(name, count):
    root = Element("Root")
    for _ in range(count):
        SubElement(root, name).text = "x"
    with pytest.raises(xpath.AmbiguousField):
        xpath.read(root, f"{name}/.")


@settings(max_examples=100, deadline=None)
@given(attr=NAME, a=VALUE, b=VALUE)
def test_attribute_and_child_is_ambiguous(attr, a, b):
    root = Element("Root", {attr: a})
    SubElement(root, attr).text = b
    with pytest.raises(xpath.AmbiguousField):
        xpath.read(root, f"@{attr}")


@settings(max_examples=100, deadline=None)
@given(parts=st.lists(NAME, min_size=1, max_size=4), attr=NAME)
def test_locator_shape(parts, attr):
    out = xpath.locator("Root", "/".join([*parts, f"@{attr}"]), index=1)
    assert out == "/Root/" + "/".join(parts) + f"[1]/@{attr}"


def test_write_none_is_noop():
    root = Element("Root")
    xpath.write(root, "A/@b", None)
    assert list(root) == [] and root.attrib == {}
