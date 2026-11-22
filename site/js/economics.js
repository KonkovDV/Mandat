// Калькулятор эффекта: формулы листа «Банк» финансовой модели (submission/analytics/build_model.py, раздел 1–6)
// и юнит-экономика scripts/effect_model.py --unit. При загрузке калькулятор считает все три сценария на входах
// модели и сверяет с её значениями; расхождение пишется в консоль как ошибка и видно под расчётом.
import { data, el, svg, num, plural, copy, tooltip } from "./common.js";

const model = data("page-data");
const NAMES = model?.scenarios ?? [];

/** Модель банка: те же строки и тот же порядок, что в build_model.py. Значения в млн ₽, если не сказано иное. */
export function compute(p) {
  const events = p.clients * p.event_share * 1000; // тысяч кейсов в год
  const hours = (events * p.minutes) / 60; // тысяч часов
  const fte = (hours * 1000) / p.hours_fte;
  const cost_fte = (p.salary_ops * 12 * (1 + p.bank_contrib) * (1 + p.overhead)) / 1000;
  const capacity = fte * cost_fte;
  const cash_saving = capacity * p.realization;
  const loss_op = p.loss_q / p.incidents_q;
  const ops_year = p.incidents_q * 4;
  const prevented = ops_year * p.vtb_share * p.authority_share * loss_op;
  const bank_loss = prevented * p.bank_loss_share;
  const effect_cash = cash_saving + bank_loss;
  // Постоянная часть подписки — услуга (сопровождение правил и поддержка): НДС 22 %, банк не принимает его к вычету.
  // Плата за клиентов — лицензия на ПО из реестра российского ПО, без НДС (пп. 26 п. 2 ст. 149 НК РФ).
  const sub_vtb = p.fixed_t1 * (1 + p.vat) + p.clients * p.price_client;
  const impl_vat = p.impl_t1 * (1 + p.vat);
  const integration = p.bank_integration_py * p.bank_it_cost;
  const one_off = impl_vat + integration + p.bank_infra + p.bank_security;
  const annual_cost = sub_vtb + p.bank_support_py * p.bank_it_cost;
  const y1_cash = effect_cash * p.ramp - annual_cost;
  const y2_cash = effect_cash - annual_cost;
  const roi_cash = (y1_cash + 2 * y2_cash - one_off) / (one_off + 3 * annual_cost);
  const payback_cash = y1_cash >= one_off ? (12 * one_off) / y1_cash : y2_cash <= 0 ? Infinity : 12 + (12 * (one_off - y1_cash)) / y2_cash;
  const manual = (p.minutes * cost_fte * 1e6) / (p.hours_fte * 60);
  const mandat = (annual_cost * 1e6) / (events * 1000);
  return { events, fte, cost_fte, capacity, cash_saving, prevented, bank_loss, effect_cash, sub_vtb, one_off, annual_cost, y1_cash, y2_cash, roi_cash, payback_cash, manual, mandat };
}

// Ползунки — только допущения команды и зарплата; остальное — входы выбранного сценария модели.
const SLIDERS = [
  { key: "event_share", name: "Клиентов с событием представительства в год", min: 0.05, max: 0.4, step: 0.01, pct: true, hint: "смена подписанта, новая МЧД, передоверие; допущение команды" },
  { key: "minutes", name: "Минут сотрудника на кейс с разбором", min: 10, max: 60, step: 5, unit: "мин", hint: "допущение команды, пилот меряет хронометражем" },
  { key: "salary_ops", name: "Зарплата сотрудника, тыс. ₽ в месяц", min: 60, max: 160, step: 5, unit: "тыс. ₽", hint: "плюс взносы 30,2 % и накладные расходы сценария" },
  { key: "vtb_share", name: "Доля ВТБ в операциях против юрлиц", min: 0.1, max: 0.35, step: 0.01, pct: true, hint: "допущение команды по доле банка в расчётах бизнеса" },
  { key: "authority_share", name: "Случаев, где виновато полномочие", min: 0.05, max: 0.5, step: 0.01, pct: true, hint: "доля операций без согласия, которые начинаются с подложного представителя" },
  { key: "realization", name: "Высвобожденного времени становится деньгами", min: 0.2, max: 0.8, step: 0.05, pct: true, hint: "остальное — ёмкость под рост без найма" },
];

let scenario = 1;
let params = {};

function inputsOf(i) {
  return Object.fromEntries(Object.entries(model.inputs).map(([k, v]) => [k, v[i]]));
}

function selfCheck() {
  const problems = [];
  for (let i = 0; i < NAMES.length; i++) {
    const got = compute(inputsOf(i));
    for (const [key, want] of Object.entries(model.outputs)) {
      const a = got[key], b = want[i];
      if (!(Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(b)))) problems.push(`${NAMES[i]}: ${key} = ${a}, в модели ${b}`);
    }
    // effect_model.py округляет рубли по правилу Python (половина — к чётному), поэтому допуск — полрубля.
    if (Math.abs(got.manual - model.unit[i].manual) > 0.5 + 1e-9) problems.push(`${NAMES[i]}: ручная проверка ${got.manual}, в effect_model ${model.unit[i].manual}`);
    if (Math.abs(got.mandat - model.unit[i].mandat) > 0.5 + 1e-9) problems.push(`${NAMES[i]}: проверка с «Мандатом» ${got.mandat}, в effect_model ${model.unit[i].mandat}`);
  }
  const node = document.getElementById("check");
  if (problems.length) {
    console.error("калькулятор разошёлся с финансовой моделью", problems);
    node.textContent = `Калькулятор разошёлся с моделью: ${problems[0]}. Верьте таблице ниже.`;
  } else {
    node.textContent = `Сверено с моделью ${model.version} от ${model.as_of}: на её входах калькулятор дал те же значения во всех трёх сценариях.`;
  }
}

function months(m) {
  if (!Number.isFinite(m)) return "не окупается";
  const v = m >= 12 ? Math.round(m) : Math.round(m * 10) / 10;
  return Number.isInteger(v) ? `${v} ${plural(v, "месяц", "месяца", "месяцев")}` : `${num(v, 1)} месяца`;
}

function buildScenarios() {
  const seg = document.getElementById("scenarios");
  seg.hidden = false;
  NAMES.forEach((name, i) => {
    seg.append(el("button", { type: "button", "aria-pressed": String(i === scenario), onclick: () => { scenario = i; params = inputsOf(i); syncInputs(); update(); markScenario(); } }, name));
  });
}
function markScenario() {
  document.querySelectorAll("#scenarios button").forEach((b, i) => b.setAttribute("aria-pressed", String(i === scenario)));
}

function show(s, value) {
  return s.pct ? `${num(value * 100, 0)} %` : `${num(value, 0)} ${s.unit}`;
}

function buildInputs() {
  const form = document.getElementById("inputs");
  form.append(el("h2", { text: "Допущения" }));
  for (const s of SLIDERS) {
    const id = `in-${s.key}`;
    const out = el("output", { for: id, id: `out-${s.key}` });
    const input = el("input", { type: "range", id, min: s.min, max: s.max, step: s.step, value: params[s.key] });
    input.addEventListener("input", () => { params[s.key] = Number(input.value); out.textContent = show(s, params[s.key]); update(); });
    form.append(el("label", { for: id }, el("span", { class: "lbl" }, s.name, out), input, el("small", { text: s.hint })));
  }
  form.append(el("p", { class: "fine", text: "Число клиентов (1,8 млн), операции без согласия по данным Банка России, цены и затраты банка берутся из выбранного сценария модели." }));
  form.addEventListener("submit", (event) => event.preventDefault());
}

function syncInputs() {
  for (const s of SLIDERS) {
    document.getElementById(`in-${s.key}`).value = params[s.key];
    document.getElementById(`out-${s.key}`).textContent = show(s, params[s.key]);
  }
}

/** Заголовок-вывод обещает только то, что выдерживает расчёт на этих допущениях. */
function headline(r) {
  if (!Number.isFinite(r.payback_cash) || r.payback_cash > 36) return "При этих допущениях разовые затраты не окупаются за три года";
  if (r.payback_cash <= 12) return `Разовые затраты окупаются в первый год: за ${months(r.payback_cash)}`;
  return `Разовые затраты окупаются за ${months(r.payback_cash)}, уже после первого года`;
}

/** Итог расчёта текстом: его можно отправить коллеге в мессенджер. */
function summaryText(r) {
  const mine = SLIDERS.some((s) => params[s.key] !== model.inputs[s.key][scenario]);
  return [
    `«Мандат», расчёт эффекта для ВТБ (${NAMES[scenario].toLowerCase()} сценарий${mine ? ", свои допущения" : ""})`,
    `Окупаемость разовых затрат: ${months(r.payback_cash)}`,
    `Проверка полномочий: вручную ≈${num(Math.round(r.manual / 10) * 10)} ₽, с «Мандатом» ≈${num(Math.round(r.mandat))} ₽`,
    `Высвобождается ставок в год: ${num(Math.round(r.fte))}; предотвращённый ущерб: ${num(r.prevented, 0)} млн ₽ в год`,
    `Отдача за три года: ${num(r.roi_cash, 1)}× на рубль затрат`,
    ...SLIDERS.map((s) => `${s.name}: ${show(s, params[s.key])}`),
    `Расчёт: ${location.href}`,
  ].join("\n");
}

function update() {
  const r = compute(params);
  document.getElementById("headline").textContent = headline(r);
  const outs = document.getElementById("outs");
  outs.replaceChildren(
    el("li", { class: "key" }, el("b", { text: months(r.payback_cash) }), el("span", { text: "окупаются разовые затраты ВТБ" })),
    el("li", {}, el("b", { text: num(Math.round(r.fte)) }), el("span", { text: `${plural(Math.round(r.fte), "ставка высвобождается", "ставки высвобождаются", "ставок высвобождается")} в год` })),
    el("li", {}, el("b", { text: `${num(r.prevented, 0)} млн ₽` }), el("span", { text: "предотвращённого ущерба клиентов в год" })),
    el("li", {}, el("b", { text: `${num(r.y2_cash, 1)} млн ₽` }), el("span", { text: "чистый денежный эффект банка в год со второго года" })),
    el("li", {}, el("b", { text: `${num(r.roi_cash, 1)}×` }), el("span", { text: "отдача за три года на рубль затрат" })),
    el("li", {}, el("b", { text: `${num(r.one_off, 1)} млн ₽` }), el("span", { text: `разовые затраты, ежегодные — ${num(r.annual_cost, 1)} млн ₽` })),
  );
  const unit = document.getElementById("unit");
  const max = Math.max(r.manual, r.mandat);
  const row = (name, value, main) => el(
    "div",
    { class: "row" },
    el("span", { text: name }),
    el("span", { class: "track2" }, (() => { const f = el("span", { class: `fill${main ? " main" : ""}` }); f.style.width = `${(100 * value) / max}%`; return f; })()),
    el("span", { class: "val", text: `${num(Math.round(value / (main ? 1 : 10)) * (main ? 1 : 10))} ₽` }),
  );
  unit.replaceChildren(
    el("h2", { text: "Цена одной проверки полномочий" }),
    row("вручную", r.manual, false),
    row("с «Мандатом»", r.mandat, true),
    el("p", { class: "note", text: `В ${num(r.manual / r.mandat, 1)} раза дешевле. Вручную — минуты сотрудника по полной стоимости ставки, с «Мандатом» — подписка и сопровождение банка на число проверок в год (${num(Math.round(r.events * 1000))}).` }),
  );
  chart(r);
  history.replaceState(null, "", `#${encode()}`);
}

// ---------- Накопленный эффект по месяцам: одна линия, ноль и точка окупаемости ----------
const W = 640, H = 250, L = 54, R = 14, T = 26, B = 30, MONTHS = 36;

function chart(r) {
  const points = [];
  for (let m = 0; m <= MONTHS; m++) {
    const value = -r.one_off + (m <= 12 ? (m * r.y1_cash) / 12 : r.y1_cash + ((m - 12) * r.y2_cash) / 12);
    points.push({ m, value });
  }
  const lo = Math.min(0, ...points.map((p) => p.value)), hi = Math.max(0, ...points.map((p) => p.value));
  const step = niceStep((hi - lo) / 4);
  const y0 = Math.floor(lo / step) * step, y1 = Math.ceil(hi / step) * step;
  const x = (m) => L + (m / MONTHS) * (W - L - R);
  const y = (v) => T + ((y1 - v) / (y1 - y0)) * (H - T - B);
  const box = document.getElementById("chart");
  box.replaceChildren();
  box.className = "viz";
  const tip = tooltip(box);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Накопленный эффект банка за 36 месяцев; окупаемость — ${months(r.payback_cash)}` });
  for (let v = y0; v <= y1 + 1e-9; v += step) {
    root.append(svg("line", { class: v === 0 ? "zero-line" : "grid", x1: L, x2: W - R, y1: y(v), y2: y(v) }));
    root.append(svg("text", { class: "tick-label", x: L - 8, y: y(v) + 4, "text-anchor": "end" }, `${num(v, 0)}`));
  }
  for (const m of [0, 6, 12, 18, 24, 30, 36]) root.append(svg("text", { class: "tick-label", x: x(m), y: H - 10, "text-anchor": "middle" }, m === 0 ? "0 мес" : String(m)));
  root.append(svg("text", { class: "tick-label", x: L - 8, y: 12, "text-anchor": "end" }, "млн ₽"));
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(p.m).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
  root.append(svg("path", { class: "series-area", d: `${d}L${x(MONTHS)},${y(0)}L${x(0)},${y(0)}Z` }));
  root.append(svg("path", { class: "series-line", d }));
  const end = points[MONTHS];
  if (Number.isFinite(r.payback_cash) && r.payback_cash <= MONTHS) {
    const px = x(r.payback_cash), py = y(0);
    root.append(svg("circle", { class: "dot", cx: px, cy: py, r: 5 }));
    // Подпись окупаемости — под нулём, слева от точки, если точка у правого края: так она не встречает подпись итога.
    const label = `окупаемость: ${months(r.payback_cash)}`;
    const right = px + 10 + label.length * 6.6 < W - R;
    root.append(svg("text", { class: "label", x: right ? px + 10 : px - 10, y: py + 20, "text-anchor": right ? "start" : "end" }, label));
  }
  root.append(svg("text", { class: "label", x: x(MONTHS) - 4, y: Math.max(y(end.value) - 10, T + 12), "text-anchor": "end" }, `${num(end.value, 0)} млн ₽ за 3 года`));
  const cross = svg("line", { class: "grid", x1: 0, x2: 0, y1: T, y2: H - B, visibility: "hidden" });
  root.append(cross);
  const hit = svg("rect", { class: "hit", x: L, y: T, width: W - L - R, height: H - T - B, tabindex: 0, role: "img", "aria-label": "Накопленный эффект по месяцам: стрелки влево и вправо называют месяц" });
  let focusM = 12;
  const at = (m, event) => {
    const p = points[m];
    cross.setAttribute("x1", x(m)); cross.setAttribute("x2", x(m)); cross.setAttribute("visibility", "visible");
    tip.show(hit, `${num(p.value, 1)} млн ₽`, [`накоплено к месяцу ${m}`], event);
  };
  hit.addEventListener("pointermove", (event) => {
    const box2 = root.getBoundingClientRect();
    const m = Math.round(((event.clientX - box2.left) * (W / box2.width) - L) / ((W - L - R) / MONTHS));
    at(Math.max(0, Math.min(MONTHS, m)), event);
  });
  hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); tip.hide(); });
  hit.addEventListener("focus", () => at(focusM));
  hit.addEventListener("blur", () => { cross.setAttribute("visibility", "hidden"); tip.hide(); });
  hit.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      focusM = Math.max(0, Math.min(MONTHS, focusM + (event.key === "ArrowRight" ? 1 : -1)));
      at(focusM);
    }
  });
  root.append(hit);
  box.append(root);
  const rows = points.filter((p) => p.m % 3 === 0);
  document.getElementById("chart-table").replaceChildren(
    el("table", {},
      el("thead", {}, el("tr", {}, el("th", { scope: "col", text: "Месяц" }), el("th", { scope: "col", class: "n", text: "Накоплено, млн ₽" }))),
      el("tbody", {}, ...rows.map((p) => el("tr", {}, el("td", { text: p.m }), el("td", { class: "n", text: num(p.value, 1) })))),
    ),
  );
}

function niceStep(raw) {
  const p = 10 ** Math.floor(Math.log10(Math.max(raw, 1e-9)));
  const f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}

// ---------- Ссылка на расчёт: сценарий и ползунки в адресе ----------
function encode() {
  return [`s=${scenario}`, ...SLIDERS.map((s) => `${s.key}=${params[s.key]}`)].join("&");
}
function decode() {
  const q = new URLSearchParams(location.hash.slice(1));
  const s = Number(q.get("s"));
  if (q.has("s") && Number.isInteger(s) && s >= 0 && s < NAMES.length) scenario = s;
  params = inputsOf(scenario);
  for (const sl of SLIDERS) {
    const v = Number(q.get(sl.key));
    if (q.has(sl.key) && Number.isFinite(v) && v >= sl.min && v <= sl.max) params[sl.key] = v;
  }
}

if (model) {
  decode();
  document.getElementById("calc").hidden = false;
  buildScenarios();
  buildInputs();
  syncInputs();
  update();
  selfCheck();
  document.getElementById("share").addEventListener("click", (event) => copy(location.href, event.currentTarget));
  document.getElementById("summary").addEventListener("click", (event) => copy(summaryText(compute(params)), event.currentTarget));
  document.getElementById("reset").addEventListener("click", () => { params = inputsOf(scenario); syncInputs(); update(); });
  document.addEventListener("themechange", () => update());
}
