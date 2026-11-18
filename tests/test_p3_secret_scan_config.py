"""P3-01: исключения gitleaks не прячут секрет, записанный рядом с безобидным совпадением.

Исключение с ``regexTarget = "line"`` снимает находку со всей строки: строка
``def f(key: Ed25519PrivateKey | Signer, token = "ghp_…")`` проходила проверку. Исключения по шаблону
действуют на совпадение или на найденное значение, исключения по пути — на один файл.
"""

from __future__ import annotations

import re
import tomllib

from conftest import ROOT

CONFIG = ROOT / ".gitleaks.toml"


def _allowlists() -> list[dict]:
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["extend"]["useDefault"] is True
    return config["allowlists"]


def test_every_exception_is_explained():
    for allowlist in _allowlists():
        assert allowlist.get("description", "").strip(), allowlist


def test_no_exception_covers_a_whole_line():
    for allowlist in _allowlists():
        assert allowlist.get("regexTarget", "secret") in {"secret", "match"}, allowlist["description"]


def test_annotation_exception_does_not_hide_a_token_on_the_same_line():
    (annotation,) = [a for a in _allowlists() if any("Ed25519" in r for r in a.get("regexes", []))]
    assert annotation["regexTarget"] == "match"
    line = 'def f(key: Ed25519PrivateKey | Signer, token = "ghp_' + "A" * 36 + '"):'
    token = re.search(r"ghp_[0-9A-Za-z]{36}", line)
    assert token is not None
    assert not any(re.search(pattern, token.group()) for pattern in annotation["regexes"])


def test_path_exceptions_name_single_files():
    for allowlist in _allowlists():
        for path in allowlist.get("paths", []):
            assert path.startswith("^") and path.endswith("$"), path
            assert not re.search(r"(?<!\\)[.*+?]|\[|\(", path.removeprefix("^").removesuffix("$")), path
