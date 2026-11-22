"""HTML-карточка кейса для сотрудника. Один файл, без внешних ресурсов и скриптов.

Все значения экранируются: данные документов клиента — недоверенный ввод. Карточка отдаётся API с политикой
``default-src 'none'; style-src 'unsafe-inline'``, поэтому в ней только разметка, встроенный CSS и встроенный SVG:
без скриптов, картинок и веб-шрифтов. Вёрстка держит ширину экрана от 280 px: на узком экране таблицы
находок и документов складываются в блоки с подписями полей. Светлая и тёмная темы — по настройке устройства,
при печати — светлая без теней.
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
# Цвет метки статуса группы: тот же смысл, что у эффекта находки.
STATUS_TONE = {
    "ok": "ok",
    "refuted": "no",
    "review": "review",
    "not_verified": "gap",
    "missing": "gap",
    "waived": "info",
    "not_applicable": "info",
    "info_only": "info",
}
EFFECT_TONE = {"supports": "ok", "refutes": "no", "needs_review": "review", "not_verified": "gap", "info": "info"}

# Знак вердикта: галочка, крест, восклицание, вопрос. Цвет — не единственный признак вердикта.
_ICON_PATH = {
    "confirmed": "M5 12.5l4.2 4.2L19 7",
    "refuted": "M7 7l10 10M17 7L7 17",
    "needs_review": "M12 6v8M12 18h.01",
    "insufficient_data": "M9.2 9a3 3 0 1 1 4.3 2.7c-.9.4-1.5 1.2-1.5 2.1V14M12 18h.01",
}
# Знак «Мандата» — тот же, что в README, на сайте и в презентации (docs/assets/logo-mark.svg).
# Цвета частей заданы в CSS отчёта, поэтому в тёмной теме знак светлеет вместе с текстом.
_MARK = (
    '<svg class="mark" viewBox="120 148 157 132" aria-hidden="true" focusable="false">'
    '<path class="m-n" d="M273 185.5 242 204 242 271 273 251.5ZM147 152 124 168 124 246.5 133 255 153.5 266'
    ' 153.5 185.5 159 185.5 181 200 204.5 185.5Z"/>'
    '<path class="m-d" d="M167 209 167 251.5 204.5 276 204.5 232Z"/>'
    '<path class="m-b" d="M273 157 264 152 183 200 207 215.5 273 175Z"/></svg>'
)

CSS = """
:root{color-scheme:light dark;--bg:#f3f6fb;--card:#fff;--ink:#0b1f44;--ink-2:#0a2973;--muted:#4f5f80;
--line:#dbe3f0;--soft:#eef2f8;--accent:#004cbc;--warn-bg:#fff6e5;--warn-line:#f0c36d;--stand-bg:#eef3fb;
--stand-line:#9db4d6;--ok:#14663f;--ok-bg:#e3f5ec;--no:#a3341a;--no-bg:#fde8e3;--review:#7a5400;
--review-bg:#fff1d4;--gap:#465570;--gap-bg:#eceff5;--info:#3d5a80;--info-bg:#e8eef8;
--shadow:0 1px 2px rgb(11 31 68/4%),0 8px 24px rgb(11 31 68/6%);--logo-n:#10223d;--logo-d:#081a34;--logo-b:#1a59ea}
@media (prefers-color-scheme:dark){:root{--logo-n:#eaf0fb;--logo-d:#b7c8e8;--logo-b:#5a8fff;--bg:#0b1426;--card:#101d36;--ink:#e8eefb;--ink-2:#d6e2fb;
--muted:#a8b6d4;--line:#24375c;--soft:#15264a;--accent:#7cb1ff;--warn-bg:#33270c;--warn-line:#7a5a17;
--stand-bg:#13284a;--stand-line:#33568c;--ok:#5fdca6;--ok-bg:#0f3526;--no:#ff9c8a;--no-bg:#3d1c17;
--review:#ffcb66;--review-bg:#3a2c0a;--gap:#b9c5de;--gap-bg:#1d2b48;--info:#a9c4f0;--info-bg:#18294a;
--shadow:0 1px 2px rgb(0 0 0/30%)}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0;padding:clamp(10px,3vw,28px);background:var(--bg);color:var(--ink);
font:400 15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
overflow-wrap:break-word}
main{max-width:1080px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:clamp(14px,3vw,24px);
margin:0 0 14px;box-shadow:var(--shadow)}
.brand{display:flex;align-items:center;gap:10px;margin:0 0 6px}
.mark{width:33px;height:28px;flex:none}.mark .m-n{fill:var(--logo-n)}.mark .m-d{fill:var(--logo-d)}.mark .m-b{fill:var(--logo-b)}
h1{margin:4px 0 12px;font-size:clamp(22px,5.2vw,32px);line-height:1.15;color:var(--ink-2);overflow-wrap:anywhere}
h2{margin:0 0 10px;font-size:clamp(17px,2.6vw,20px);line-height:1.25;color:var(--ink-2)}
p{margin:10px 0}
.verdict{display:inline-flex;align-items:center;gap:8px;padding:8px 16px 8px 12px;border-radius:999px;color:#fff;
font-weight:700;font-size:clamp(15px,3.6vw,18px);letter-spacing:.03em}
.verdict svg{width:20px;height:20px;flex:none}
.v-confirmed{background:#1f7a4d}.v-refuted{background:#b3401a}.v-needs_review{background:#9a6a00}
.v-insufficient_data{background:#4a5a6a}
.objection{margin:14px 0 4px;padding:10px 14px;border-left:4px solid var(--accent);background:var(--soft);
border-radius:0 10px 10px 0}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:10px;margin:14px 0 0}
.facts div{background:var(--soft);border-radius:12px;padding:10px 12px}
.facts dt{font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--muted)}
.facts dd{margin:2px 0 0}
table{border-collapse:collapse;width:100%}
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
.table-wrap:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
th,td{border-bottom:1px solid var(--line);padding:9px 10px;text-align:left;vertical-align:top;font-size:14px}
th{background:var(--soft);color:var(--muted);font-weight:600;font-size:13px}
caption{text-align:left;padding:0 0 8px}
code{font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow-wrap:anywhere;word-break:break-word}
.muted{color:var(--muted);font-size:13px}
.tag{display:inline-block;padding:2px 9px;border-radius:999px;font-size:13px;font-weight:600;white-space:nowrap}
.t-ok{background:var(--ok-bg);color:var(--ok)}.t-no{background:var(--no-bg);color:var(--no)}
.t-review{background:var(--review-bg);color:var(--review)}.t-gap{background:var(--gap-bg);color:var(--gap)}
.t-info{background:var(--info-bg);color:var(--info)}
td .tag{white-space:normal}
.warn{background:var(--warn-bg);border-color:var(--warn-line)}
.stand{background:var(--stand-bg);border-color:var(--stand-line)}
.stand h2{font-size:16px}
ul{margin:6px 0;padding-left:1.25em}li{margin:4px 0}
.steps li::marker{color:var(--accent)}
.foot{font-size:13px;color:var(--muted)}
@media (hover:hover){tbody tr:hover td{background:var(--soft)}}
@media (max-width:700px){
table.stack,table.stack tbody,table.stack tr,table.stack td{display:block;width:100%}
table.stack tr.head{display:none}
table.stack caption{display:block;width:auto}
table.stack tr{border:1px solid var(--line);border-radius:12px;padding:6px 12px;margin:0 0 10px}
table.stack td{border:0;padding:5px 0}
table.stack td::before{content:attr(data-label);display:block;font-size:11px;font-weight:600;letter-spacing:.05em;
text-transform:uppercase;color:var(--muted)}
}
@media print{:root{color-scheme:light}body{background:#fff;padding:0}.card{box-shadow:none;break-inside:avoid}}
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
    """Разряды и знак рубля через неразрывный пробел: сумма не переносится внутри числа."""
    return "—" if amount is None else f"{amount:,}".replace(",", "\xa0") + "\xa0₽"


def _tag(text: str, tone: str) -> str:
    return f'<span class="tag t-{tone}">{_e(text)}</span>'


def _icon(verdict: str) -> str:
    path = _ICON_PATH.get(verdict, _ICON_PATH["insufficient_data"])
    return (
        '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="'
        + path
        + '" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )


def render(report: dict[str, Any]) -> str:
    verdict = str(report["verdict"])
    verdict_class = verdict if verdict in COLORS else "insufficient_data"
    op = report["operation"]
    rows = []
    for f in report["findings"]:
        refs = "<br>".join(
            f"<code>{_e(r['doc_id'])}@{_e(r['doc_sha256'][:12])}#{_e(r['locator'])}</code>" for r in f["refs"]
        )
        effect = str(f["effect"])
        rows.append(
            f'<tr><td data-label="Правило"><b>{_e(f["rule_id"])}</b></td>'
            f'<td data-label="Эффект">{_tag(EFFECT_RU.get(effect, effect), EFFECT_TONE.get(effect, "info"))}</td>'
            f'<td data-label="Вывод">{_e(f["message"])}'
            f"<div class='muted'>{_e(f['norm_ref'])} · правило {_e(f['rule_status'])}</div></td>"
            f'<td data-label="Источник">{refs or "—"}</td></tr>'
        )
    checks = "".join(
        f"<tr><td>{_e(v['label'])}</td>"
        f"<td>{_tag(STATUS_RU.get(v['status'], v['status']), STATUS_TONE.get(v['status'], 'info'))}</td></tr>"
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
    draft_html = (
        "<li>Правила набора — черновик (draft): юрист банка их не утверждал.</li>"
        if (report.get("rule_pack") or {}).get("status", "draft") == "draft"
        else ""
    )
    docs = "".join(
        f'<tr><td data-label="Номер"><code>{_e(d.get("number"))}</code></td>'
        f'<td data-label="Выдана">{_e(_date(d.get("issue_date")))}</td>'
        f'<td data-label="Действует до">{_e(_date(d.get("effective_until")))}'
        f"{' (по закону)' if d.get('term_by_law') else ''}</td>"
        f'<td data-label="Выдал">{_e(d.get("signer"))}</td>'
        f'<td data-label="Представитель">{_e(", ".join(d.get("representatives") or []))}</td></tr>'
        for d in (report.get("chain") or {}).get("documents") or []
    )
    docs_html = (
        '<div class="card"><h2>Документы, подтверждающие полномочия</h2><table class="stack">'
        '<caption class="muted">Сведения для идентификации представителя клиента (115-ФЗ, ст. 7, п. 1, пп. 1)</caption>'
        '<tr class="head"><th scope="col">Номер</th><th scope="col">Выдана</th><th scope="col">Действует до</th>'
        f'<th scope="col">Выдал</th><th scope="col">Представитель</th></tr>{docs}</table></div>'
        if docs
        else ""
    )
    organization = report["organization"]
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><meta name="referrer" content="no-referrer">
<title>Мандат — кейс {_e(report["case_id"])}</title><style>{CSS}</style></head><body><main>
<div class="card"><div class="brand">{_MARK}<span class="muted">Мандат · проверка полномочий · профиль {_e(report["profile"])}</span></div>
<h1>Кейс {_e(report["case_id"])}</h1>
<span class="verdict v-{verdict_class}">{_icon(verdict_class)}{_e(report["verdict_label"])}</span>
<p class="objection"><b>Сильнейшее возражение:</b> {_e(report.get("objection"))}</p>
<dl class="facts">
<div><dt>Операция</dt><dd>{_e(op.get("name") or op["code"])} · {_e(_date(op["date"]))} · сумма {_e(_rub(op.get("amount_rub")))}</dd></div>
<div><dt>Организация</dt><dd>{_e(organization.get("name"))} (ИНН {_e(organization.get("inn"))})</dd></div>
<div><dt>Заявитель</dt><dd>{_e(report["requester"].get("full_name"))}</dd></div>
</dl></div>
{signals_html}
{stand_html}
<div class="card"><h2>Цепочка полномочий</h2><ul>{chain}</ul></div>
{docs_html}
<div class="card"><h2>Что сделать дальше</h2><ul class="steps">{steps}</ul>
<p class="muted">Очередь: {_e(QUEUE_RU.get(str(routing.get("queue")), routing.get("queue")))}
 · приоритет {_e(routing.get("priority") or "—")} · SLA {_e(routing.get("sla_hours") or "—")} ч (заглушка до калибровки с банком)</p>
{right_html}</div>
<div class="card"><h2>Группы проверок</h2><div class="table-wrap" role="region" aria-label="Группы проверок" tabindex="0"><table><caption class="muted">Статус каждой группы проверок</caption><tr><th scope="col">Группа</th><th scope="col">Статус</th></tr>{checks}</table></div></div>
<div class="card"><h2>Находки и доказательства</h2><table class="stack"><caption class="muted">Правило, его эффект, вывод и ссылка на источник</caption><tr class="head"><th scope="col">Правило</th><th scope="col">Эффект</th><th scope="col">Вывод</th>
<th scope="col">Источник</th></tr>{"".join(rows)}</table></div>
<div class="card warn"><h2>Границы результата</h2><ul>{warnings}
<li>«Подтверждено» означает соответствие правилам этой версии, а не юридическое заключение и не выдачу прав.</li>
{draft_html}
</ul></div>
<div class="card foot">Набор правил {_e(report["rule_pack"]["pack_id"])} {_e(report["rule_pack"]["version"])}
 (<code>{_e(report["rule_pack"]["sha256"][:16])}</code>) · хеш отчёта <code>{_e(report["reproducibility_hash"][:16])}</code>
 · {_e(report["generated_at"])} · {_e(report["engine"])}</div>
</main></body></html>
"""
