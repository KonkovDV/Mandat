"""evidence.json не собирается, пока в каталогах исходников есть файлы вне git: иначе CI его отвергнет."""

from __future__ import annotations

import subprocess
import sys

import pytest
from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import build_evidence  # noqa: E402


def _git(cwd, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_untracked_source_files_are_listed_and_ignored_ones_are_not(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    for folder in ("src", "tests", "docs"):
        (tmp_path / folder).mkdir()
    (tmp_path / "src" / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "src/tracked.py")
    (tmp_path / "tests" / "test_new.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    (tmp_path / "tests" / "cache.pyc").write_bytes(b"\0")
    (tmp_path / "docs" / "note.md").write_text("вне каталогов исходников\n", encoding="utf-8")
    monkeypatch.setattr(build_evidence, "ROOT", tmp_path)
    assert build_evidence.untracked_sources() == ["tests/test_new.py"]


def test_build_stops_before_running_anything_when_a_source_file_is_untracked(monkeypatch):
    monkeypatch.setattr(build_evidence, "untracked_sources", lambda: ["tests/test_new.py"])
    monkeypatch.setattr(sys, "argv", ["build_evidence.py", "--no-tests"])
    monkeypatch.setattr(build_evidence, "default_pack", lambda: pytest.fail("сборка не должна начинаться"))
    with pytest.raises(SystemExit) as stop:
        build_evidence.main()
    assert "tests/test_new.py" in str(stop.value) and "git add" in str(stop.value)


def test_the_repository_itself_has_no_untracked_source_files():
    """Тот же запрет на самом дереве: новый тест без git add здесь тоже виден сразу."""
    assert build_evidence.untracked_sources() == []
