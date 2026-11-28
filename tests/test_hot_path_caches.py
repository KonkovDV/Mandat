"""Кэши горячих путей не меняют решений: тот же отчёт байт в байт и тот же допуск, что без кэша.

Разбор МЧД и ЕГРЮЛ кэшируется по виду, id и SHA-256 байтов документа; списки статусов — по SHA-256 текста и
ключам издателя; sequence издателя — по stat и началу registry.json. Свежесть списка, high-watermark, эпоха и бит
статуса считаются на каждый запрос, поэтому отзыв после кэшированной проверки виден сразу, а устаревший список
отвергается и из кэша.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandat.api.storage import MemoryStore
from mandat.audit import attestation, status_list
from mandat.audit.attestation import OperationRequest
from mandat.audit.signing import Keyring
from mandat.audit.status_list import StatusRegistry, check_status
from mandat.eval.synth import build, production_corpus
from mandat.intake import case_loader
from mandat.intake.case_loader import ParseCache, load_case_inline
from mandat.services import admission
from mandat.services.verify import verify_case

NOW = datetime(2026, 10, 21, 9, tzinfo=UTC)
ORG, REP, AUDIENCE = "7707083893", "123456789012", "bank-dbo-msb"
PSEUDONYM_KEY = b"p" * 32


@pytest.fixture(autouse=True)
def _cold_status_caches():
    status_list.clear_caches()
    yield
    status_list.clear_caches()


def _reports(manifests: list[dict], cache: ParseCache, monkeypatch) -> list[str]:
    monkeypatch.setattr(case_loader, "PARSE_CACHE", cache)
    return [
        json.dumps(verify_case(load_case_inline(m), now=NOW).as_dict(), ensure_ascii=False, sort_keys=True)
        for m in manifests
    ]


def test_reports_are_byte_identical_with_and_without_the_parse_cache(monkeypatch):
    manifests = [s.inline_manifest() for s in production_corpus(400)]
    cold = _reports(manifests, ParseCache(0), monkeypatch)
    warm_cache = ParseCache(4096)  # весь образец помещается: второй проход — только попадания
    first = _reports(manifests, warm_cache, monkeypatch)
    again = _reports(manifests, warm_cache, monkeypatch)
    assert cold == first == again
    assert warm_cache.hits > len(manifests)  # повторные проверки и второй проход читают разбор из кэша


def test_the_same_bytes_under_another_id_are_parsed_again(monkeypatch):
    monkeypatch.setattr(case_loader, "PARSE_CACHE", ParseCache(64))
    manifest = build("ok_charter_cleared", 3).inline_manifest()
    renamed = json.loads(json.dumps(manifest))
    renamed["documents"][0]["id"] = "poa-main"
    first, second = load_case_inline(manifest), load_case_inline(renamed)
    assert first.poas[0].doc_id == "poa-root"
    assert second.poas[0].doc_id == "poa-main"
    assert {ref.doc_id for ref in second.poas[0].refs.values()} == {"poa-main"}


def test_parse_cache_is_bounded_and_does_not_keep_failures():
    cache = ParseCache(2)
    for i in range(5):
        assert cache.get(("emchd_xml", "d", str(i)), lambda i=i: i) == i
    assert len(cache._data) == 2 and cache.misses == 5
    calls = []

    def broken() -> None:
        calls.append(1)
        raise ValueError("не разобрано")

    for _ in range(2):
        with pytest.raises(ValueError):
            cache.get(("emchd_xml", "bad", "x"), broken)
    assert len(calls) == 2


def _issuer(tmp_path, verdict: str = "confirmed"):
    key = Ed25519PrivateKey.generate()
    registry = StatusRegistry.open(tmp_path / "status")
    index = registry.allocate({"org_inn": ORG, "verdict": verdict, "poa_numbers": ["poc"]})
    report = {
        "verdict": verdict,
        "profile": "strict",
        "organization": {"inn": ORG},
        "requester": {"inn": REP},
        "operation": {"code": "PAYMENT_SIGN", "date": NOW.date().isoformat(), "amount_rub": 500_000},
        "chain": {"poa_numbers": ["poc"]},
        "rule_pack": {"sha256": "a" * 64},
        "reproducibility_hash": "b" * 64,
    }
    credential = attestation.issue(
        report,
        key,
        now=NOW,
        status=registry.credential_status(index),
        pseudonym_key=PSEUDONYM_KEY,
        audience=AUDIENCE,
        nonce="n-" + "1" * 20,
    )
    registry.save(key, now=NOW)
    return key, registry, index, credential


def test_revocation_after_a_cached_check_is_seen_at_once(tmp_path):
    key, registry, index, credential = _issuer(tmp_path)
    for _ in range(3):
        assert check_status(credential, tmp_path / "status", key.public_key(), now=NOW)["state"] == "действует"
    registry.mark(index, "revocation", "POA_REVOKED")
    registry.save(key, now=NOW)
    assert check_status(credential, tmp_path / "status", key.public_key(), now=NOW)["state"] == "отозвано"


def test_freshness_window_is_applied_to_a_cached_list(tmp_path):
    key, _, _, credential = _issuer(tmp_path)
    status_dir = tmp_path / "status"
    assert check_status(credential, status_dir, key.public_key(), now=NOW, operation_risk="payment")["state"] == (
        "действует"
    )
    later = NOW + timedelta(hours=3)
    assert check_status(credential, status_dir, key.public_key(), now=later, operation_risk="payment")["state"] == (
        "список устарел"
    )


def test_issuer_state_rewritten_in_place_is_read_again(tmp_path):
    """Тот же inode и, возможно, тот же такт часов файловой системы: новое sequence видно по началу файла."""
    key, _, _, credential = _issuer(tmp_path)
    status_dir = tmp_path / "status"
    assert check_status(credential, status_dir, key.public_key(), now=NOW)["state"] == "действует"
    state = status_list.published_root(status_dir) / "registry.json"
    text = state.read_text(encoding="utf-8")
    assert '"sequence": 1,' in text
    with state.open("r+", encoding="utf-8") as fh:  # запись на месте, без os.replace
        fh.write(text.replace('"sequence": 1,', '"sequence": 2,'))
    with pytest.raises(ValueError, match="старше состояния издателя"):
        check_status(credential, status_dir, key.public_key(), now=NOW)


def test_list_cached_for_one_issuer_key_is_not_trusted_for_another(tmp_path):
    key, _, _, credential = _issuer(tmp_path)
    assert check_status(credential, tmp_path / "status", key.public_key(), now=NOW)["state"] == "действует"
    stranger = Ed25519PrivateKey.generate().public_key()
    with pytest.raises(ValueError, match="подпись списка"):
        check_status(credential, tmp_path / "status", stranger, now=NOW)


@pytest.mark.parametrize(
    ("change", "allowed"),
    [
        ({}, True),
        ({"amount_rub": 500_001}, False),
        ({"audience": "other"}, False),
        ({"representative_inn": "1" * 12}, False),
    ],
)
def test_admission_decision_is_the_same_cold_and_warm(tmp_path, change, allowed):
    key, _, _, credential = _issuer(tmp_path)
    config = admission.AdmissionConfig(Keyring.of([key.public_key()]), tmp_path / "status", PSEUDONYM_KEY)
    fields = {
        "operation": "PAYMENT_SIGN",
        "amount_rub": 500_000,
        "representative_inn": REP,
        "audience": AUDIENCE,
        "nonce": "n-" + "1" * 20,
        "organization_inn": ORG,
        "operation_date": NOW.date(),
        **change,
    }
    moment = NOW + timedelta(minutes=1)

    def decide() -> list[str]:
        request = admission.AdmissionRequest(credential, OperationRequest(**fields), "payment")
        return admission.decide_detailed(request, config, MemoryStore(), now=moment, protected=True).problems

    status_list.clear_caches()
    cold = decide()
    warm = [decide() for _ in range(3)]
    assert warm == [cold] * 3
    assert (cold == []) is allowed


def _jcs_reference(text: str) -> str:
    """Прежний посимвольный цикл: эталон для быстрого пути ``jcs._string``."""
    from mandat.audit import jcs

    out = ['"']
    for char in text:
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            raise jcs.JcsError("одиночный суррогат в строке")
        out.append(jcs._ESCAPES.get(char) or (f"\\u{code:04x}" if code < 0x20 else char))
    return "".join(out) + '"'


def test_jcs_fast_paths_write_the_same_text():
    from hypothesis import given, settings
    from hypothesis import strategies as st

    from mandat.audit import jcs

    @settings(max_examples=400, deadline=None)
    @given(st.text(alphabet=st.characters(codec=None, min_codepoint=0, max_codepoint=0x10FFFF), max_size=12))
    def same(text: str) -> None:
        try:
            expected = _jcs_reference(text)
        except jcs.JcsError:
            with pytest.raises(jcs.JcsError):
                jcs._string(text)
            return
        assert jcs._string(text) == expected

    same()
    for number in (0, 7, -7, 1000, 10**15, 2**53 - 1, -(2**53 - 1), 123_456_789):
        assert jcs.canonicalize(number) == jcs._number(float(number))


def test_redis_watermarks_go_in_one_round_trip_with_the_same_values():
    import fakeredis

    from mandat.api.storage import RedisStore

    store = RedisStore(fakeredis.FakeRedis())
    store.watermark_raise("a#1", 5)
    calls: list[str] = []
    for name in ("watermark_get", "watermark_raise", "watermark_get_many", "watermark_raise_many"):
        method = getattr(store, name)
        setattr(store, name, lambda *a, _m=method, _n=name: (calls.append(_n), _m(*a))[1])
    seen = admission.StoreWatermarks(store)
    seen.prefetch(["a#1", "b#1"])
    assert (seen.get("a#1"), seen.get("b#1", -1)) == (5, -1)
    seen["b#1"] = 3
    seen["a#1"] = 4  # watermark только растёт: 4 не опускает 5
    seen.save()
    assert calls == ["watermark_get_many", "watermark_raise_many"]
    assert (store.watermark_get("a#1"), store.watermark_get("b#1")) == (5, 3)


def test_sealed_store_passes_batch_watermarks_through_only_if_the_store_has_them(tmp_path, monkeypatch):
    import fakeredis

    from mandat.api.sealing import sealed
    from mandat.api.storage import RedisStore, SQLiteStore

    monkeypatch.setenv("MANDAT_STORE_KEY", "s" * 64)
    assert sealed(RedisStore(fakeredis.FakeRedis())).watermark_get_many(["x"]) == {"x": None}
    assert getattr(sealed(SQLiteStore(tmp_path / "s.db")), "watermark_get_many", None) is None


def test_serve_takes_uvloop_and_httptools_only_when_installed(monkeypatch):
    import importlib.util

    from mandat import cli

    for present in (set(), {"uvloop", "httptools"}):
        monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, _p=present: object() if name in _p else None)
        options = cli.server_options()
        assert options["http"].endswith("NoDelayHttpToolsProtocol" if present else "NoDelayH11Protocol")
        assert options.get("loop") == ("uvloop" if present else None)
