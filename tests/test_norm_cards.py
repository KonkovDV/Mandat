"""Карточка нормы на каждое правило для юриста банка.

Карточки собирает scripts/gen_norm_cards.py из пакета правил, трассировки норм и вопросов юристу. Файлы
совпадают с генератором, ни одна карточка не называет правило утверждённым, цитата нормы — только с
официального сайта с адресом и датой, а хеш на карточке — тот, что подписывает запись утверждения.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date

import pytest
from conftest import ROOT
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandat.audit.signing import LocalSigner
from mandat.rules.approvals import rule_text_sha256, sign_record
from mandat.rules.pack import default_pack

sys.path.insert(0, str(ROOT / "scripts"))
import gen_norm_cards as gen  # noqa: E402

PACK = default_pack()
CARDS = ROOT / "docs" / "legal" / "cards"


@pytest.fixture(scope="module")
def rendered() -> dict:
    return gen.render_all()


def test_norm_cards_are_up_to_date(rendered):
    for path, text in rendered.items():
        assert path.read_text(encoding="utf-8") == text, f"{path.name} устарел: python scripts/gen_norm_cards.py"
    assert {p.name for p in CARDS.glob("*.md")} == {p.name for p in rendered if p.parent == CARDS}


def test_every_rule_has_exactly_one_card():
    assert sorted(p.stem for p in CARDS.glob("*.md")) == sorted(PACK.rules)


@pytest.mark.parametrize("rule_id", sorted(PACK.rules))
def test_card_does_not_claim_approval_and_shows_the_signed_hash(rule_id):
    text = (CARDS / f"{rule_id}.md").read_text(encoding="utf-8")
    assert "**Статус:** черновик (`draft`). Юрист банка правило не утверждал." in text
    for claim in ("утверждено юристом", "согласовано юристом", "Статус:** утверждено", "status: approved"):
        assert claim not in text
    assert f"`{rule_text_sha256(PACK.rules[rule_id])}`" in text
    assert "- [ ] Утверждаю" in text and "- [ ] Отклоняю" in text
    for test_name in PACK.rules[rule_id].tests:
        assert f"`{test_name}`" in text


def test_hash_on_the_card_is_what_an_approval_record_signs():
    spec = PACK.rules["R-TERM-002"]
    record = sign_record(
        spec,
        PACK.pack_id,
        LocalSigner(Ed25519PrivateKey.generate()),
        approver_role="юрист банка",
        norm_edition="ред.",
        norm_checked_on=date(2026, 11, 16),
        valid_until=date(2027, 11, 15),
    )
    assert f"`{record['rule_sha256']}`" in (CARDS / "R-TERM-002.md").read_text(encoding="utf-8")


B3_RULES = {"R-FMT-003", "R-ID-003", "R-ID-004", "R-COV-001", "R-COV-002"}
B3_DOCUMENTS = {"0001202110080020", "0001202601210006", "0001202110080021"}  # № 857, № 1001, № 856


def test_repository_quotes_are_the_b3_orders_from_official_publication():
    """Цитаты есть у пяти правил, все — из официального опубликования приказов № 857, № 1001, № 856."""
    payload = json.loads((ROOT / "docs" / "legal" / "quotes.json").read_text(encoding="utf-8"))
    rows = payload["quotes"]
    assert payload["schema"] == 1 and {row["rule_id"] for row in rows} == B3_RULES
    assert {row["url"].rsplit("/", 1)[1] for row in rows} == B3_DOCUMENTS
    for path in CARDS.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        if path.stem in B3_RULES:
            assert "сверено 2026-11-17" in text and "Цитаты из официальной публикации" not in text, path.name
        else:
            assert "Цитаты из официальной публикации с адресом и датой сверки нет." in text, path.name


@pytest.mark.parametrize(
    ("text", "shown"), [("3) сведения", "> 3\\) сведения"), ("7. Классификатор", "> 7\\. Классификатор")]
)
def test_quote_that_starts_with_an_item_number_stays_verbatim(tmp_path, monkeypatch, text, shown):
    quotes = tmp_path / "quotes.json"
    quotes.write_text(json.dumps({"schema": 1, "quotes": [_quote(text=text)]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(gen, "QUOTES", quotes)
    loaded = gen.load_quotes(list(PACK.rules))
    card = gen.render_card(PACK.rules["R-TERM-002"], PACK, [], [], loaded["R-TERM-002"], {})
    assert shown in card


def _quote(**over) -> dict:
    row = {
        "rule_id": "R-TERM-002",
        "source": "Гражданский кодекс Российской Федерации, ст. 186",
        "url": "https://publication.pravo.gov.ru/document/0000000000000000",
        "checked_on": "2026-11-16",
        "text": "текст пункта",
    }
    row.update(over)
    return row


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"url": "https://www.consultant.ru/document/cons_doc_LAW_5142/"}, "не официальной публикации"),
        ({"url": "http://publication.pravo.gov.ru/document/1"}, "не официальной публикации"),
        ({"url": "https://publication.pravo.gov.ru.example.com/x"}, "не официальной публикации"),
        ({"checked_on": ""}, "нужны source, text и checked_on"),
        ({"rule_id": "R-NOPE-001"}, "нет в пакете"),
    ],
)
def test_quotes_only_from_official_publication(tmp_path, monkeypatch, change, message):
    quotes = tmp_path / "quotes.json"
    quotes.write_text(json.dumps({"schema": 1, "quotes": [_quote(**change)]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(gen, "QUOTES", quotes)
    with pytest.raises(SystemExit, match=message):
        gen.load_quotes(list(PACK.rules))


def test_official_quote_appears_on_the_card_with_address_and_date(tmp_path, monkeypatch):
    quotes = tmp_path / "quotes.json"
    quotes.write_text(json.dumps({"schema": 1, "quotes": [_quote()]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(gen, "QUOTES", quotes)
    loaded = gen.load_quotes(list(PACK.rules))
    card = gen.render_card(PACK.rules["R-TERM-002"], PACK, [], [], loaded["R-TERM-002"], {})
    assert "> текст пункта" in card
    assert "<https://publication.pravo.gov.ru/document/0000000000000000>, сверено 2026-11-16" in card
    assert "Цитаты из официальной публикации" not in card


def test_rule_ranges_are_expanded_without_parenthetical_notes():
    known = list(PACK.rules)
    found = gen.expand("R-SUB-001…005, R-SUB-007…010 (R-SUB-006 — ст. 188 п. 3)", known)
    assert "R-SUB-006" not in found
    assert found == [f"R-SUB-{n:03d}" for n in (1, 2, 3, 4, 5, 7, 8, 9, 10)]
    assert gen.expand("R-SUB-001…R-SUB-003", known) == ["R-SUB-001", "R-SUB-002", "R-SUB-003"]
    assert set(gen.expand("R-EIO-*", known)) == {r for r in known if r.startswith("R-EIO-")}


def test_observed_fields_carry_no_case_values(rendered):
    """В таблице полей нет ИНН, ОГРН и номеров МЧД из кейсов: только пути и метки."""
    for path, text in rendered.items():
        if path.parent != CARDS:
            continue
        fields = text.split("## Поля документов", 1)[1].split("## Вопрос юристу", 1)[0]
        assert not re.search(r"\d{10}", fields), path.name
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", fields), path.name
