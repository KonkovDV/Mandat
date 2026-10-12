"""HTML-карточка кейса для сотрудника. Один файл, без внешних ресурсов и скриптов.

Все значения экранируются: данные документов клиента — недоверенный ввод.
"""

from __future__ import annotations

from html import escape
from typing import Any

COLORS = {
    "confirmed": "#1f7a4d",
    "refuted": "#b3401a",
    "needs_review": "#9a6a00",
    "insufficient_data": "#4a5a6a",
}
EFFECT_RU = {
    "refutes": "опровергает",
    "not_verified": "не проверено",
    "needs_review": "на разбор",
    "supports": "подтверждает",
    "info": "справка",
}
STATUS_RU = {
    "ok": "выполнено",
    "refuted": "опровергнуто",
    "review": "на разбор",
    "not_verified": "не проверено",
    "missing": "нет данных",
    "waived": "не выполнялась (профиль)",
    "not_applicable": "не применимо",
    "info_only": "только справка",
}

CSS = """
body{font-family:Calibri,Arial,sans-serif;background:#f4f6f8;color:#14202c;margin:0;padding:24px}
main{max-width:1080px;margin:0 auto}
.card{background:#fff;border:1px solid #d9e0e6;border-radius:10px;padding:18px 22px;margin-bottom:16px}
.verdict{display:inline-block;color:#fff;font-weight:700;padding:6px 14px;border-radius:999px;font-size:18px}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #e3e8ed;padding:6px 8px;text-align:left;
vertical-align:top;font-size:14px}th{background:#eef2f5}.muted{color:#5a6673;font-size:13px}
.warn{background:#fff6e5;border-color:#f0c36d}code{font-size:12px;word-break:break-all}
.stand{background:#eef3fb;border-color:#9db4d6}.stand h2{margin-top:0;font-size:16px}
"""


def _e(value: Any) -> str:
    return escape("" if value is None else str(value))


RIGHT_STATUS_RU = {"recommended": "рекомендована", "pending_review": "после разбора"}
MODE_RU = {"shadow_recommendation": "теневой режим: только рекомендация"}
QUEUE_RU = {"exceptions": "исключения", "auto_recommendation": "без разбора, рекомендация"}


def _date(iso: str | None) -> str:
    if not iso or len(iso) < 10:
        return iso or "—"
    return f"{iso[8:10]}.{iso[5:7]}.{iso[0:4]}"


def _rub(amount: int | None) -> str:
    return "—" if amount is None else f"{amount:,}".replace(",", " ") + " ₽"


def render(report: dict[str, Any]) -> str:
    color = COLORS.get(report["verdict"], "#4a5a6a")
    op = report["operation"]
    rows = []
    for f in report["findings"]:
        refs = "<br>".join(
            f"<code>{_e(r['doc_id'])}@{_e(r['doc_sha256'][:12])}#{_e(r['locator'])}</code>" for r in f["refs"]
        )
        rows.append(
            f"<tr><td>{_e(f['rule_id'])}</td><td>{_e(EFFECT_RU.get(f['effect'], f['effect']))}</td>"
            f"<td>{_e(f['message'])}<div class='muted'>{_e(f['norm_ref'])} · правило {_e(f['rule_status'])}</div></td>"
            f"<td>{refs}</td></tr>"
        )
    checks = "".join(
        f"<tr><td>{_e(v['label'])}</td><td>{_e(STATUS_RU.get(v['status'], v['status']))}</td></tr>"
        for v in report["checks"].values()
    )
    steps = (
        "".join(
            f"<li><b>{_e(s['action'])}</b> <span class='muted'>— {_e(s['unit'])}</span></li>"
            for s in report["next_steps"]
        )
        or "<li>До операции в банке нужна проверка доверенных источников: реестр МЧД, ЕГРЮЛ и КЭП. Это не разрешение провести платёж.</li>"
    )
    chain = "".join(f"<li>{_e(row)}</li>" for row in report["chain"]["links"]) or "<li>Цепочка не построена</li>"
    warnings = "".join(f"<li>{_e(w)}</li>" for w in report["warnings"])
    right = report.get("proposed_right")
    right_html = (
        f"<p>Роль <b>{_e(right['role'])}</b> — {_e(RIGHT_STATUS_RU.get(right['status'], right['status']))}, "
        f"{_e(MODE_RU.get(right['mode'], right['mode']))}; "
        f"основание: {_e(', '.join(right['basis']))}</p>"
        if right
        else "<p>Права не рекомендуются</p>"
    )
    routing = report.get("routing") or {}
    stand = "".join(f"<li>{_e(note)}</li>" for note in report.get("stand") or [])
    stand_html = f'<div class="card stand"><h2>Режим стенда</h2><ul>{stand}</ul></div>' if stand else ""
    signals = "".join(f"<li>{_e(msg)}</li>" for msg in report.get("signals") or [])
    signals_html = f'<div class="card warn"><h2>Сигналы</h2><ul>{signals}</ul></div>' if signals else ""
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Мандат — кейс {_e(report["case_id"])}</title><style>{CSS}</style></head><body><main>
<div class="card"><div class="muted">Мандат · проверка полномочий · профиль {_e(report["profile"])}</div>
<h1>Кейс {_e(report["case_id"])}</h1>
<span class="verdict" style="background:{color}">{_e(report["verdict_label"])}</span>
<p><b>Сильнейшее возражение:</b> {_e(report.get("objection"))}</p>
<p><b>Операция:</b> {_e(op.get("name") or op["code"])} · {_e(_date(op["date"]))} · сумма {_e(_rub(op.get("amount_rub")))}</p>
<p><b>Организация:</b> {_e(report["organization"].get("name"))} (ИНН {_e(report["organization"].get("inn"))})
 · <b>Заявитель:</b> {_e(report["requester"].get("full_name"))}</p></div>
{signals_html}
{stand_html}
<div class="card"><h2>Цепочка полномочий</h2><ul>{chain}</ul></div>
<div class="card"><h2>Что сделать дальше</h2><ul>{steps}</ul>
<p class="muted">Очередь: {_e(QUEUE_RU.get(routing.get("queue"), routing.get("queue")))}
 · приоритет {_e(routing.get("priority") or "—")} · SLA {_e(routing.get("sla_hours") or "—")} ч (заглушка до калибровки с банком)</p>
{right_html}</div>
<div class="card"><h2>Группы проверок</h2><table><tr><th>Группа</th><th>Статус</th></tr>{checks}</table></div>
<div class="card"><h2>Находки и доказательства</h2><table><tr><th>Правило</th><th>Эффект</th><th>Вывод</th>
<th>Источник</th></tr>{"".join(rows)}</table></div>
<div class="card warn"><h2>Границы результата</h2><ul>{warnings}
<li>«Подтверждено» означает соответствие правилам этой версии, а не юридическое заключение и не выдачу прав.</li>
</ul></div>
<div class="card muted">Набор правил {_e(report["rule_pack"]["pack_id"])} {_e(report["rule_pack"]["version"])}
 (<code>{_e(report["rule_pack"]["sha256"][:16])}</code>) · хеш отчёта <code>{_e(report["reproducibility_hash"][:16])}</code>
 · {_e(report["generated_at"])} · {_e(report["engine"])}</div>
</main></body></html>
"""
