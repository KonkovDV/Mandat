"""Разбор недоверенного XML: defusedxml плюс лимиты размера, числа элементов и глубины.

Перенесено из AeroBIM (``core/security/xml_limits.py``): XML-бомбы и внешние сущности
отклоняются до разбора полномочий.
"""

from __future__ import annotations

from typing import Any
from xml.etree.ElementTree import Element, ParseError

MAX_XML_BYTES = 2 * 1024 * 1024
MAX_ELEMENTS = 20_000
MAX_DEPTH = 48
MAX_TEXT_CHARS = 200_000


class XmlRejected(ValueError):
    """XML не прошёл проверки безопасности или не разбирается."""


def _defused() -> tuple[Any, type[Exception]]:
    from defusedxml import ElementTree as DefusedET
    from defusedxml.common import DefusedXmlException

    return DefusedET, DefusedXmlException


def _depth(root: Element) -> int:
    best = 1
    stack: list[tuple[Element, int]] = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        best = max(best, depth)
        stack.extend((child, depth + 1) for child in list(node))
    return best


def safe_fromstring(data: bytes | str) -> Element:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > MAX_XML_BYTES:
        raise XmlRejected(f"XML больше допустимого ({len(raw)} > {MAX_XML_BYTES} байт)")
    defused_et, defused_error = _defused()
    try:
        root = defused_et.fromstring(raw, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except defused_error as exc:
        raise XmlRejected(f"XML отклонён защитой: {type(exc).__name__}") from exc
    except ParseError as exc:
        raise XmlRejected(f"XML не разбирается: {exc}") from exc
    count = sum(1 for _ in root.iter())
    if count > MAX_ELEMENTS:
        raise XmlRejected(f"слишком много элементов ({count} > {MAX_ELEMENTS})")
    if _depth(root) > MAX_DEPTH:
        raise XmlRejected(f"слишком глубокая вложенность (> {MAX_DEPTH})")
    for node in root.iter():
        for chunk in (node.text, node.tail):
            if chunk is not None and len(chunk) > MAX_TEXT_CHARS:
                raise XmlRejected("слишком длинный текстовый узел")
    return root
