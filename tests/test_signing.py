"""RT-05: подписант за абстракцией, зашифрованный PEM, HSM/Vault/KMS на заглушках, связка ключей с ротацией."""

from __future__ import annotations

import base64
import io
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandat import cli
from mandat.audit import attestation, signing
from mandat.audit.signing import Keyring, SignerError

PASS = "correct horse battery staple"
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def _report() -> dict[str, Any]:
    return {
        "case_id": "c1",
        "verdict": "confirmed",
        "profile": "strict",
        "organization": {"inn": "7707083893"},
        "chain": {"poa_numbers": ["1"]},
        "reproducibility_hash": "0" * 64,
        "rule_pack": {"version": "t", "sha256": "0" * 64},
    }


def _cred(signer, now=NOW):
    return attestation.sign(
        {
            "@context": attestation.CONTEXTS,
            "type": ["VerifiableCredential", "MandatAuthorityAttestation"],
            "issuer": attestation.ISSUER,
            "validFrom": now.isoformat(),
            "validUntil": (now + timedelta(days=30)).isoformat(),
            "credentialSubject": {"verdict": "confirmed", "profile": "strict"},
        },
        signer,
        now=now,
    )


# --- файл PEM ---------------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["pilot", "prod"])
def test_plain_pem_refused_in_protected_mode(tmp_path, monkeypatch, env):
    monkeypatch.setenv("MANDAT_ENV", env)
    monkeypatch.delenv(signing.PASSPHRASE_ENV, raising=False)
    with pytest.raises(SignerError, match="открытом PEM"):
        signing.load_pem_signer(tmp_path / "k.pem")
    assert not (tmp_path / "k.pem").exists()
    with pytest.raises(ValueError, match="открытом PEM"):
        attestation.load_or_create_key(tmp_path / "k.pem")


def test_plain_pem_allowed_in_demo(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    monkeypatch.delenv(signing.PASSPHRASE_ENV, raising=False)
    first = signing.load_pem_signer(tmp_path / "k.pem")
    again = signing.load_pem_signer(tmp_path / "k.pem")
    assert first.key_id == again.key_id
    assert b"BEGIN PRIVATE KEY" in (tmp_path / "k.pem").read_bytes()


def test_encrypted_pem_roundtrip_in_prod(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    monkeypatch.setenv(signing.PASSPHRASE_ENV, PASS)
    path = tmp_path / "k.pem"
    created = signing.load_pem_signer(path)
    data = path.read_bytes()
    assert b"ENCRYPTED PRIVATE KEY" in data
    assert path.stat().st_mode & 0o077 == 0
    assert signing.load_pem_signer(path).key_id == created.key_id
    cred = _cred(created)
    assert attestation.verify(cred, created.public_key())
    assert cred["proof"]["verificationMethod"] == f"{attestation.ISSUER}#{created.key_id}"


def test_encrypted_pem_wrong_or_missing_passphrase(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "prod")
    monkeypatch.setenv(signing.PASSPHRASE_ENV, PASS)
    path = tmp_path / "k.pem"
    signing.load_pem_signer(path)
    with pytest.raises(SignerError, match="неверная парольная фраза"):
        signing.load_pem_signer(path, passphrase="another passphrase 123")
    monkeypatch.setenv("MANDAT_ENV", "demo")
    monkeypatch.delenv(signing.PASSPHRASE_ENV)
    with pytest.raises(SignerError, match="зашифрован"):
        signing.load_pem_signer(path)


def test_plain_file_with_passphrase_is_not_silently_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    path = tmp_path / "k.pem"
    signing.load_pem_signer(path, passphrase="")
    with pytest.raises(SignerError, match="не зашифрован"):
        signing.load_pem_signer(path, passphrase=PASS)


def test_short_passphrase_refused(tmp_path):
    with pytest.raises(SignerError, match="короче"):
        signing.load_pem_signer(tmp_path / "k.pem", passphrase="short")


def test_missing_file_without_create(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    with pytest.raises(SignerError, match="нет ключа"):
        signing.load_pem_signer(tmp_path / "none.pem", create=False)


def test_non_ed25519_pem_refused(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import ec

    monkeypatch.setenv("MANDAT_ENV", "demo")
    path = tmp_path / "ec.pem"
    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    path.write_bytes(pem)
    path.chmod(0o600)
    with pytest.raises(SignerError, match="Ed25519"):
        signing.load_pem_signer(path)


# --- проверка подписи подписанта --------------------------------------------------------------


class _Liar:
    """Модуль, подписавший другой версией ключа."""

    def __init__(self):
        self._shown = Ed25519PrivateKey.generate()
        self._real = Ed25519PrivateKey.generate()

    @property
    def key_id(self):
        return signing.key_id(self._shown.public_key())

    def public_key(self):
        return self._shown.public_key()

    def sign(self, data):
        return self._real.sign(data)


def test_signature_from_other_key_is_not_issued():
    with pytest.raises(SignerError, match="другим ключом"):
        _cred(_Liar())


def test_issue_accepts_signer():
    signer = signing.LocalSigner(Ed25519PrivateKey.generate())
    assert isinstance(signer, signing.Signer)
    cred = attestation.issue(_report(), signer, now=NOW)
    assert attestation.verify(cred, signer.public_key())


# --- PKCS#11 на заглушке python-pkcs11 ---------------------------------------------------------


def _fake_pkcs11(private: Ed25519PrivateKey, point: bytes, calls: list):
    class Key:
        def __init__(self, attrs):
            self.attrs = attrs

        def __getitem__(self, name):
            return self.attrs[name]

        def sign(self, data, mechanism):
            calls.append(mechanism)
            return private.sign(data)

    class Session:
        def get_key(self, object_class, label):
            assert label == "mandat-issuer"
            return Key({"EC_POINT": point})

        def close(self):
            calls.append("closed")

    class Token:
        def open(self, user_pin):
            assert user_pin == "1234"
            return Session()

    class Lib:
        def __init__(self, path):
            assert path == "/usr/lib/softhsm/libsofthsm2.so"

        def get_token(self, token_label):
            assert token_label == "mandat"
            return Token()

    return SimpleNamespace(
        lib=Lib,
        ObjectClass=SimpleNamespace(PRIVATE_KEY="priv", PUBLIC_KEY="pub"),
        Attribute=SimpleNamespace(EC_POINT="EC_POINT"),
        Mechanism=SimpleNamespace(EDDSA="CKM_EDDSA"),
    )


@pytest.mark.parametrize("wrap", [True, False])
def test_pkcs11_signer(wrap):
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    calls: list = []
    fake = _fake_pkcs11(private, (b"\x04\x20" + raw) if wrap else raw, calls)
    signer = signing.Pkcs11Signer("/usr/lib/softhsm/libsofthsm2.so", "mandat", "mandat-issuer", "1234", lib_loader=fake)
    cred = _cred(signer)
    assert attestation.verify(cred, private.public_key())
    assert calls == ["CKM_EDDSA"]
    signer.close()
    assert calls[-1] == "closed"


def test_pkcs11_bad_point():
    with pytest.raises(SignerError, match="EC_POINT"):
        signing._ec_point_to_raw(b"\x01" * 10)


def test_pkcs11_extra_missing(monkeypatch):
    import builtins

    real = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name in ("pkcs11", "boto3"):
            raise ModuleNotFoundError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(SignerError, match=r"mandat\[pkcs11\]"):
        signing.Pkcs11Signer("lib", "t", "k", "p")
    with pytest.raises(SignerError, match=r"mandat\[aws\]"):
        signing.AwsKmsSigner("arn")


# --- Vault Transit на заглушке HTTP ------------------------------------------------------------


class _Vault:
    def __init__(self, versions: dict[int, Ed25519PrivateKey], key_type="ed25519", sign_version=None):
        self.versions = versions
        self.key_type = key_type
        self.sign_version = sign_version
        self.requests: list = []

    def __call__(self, request):
        self.requests.append(request)
        assert request.get_header("X-vault-token") == "s.token"
        if request.get_method() == "GET":
            keys = {
                str(v): {
                    "public_key": base64.b64encode(
                        k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                    ).decode()
                }
                for v, k in self.versions.items()
            }
            body = {"data": {"type": self.key_type, "latest_version": max(self.versions), "keys": keys}}
        else:
            payload = json.loads(request.data)
            version = self.sign_version or payload["key_version"]
            sig = self.versions[payload["key_version"]].sign(base64.b64decode(payload["input"]))
            body = {"data": {"signature": f"vault:v{version}:" + base64.b64encode(sig).decode()}}
        return io.BytesIO(json.dumps(body).encode())


def test_vault_transit_signer_pins_latest_version():
    v1, v2 = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    fake = _Vault({1: v1, 2: v2})
    signer = signing.VaultTransitSigner("https://vault.bank:8200", "mandat", "s.token", opener=fake)
    assert signer.version == 2
    cred = _cred(signer)
    assert attestation.verify(cred, v2.public_key())
    assert fake.requests[-1].full_url == "https://vault.bank:8200/v1/transit/sign/mandat"
    old = signing.VaultTransitSigner("https://vault.bank:8200", "mandat", "s.token", key_version=1, opener=fake)
    assert attestation.verify(_cred(old), v1.public_key())


def test_vault_refuses_plain_http_wrong_type_and_version_drift():
    key = Ed25519PrivateKey.generate()
    with pytest.raises(SignerError, match="https"):
        signing.VaultTransitSigner("http://vault.bank", "k", "s.token", opener=_Vault({1: key}))
    with pytest.raises(SignerError, match="VAULT_TOKEN"):
        signing.VaultTransitSigner("https://vault.bank", "k", "", opener=_Vault({1: key}))
    with pytest.raises(SignerError, match="ed25519"):
        signing.VaultTransitSigner("https://vault.bank", "k", "s.token", opener=_Vault({1: key}, key_type="rsa-2048"))
    with pytest.raises(SignerError, match="версии 7"):
        signing.VaultTransitSigner("https://vault.bank", "k", "s.token", key_version=7, opener=_Vault({1: key}))
    drift = signing.VaultTransitSigner("https://vault.bank", "k", "s.token", opener=_Vault({1: key}, sign_version=3))
    with pytest.raises(SignerError, match="не версией"):
        drift.sign(b"x")


# --- AWS KMS на заглушке boto3 -----------------------------------------------------------------


class _Kms:
    def __init__(self, key: Ed25519PrivateKey, spec="ECC_NIST_EDWARDS25519"):
        self.key, self.spec, self.calls = key, spec, []

    def get_public_key(self, KeyId):  # noqa: N803 - имена параметров boto3
        der = self.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return {"KeySpec": self.spec, "PublicKey": der}

    def sign(self, **kwargs):
        self.calls.append(kwargs)
        return {"Signature": self.key.sign(kwargs["Message"])}


def test_aws_kms_signer():
    key = Ed25519PrivateKey.generate()
    fake = _Kms(key)
    signer = signing.AwsKmsSigner("alias/mandat", client=fake)
    assert attestation.verify(_cred(signer), key.public_key())
    assert fake.calls[0]["MessageType"] == "RAW"
    assert fake.calls[0]["SigningAlgorithm"] == "ED25519_SHA_512"
    with pytest.raises(SignerError, match="ECC_NIST_EDWARDS25519"):
        signing.AwsKmsSigner("alias/x", client=_Kms(key, spec="RSA_2048"))


# --- выбор по окружению ------------------------------------------------------------------------


def test_signer_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    for name in ("MANDAT_SIGNER", "MANDAT_SIGNING_KEY_FILE", signing.PASSPHRASE_ENV):
        monkeypatch.delenv(name, raising=False)
    assert isinstance(signing.signer_from_env(tmp_path / "k.pem"), signing.LocalSigner)
    monkeypatch.setenv("MANDAT_SIGNING_KEY_FILE", str(tmp_path / "other.pem"))
    signing.signer_from_env(tmp_path / "k.pem")
    assert (tmp_path / "other.pem").exists()
    monkeypatch.setenv("MANDAT_SIGNER", "nope")
    with pytest.raises(SignerError, match="допустимы"):
        signing.signer_from_env(tmp_path / "k.pem")
    for kind, var in (("pkcs11", "MANDAT_PKCS11_LIB"), ("vault", "VAULT_ADDR"), ("aws-kms", "MANDAT_KMS_KEY_ID")):
        monkeypatch.setenv("MANDAT_SIGNER", kind)
        monkeypatch.delenv(var, raising=False)
        with pytest.raises(SignerError, match=var):
            signing.signer_from_env(tmp_path / "k.pem")


def test_signer_from_env_vault(monkeypatch, tmp_path):
    key = Ed25519PrivateKey.generate()
    fake = _Vault({1: key, 2: key})
    monkeypatch.setattr(signing, "_urlopen", fake)
    monkeypatch.setenv("MANDAT_SIGNER", "vault")
    monkeypatch.setenv("VAULT_ADDR", "https://vault.bank")
    monkeypatch.setenv("VAULT_TOKEN", "s.token")
    monkeypatch.setenv("MANDAT_VAULT_KEY", "mandat")
    monkeypatch.setenv("MANDAT_VAULT_KEY_VERSION", "1")
    signer = signing.signer_from_env(tmp_path / "unused.pem")
    assert isinstance(signer, signing.VaultTransitSigner) and signer.version == 1


# --- связка ключей и ротация -------------------------------------------------------------------


def _keyring(entries):
    return Keyring.parse({"keys": entries})


def test_rotation_old_credentials_verify_with_retired_key():
    old, new = signing.LocalSigner(Ed25519PrivateKey.generate()), signing.LocalSigner(Ed25519PrivateKey.generate())
    before = _cred(old, NOW)
    rotated = NOW + timedelta(days=1)
    after_old = _cred(old, rotated + timedelta(hours=1))  # старым ключом после вывода — не принимается
    fresh = _cred(new, rotated + timedelta(hours=1))
    ring = _keyring(
        [
            {"kid": new.key_id, "status": "active"},
            {"kid": old.key_id, "status": "retired", "retired_at": rotated.isoformat()},
        ]
    )
    check = rotated + timedelta(hours=2)
    assert attestation.validate_attestation(before, ring, now=check) == []
    assert attestation.validate_attestation(fresh, ring, now=check) == []
    assert any("не из списка" in p for p in attestation.validate_attestation(after_old, ring, now=check))


def test_revoked_key_verifies_nothing():
    key = signing.LocalSigner(Ed25519PrivateKey.generate())
    ring = _keyring([{"kid": key.key_id, "status": "revoked"}])
    problems = attestation.validate_attestation(_cred(key), ring, now=NOW + timedelta(hours=1))
    assert any("не из списка" in p for p in problems)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"keys": []},
        {"keys": [{"status": "active"}]},
        {"keys": [{"kid": "zX", "status": "lost"}]},
        {"keys": [{"kid": "z1", "status": "retired", "retired_at": "2026-10-14T00:00:00"}]},
    ],
)
def test_bad_keyring(payload):
    with pytest.raises(ValueError):
        Keyring.parse(payload)


def test_duplicate_kid():
    kid = signing.key_id(Ed25519PrivateKey.generate().public_key())
    with pytest.raises(ValueError, match="дважды"):
        Keyring.parse({"keys": [{"kid": kid}, {"kid": kid}]})


def test_status_list_after_rotation_checks_with_new_key(tmp_path, monkeypatch):
    """Список статусов переподписан новым ключом, удостоверение — старым: статус всё равно проверяется."""
    from mandat.audit.status_list import StatusRegistry

    old, new = signing.LocalSigner(Ed25519PrivateKey.generate()), signing.LocalSigner(Ed25519PrivateKey.generate())
    registry = StatusRegistry.open(tmp_path / "status")
    index = registry.allocate({"case_id": "c1", "org_inn": "7707083893", "poa_numbers": ["1"]})
    now = datetime.now(UTC)
    cred = attestation.issue(_report(), old, status=registry.credential_status(index), now=now)
    registry.save(new)
    ring = _keyring(
        [
            {"kid": new.key_id, "status": "active"},
            {"kid": old.key_id, "status": "retired", "retired_at": (now + timedelta(minutes=1)).isoformat()},
        ]
    )
    problems = attestation.validate_attestation(
        cred, ring, now=now + timedelta(minutes=2), status_dir=tmp_path / "status", allow_demo=True
    )
    assert problems == []


def test_cli_attest_verify_with_keyring(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MANDAT_ENV", "demo")
    signer = signing.LocalSigner(Ed25519PrivateKey.generate())
    now = datetime.now(UTC)
    cred_path = tmp_path / "cred.json"
    cred_path.write_text(attestation.dump(attestation.issue(_report(), signer, now=now)), encoding="utf-8")
    ring = tmp_path / "keyring.json"
    ring.write_text(json.dumps({"keys": [{"kid": signer.key_id, "status": "active"}]}), encoding="utf-8")
    assert cli.main(["attest-verify", str(cred_path), "--keyring", str(ring)]) == 0
    assert "принимается" in capsys.readouterr().out
    other = tmp_path / "other.json"
    other_kid = signing.key_id(Ed25519PrivateKey.generate().public_key())
    other.write_text(json.dumps({"keys": [{"kid": other_kid}]}), encoding="utf-8")
    assert cli.main(["attest-verify", str(cred_path), "--keyring", str(other)]) == 4
    (tmp_path / "broken.json").write_text("{}", encoding="utf-8")
    assert cli.main(["attest-verify", str(cred_path), "--keyring", str(tmp_path / "broken.json")]) == 2


def test_cli_demo_refuses_plain_key_in_pilot(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    monkeypatch.setenv("MANDAT_JOURNAL_KEY", "j" * 32)
    monkeypatch.setenv("MANDAT_PII_KEY", "p" * 32)
    monkeypatch.delenv("MANDAT_SIGNER", raising=False)
    monkeypatch.delenv(signing.PASSPHRASE_ENV, raising=False)
    code = cli.main(["demo", "--out", str(tmp_path / "out")])
    assert code == 2
    assert "открытом PEM" in capsys.readouterr().err
