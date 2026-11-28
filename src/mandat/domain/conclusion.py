"""Заключение юриста по уставу клиента: вывод об ограничениях полномочий руководителя для одной редакции устава.

Юрист читает устав один раз на редакцию, а правила применяют его вывод ко всем операциям клиента, пока редакция
та же (``docs/CONCLUSIONS.md``). Область заключения — ОГРН клиента, sha256 документа устава (редакция),
необязательный список операций и предел суммы. Вывод — «применимых ограничений нет» или ограничения тех же видов,
что у фактов устава (``intake.charter``): порог одобрения сделки в рублях, совместная подпись, одобрение крупной
сделки (порог по желанию).

Жизнь записи: черновик → утверждено → отозвано; утверждённое заключение уходит на перепроверку, когда в кейсе
клиента приходит другая редакция устава. Истечение не хранится: утверждённое заключение истекает само, когда день
проверки позже ``valid_until``. Кто что может, решает ``review.conclusions``, как правила применяют вывод —
``rules.conclusions``.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from mandat.data import sha256_json
from mandat.domain.identifiers import ogrn_valid

# Дни считаются по Москве (UTC+3, без перехода на летнее время), как даты выписок в правилах.
MSK = timezone(timedelta(hours=3), "MSK")
OUTCOMES = ("no_restriction", "restriction")
RESTRICTION_KINDS = ("approval_threshold", "joint_signature", "large_deal_approval")
STATUSES = ("draft", "approved", "revoked", "recheck")
EXPIRED = "expired"
# Кто подготовил черновик: юрист сам, модель банка через шлюз цитаты или кейс без модели (пустой проект).
ORIGINS = ("manual", "advisory", "case")
STATUS_RU = {
    "draft": "черновик",
    "approved": "утверждено",
    "revoked": "отозвано",
    "recheck": "на перепроверке",
    EXPIRED: "срок истёк",
}
OUTCOME_RU = {"no_restriction": "применимых ограничений нет", "restriction": "есть ограничение"}
MAX_BASIS_CHARS = 4000
MAX_QUOTE_CHARS = 400
MAX_RESTRICTIONS = 5
MAX_SCOPE_OPERATIONS = 20
# Срок заключения задаёт процедура банка, но не дальше года: так же ограничены записи утверждения правил.
MAX_VALIDITY_DAYS = 366
MAX_AMOUNT_RUB = 2**53 - 1
ID = re.compile(r"cnc-[0-9a-f]{12}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
# Поля вывода: их хеш — отпечаток заключения, его подписывает журнал при утверждении и называет отчёт.
CONTENT_FIELDS = (
    "id",
    "ogrn",
    "charter_sha256",
    "outcome",
    "restrictions",
    "basis",
    "operations",
    "max_amount_rub",
    "valid_until",
)
EDITABLE_FIELDS = ("outcome", "restrictions", "basis", "operations", "max_amount_rub", "valid_until")


def msk_day(moment: datetime) -> date:
    return moment.astimezone(MSK).date()


def rub(amount: int | None) -> str:
    return "—" if amount is None else f"{amount:,} ₽".replace(",", " ")


def ogrn_ok(value: object) -> bool:
    """ОГРН юрлица: устав есть только у организации, поэтому 13 цифр с верным контрольным числом."""
    return isinstance(value, str) and len(value) == 13 and ogrn_valid(value)


def content_sha256(body: Mapping[str, Any]) -> str:
    return sha256_json({name: body.get(name) for name in CONTENT_FIELDS})


@dataclass(frozen=True)
class Restriction:
    kind: str
    value_rub: int | None = None
    # Порог «и более»: сумма, равная порогу, тоже требует одобрения. По умолчанию так и считается — это строже.
    inclusive: bool = True
    quote: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "value_rub": self.value_rub, "inclusive": self.inclusive, "quote": self.quote}

    def describe(self) -> str:
        edge = "от" if self.inclusive else "свыше"
        if self.kind == "approval_threshold":
            return f"сделки на сумму {edge} {rub(self.value_rub)} — только с одобрения органа общества"
        if self.kind == "joint_signature":
            return "документы подписываются совместно: одной подписи руководителя мало"
        if self.value_rub is None:
            return "крупные сделки — только с одобрения органа общества, порог в рублях не указан"
        return f"крупная сделка {edge} {rub(self.value_rub)} — только с одобрения органа общества"


def _amount(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_AMOUNT_RUB:
        raise ValueError(f"{name} — целое число рублей от 1 до 2**53 − 1")
    return value


def parse_restriction(raw: object) -> Restriction:
    """Одно ограничение из записи или запроса. ``ValueError`` называет, что не так."""
    if not isinstance(raw, Mapping):
        raise ValueError("ограничение — объект с полями kind, value_rub, inclusive, quote")
    extra = sorted(set(raw) - {"kind", "value_rub", "inclusive", "quote"})
    if extra:
        raise ValueError(f"ограничение: лишние поля {extra[:5]}")
    kind = raw.get("kind")
    if kind not in RESTRICTION_KINDS:
        raise ValueError(f"вид ограничения — один из {', '.join(RESTRICTION_KINDS)}")
    value = _amount(raw.get("value_rub"), "value_rub")
    if kind == "approval_threshold" and value is None:
        raise ValueError("у порога одобрения нужна сумма value_rub")
    if kind == "joint_signature" and value is not None:
        raise ValueError("у совместной подписи суммы нет")
    inclusive = raw.get("inclusive", True)
    if not isinstance(inclusive, bool):
        raise ValueError("inclusive — true или false")
    quote = raw.get("quote")
    if quote is not None and (not isinstance(quote, str) or len(quote) > MAX_QUOTE_CHARS):
        raise ValueError(f"цитата ограничения — строка до {MAX_QUOTE_CHARS} знаков")
    return Restriction(str(kind), value, inclusive, (quote.strip() or None) if isinstance(quote, str) else None)


def checked_content(fields: Mapping[str, Any], *, operations: Collection[str], today: date) -> dict[str, Any]:
    """Поля вывода нового или исправленного заключения, проверенные и в каноническом виде.

    ``operations`` — коды каталога операций, ``today`` — день по Москве. ``ValueError`` — что не так, по-русски.
    """
    ogrn = fields.get("ogrn")
    if not ogrn_ok(ogrn):
        raise ValueError("ОГРН юрлица: 13 цифр с верным контрольным числом")
    sha = fields.get("charter_sha256")
    if not isinstance(sha, str) or not _HEX64.fullmatch(sha):
        raise ValueError("charter_sha256 — 64 строчных шестнадцатеричных знака")
    outcome = fields.get("outcome")
    if outcome not in OUTCOMES:
        raise ValueError("вывод: no_restriction (ограничений нет) или restriction (есть ограничение)")
    raw = fields.get("restrictions") or []
    if not isinstance(raw, list) or len(raw) > MAX_RESTRICTIONS:
        raise ValueError(f"restrictions — список до {MAX_RESTRICTIONS} ограничений")
    restrictions = [parse_restriction(item) for item in raw]
    if outcome == "restriction" and not restrictions:
        raise ValueError("вывод «есть ограничение» без единого ограничения")
    if outcome == "no_restriction" and restrictions:
        raise ValueError("вывод «ограничений нет» не несёт ограничений")
    basis = fields.get("basis")
    if not isinstance(basis, str) or not basis.strip():
        raise ValueError("нужно основание: какие положения устава юрист прочитал и почему такой вывод")
    if len(basis) > MAX_BASIS_CHARS:
        raise ValueError(f"основание длиннее {MAX_BASIS_CHARS} знаков")
    codes = fields.get("operations") or []
    if not isinstance(codes, list) or len(codes) > MAX_SCOPE_OPERATIONS or not all(isinstance(c, str) for c in codes):
        raise ValueError(f"operations — список до {MAX_SCOPE_OPERATIONS} кодов операций")
    unknown = sorted(set(codes) - set(operations))
    if unknown:
        raise ValueError(f"операции не из каталога: {', '.join(code[:40] for code in unknown[:5])}")
    cap = _amount(fields.get("max_amount_rub"), "max_amount_rub")
    raw_day = fields.get("valid_until")
    if not isinstance(raw_day, str) or not _DAY.fullmatch(raw_day):
        raise ValueError("valid_until — дата ГГГГ-ММ-ДД")
    try:
        until = date.fromisoformat(raw_day)
    except ValueError as exc:
        raise ValueError("valid_until — дата ГГГГ-ММ-ДД") from exc
    if until < today:
        raise ValueError(f"срок заключения {until:%d.%m.%Y} уже прошёл")
    if until > today + timedelta(days=MAX_VALIDITY_DAYS):
        raise ValueError(f"срок заключения — не дальше {MAX_VALIDITY_DAYS} дней от сегодня")
    return {
        "ogrn": ogrn,
        "charter_sha256": sha,
        "outcome": outcome,
        "restrictions": [r.as_dict() for r in restrictions],
        "basis": basis.strip(),
        "operations": sorted(set(codes)),
        "max_amount_rub": cap,
        "valid_until": until.isoformat(),
    }


def _moment(value: object, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} — дата и время ISO 8601")
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError(f"{name} без часового пояса")
    return moment


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} — непустая строка")
    return value


@dataclass(frozen=True)
class Conclusion:
    """Запись реестра в том виде, в каком её читают правила. Строится только ``from_record``."""

    id: str
    ogrn: str
    charter_sha256: str
    status: str
    outcome: str | None
    restrictions: tuple[Restriction, ...]
    basis: str
    operations: tuple[str, ...]
    max_amount_rub: int | None
    valid_until: date
    author: str
    created_at: datetime
    confirmed: bool
    approver: str | None
    approved_at: datetime | None
    revoked_at: datetime | None
    recheck_at: datetime | None
    version: int
    content_sha256: str

    @classmethod
    def from_record(cls, body: Mapping[str, Any], version: int = 0) -> Conclusion:
        """Запись из хранилища. Повреждённая запись — ``ValueError``: такое заключение не применяется.

        Утверждённое заключение и заключение на перепроверке обязаны быть полными. Черновик бывает и пустым
        (проект из кейса, который автор ещё не заполнил): у него вывод может отсутствовать.
        """
        cid = body.get("id")
        if not isinstance(cid, str) or not ID.fullmatch(cid):
            raise ValueError("номер заключения не по формату")
        ogrn, sha = body.get("ogrn"), body.get("charter_sha256")
        if not ogrn_ok(ogrn) or not isinstance(sha, str) or not _HEX64.fullmatch(sha):
            raise ValueError("ОГРН или sha256 устава не по формату")
        status = body.get("status")
        if status not in STATUSES:
            raise ValueError("статус заключения не из списка")
        raw_day = body.get("valid_until")
        if not isinstance(raw_day, str) or not _DAY.fullmatch(raw_day):
            raise ValueError("valid_until — дата ГГГГ-ММ-ДД")
        complete = status in {"approved", "recheck"}
        outcome = body.get("outcome")
        if (outcome is not None and outcome not in OUTCOMES) or (complete and outcome is None):
            raise ValueError("вывод заключения не из списка")
        restrictions: list[Restriction] = []
        for item in body.get("restrictions") or []:
            try:
                restrictions.append(parse_restriction(item))
            except ValueError:
                if complete:
                    raise
        if complete and (outcome == "restriction") != bool(restrictions):
            raise ValueError("вывод и ограничения заключения не согласованы")
        basis = body.get("basis") or ""
        if not isinstance(basis, str) or (complete and not basis.strip()):
            raise ValueError("у утверждённого заключения нет основания")
        codes = body.get("operations") or []
        if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
            raise ValueError("operations — список кодов")
        approver = body.get("approver")
        approved_at = _moment(body.get("approved_at"), "approved_at")
        if complete and (not isinstance(approver, str) or not approver.strip() or approved_at is None):
            raise ValueError("у утверждённого заключения нет утверждающего или даты утверждения")
        created_at = _moment(body.get("created_at"), "created_at")
        if created_at is None:
            raise ValueError("нет даты создания заключения")
        return cls(
            id=cid,
            ogrn=str(ogrn),
            charter_sha256=sha,
            status=str(status),
            outcome=None if outcome is None else str(outcome),
            restrictions=tuple(restrictions),
            basis=basis,
            operations=tuple(codes),
            max_amount_rub=_amount(body.get("max_amount_rub"), "max_amount_rub"),
            valid_until=date.fromisoformat(raw_day),
            author=_text(body.get("author"), "author"),
            created_at=created_at,
            confirmed=body.get("confirmed") is True,
            approver=approver if isinstance(approver, str) else None,
            approved_at=approved_at,
            revoked_at=_moment(body.get("revoked_at"), "revoked_at"),
            recheck_at=_moment(body.get("recheck_at"), "recheck_at"),
            version=version,
            content_sha256=content_sha256(body),
        )

    def status_on(self, day: date) -> str:
        """Статус на день по Москве: утверждённое заключение после ``valid_until`` — истёкшее."""
        if self.status == "approved" and day > self.valid_until:
            return EXPIRED
        return self.status

    def scope_text(self) -> str:
        """Область сверх ОГРН и редакции устава: операции и предел суммы, если они заданы."""
        parts = []
        if self.operations:
            parts.append(", ".join(self.operations))
        if self.max_amount_rub is not None:
            parts.append(f"суммы до {rub(self.max_amount_rub)}")
        return "; ".join(parts)
