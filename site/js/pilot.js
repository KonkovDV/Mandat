// Пилот и масштаб: недели пилота и ворота перехода — вкладки; адрес #G2 открывает нужные ворота.
import { el, copy } from "./common.js";
import { tabs } from "./tabs.js";

const weeks = document.getElementById("weeks");
if (weeks) tabs(weeks);
const gates = document.getElementById("gates");
if (gates) {
  // Адрес читается до вкладок: при запуске они сами ставят #G0.
  const wanted = location.hash.slice(1);
  let ready = false;
  const control = tabs(gates, (tab) => ready && history.replaceState(null, "", `#${tab.id}`));
  if (/^G[0-4]$/.test(wanted)) control.select(wanted);
  ready = true;
}

// ---------- Календарь пилота: понедельник старта → недели 1, 2–5, 6 и дата решения ----------
const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const DAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const fmt = (d) => `${DAYS[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
const plus = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };

function mondays() {
  const out = [];
  const d = new Date();
  d.setHours(12, 0, 0, 0);
  while (out.length < 3) {
    d.setDate(d.getDate() + 1);
    if (d.getDay() === 1) out.push(new Date(d));
  }
  const planned = new Date(2027, 2, 1, 12);
  if (!out.some((x) => x.toDateString() === planned.toDateString()) && planned > out[0]) out.push(planned);
  return out;
}

function plan(start) {
  return [
    { name: "Неделя 1", from: start, to: plus(start, 4), what: "старт теневой проверки, хронометраж ручной" },
    { name: "Недели 2–5", from: plus(start, 7), to: plus(start, 32), what: "параллельная проверка, журнал расхождений" },
    { name: "Неделя 6", from: plus(start, 35), to: plus(start, 39), what: "независимая разметка и замер" },
    { name: "Решение G2", from: plus(start, 39), to: null, what: "акт: продолжать или остановить", goal: true },
  ];
}

const planner = document.getElementById("planner");
if (planner) {
  const starts = document.getElementById("starts");
  const list = document.getElementById("plan");
  let current = null;
  const show = (start) => {
    current = plan(start);
    list.replaceChildren(...current.map((p) => el("li", { class: p.goal ? "goal" : "" }, el("b", { text: p.name }), el("span", { text: p.to ? `${fmt(p.from)} — ${fmt(p.to)}` : fmt(p.from) }), el("span", { class: "what", text: p.what }))));
  };
  const options = mondays();
  options.forEach((d, i) => {
    const b = el("button", { class: "chip", type: "button", "aria-pressed": String(i === options.length - 1), text: d.getMonth() === 2 && d.getDate() === 1 && d.getFullYear() === 2027 ? `${fmt(d)} · план` : fmt(d) });
    b.addEventListener("click", () => {
      for (const x of starts.children) x.setAttribute("aria-pressed", String(x === b));
      show(d);
    });
    starts.append(b);
  });
  show(options[options.length - 1]);
  document.getElementById("copy-plan").addEventListener("click", (event) => {
    const text = ["Теневой пилот «Мандата» в ВТБ: право подписи платёжных документов по МЧД", ...current.map((p) => `${p.name}: ${p.to ? `${fmt(p.from)} — ${fmt(p.to)}` : fmt(p.from)} — ${p.what}`), "Стоп-критерий: первое ложное «подтверждено»", location.href.split("#")[0]].join("\n");
    copy(text, event.currentTarget);
  });
  planner.hidden = false;
}
