// Календарь норм: ползунок даты, полосы действия актов и правила, которые на эту дату нельзя подтверждать.
// Логика та же, что у mandat law-calendar: акт действует с даты вступления до даты утраты силы, не включая её.
import { data, el, svg, ruDate, plural, tooltip } from "./common.js";

const page = data("page-data");
const DAY = 86400000;
const START = Date.UTC(2025, 0, 1), END = Date.UTC(2029, 5, 30);
const W = 760, L = 230, R = 16, LANE = 40, BAR = 20, TOP = 26;

const norms = (page?.norms || []).map((n) => ({ ...n, a: n.from ? Date.parse(n.from) : null, b: n.until ? Date.parse(n.until) : null }));
const state = (n, t) => (n.a !== null && t < n.a ? "future" : n.b !== null && t >= n.b ? "ended" : "active");
const WORD = { active: "действует", future: "ещё не вступил", ended: "утратил силу" };
let filter = "";

function short(title) {
  const m = title.match(/^(Федеральный закон|Приказ Минцифры России) от [\d.]+ (№ \d+(?:-ФЗ)?)/);
  return m ? `${m[1] === "Приказ Минцифры России" ? "Приказ Минцифры" : "Закон"} ${m[2]}` : title.slice(0, 32);
}

function render(t) {
  document.getElementById("day-out").textContent = ruDate(new Date(t).toISOString());
  const counts = { active: 0, future: 0, ended: 0 };
  const blocked = new Set();
  for (const n of norms) {
    const s = state(n, t);
    counts[s]++;
    if (s === "ended") n.rules.forEach((r) => blocked.add(r));
  }
  const sum = document.getElementById("law-sum");
  sum.replaceChildren(
    el("span", { class: "badge ok", text: `действуют: ${counts.active}` }),
    el("span", { class: "badge info", text: `ещё не вступили: ${counts.future}` }),
    el("span", { class: "badge gap", text: `утратили силу: ${counts.ended}` }),
    blocked.size
      ? el("span", { class: "badge review", text: `${blocked.size} ${plural(blocked.size, "правило", "правила", "правил")} — «на разбор» до пересверки: ${[...blocked].sort().join(", ")}` })
      : el("span", { class: "badge ok", text: "все правила пакета опираются на действующие акты" }),
  );
  chart(t);
  for (const row of document.querySelectorAll("#law-rows tr")) {
    const n = norms[[...row.parentNode.children].indexOf(row)];
    let cell = row.querySelector("td.state");
    if (!cell) { cell = el("td", { class: "state" }); row.append(cell); }
    const s = state(n, t);
    cell.replaceChildren(el("span", { class: `badge ${s === "active" ? "ok" : s === "future" ? "info" : "gap"}`, text: WORD[s] }));
    row.hidden = Boolean(filter) && !n.rules.some((r) => r.toLowerCase().includes(filter));
  }
}

function chart(t) {
  const box = document.getElementById("law-chart");
  box.replaceChildren();
  box.className = "viz";
  const tip = tooltip(box);
  const shown = norms.filter((n) => !filter || n.rules.some((r) => r.toLowerCase().includes(filter)));
  const H = TOP + shown.length * LANE + 30;
  const x = (ms) => L + ((Math.min(Math.max(ms, START), END) - START) / (END - START)) * (W - L - R);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Сроки действия актов пакета правил на ${ruDate(new Date(t).toISOString())}` });
  for (let y = 2025; y <= 2029; y++) {
    const gx = x(Date.UTC(y, 0, 1));
    root.append(svg("line", { class: "grid", x1: gx, x2: gx, y1: TOP - 8, y2: TOP + shown.length * LANE }));
    root.append(svg("text", { class: "tick-label", x: gx + 3, y: TOP + shown.length * LANE + 16 }, String(y)));
  }
  shown.forEach((n, i) => {
    const y = TOP + i * LANE + (LANE - BAR) / 2;
    root.append(svg("text", { class: "lane-label", x: 0, y: y + BAR / 2 + 4 }, short(n.title)));
    const a = n.a ?? START, b = n.b ?? END;
    const s = state(n, t);
    const g = svg("g", {});
    g.append(svg("rect", { class: `mark act-bar${s === "ended" ? " ended" : s === "future" ? " future" : ""}`, x: x(a), y, width: Math.max(x(b) - x(a), 4), height: BAR, rx: 4 }));
    const label = `${n.a !== null && n.a < START ? "← " : ""}${n.from ? ruDate(n.from) : "…"} — ${n.until ? ruDate(n.until) : "бессрочно →"}`;
    const fits = label.length * 6.4 + 12 < x(b) - x(a);
    g.append(svg("text", { class: `bar-label ${fits && s !== "future" ? "on-dark" : "on-light"}`, fill: fits ? null : "var(--ink)", x: fits ? x(a) + 8 : x(b) + 6, y: y + BAR / 2 + 4 }, label));
    const lines = [`${WORD[s]} на выбранную дату`, `правила: ${n.rules.join(", ")}`];
    // Подсказка — для мыши и пальца; с клавиатуры то же читается в таблице актов ниже.
    const hit = svg("rect", { class: "hit", x: x(a), y: y - 6, width: Math.max(x(b) - x(a), 24), height: BAR + 12 });
    const on = (event) => { g.classList.add("on"); tip.show(hit, short(n.title), [n.title, ...lines], event); };
    const off = () => { g.classList.remove("on"); tip.hide(); };
    hit.addEventListener("pointermove", on);
    hit.addEventListener("pointerleave", off);
    g.prepend(hit);
    root.append(g);
  });
  const nx = x(t);
  root.insertBefore(svg("line", { class: "now-line", x1: nx, x2: nx, y1: 4, y2: TOP + shown.length * LANE }), root.querySelector("g"));
  root.append(svg("text", { class: "now-text", x: Math.min(nx + 4, W - 70), y: 12 }, ruDate(new Date(t).toISOString())));
  box.append(root);
}

if (norms.length) {
  const ctl = document.getElementById("law-ctl");
  const input = document.getElementById("day");
  const days = Math.round((END - START) / DAY);
  input.max = String(days);
  const today = Date.parse(page.today);
  const wanted = Date.parse(new URLSearchParams(location.hash.slice(1)).get("on") || "");
  input.value = String(Math.round(((Number.isFinite(wanted) ? wanted : today) - START) / DAY));
  ctl.hidden = false;
  const at = () => START + Number(input.value) * DAY;
  input.addEventListener("input", () => { render(at()); history.replaceState(null, "", `#on=${new Date(at()).toISOString().slice(0, 10)}`); });
  const search = document.getElementById("rule-filter");
  search.hidden = false;
  search.addEventListener("input", () => { filter = search.value.trim().toLowerCase(); render(at()); });
  const head = document.querySelector("#law-rows")?.closest("table")?.querySelector("thead tr");
  if (head) head.append(el("th", { scope: "col", text: "На выбранную дату" }));
  render(at());
}
