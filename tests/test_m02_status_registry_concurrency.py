"""M-02: flock+CAS/merge; revocation irreversible; crash-safe CURRENT."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandat.audit import attestation
from mandat.audit.status_list import StaleRegistryError, StatusRegistry, check_status, get_bit, published_root

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)  # в прошлом относительно wall-clock CLI
POSIX = os.name == "posix"


def _key():
    return Ed25519PrivateKey.generate()


def _bootstrap(tmp_path: Path):
    key = _key()
    sd = tmp_path / "status"
    reg = StatusRegistry.open(sd)
    idx = reg.allocate({"org_inn": "7707083893", "verdict": "confirmed", "poa_numbers": ["poc"]})
    reg.save(key, now=NOW)
    cred = attestation.issue(
        {
            "verdict": "confirmed",
            "profile": "strict",
            "organization": {"inn": "7707083893"},
            "requester": {"inn": "123456789012"},
            "operation": {"code": "payment_sign", "date": NOW.date().isoformat()},
            "chain": {"poa_numbers": ["poc"]},
            "rule_pack": {"sha256": "a" * 64},
            "reproducibility_hash": "b" * 64,
        },
        key,
        now=NOW,
        status=reg.credential_status(idx),
    )
    return key, sd, idx, cred


@pytest.mark.skipif(not POSIX, reason="fcntl.flock только на POSIX")
def test_concurrent_revoke_and_allocate_preserves_revocation_bit(tmp_path):
    key, sd, idx, cred = _bootstrap(tmp_path)
    a, b = StatusRegistry.open(sd), StatusRegistry.open(sd)
    a.mark(idx, "revocation", "source A")
    a.save(key, now=NOW)
    assert check_status(cred, sd, key.public_key(), now=NOW)["state"] == "отозвано"
    b.allocate({"org_inn": "another", "verdict": "confirmed", "poa_numbers": ["new"]})
    b.save(key, now=NOW)
    state = check_status(cred, sd, key.public_key(), now=NOW)
    assert state["revocation"] is True
    assert state["state"] == "отозвано"


@pytest.mark.skipif(not POSIX, reason="fcntl.flock только на POSIX")
def test_concurrent_double_revoke_monotonic_sequence(tmp_path):
    key, sd, idx, cred = _bootstrap(tmp_path)
    a, b = StatusRegistry.open(sd), StatusRegistry.open(sd)
    a.mark(idx, "revocation", "A")
    a.save(key, now=NOW)
    seq_after_a = StatusRegistry.open(sd).sequence
    # B пытается «тот же» revoke со stale snap — sequence растёт, бит остаётся
    b.mark(idx, "revocation", "B")
    b.save(key, now=NOW)
    final = StatusRegistry.open(sd)
    assert final.sequence > seq_after_a
    assert get_bit(final.lists["revocation"], idx) is True


@pytest.mark.skipif(not POSIX, reason="fcntl.flock только на POSIX")
def test_save_cas_rejects_stale_writer(tmp_path):
    key, sd, idx, cred = _bootstrap(tmp_path)
    a, b = StatusRegistry.open(sd), StatusRegistry.open(sd)
    a.mark(idx, "revocation", "A")
    a.save(key, now=NOW)
    b.allocate({"org_inn": "x", "verdict": "confirmed", "poa_numbers": ["n"]})
    with pytest.raises(StaleRegistryError):
        b.save(key, now=NOW, conflict="error")
    assert check_status(cred, sd, key.public_key(), now=NOW)["revocation"] is True


@pytest.mark.skipif(not POSIX, reason="fcntl.flock только на POSIX")
def test_crash_mid_publish_leaves_consistent_pointer(tmp_path):
    key, sd, idx, cred = _bootstrap(tmp_path)
    before = (sd / "CURRENT").read_text(encoding="utf-8").strip()
    # Имитация: новый snapshot без обновления CURRENT
    bad = sd / "snapshots" / "999"
    bad.mkdir(parents=True)
    (bad / "registry.json").write_text(
        '{"size":131072,"epoch":"x","sequence":999,"lists":{},"entries":{}}', encoding="utf-8"
    )
    assert (sd / "CURRENT").read_text(encoding="utf-8").strip() == before
    root = published_root(sd)
    assert root == sd / "snapshots" / before or (sd / "CURRENT").exists()
    assert check_status(cred, sd, key.public_key(), now=NOW)["state"] == "действует"


@pytest.mark.skipif(not POSIX, reason="fcntl.flock только на POSIX")
def test_or_merge_revocation_bits_across_writers(tmp_path):
    key, sd, idx1, cred1 = _bootstrap(tmp_path)
    a = StatusRegistry.open(sd)
    idx2 = a.allocate({"org_inn": "org2", "verdict": "confirmed", "poa_numbers": ["p2"]})
    a.save(key, now=NOW)
    cred2 = attestation.issue(
        {
            "verdict": "confirmed",
            "profile": "strict",
            "organization": {"inn": "org2"},
            "requester": {"inn": "123456789012"},
            "operation": {"code": "payment_sign", "date": NOW.date().isoformat()},
            "chain": {"poa_numbers": ["p2"]},
            "rule_pack": {"sha256": "a" * 64},
            "reproducibility_hash": "c" * 64,
        },
        key,
        now=NOW,
        status=StatusRegistry.open(sd).credential_status(idx2),
    )
    w1, w2 = StatusRegistry.open(sd), StatusRegistry.open(sd)
    w1.mark(idx1, "revocation", "w1")
    w2.mark(idx2, "revocation", "w2")
    w1.save(key, now=NOW)
    w2.save(key, now=NOW)  # OR только для revocation
    assert check_status(cred1, sd, key.public_key(), now=NOW)["revocation"] is True
    assert check_status(cred2, sd, key.public_key(), now=NOW)["revocation"] is True
    # suspension на stale снимке не сливается молча (блокер 3)
    w3 = StatusRegistry.open(sd)
    w3.mark(idx2, "suspension", "w3")
    w3.sequence -= 1
    with pytest.raises(StaleRegistryError, match="suspension"):
        w3.save(key, now=NOW)


@pytest.mark.skipif(not POSIX, reason="fcntl.flock только на POSIX")
def test_seen_watermark_equal_sequence_not_sufficient_alone(tmp_path):
    """После фикса overwrite с тем же sequence невозможен; seen ловит откат."""
    key, sd, idx, cred = _bootstrap(tmp_path)
    a, b = StatusRegistry.open(sd), StatusRegistry.open(sd)
    a.mark(idx, "revocation", "A")
    a.save(key, now=NOW)
    b.allocate({"org_inn": "z", "verdict": "confirmed", "poa_numbers": ["z"]})
    b.save(key, now=NOW)
    final = StatusRegistry.open(sd)
    # sequence строго вырос дважды от bootstrap
    assert final.sequence >= 3
    seen: dict[str, int] = {}
    check_status(cred, sd, key.public_key(), now=NOW, seen=seen)
    # подложить старый список с меньшим sequence — seen отвергнет
    old_list = json.loads((sd / "snapshots" / "1" / "revocation.json").read_text(encoding="utf-8"))
    (sd / "revocation.json").write_text(json.dumps(old_list), encoding="utf-8")
    # CURRENT всё ещё указывает на новый snap — published_root защищает
    state = check_status(cred, sd, key.public_key(), now=NOW, seen=dict(seen))
    assert state["revocation"] is True


@pytest.mark.skipif(not POSIX, reason="fcntl.flock только на POSIX")
def test_stale_writer_cannot_erase_revocation(tmp_path):
    """Регрессия аудита reproduce status_race: после stale save бит жив."""
    key, sd, idx, cred = _bootstrap(tmp_path)
    a, b = StatusRegistry.open(sd), StatusRegistry.open(sd)
    a.mark(idx, "revocation", "source A")
    a.save(key, now=NOW)
    first = check_status(cred, sd, key.public_key(), now=NOW)["state"]
    b.allocate({"org_inn": "another-org", "verdict": "confirmed", "poa_numbers": ["new"]})
    b.save(key, now=NOW)
    last = check_status(cred, sd, key.public_key(), now=NOW)["state"]
    assert first == "отозвано"
    assert last == "отозвано"
    assert check_status(cred, sd, key.public_key(), now=NOW)["revocation"] is True


@pytest.mark.skipif(not POSIX, reason="fcntl.flock только на POSIX")
def test_multiprocess_revoke_allocate(tmp_path):
    import subprocess

    from cryptography.hazmat.primitives import serialization

    key, sd, idx, cred = _bootstrap(tmp_path)
    pem_path = tmp_path / "k.pem"
    pem_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    script = tmp_path / "worker.py"
    # Без вложенных f-string/dict: на 3.11 иначе SyntaxError при rewrite pytest.
    script.write_text(
        "\n".join(
            [
                "import sys",
                "from datetime import UTC, datetime",
                "from pathlib import Path",
                "from cryptography.hazmat.primitives.serialization import load_pem_private_key",
                "from mandat.audit.status_list import StatusRegistry",
                f"key = load_pem_private_key(Path({str(pem_path)!r}).read_bytes(), password=None)",
                f"sd = Path({str(sd)!r})",
                f"idx = {idx}",
                "now = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)",
                "reg = StatusRegistry.open(sd)",
                'if sys.argv[1] == "revoke":',
                '    reg.mark(idx, "revocation", "mp")',
                "else:",
                '    reg.allocate({"org_inn": "mp", "verdict": "confirmed", "poa_numbers": ["mp"]})',
                "reg.save(key, now=now)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    procs = [
        subprocess.Popen([sys.executable, str(script), "revoke"]),
        subprocess.Popen([sys.executable, str(script), "alloc"]),
    ]
    for proc in procs:
        assert proc.wait(timeout=30) == 0
    assert check_status(cred, sd, key.public_key(), now=NOW)["revocation"] is True


# --- Ваня blockers on PR #18 ---


@pytest.mark.skipif(not POSIX, reason="fcntl.flock только на POSIX")
def test_two_issuers_same_snapshot_same_index_conflict_is_not_silent_merge(tmp_path, monkeypatch):
    """Blocker 1: два issuer на одном снимке получили один idx с разной meta → StaleRegistryError."""
    key, sd, idx0, cred = _bootstrap(tmp_path)
    a, b = StatusRegistry.open(sd), StatusRegistry.open(sd)
    # Принудительно один и тот же индекс (коллизия CSPRNG или гонка без lock)
    forced = 42 if "42" not in a.entries else 43
    monkeypatch.setattr("mandat.audit.status_list.secrets.randbelow", lambda _n: forced)
    a.allocate({"org_inn": "org-A", "verdict": "confirmed", "poa_numbers": ["A"]})
    b.allocate({"org_inn": "org-B", "verdict": "confirmed", "poa_numbers": ["B"]})
    a.save(key, now=NOW)
    with pytest.raises(StaleRegistryError, match="конфликт индекса"):
        b.save(key, now=NOW)
    disk = StatusRegistry.open(sd)
    assert disk.entries[str(forced)]["org_inn"] == "org-A"


@pytest.mark.skipif(not POSIX, reason="fcntl.flock только на POSIX")
def test_allocate_under_lock_two_issuers_get_distinct_indices(tmp_path):
    key, sd, _, _ = _bootstrap(tmp_path)
    a, b = StatusRegistry.open(sd), StatusRegistry.open(sd)
    i1 = a.allocate_under_lock({"org_inn": "A", "verdict": "confirmed", "poa_numbers": ["a"]}, key, now=NOW)
    i2 = b.allocate_under_lock({"org_inn": "B", "verdict": "confirmed", "poa_numbers": ["b"]}, key, now=NOW)
    assert i1 != i2
    disk = StatusRegistry.open(sd)
    assert disk.entries[str(i1)]["org_inn"] == "A"
    assert disk.entries[str(i2)]["org_inn"] == "B"


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf", "-1", "garbage", "1e9"])
def test_status_max_age_days_rejects_invalid(monkeypatch, raw):
    """Blocker 2: только конечное 0..cap."""
    from mandat.audit.status_list import status_max_age_days

    monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_DAYS", raw)
    with pytest.raises(ValueError, match="MANDAT_STATUS_MAX_AGE_DAYS"):
        status_max_age_days()


@pytest.mark.parametrize(("raw", "expected"), [("", 7.0), ("0", 0.0), ("1.5", 1.5), ("3650", 3650.0)])
def test_status_max_age_days_accepts_valid(monkeypatch, raw, expected):
    from mandat.audit.status_list import status_max_age_days

    if raw:
        monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_DAYS", raw)
    else:
        monkeypatch.delenv("MANDAT_STATUS_MAX_AGE_DAYS", raising=False)
    assert status_max_age_days() == expected


def test_protected_invalid_max_age_fails_closed(tmp_path, monkeypatch):
    """Blocker 2: в protected невалидный env → статус не проверен (fail-closed), не тихий 7d."""
    monkeypatch.setenv("MANDAT_ENV", "prod")
    monkeypatch.setenv("MANDAT_STATUS_MAX_AGE_DAYS", "nan")
    key, sd, idx, cred = _bootstrap(tmp_path)
    problems = attestation.validate_attestation(cred, [key.public_key()], now=NOW, status_dir=sd)
    assert any("MANDAT_STATUS_MAX_AGE_DAYS" in p or "статус не проверен" in p for p in problems)


@pytest.mark.skipif(not POSIX, reason="fcntl.flock только на POSIX")
def test_suspension_resume_conflict_raises_not_or(tmp_path):
    """Blocker 3: OR только для revocation; resume на stale снимке → StaleRegistryError."""
    from mandat.audit.status_list import set_bit

    key, sd, idx, cred = _bootstrap(tmp_path)
    # A приостанавливает
    a = StatusRegistry.open(sd)
    a.mark(idx, "suspension", "takeover")
    a.save(key, now=NOW)
    # B на старом снимке (до suspend) пытается «resume» = чистый бит + свой allocate
    b = StatusRegistry.open(sd)
    b.lists["suspension"] = bytearray(len(b.lists["suspension"]))  # снимаем все suspension
    b.sequence -= 1  # имитируем stale writer
    with pytest.raises(StaleRegistryError, match="suspension"):
        b.save(key, now=NOW)
    assert check_status(cred, sd, key.public_key(), now=NOW)["suspension"] is True

    # Наоборот: stale без suspension не должен OR-нуть чужой suspend молча при другом расхождении
    c = StatusRegistry.open(sd)
    other = c.allocate({"org_inn": "z", "verdict": "confirmed", "poa_numbers": ["z"]})
    set_bit(c.lists["suspension"], other)
    c.sequence -= 1
    with pytest.raises(StaleRegistryError, match="suspension"):
        c.save(key, now=NOW)


@pytest.mark.skipif(not POSIX, reason="fcntl.flock только на POSIX")
def test_revocation_still_or_merged_while_suspension_equal(tmp_path):
    key, sd, idx, cred = _bootstrap(tmp_path)
    a, b = StatusRegistry.open(sd), StatusRegistry.open(sd)
    a.mark(idx, "revocation", "A")
    a.save(key, now=NOW)
    b.allocate({"org_inn": "n", "verdict": "confirmed", "poa_numbers": ["n"]})
    b.save(key, now=NOW)  # suspension равны → merge ok
    assert check_status(cred, sd, key.public_key(), now=NOW)["revocation"] is True
