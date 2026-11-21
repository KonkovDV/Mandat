// Плеер сценария mandat showcase: шаги по одному, панель состояния и журнал, который растёт с каждым шагом.
// Без скриптов страница показывает все шаги сразу и итоговое состояние.
import { data, el, plural, reducedMotion } from "./common.js";

const show = data("page-data");
const track = document.getElementById("track");
const items = [...track.children];
const controls = document.getElementById("controls");
const play = document.getElementById("play");
const pos = document.getElementById("pos");
const s = {
  att: document.getElementById("s-att"),
  nonce: document.getElementById("s-nonce"),
  dbo: document.getElementById("s-dbo"),
  journal: document.getElementById("s-journal"),
  records: document.getElementById("s-records"),
};
const ACTION = {
  verify: (d) => `проверка кейса: ${d.verdict === "confirmed" ? "подтверждено" : d.verdict}`,
  authorize_operation: (d) => `допуск ДБО: ${d.allowed ? "допущено" : "отказ"}`,
  status_change: (d) => `статус удостоверения: ${d.status === "suspension" ? "приостановлено" : "отозвано"}`,
};

let at = reducedMotion() ? items.length - 1 : 0;
let timer = null;

function stateAfter(i) {
  const steps = show.steps.slice(0, i + 1);
  const issued = steps.some((x) => x.outcome === "ВЫПУЩЕНО");
  const takeover = steps.some((x) => x.outcome === "ПРИЗНАКИ ЗАХВАТА");
  const admitted = steps.some((x) => x.outcome === "ДОПУЩЕНО");
  const last = [...steps].reverse().find((x) => x.title.startsWith("ДБО"));
  return {
    att: !issued ? "ещё не выпущено" : takeover ? "приостановлено: признаки захвата" : "действует, на один платёж",
    nonce: !issued ? "—" : admitted ? "израсходован первым платежом" : "свободен",
    dbo: !last ? "запросов ещё не было" : last.outcome === "ДОПУЩЕНО" ? "допущено" : `отказ: ${last.detail}`,
    records: steps.flatMap((x) => x.records),
  };
}

function render() {
  items.forEach((item, i) => {
    item.classList.toggle("later", i > at);
    item.classList.toggle("current", i === at);
    item.setAttribute("aria-current", i === at ? "step" : "false");
  });
  const st = stateAfter(at);
  s.att.textContent = st.att;
  s.nonce.textContent = st.nonce;
  s.dbo.textContent = st.dbo;
  const n = st.records.length;
  s.journal.textContent = `${n} ${plural(n, "запись", "записи", "записей")}, цепочка цела`;
  s.records.replaceChildren(...st.records.map((r) => el("li", { value: r.seq, text: (ACTION[r.action] || (() => r.action))(r.details) })));
  pos.textContent = `Шаг ${at + 1} из ${items.length}`;
  document.getElementById("prev").disabled = at === 0;
  document.getElementById("next").disabled = at === items.length - 1;
}

function stop() {
  clearInterval(timer);
  timer = null;
  play.textContent = at === items.length - 1 ? "Ещё раз" : "Пуск";
}

function go(i) {
  at = Math.max(0, Math.min(items.length - 1, i));
  render();
  if (at === items.length - 1) stop();
}

if (show && items.length === show.steps.length) {
  controls.hidden = false;
  play.addEventListener("click", () => {
    if (timer) return stop();
    if (at === items.length - 1) go(0);
    play.textContent = "Пауза";
    timer = setInterval(() => go(at + 1), 1700);
  });
  document.getElementById("prev").addEventListener("click", () => { stop(); go(at - 1); });
  document.getElementById("next").addEventListener("click", () => { stop(); go(at + 1); });
  document.getElementById("reset").addEventListener("click", () => { stop(); go(0); });
  items.forEach((item, i) => item.addEventListener("click", () => { stop(); go(i); }));
  addEventListener("keydown", (event) => {
    if (event.target.closest("input, textarea, select")) return;
    if (event.key === "ArrowRight") { stop(); go(at + 1); }
    if (event.key === "ArrowLeft") { stop(); go(at - 1); }
  });
  render();
  stop();
}
