"""Список статусов: случайные индексы и минимальный размер для групповой приватности."""

from __future__ import annotations

import json

import pytest

from mandat.audit import status_list
from mandat.audit.status_list import MIN_BITS, StatusRegistry


def test_indexes_are_random_and_unique(tmp_path):
    reg = StatusRegistry.open(tmp_path)
    picked = [reg.allocate({"org_inn": "1"}) for _ in range(500)]
    assert len(set(picked)) == 500
    assert picked != sorted(picked)
    assert max(picked) > MIN_BITS // 2


def test_full_list_refuses_new_index(tmp_path):
    reg = StatusRegistry.open(tmp_path)
    reg.entries = {str(i): {} for i in range(reg.size)}
    with pytest.raises(RuntimeError):
        reg.allocate({})


def test_crowded_list_still_finds_the_free_slot(tmp_path, monkeypatch):
    reg = StatusRegistry.open(tmp_path)
    reg.entries = {str(i): {} for i in range(reg.size) if i != 4242}
    monkeypatch.setattr(status_list.secrets, "randbelow", lambda n: 0)
    assert reg.allocate({}) == 4242


def test_small_list_on_disk_is_rejected(tmp_path):
    state = {
        "size": 1024,
        "epoch": "e",
        "lists": {p: status_list.encode_bits(bytearray(128)) for p in status_list.PURPOSES},
        "entries": {},
    }
    (tmp_path / "registry.json").write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="групповая приватность"):
        StatusRegistry.open(tmp_path)


@pytest.mark.parametrize("index", [-1, 5, MIN_BITS])
def test_mark_of_unissued_index_changes_nothing(tmp_path, index):
    reg = StatusRegistry.open(tmp_path)
    reg.entries = {"7": {"org_inn": "1"}}
    before = {p: bytes(b) for p, b in reg.lists.items()}
    with pytest.raises(ValueError, match="не выдан"):
        reg.mark(index, "revocation", "test")
    assert {p: bytes(b) for p, b in reg.lists.items()} == before


def test_unissued_index_check_catches_its_own_removal(tmp_path, monkeypatch):
    """Контроль мутацией: без проверки индекса запись в список проходит, и тест выше это ловит."""
    import importlib.util
    import sys
    from pathlib import Path

    guard = "        if not 0 <= index < self.size or str(index) not in self.entries:\n"
    source = Path(status_list.__file__).read_text(encoding="utf-8")
    assert source.count(guard) == 1
    spec = importlib.util.spec_from_loader("status_list_mutant", loader=None)
    mutant = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "status_list_mutant", mutant)  # dataclass ищет модуль класса здесь
    exec(compile(source.replace(guard, "        if False:\n"), status_list.__file__, "exec"), mutant.__dict__)
    reg = mutant.StatusRegistry.open(tmp_path)
    reg.entries = {"7": {"org_inn": "1"}}
    before = bytes(reg.lists["revocation"])
    try:
        reg.mark(-1, "revocation", "test")
    except KeyError:
        pass
    assert bytes(reg.lists["revocation"]) != before
