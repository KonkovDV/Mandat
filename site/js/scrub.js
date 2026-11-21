// Главная: дата операции на оси времени кейса 14 и ответ ядра на этот день.
// Ответы посчитаны при сборке (scripts/build_site.py, _scrub): страница их только показывает.
import { data, el, svg, rub, ruDate } from "./common.js";

const scrub = data("page-data");
const DAY = 86400000;
const LINKS = [["org", "Компания"], ["eio", "Руководитель"], ["poa", "Доверенность"], ["charter", "Устав"]];
const W = 1000, H = 92, BAR_Y = 30, BAR_H = 24;

function dayIndex(iso) {
  return Math.round((Date.parse(iso) - Date.parse(scrub.start)) / DAY);
}
function isoOf(i) {
  return new Date(Date.parse(scrub.start) + i * DAY).toISOString().slice(0, 10);
}
function segmentAt(iso) {
  return scrub.segments.find((s) => s.from <= iso && iso <= s.to);
}

function drawTrack(track, total) {
  const x = (i) => (i / total) * W;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", class: "scrub-svg", "aria-hidden": "true" });
  for (const s of scrub.segments) {
    const a = x(dayIndex(s.from)), b = x(dayIndex(s.to) + 1);
    root.append(svg("rect", { class: `seg ${s.css}`, x: a + 1, y: BAR_Y, width: Math.max(b - a - 2, 1), height: BAR_H, rx: 4 }));
  }
  for (const m of scrub.marks) {
    const mx = x(dayIndex(m.date));
    if (mx < 0 || mx > W) continue;
    root.append(svg("line", { class: "mark-line", x1: mx, x2: mx, y1: BAR_Y - 8, y2: BAR_Y + BAR_H + 8 }));
  }
  track.prepend(root);
  const labels = el("div", { class: "scrub-marks", "aria-hidden": "true" });
  for (const m of scrub.marks) {
    const pos = (100 * dayIndex(m.date)) / total;
    if (pos < 0 || pos > 100) continue;
    const tag = el("span", {}, el("b", { text: ruDate(m.date) }), m.text);
    tag.style.left = `${pos}%`;
    labels.append(tag);
  }
  track.append(labels);
  // Подпись вердикта на отрезке, если он достаточно широк: смысл не держится на одном цвете.
  const words = el("div", { class: "scrub-words", "aria-hidden": "true" });
  for (const s of scrub.segments) {
    const a = dayIndex(s.from), b = dayIndex(s.to) + 1;
    if ((b - a) / total < 0.11) continue;
    const tag = el("span", { class: s.css, text: s.label });
    tag.style.left = `${(100 * (a + b)) / 2 / total}%`;
    words.append(tag);
  }
  track.append(words);
  const legend = el("div", { class: "scrub-legend" });
  const seen = new Set();
  for (const s of scrub.segments) {
    if (seen.has(s.verdict)) continue;
    seen.add(s.verdict);
    legend.append(el("span", { class: `badge ${s.css}`, text: s.label }));
  }
  track.after(legend);
}

function render(i) {
  const iso = isoOf(i);
  const s = segmentAt(iso);
  document.getElementById("scrub-out").textContent = ruDate(iso);
  const input = document.getElementById("scrub-day");
  input.setAttribute("aria-valuetext", `${ruDate(iso)}: ${s ? s.label : ""}`);
  if (!s) return;
  const hot = new Set(s.why.map((w) => w.link));
  document.getElementById("scrub-chain").replaceChildren(
    ...LINKS.map(([key, name]) => el("span", { class: `node ${hot.has(key) ? s.css : "calm"}` }, name)),
  );
  const card = document.getElementById("scrub-card");
  const why = s.why.length
    ? s.why.map((w) => el("div", { class: "why" }, el("span", { class: "rid", text: w.rule }), el("p", { text: w.message }), w.norm ? el("p", { class: "norm", text: `Норма: ${w.norm}` }) : null, w.step ? el("p", { class: "step", text: `Что сделать: ${w.step}` }) : null))
    : [el("p", { text: "Все обязательные проверки сходятся, возражений нет." })];
  card.replaceChildren(
    el("div", { class: "scrub-verdict" }, el("span", { class: `badge big ${s.css}`, text: s.label }), el("span", { class: "note", text: `платёж ${rub(scrub.amount)} · ${ruDate(iso)}` })),
    ...why,
    el("p", { class: "fine", text: `Так ядро отвечает с ${ruDate(s.from)} по ${ruDate(s.to)}.` }),
  );
  const changed = card.dataset.seg !== s.from;
  card.dataset.seg = s.from;
  card.className = `scrub-card ${s.css}`;
  if (changed) {
    card.classList.remove("flash");
    requestAnimationFrame(() => card.classList.add("flash"));
  }
}

if (scrub && scrub.segments?.length) {
  const box = document.getElementById("scrub");
  const total = dayIndex(scrub.end) + 1;
  const input = document.getElementById("scrub-day");
  input.max = String(total - 1);
  input.value = String(dayIndex(scrub.op));
  input.setAttribute("aria-label", "Дата операции");
  drawTrack(document.getElementById("scrub-track"), total);
  const jumps = document.getElementById("scrub-jumps");
  for (const s of scrub.segments) {
    const mid = Math.round((dayIndex(s.from) + dayIndex(s.to)) / 2);
    jumps.append(el("button", { class: "chip", type: "button", onclick: () => { input.value = String(mid); render(mid); } }, el("span", { class: `dot-mark ${s.css}`, "aria-hidden": "true" }), `${ruDate(s.from).slice(0, 5)}–${ruDate(s.to).slice(0, 5)} · ${s.label}`));
  }
  input.addEventListener("input", () => render(Number(input.value)));
  box.hidden = false;
  document.getElementById("scrub-table").hidden = true;
  render(Number(input.value));
}
