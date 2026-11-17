"""B1 (#101, R15): статус «approved» — только по подписанной записи юриста.

Правка строки в rules.json больше ничего не утверждает. Запись связывает пакет, хеш нормативного текста
правила, ключ из связки утверждающих и срок. Подделка, чужой ключ, истёкший срок и правка нормы после подписи
оставляют правило черновиком, и строгий профиль по нему «подтверждено» не даёт.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from conftest import ROOT
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandat.audit.signing import Keyring, LocalSigner
from mandat.cli import main
from mandat.domain.model import PowerOfAttorney
from mandat.eval import synth
from mandat.intake.case_loader import load_case_dir, load_case_inline
from mandat.ports import FixtureBankruptcy, FixtureRegistry, Ports, SignatureResult, SignatureStatus
from mandat.report.html import render
from mandat.rules.approvals import (
    Approvals,
    check_record,
    effective_pack,
    load_approvals,
    rule_text_sha256,
    sign_record,
)
from mandat.rules.pack import default_pack
from mandat.services.verify import verify_case

PACK = default_pack()
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
SIGNED = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
CONFIRMED = ROOT / "examples" / "cases" / "14_old_director_charter_cleared"
TRUSTED = frozenset({"registry", "egrul", "bankruptcy", "signature"})


class _VerifiedSignature:
    """Стенд СКЗИ: подпись каждой МЧД подтверждена. Нужен, чтобы дойти до «подтверждено» в strict."""

    name = "test-skzi"
    is_stub = False

    def verify(self, poa: PowerOfAttorney) -> SignatureResult:
        return SignatureResult(SignatureStatus.VERIFIED, "подпись проверена (тест)")


def _signer() -> LocalSigner:
    return LocalSigner(Ed25519PrivateKey.generate())


def _record(rule_id: str, signer: LocalSigner, pack=PACK, **over) -> dict:
    kwargs = {
        "approver_role": "юрист банка",
        "norm_edition": "редакция на 19.11.2026",
        "norm_checked_on": date(2026, 11, 19),
        "valid_until": date(2027, 11, 19),
        "signed_at": SIGNED,
    }
    kwargs.update(over)
    return sign_record(pack.rules[rule_id], pack.pack_id, signer, **kwargs)


def _keyring(*entries: dict) -> Keyring:
    return Keyring.parse({"keys": list(entries)})


def _active(signer: LocalSigner) -> dict:
    return {"kid": signer.key_id, "status": "active"}


def _approvals(records, keyring) -> Approvals:
    return Approvals(tuple(records), keyring, "0" * 64, "тест")


def _case():
    """Синтетический кейс «всё в порядке» на операцию без устава: снимок реестра с моментом запроса,
    банкротства нет ни у доверителя, ни у заявителя. В strict это все обязательные проверки."""
    manifest = synth.build("ok", 1).inline_manifest()
    manifest["operation"]["code"] = "STATEMENT_VIEW"
    day = manifest["operation"]["date"]
    for number, entry in list(manifest["registry"].items()):
        status = entry if isinstance(entry, str) else entry["status"]
        manifest["registry"][number] = {"status": status, "observed_at": f"{day}T09:00:00+03:00"}
    case = load_case_inline(manifest)
    return dataclasses.replace(case, bankruptcy={case.organization.inn: "none", case.requester.inn: "none"})


def _strict(case, pack, approvals: Approvals | None, monkeypatch):
    if approvals is not None:
        monkeypatch.setattr("mandat.rules.approvals.load_approvals", lambda *a, **k: approvals)
    ports = Ports(
        registry=FixtureRegistry(case.registry),
        signature=_VerifiedSignature(),
        bankruptcy=FixtureBankruptcy(case.bankruptcy),
    )
    return verify_case(case, profile="strict", pack=pack, ports=ports, trusted_sources=TRUSTED, now=NOW)


def test_repository_ships_no_approvals_and_every_rule_is_draft():
    payload = json.loads((ROOT / "src" / "mandat" / "data" / "approvals.json").read_text(encoding="utf-8"))
    assert payload["schema"] == 1
    assert payload["approvals"] == []
    rules = json.loads((ROOT / "src" / "mandat" / "data" / "rules.json").read_text(encoding="utf-8"))["rules"]
    assert {row["status"] for row in rules} == {"draft"}
    pack, notes = effective_pack(PACK, NOW)
    assert pack is PACK and notes == []
    assert {spec.status for spec in pack.rules.values()} == {"draft"}


def test_signed_approvals_reach_confirmed_and_forged_status_does_not(monkeypatch):
    """Подделанный «approved» во всех правилах без записей — черновики; с записями — «подтверждено»."""
    case = _case()
    forged = dataclasses.replace(
        PACK, rules={k: dataclasses.replace(v, status="approved") for k, v in PACK.rules.items()}
    )
    plain = _strict(case, forged, _approvals([], None), monkeypatch)
    assert plain.verdict.value != "confirmed"
    assert {f.rule_status for f in plain.findings if f.origin == "deterministic"} == {"draft"}

    signer = _signer()
    records = [_record(rule_id, signer) for rule_id in PACK.rules]
    signed = _strict(case, PACK, _approvals(records, _keyring(_active(signer))), monkeypatch)
    assert signed.verdict.value == "confirmed", signed.warnings
    body = signed.as_dict()
    assert body["rule_pack"]["approvals"]["approved"] == sorted(PACK.rules)
    assert {f["rule_status"] for f in body["findings"] if f["origin"] == "deterministic"} == {"approved"}
    assert "правило approved" in render(body)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"valid_until": date(2026, 11, 19)}, "срок записи"),
        ({"signed_at": NOW + timedelta(hours=1)}, "в будущем"),
        ({"valid_until": date(2027, 11, 22)}, "не длиннее года"),
        ({"norm_checked_on": date(2026, 11, 21)}, "сверена позже подписи"),
        ({"approver_role": ""}, "approver_role"),
    ],
)
def test_record_outside_its_terms_does_not_approve(change, reason):
    signer = _signer()
    record = _record("R-TERM-002", signer, **change)
    assert reason in (check_record(record, PACK, _keyring(_active(signer)), NOW) or "")


def test_expired_record_stops_approving_the_day_after_valid_until():
    signer = _signer()
    keyring = _keyring(_active(signer))
    record = _record("R-TERM-002", signer, valid_until=date(2026, 12, 1))
    assert check_record(record, PACK, keyring, datetime(2026, 12, 1, 23, 0, tzinfo=UTC)) is None
    assert "истёк" in check_record(record, PACK, keyring, datetime(2026, 12, 2, 0, 1, tzinfo=UTC))


def test_foreign_revoked_and_retired_keys():
    signer, other = _signer(), _signer()
    record = _record("R-TERM-002", signer)
    assert "связки" in check_record(record, PACK, _keyring(_active(other)), NOW)
    assert "связки" in check_record(record, PACK, _keyring({"kid": signer.key_id, "status": "revoked"}), NOW)
    after = {"kid": signer.key_id, "status": "retired", "retired_at": (SIGNED + timedelta(minutes=1)).isoformat()}
    before = {"kid": signer.key_id, "status": "retired", "retired_at": (SIGNED - timedelta(minutes=1)).isoformat()}
    assert check_record(record, PACK, _keyring(after), NOW) is None
    assert "связки" in check_record(record, PACK, _keyring(before), NOW)
    assert "связка ключей" in check_record(record, PACK, None, NOW)


@pytest.mark.parametrize("field", ["norm_ref", "title", "check", "effects"])
def test_editing_the_rule_after_signing_drops_the_approval(field):
    signer = _signer()
    record = _record("R-TERM-002", signer)
    spec = PACK.rules["R-TERM-002"]
    edited_value = {
        "norm_ref": spec.norm_ref + ", п. 2",
        "title": spec.title + " (ред.)",
        "check": "registry",
        "effects": (*spec.effects, "info"),
    }[field]
    edited = dataclasses.replace(
        PACK, rules={**PACK.rules, "R-TERM-002": dataclasses.replace(spec, **{field: edited_value})}
    )
    assert rule_text_sha256(edited.rules["R-TERM-002"]) != record["rule_sha256"]
    assert "изменён после подписи" in check_record(record, edited, _keyring(_active(signer)), NOW)
    pack, notes = effective_pack(edited, NOW, _approvals([record], _keyring(_active(signer))))
    assert pack.rules["R-TERM-002"].status == "draft"
    assert any("R-TERM-002" in note and "изменён после подписи" in note for note in notes)


def test_operational_fields_do_not_need_a_new_signature():
    """Параметры банка, подразделение и следующий шаг не входят в утверждаемый текст."""
    signer = _signer()
    record = _record("R-EIO-002", signer)
    spec = PACK.rules["R-EIO-002"]
    tuned = dataclasses.replace(spec, params={**spec.params, "tuned": 1}, unit="Другое подразделение", next_step="x")
    pack = dataclasses.replace(PACK, rules={**PACK.rules, "R-EIO-002": tuned})
    assert check_record(record, pack, _keyring(_active(signer)), NOW) is None


@pytest.mark.parametrize("field", ["approver_role", "valid_until", "rule_id", "norm_edition"])
def test_tampered_record_fails_the_signature(field):
    signer = _signer()
    record = _record("R-TERM-002", signer)
    record[field] = {
        "approver_role": "другая роль",
        "valid_until": "2027-11-18",
        "rule_id": "R-TERM-001",
        "norm_edition": "другая редакция",
    }[field]
    if field == "rule_id":
        record["rule_sha256"] = rule_text_sha256(PACK.rules["R-TERM-001"])
    assert check_record(record, PACK, _keyring(_active(signer)), NOW) == "подпись не совпадает"


def test_record_of_another_pack_or_with_extra_fields_is_rejected():
    signer = _signer()
    record = _record("R-TERM-002", signer)
    other = copy.deepcopy(record)
    other["pack_id"] = "other-pack"
    assert "другого пакета" in check_record(other, PACK, _keyring(_active(signer)), NOW)
    extra = {**record, "approved_by": "Иванов"}
    assert "не по контракту" in check_record(extra, PACK, _keyring(_active(signer)), NOW)
    assert "не объект" in check_record(["R-TERM-002"], PACK, _keyring(_active(signer)), NOW)


def test_retired_rule_is_never_resurrected():
    signer = _signer()
    record = _record("R-TERM-002", signer)
    retired = dataclasses.replace(
        PACK, rules={**PACK.rules, "R-TERM-002": dataclasses.replace(PACK.rules["R-TERM-002"], status="retired")}
    )
    pack, notes = effective_pack(retired, NOW, _approvals([record], _keyring(_active(signer))))
    assert pack.rules["R-TERM-002"].status == "retired"
    assert any("retired" in note for note in notes)


def _files(tmp_path, records, signer):
    approvals = tmp_path / "approvals.json"
    keys = tmp_path / "approval-keys.json"
    approvals.write_text(json.dumps({"schema": 1, "approvals": records}, ensure_ascii=False), encoding="utf-8")
    keys.write_text(json.dumps({"keys": [_active(signer)]}), encoding="utf-8")
    return approvals, keys


def test_environment_points_to_the_bank_files(tmp_path, monkeypatch):
    signer = _signer()
    approvals, keys = _files(tmp_path, [_record("R-TERM-002", signer)], signer)
    monkeypatch.setenv("MANDAT_APPROVALS", str(approvals))
    monkeypatch.setenv("MANDAT_APPROVAL_KEYS", str(keys))
    pack, notes = effective_pack(PACK, NOW)
    assert notes == []
    assert pack.rules["R-TERM-002"].status == "approved"
    assert {s.status for k, s in pack.rules.items() if k != "R-TERM-002"} == {"draft"}
    assert pack.summary()["approvals"]["approved"] == ["R-TERM-002"]


def test_unreadable_files_leave_every_rule_draft_with_a_warning(tmp_path, monkeypatch):
    broken = tmp_path / "approvals.json"
    broken.write_text('{"schema": 1, "approvals": [], "approvals": []}', encoding="utf-8")
    monkeypatch.setenv("MANDAT_APPROVALS", str(broken))
    pack, notes = effective_pack(PACK, NOW)
    assert {s.status for s in pack.rules.values()} == {"draft"}
    assert notes and "не прочитаны" in notes[0]
    report = verify_case(load_case_dir(CONFIRMED), now=NOW).as_dict()
    assert any("не прочитаны" in w for w in report["warnings"])
    assert "approvals" not in report["rule_pack"]


def test_demo_report_is_unchanged_without_approvals():
    report = verify_case(load_case_dir(CONFIRMED)).as_dict()
    assert "approvals" not in report["rule_pack"]
    assert not [w for w in report["warnings"] if "Утверждени" in w]


def test_load_approvals_rejects_wrong_shapes(tmp_path):
    from mandat.rules.approvals import ApprovalError

    target = tmp_path / "a.json"
    for text in ('{"approvals": []}', '{"schema": 1, "approvals": {}}', "[]"):
        target.write_text(text, encoding="utf-8")
        with pytest.raises(ApprovalError):
            load_approvals(target)
    target.write_text(json.dumps({"schema": 1, "approvals": [{}] * 1001}), encoding="utf-8")
    with pytest.raises(ApprovalError, match="больше"):
        load_approvals(target)


def _pem(tmp_path, signer_key: Ed25519PrivateKey):
    path = tmp_path / "lawyer.pem"
    path.write_bytes(
        signer_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    path.chmod(0o600)
    return path


def test_cli_signs_and_verifies(tmp_path, monkeypatch, capsys):
    key = Ed25519PrivateKey.generate()
    signer = LocalSigner(key)
    approvals, keys = _files(tmp_path, [], signer)
    args = [
        "approvals-sign",
        "--rule",
        "R-TERM-002",
        "--key",
        str(_pem(tmp_path, key)),
        "--role",
        "юрист банка",
        "--norm-edition",
        "редакция на 19.11.2026",
        "--checked-on",
        datetime.now(UTC).date().isoformat(),
        "--valid-until",
        (datetime.now(UTC).date() + timedelta(days=300)).isoformat(),
        "--file",
        str(approvals),
    ]
    assert main(args) == 0
    assert signer.key_id in capsys.readouterr().err
    assert main(["approvals-verify", "--file", str(approvals), "--keys", str(keys)]) == 0
    assert "R-TERM-002: действует" in capsys.readouterr().out

    payload = json.loads(approvals.read_text(encoding="utf-8"))
    payload["approvals"][0]["approver_role"] = "кто-то другой"
    approvals.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert main(["approvals-verify", "--file", str(approvals), "--keys", str(keys)]) == 1
    assert "подпись не совпадает" in capsys.readouterr().out

    approvals.write_text("{", encoding="utf-8")
    assert main(["approvals-verify", "--file", str(approvals), "--keys", str(keys)]) == 2


def test_cli_refuses_an_open_pem_key_in_pilot(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    key = _pem(tmp_path, Ed25519PrivateKey.generate())
    args = ["approvals-sign", "--rule", "R-TERM-002", "--key", str(key), "--role", "юрист банка"]
    args += ["--norm-edition", "ред.", "--checked-on", "2026-11-19", "--valid-until", "2027-11-19"]
    assert main(args) == 2
    assert "запрещён" in capsys.readouterr().err


def test_protected_mode_refuses_a_keyring_the_service_can_write(tmp_path, monkeypatch):
    """Кто пишет в связку утверждающих, тот утверждает правила своим ключом: в pilot файл только для чтения."""
    signer = _signer()
    approvals, keys = _files(tmp_path, [_record("R-TERM-002", signer)], signer)
    monkeypatch.setenv("MANDAT_ENV", "pilot")
    monkeypatch.setenv("MANDAT_APPROVALS", str(approvals))
    monkeypatch.setenv("MANDAT_APPROVAL_KEYS", str(keys))
    keys.chmod(0o644)
    pack, notes = effective_pack(PACK, NOW)
    assert pack.rules["R-TERM-002"].status == "draft"
    assert notes and "на запись" in notes[0]
    # Сервис файл не пишет (смонтирован только для чтения), но права открывают запись группе и всем.
    monkeypatch.setattr("mandat.rules.approvals.os.access", lambda path, mode: False)
    keys.chmod(0o666)
    assert "на запись" in effective_pack(PACK, NOW)[1][0]
    keys.chmod(0o444)
    pack, notes = effective_pack(PACK, NOW)
    assert notes == []
    assert pack.rules["R-TERM-002"].status == "approved"


def test_stand_reads_a_writable_keyring(tmp_path, monkeypatch):
    signer = _signer()
    approvals, keys = _files(tmp_path, [_record("R-TERM-002", signer)], signer)
    keys.chmod(0o666)
    pack, _ = effective_pack(PACK, NOW, load_approvals(approvals, keys))
    assert pack.rules["R-TERM-002"].status == "approved"
