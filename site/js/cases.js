// Разбор демо-кейсов: фильтр по вердикту, ось времени полномочий, проверки и находки с нормой.
// Данные — выдержка из отчётов mandat demo этого коммита (scripts/build_site.py, _cases).
import { data, el, svg, rub, ruDate, tooltip } from "./common.js";

const page = data("page-data") || { cases: [], whatif: null };
const cases = page.cases;
const VERDICT_ORDER = ["confirmed", "refuted", "needs_review", "insufficient_data"];
const VERDICT_WORD = { confirmed: "подтверждено", refuted: "опровергнуто", needs_review: "на разбор", insufficient_data: "недостаточно данных" };
const VERDICT_CSS = { confirmed: "ok", refuted: "no", needs_review: "review", insufficient_data: "gap" };
const EFFECT = {
  refutes: ["опровергает", "no"],
  needs_review: ["нужен человек", "review"],
  not_verified: ["не проверено", "gap"],
  info: ["справка", "info"],
  supports: ["подтверждает", "ok"],
};
const MARK = { ok: "✓", no: "✕", review: "!", gap: "?", skip: "–" };
const QUEUE = { exceptions: "очередь разбора", auto_recommendation: "рекомендация без разбора" };

const list = document.getElementById("list");
const panel = document.getElementById("panel");
const filters = document.getElementById("filters");
let shown = new Set(VERDICT_ORDER);
let current = null;

function buildFilters() {
  filters.hidden = false;
  const counts = Object.fromEntries(VERDICT_ORDER.map((v) => [v, cases.filter((c) => c.verdict === v).length]));
  const all = el("button", { class: "chip", type: "button", "aria-pressed": "true", "data-v": "all" }, "Все ", el("span", { class: "count", text: cases.length }));
  filters.append(all);
  for (const v of VERDICT_ORDER) {
    if (!counts[v]) continue;
    filters.append(el("button", { class: "chip", type: "button", "aria-pressed": "false", "data-v": v }, `${VERDICT_WORD[v]} `, el("span", { class: "count", text: counts[v] })));
  }
  filters.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    const v = button.dataset.v;
    shown = v === "all" ? new Set(VERDICT_ORDER) : new Set([v]);
    for (const b of filters.querySelectorAll("button")) b.setAttribute("aria-pressed", String(b === button));
    renderList();
    const first = cases.find((c) => shown.has(c.verdict));
    if (first && !shown.has(current?.verdict)) select(first.id, false);
  });
}

function renderList() {
  list.replaceChildren(
    ...cases
      .filter((c) => shown.has(c.verdict))
      .map((c) =>
        el(
          "li",
          {},
          el(
            "button",
            { type: "button", "aria-current": String(current?.id === c.id), "data-id": c.id, onclick: () => select(c.id, true) },
            el("span", { class: "id", text: c.id }),
            el("b", { text: c.title }),
            el("span", { class: `badge ${c.css}`, text: c.label }),
          ),
        ),
      ),
  );
}

function select(id, focusPanel) {
  const found = cases.find((c) => c.id === id);
  if (!found) return;
  current = found;
  for (const b of list.querySelectorAll("button")) b.setAttribute("aria-current", String(b.dataset.id === id));
  history.replaceState(null, "", `#${id}`);
  panel.replaceChildren(...render(found));
  if (focusPanel && matchMedia("(max-width: 900px)").matches) panel.scrollIntoView({ block: "start" });
}

function render(c) {
  const head = el(
    "div",
    { class: "case-head" },
    el("h2", { text: c.title }),
    el("span", { class: `badge ${c.css}`, text: c.label }),
  );
  const routing = c.routing.priority
    ? `${QUEUE[c.routing.queue] || c.routing.queue}, ${c.routing.priority}, срок ${c.routing.sla_hours} ч`
    : QUEUE[c.routing.queue] || c.routing.queue;
  const facts = el(
    "dl",
    { class: "facts" },
    fact("Операция", c.operation.name),
    fact("Дата и сумма", `${ruDate(c.operation.date)}${c.operation.amount ? ` · ${rub(c.operation.amount)}` : ""}`),
    fact("Организация", c.org),
    fact("Представитель", c.chain_kind === "eio_direct" ? `${c.requester}, руководитель` : c.requester),
    fact("Куда идёт кейс", routing),
  );
  const parts = [head, facts];
  if (c.objection) {
    parts.push(el("div", { class: "objection" }, el("b", { text: "Сильнейшее возражение" }), el("span", { text: c.objection })));
  }
  parts.push(el("h3", { text: "Даты, на которых держится ответ" }), timeline(c));
  parts.push(el("h3", { text: "Проверки" }), checks(c));
  const against = c.findings.filter((f) => f.effect !== "supports");
  const supports = c.findings.filter((f) => f.effect === "supports");
  if (against.length) parts.push(el("h3", { text: "Что мешает бесспорному «да»" }), findings(against));
  if (supports.length) {
    parts.push(el("details", {}, el("summary", { text: `Что сходится: ${supports.length} ${word(supports.length)}` }), findings(supports)));
  }
  if (c.next_steps.length) {
    parts.push(
      el("h3", { text: "Следующий шаг сотрудника" }),
      el("ul", { class: "ticks" }, ...c.next_steps.map((s) => el("li", {}, s.action, s.unit ? el("span", { class: "note", text: ` · ${s.unit}` }) : null))),
    );
  }
  parts.push(
    el(
      "p",
      { class: "cta" },
      el("a", { class: "btn primary", href: c.report, text: "Полная карточка сотрудника" }),
      el("a", { class: "btn", href: `https://github.com/KonkovDV/Mandat/tree/main/examples/cases/${c.id}`, text: "Документы кейса" }),
    ),
  );
  return parts;
}

function word(n) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return "находка";
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "находки";
  return "находок";
}

function fact(name, value) {
  return el("div", {}, el("dt", { text: name }), el("dd", { text: value }));
}

function checks(c) {
  return el(
    "ul",
    { class: "checks" },
    ...c.checks.map((k) =>
      el("li", { class: k.mark }, el("span", { class: "mark", "aria-hidden": "true", text: MARK[k.mark] }), el("span", {}, k.label, el("small", { text: k.word }))),
    ),
  );
}

function findings(items) {
  return el(
    "ul",
    { class: "findings" },
    ...items.map((f) => {
      const [word, css] = EFFECT[f.effect] || [f.effect, "info"];
      return el(
        "li",
        {},
        el("div", { class: "row" }, el("span", { class: "rid", text: f.rule }), el("span", { class: `badge ${css}`, text: word })),
        el("p", { text: f.message }),
        f.norm ? el("p", { class: "norm", text: `Норма: ${f.norm}` }) : null,
        f.step ? el("p", { class: "step", text: `Шаг: ${f.step}${f.unit ? ` (${f.unit})` : ""}` }) : null,
        f.loc && f.loc.length > 6 ? el("p", { class: "loc", text: `Где: ${f.loc}` }) : null,
      );
    }),
  );
}

// ---------- Ось времени ----------
const DAY = 86400000;
const W = 760, PAD_L = 170, PAD_R = 18, LANE = 44, BAR = 22, TOP = 34;
const MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

function timeline(c) {
  const t = c.timeline;
  const op = Date.parse(t.op);
  const dates = [op];
  for (const d of t.documents) {
    if (d.issue) dates.push(Date.parse(d.issue));
    if (d.until) dates.push(Date.parse(d.until));
  }
  let from = Math.min(...dates) - 75 * DAY;
  let to = Math.max(...dates) + 45 * DAY;
  for (const e of t.executives) {
    if (e.from) from = Math.min(from, Math.max(Date.parse(e.from), op - 540 * DAY));
  }
  from = Date.UTC(new Date(from).getUTCFullYear(), new Date(from).getUTCMonth(), 1);
  const x = (ms) => PAD_L + ((Math.min(Math.max(ms, from), to) - from) / (to - from)) * (W - PAD_L - PAD_R);
  const lanes = [{ label: "Директор по ЕГРЮЛ", bars: [] }];
  t.executives.forEach((e, i) => {
    const start = e.from ? Date.parse(e.from) : from;
    const end = e.to ? Date.parse(e.to) + DAY : to;
    lanes[0].bars.push({
      start, end, cut: start < from, open: !e.to,
      text: e.name.replace(/^(\S+) (\S)\S* (\S)\S*$/, "$1 $2. $3."),
      tone: i % 2 ? "main" : "soft",
      tip: [e.name, `в ЕГРЮЛ с ${ruDate(e.from)}${e.to ? ` по ${ruDate(e.to)}` : ", действует"}`, e.unreliable ? "отметка о недостоверности" : null].filter(Boolean),
    });
  });
  t.documents.forEach((d) => {
    const revoked = d.status === "revoked";
    lanes.push({
      label: d.parent ? `Передоверие ${d.number.slice(0, 8)}…` : `МЧД ${d.number.slice(0, 8)}…`,
      bars: [{
        start: Date.parse(d.issue), end: Date.parse(d.until) + DAY, cut: false, open: false,
        text: revoked ? "отменена в реестре" : `${ruDate(d.issue)} — ${ruDate(d.until)}${d.by_law ? ", срок по закону" : ""}`,
        tone: revoked ? "no" : "main",
        tip: [
          `${d.parent ? "Передоверие" : "МЧД"} ${d.number}`,
          `выдана ${ruDate(d.issue)}, действует до ${ruDate(d.until)}${d.by_law ? " (срок по закону: год со дня выдачи)" : ""}`,
          `${d.signer} → ${d.to}`,
          `реестр: ${statusWord(d.status)}`,
        ],
      }],
    });
  });
  const H = TOP + lanes.length * LANE + 34;
  const box = el("div", { class: "viz" });
  const tip = tooltip(box);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Ось времени кейса ${c.id}: руководитель по ЕГРЮЛ, доверенности и дата операции ${ruDate(t.op)}` });
  // Сетка по кварталам и подписи месяцев
  for (let d = new Date(from); d.getTime() <= to; d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 3, 1))) {
    const gx = x(d.getTime());
    root.append(svg("line", { class: "grid", x1: gx, x2: gx, y1: TOP - 6, y2: TOP + lanes.length * LANE }));
    const label = d.getUTCMonth() === 0 ? `${MONTHS[0]} ${d.getUTCFullYear()}` : MONTHS[d.getUTCMonth()];
    root.append(svg("text", { class: "tick-label", x: gx + 3, y: TOP + lanes.length * LANE + 16 }, label));
  }
  lanes.forEach((lane, li) => {
    const y = TOP + li * LANE + (LANE - BAR) / 2;
    root.append(svg("text", { class: "lane-label", x: 0, y: y + BAR / 2 + 4 }, lane.label));
    for (const b of lane.bars) {
      const x0 = x(b.start), x1 = Math.max(x(b.end), x0 + 4);
      const fill = b.tone === "no" ? "var(--no-mark)" : b.tone === "soft" ? "var(--series-soft)" : "var(--series)";
      const g = svg("g", {});
      g.append(svg("rect", { class: "mark", x: x0, y, width: x1 - x0 - 2, height: BAR, rx: 4, fill }));
      const fits = b.text.length * 6.6 + 14 < x1 - x0;
      const onDark = b.tone !== "soft";
      const label = (b.cut ? "← " : "") + b.text + (b.open ? " →" : "");
      if (fits) {
        g.append(svg("text", { class: `bar-label ${onDark ? "on-dark" : "on-light"}`, x: x0 + 8, y: y + BAR / 2 + 4 }, label));
      } else {
        const right = x1 + 6 + label.length * 6.6 < W;
        g.append(svg("text", { class: "bar-label on-light", fill: "var(--ink)", x: right ? x1 + 6 : x0 - 6, y: y + BAR / 2 + 4, "text-anchor": right ? "start" : "end" }, label));
      }
      // Подсказка — для мыши и пальца; с клавиатуры те же даты читаются в таблице под осью.
      const hit = svg("rect", { class: "hit", x: x0, y: y - 6, width: Math.max(x1 - x0, 24), height: BAR + 12 });
      const show = (event) => { g.classList.add("on"); tip.show(hit, b.tip[0], b.tip.slice(1), event); };
      const hide = () => { g.classList.remove("on"); tip.hide(); };
      hit.addEventListener("pointermove", show);
      hit.addEventListener("pointerleave", hide);
      g.prepend(hit);
      root.append(g);
    }
  });
  // Дата операции: вертикальная линия под полосами (текст полос не перечёркивается) и флажок сверху
  const ox = x(op);
  root.insertBefore(svg("line", { class: "op-line", x1: ox, x2: ox, y1: 14, y2: TOP + lanes.length * LANE }), root.querySelector("g"));
  const flag = `операция ${ruDate(t.op)}`;
  const fw = flag.length * 6.6 + 14;
  const fx = Math.min(Math.max(ox - fw / 2, PAD_L - 40), W - fw - 2);
  root.append(svg("rect", { class: "op-flag", x: fx, y: 0, width: fw, height: 20, rx: 10 }));
  root.append(svg("text", { class: "op-text", x: fx + fw / 2, y: 14, "text-anchor": "middle" }, flag));
  box.append(root);
  const table = el(
    "table",
    {},
    el("caption", { class: "sr-only", text: "Даты кейса" }),
    el("thead", {}, el("tr", {}, el("th", { scope: "col", text: "Что" }), el("th", { scope: "col", text: "С" }), el("th", { scope: "col", text: "По" }), el("th", { scope: "col", text: "Примечание" }))),
    el(
      "tbody",
      {},
      ...t.executives.map((e) => el("tr", {}, el("td", { text: `Руководитель: ${e.name}` }), el("td", { text: ruDate(e.from) }), el("td", { text: e.to ? ruDate(e.to) : "действует" }), el("td", { text: e.unreliable ? "недостоверность" : "ЕГРЮЛ" }))),
      ...t.documents.map((d) => el("tr", {}, el("td", { text: `${d.parent ? "Передоверие" : "МЧД"} ${d.number}` }), el("td", { text: ruDate(d.issue) }), el("td", { text: ruDate(d.until) }), el("td", { text: `реестр: ${statusWord(d.status)}` }))),
      el("tr", {}, el("td", { text: "Операция" }), el("td", { text: ruDate(t.op) }), el("td", { text: "—" }), el("td", { text: c.operation.amount ? rub(c.operation.amount) : "—" })),
    ),
  );
  return el("div", {}, box, el("details", {}, el("summary", { text: "Те же даты таблицей" }), el("div", { class: "table-wrap" }, table)));
}

function statusWord(status) {
  return { active: "действует", revoked: "отменена", no_answer: "нет ответа" }[status] || status;
}

// ---------- Что если: сочетания фактов кейса 14, ответы ядра посчитаны при сборке ----------
function lab(whatif) {
  const box = document.getElementById("lab-box");
  const card = document.getElementById("lab-card");
  const state = { registry: "active", on: new Set() };
  const show = () => {
    const key = `${state.registry}-${whatif.toggles.map((t) => (state.on.has(t) ? 1 : 0)).join("")}`;
    const a = whatif.answers[key];
    if (!a) return;
    card.className = `scrub-card ${a.css}`;
    card.replaceChildren(
      el("div", { class: "scrub-verdict" }, el("span", { class: `badge big ${a.css}`, text: a.label }), el("span", { class: "note", text: "кейс 14, платёж 480 000 ₽, 20.10.2026" })),
      ...(a.why.length
        ? a.why.map((w) => el("div", { class: "why" }, el("span", { class: "rid", text: w.rule }), el("p", { text: w.message }), w.norm ? el("p", { class: "norm", text: `Норма: ${w.norm}` }) : null, w.step ? el("p", { class: "step", text: `Что сделать: ${w.step}` }) : null))
        : [el("p", { text: "Все обязательные проверки сходятся. Смена директора давняя — это сигнал на карточке, а не отказ." })]),
    );
  };
  document.getElementById("lab-registry").addEventListener("click", (event) => {
    const b = event.target.closest("button");
    if (!b) return;
    state.registry = b.dataset.v;
    for (const x of document.querySelectorAll("#lab-registry button")) x.setAttribute("aria-pressed", String(x === b));
    show();
  });
  document.getElementById("lab-toggles").addEventListener("click", (event) => {
    const b = event.target.closest("button");
    if (!b) return;
    const t = b.dataset.t;
    if (state.on.has(t)) state.on.delete(t); else state.on.add(t);
    b.setAttribute("aria-pressed", String(state.on.has(t)));
    show();
  });
  box.hidden = false;
  show();
}

if (page.whatif) lab(page.whatif);

if (cases.length) {
  buildFilters();
  renderList();
  const wanted = decodeURIComponent(location.hash.slice(1));
  select(cases.some((c) => c.id === wanted) ? wanted : cases.find((c) => c.verdict === "needs_review" && c.id.startsWith("05"))?.id || cases[0].id, false);
  addEventListener("hashchange", () => select(decodeURIComponent(location.hash.slice(1)), false));
}
