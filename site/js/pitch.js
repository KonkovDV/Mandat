// Просмотр слайдов: стрелки, PageUp и PageDown, пробел, Home и End, F — во весь экран (Ctrl+F остаётся поиском),
// свайп на телефоне, адрес #N у каждого слайда. Под слайдом — текст выступления из заметок докладчика.
import { data, el } from "./common.js";

const deck = data("page-data");
const slides = deck?.slides ?? [];
const viewer = document.getElementById("viewer");
const img = document.getElementById("slide-img");
const cnt = document.getElementById("cnt");
const pg = document.getElementById("pg");
const thumbs = document.getElementById("thumbs");
const notes = document.getElementById("notes");
const stage = document.getElementById("stage");
let cur = -1;

/** Номер слайда из адреса: #7 или #slide-7; чужой адрес не сбивает текущий слайд. */
function fromHash() {
  const m = location.hash.match(/^#(?:slide-)?(\d+)$/);
  const n = m ? Number(m[1]) : NaN;
  return n >= 1 && n <= slides.length ? n - 1 : -1;
}

function go(i, push = true) {
  i = Math.max(0, Math.min(slides.length - 1, i));
  if (i === cur) return;
  cur = i;
  const s = slides[i];
  img.src = `slides/${s.src || s.image}`;
  img.alt = `Слайд ${s.n}: ${s.title}`;
  cnt.textContent = `${s.n} / ${slides.length}`;
  pg.style.width = `${(100 * (i + 1)) / slides.length}%`;
  for (const [k, a] of [...thumbs.children].entries()) {
    a.setAttribute("aria-current", String(k === i));
    if (k === i) a.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
  const section = s.n <= deck.pitch ? `Питч · ${s.time || ""}`.replace(/ · $/, "") : "Приложение";
  const parts = [
    el("p", { class: "kicker", text: `${section}${s.kicker ? ` · ${s.kicker}` : ""}` }),
    el("h2", { text: s.title }),
    ...(s.notes ? s.notes.split(/\n+/).map((p) => el("p", { text: p })) : [el("p", { class: "note", text: "Слайд приложения: открывается под вопрос жюри, текста выступления нет." })]),
  ];
  if (s.source) parts.push(el("p", { class: "fine", text: `Источник: ${s.source}` }));
  notes.replaceChildren(...parts);
  if (push) history.replaceState(null, "", `#${s.n}`);
  const next = slides[i + 1];
  if (next) new Image().src = `slides/${next.src || next.image}`;
  document.getElementById("prev").disabled = i === 0;
  document.getElementById("next").disabled = i === slides.length - 1;
}

function fullscreen() {
  try {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (stage.requestFullscreen) stage.requestFullscreen().catch(() => {});
  } catch {
    /* браузер без полноэкранного режима: слайд остаётся на странице */
  }
}

if (slides.length) {
  viewer.hidden = false;
  document.getElementById("slide-list").classList.add("enhanced");
  for (const s of slides) {
    const a = el("a", { href: `#${s.n}`, "aria-label": `Слайд ${s.n}: ${s.title}`, "aria-current": "false" }, el("img", { src: `slides/${s.thumb_src || s.src || s.image}`, alt: "", width: 128, height: 72, loading: "lazy", decoding: "async" }));
    a.addEventListener("click", (event) => { event.preventDefault(); go(s.n - 1); });
    thumbs.append(a);
  }
  document.getElementById("prev").addEventListener("click", () => go(cur - 1));
  document.getElementById("next").addEventListener("click", () => go(cur + 1));
  document.getElementById("full").addEventListener("click", fullscreen);
  stage.addEventListener("click", (event) => {
    const box = stage.getBoundingClientRect();
    go(event.clientX - box.left > box.width / 3 ? cur + 1 : cur - 1);
  });
  let x0 = null;
  stage.addEventListener("pointerdown", (event) => { x0 = event.clientX; });
  stage.addEventListener("pointerup", (event) => {
    if (x0 !== null && Math.abs(event.clientX - x0) > 40) go(event.clientX < x0 ? cur + 1 : cur - 1);
    x0 = null;
  });
  addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented) return;
    const tag = document.activeElement?.tagName || "";
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag)) return;
    const k = event.key;
    if ((k === " " || k === "Enter") && /^(BUTTON|A)$/.test(tag)) return;
    if (["ArrowRight", "ArrowDown", "PageDown", " "].includes(k)) { event.preventDefault(); go(cur + 1); }
    else if (["ArrowLeft", "ArrowUp", "PageUp"].includes(k)) { event.preventDefault(); go(cur - 1); }
    else if (k === "Home") { event.preventDefault(); go(0); }
    else if (k === "End") { event.preventDefault(); go(slides.length - 1); }
    else if (/^[fFаА]$/.test(k)) fullscreen();
  });
  addEventListener("hashchange", () => { const h = fromHash(); if (h >= 0) go(h, false); });
  const h = fromHash();
  go(h >= 0 ? h : 0, h >= 0);
}
