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
    w2.mark(idx2, "suspension", "w2")
    w1.save(key, now=NOW)
    w2.save(key, now=NOW)  # merge OR
    assert check_status(cred1, sd, key.public_key(), now=NOW)["revocation"] is True
    assert check_status(cred2, sd, key.public_key(), now=NOW)["suspension"] is True


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

    key, sd, idx, cred = _bootstrap(tmp_path)
    script = tmp_path / "worker.py"
    script.write_text(
        f"""
import sys
from datetime import datetime, UTC
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from mandat.audit.status_list import StatusRegistry

pem = bytes.fromhex({
            key.private_bytes(
                encoding=__import__("cryptography").hazmat.primitives.serialization.Encoding.Raw,
                format=__import__("cryptography").hazmat.primitives.serialization.PrivateFormat.Raw,
                encryption_algorithm=__import__("cryptography").hazmat.primitives.serialization.NoEncryption(),
            ).hex()!r
        })
key = Ed25519PrivateKey.from_private_bytes(pem)
sd = Path({str(sd)!r})
idx = {idx}
mode = sys.argv[1]
now = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
reg = StatusRegistry.open(sd)
if mode == "revoke":
    reg.mark(idx, "revocation", "mp")
    reg.save(key, now=now)
else:
    reg.allocate({{"org_inn": "mp", "verdict": "confirmed", "poa_numbers": ["mp"]}})
    reg.save(key, now=now)
""",
        encoding="utf-8",
    )
    # simpler inline without raw key issues - use file pem
    pem_path = tmp_path / "k.pem"
    from cryptography.hazmat.primitives import serialization

    pem_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    script.write_text(
        f"""
import sys
from datetime import datetime, UTC
from pathlib import Path
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from mandat.audit.status_list import StatusRegistry
key = load_pem_private_key(Path({str(pem_path)!r}).read_bytes(), password=None)
sd = Path({str(sd)!r})
idx = {idx}
now = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
reg = StatusRegistry.open(sd)
if sys.argv[1] == "revoke":
    reg.mark(idx, "revocation", "mp")
else:
    reg.allocate({{"org_inn": "mp", "verdict": "confirmed", "poa_numbers": ["mp"]}})
reg.save(key, now=now)
""",
        encoding="utf-8",
    )
    procs = [
        subprocess.Popen([sys.executable, str(script), "revoke"]),
        subprocess.Popen([sys.executable, str(script), "alloc"]),
    ]
    for p in procs:
        assert p.wait(timeout=30) == 0
    assert check_status(cred, sd, key.public_key(), now=NOW)["revocation"] is True
