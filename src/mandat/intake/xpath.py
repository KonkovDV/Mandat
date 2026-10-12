"""Простые пути вида ``А/Б/@Атр`` для чтения и записи XML без зависимости от lxml.

Атрибут ищем сначала как атрибут, затем как дочерний элемент с тем же именем:
описания форматов ФНС не всегда однозначны в этом месте. ``.`` — текст узла.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement


def _split(path: str) -> tuple[list[str], str | None]:
    parts = [p for p in path.split("/") if p]
    attr = None
    if parts and parts[-1].startswith("@"):
        attr = parts.pop()[1:]
    elif parts and parts[-1] == ".":
        parts.pop()
        attr = "."
    return parts, attr


def _walk(node: Element, parts: list[str]) -> tuple[Element | None, list[int]]:
    indexes: list[int] = []
    for part in parts:
        found = node.find(part)
        if found is None:
            return None, indexes
        node = found
        indexes.append(1)
    return node, indexes


def read(node: Element, path: str) -> str | None:
    parts, attr = _split(path)
    target, _ = _walk(node, parts)
    if target is None:
        return None
    if attr is None or attr == ".":
        text = (target.text or "").strip()
        return text or None
    if attr in target.attrib:
        value = target.attrib[attr].strip()
        return value or None
    child = target.find(attr)
    if child is not None and (child.text or "").strip():
        return (child.text or "").strip()
    return None


def locator(root_tag: str, path: str, index: int | None = None, base: str | None = None) -> str:
    """Человекочитаемый XPath для ссылки на доказательство."""
    prefix = base or f"/{root_tag}"
    parts, attr = _split(path)
    body = "/".join(parts)
    if index is not None and body:
        body = f"{body}[{index}]"
    out = f"{prefix}/{body}" if body else prefix
    if attr and attr != ".":
        out += f"/@{attr}"
    return out


def write(node: Element, path: str, value: str | None) -> None:
    if value is None:
        return
    parts, attr = _split(path)
    for part in parts:
        child = node.find(part)
        node = child if child is not None else SubElement(node, part)
    if attr is None or attr == ".":
        node.text = value
    else:
        node.set(attr, value)
