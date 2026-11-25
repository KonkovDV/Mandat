// Главная, первый экран: один и тот же человек с одной доверенностью, меняется только дата операции.
// Ответы на каждую дату посчитаны при сборке страницы (scripts/build_site.py, _scrub) и лежат в page-data:
// карточка их только показывает. Смену дат можно остановить кнопкой; при «уменьшить движение» она не идёт.
import { data, ruDate } from "./common.js";

const scrub = data("page-data");
const root = document.getElementById("live");
if (root && scrub && Array.isArray(scrub.segments)) {
  const DAY = 86400000;
  const WORD = { confirmed: "Подтверждено", refuted: "Опровергнуто", needs_review: "На разбор", insufficient_data: "Недостаточно данных" };
  const NOTE = { confirmed: "все звенья держатся в этот день", refuted: "полномочия в этот день нет", needs_review: "спорный случай решает человек", insufficient_data: "данных для ответа не хватает" };

  // Коротко о причине: что именно решило ответ в этом отрезке дат.
  function reason(segment) {
    const first = segment.why[0];
    if (segment.verdict === "confirmed" || !first) return NOTE[segment.verdict];
    if (/раньше выдачи/.test(first.message)) return "доверенность ещё не выдана";
    if (/истекла/.test(first.message)) return "срок доверенности истёк";
    if (first.rule === "R-EIO-002") return "директор сменился недавно: решает человек";
    if (first.rule === "R-EIO-006") return "выписка ЕГРЮЛ устарела: нужна свежая";
    return NOTE[segment.verdict];
  }
  function dateIn(segment) {
    if (segment.from <= scrub.op && scrub.op <= segment.to) return scrub.op;
    const a = Date.parse(segment.from), b = Date.parse(segment.to);
    return new Date(a + Math.round((b - a) / 2 / DAY) * DAY).toISOString().slice(0, 10);
  }

  const frames = scrub.segments.map((segment) => ({
    date: ruDate(dateIn(segment)),
    css: segment.css,
    word: WORD[segment.verdict],
    note: reason(segment),
    link: segment.verdict === "confirmed" || !segment.why[0] ? "" : segment.why[0].link,
  }));
  const start = Math.max(scrub.segments.findIndex((s) => s.from <= scrub.op && scrub.op <= s.to), 0);

  const dateNode = document.getElementById("live-date");
  const verdict = document.getElementById("live-verdict");
  const links = [...root.querySelectorAll("#live-chain li")];
  const toggle = document.getElementById("live-toggle");

  function show(frame) {
    dateNode.textContent = frame.date;
    verdict.className = `live-verdict ${frame.css}`;
    verdict.querySelector("b").textContent = frame.word;
    verdict.querySelector("span").textContent = frame.note;
    for (const li of links) li.className = li.dataset.link === frame.link ? frame.css : "ok";
    root.classList.remove("is-fresh");
    void root.offsetWidth; // перезапуск короткой анимации смены даты
    root.classList.add("is-fresh");
  }

  let index = start, timer = 0, paused = false, visible = true;
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  const tick = () => {
    index = (index + 1) % frames.length;
    show(frames[index]);
  };
  function sync() {
    clearInterval(timer);
    timer = 0;
    if (!paused && visible && !document.hidden && !still.matches) timer = setInterval(tick, 2600);
  }
  show(frames[index]);

  if (toggle) {
    toggle.hidden = still.matches;
    toggle.addEventListener("click", () => {
      paused = !paused;
      toggle.setAttribute("aria-pressed", String(paused));
      toggle.setAttribute("aria-label", paused ? "Запустить смену дат" : "Остановить смену дат");
      sync();
    });
  }
  new IntersectionObserver((entries) => {
    visible = entries[0].isIntersecting;
    sync();
  }).observe(root);
  document.addEventListener("visibilitychange", sync);
  still.addEventListener("change", () => {
    if (toggle) toggle.hidden = still.matches;
    sync();
  });
  sync();
}
