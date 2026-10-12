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
