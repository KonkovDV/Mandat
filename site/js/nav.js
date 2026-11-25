// Навигация по сайту: маршруты по ролям, «Дальше» по шагам маршрута и активный пункт меню на телефоне.
// Маршрут выбирают на главной, в строке «Маршрут для» или в «Кому полезно» в начале страницы. Он держится в адресе
// (?route=jury) и в sessionStorage, пока открыта вкладка. Без скриптов работают обычные «Назад» и «Дальше»
// в порядке страниц сайта: их собирает scripts/build_site.py.
import { data, el, svg } from "./common.js";

const KEY = "mandat-route";
const site = data("site-routes");
const here = location.pathname.split("/").pop().replace(/\.html$/, "") || "index";
const pagerBox = document.querySelector(".pager");
const original = pagerBox ? [...pagerBox.childNodes].map((n) => n.cloneNode(true)) : [];
let bar = null;
let watcher = null;

const arrow = (dir) =>
  svg("svg", { class: "ar", viewBox: "0 0 24 24", "aria-hidden": "true" }, svg("path", { d: dir === "left" ? "M19 12H5M12 19l-7-7 7-7" : "M5 12h14M12 5l7 7-7 7" }));
const cross = () => svg("svg", { class: "x", viewBox: "0 0 24 24", "aria-hidden": "true" }, svg("path", { d: "M18 6 6 18M6 6l12 12" }));
const title = (step) => site.pages[step.page].title;
const routeOf = (id) => site.routes.find((r) => r.id === id) || null;

// ---------- Меню на телефоне: текущая страница в середине ленты, края подсказывают, что лента листается ----------
function menu() {
  const nav = document.querySelector(".top .nav");
  if (!nav) return;
  const sync = () => {
    const max = nav.scrollWidth - nav.clientWidth;
    nav.classList.toggle("can-left", max > 1 && nav.scrollLeft > 4);
    nav.classList.toggle("can-right", max > 1 && nav.scrollLeft < max - 4);
  };
  const current = nav.querySelector('[aria-current="page"]');
  if (current && nav.scrollWidth > nav.clientWidth) {
    const a = current.getBoundingClientRect();
    const n = nav.getBoundingClientRect();
    nav.scrollLeft += a.left - n.left - (n.width - a.width) / 2;
  }
  sync();
  nav.addEventListener("scroll", sync, { passive: true });
  addEventListener("resize", sync, { passive: true });
}

// ---------- Состояние маршрута ----------
function read() {
  let state = null;
  try {
    state = JSON.parse(sessionStorage.getItem(KEY) || "null");
  } catch {
    state = null;
  }
  const asked = new URLSearchParams(location.search).get("route");
  if (asked && routeOf(asked) && (!state || state.id !== asked)) state = { id: asked, seen: [] };
  if (!state || !routeOf(state.id)) return null;
  if (!Array.isArray(state.seen)) state.seen = [];
  return state;
}

function write(state) {
  try {
    if (state) sessionStorage.setItem(KEY, JSON.stringify(state));
    else sessionStorage.removeItem(KEY);
  } catch {
    /* хранилище закрыто настройками браузера: маршрут держится только в адресе */
  }
}

function dropParam() {
  const url = new URL(location.href);
  if (!url.searchParams.has("route")) return;
  url.searchParams.delete("route");
  history.replaceState(history.state, "", url.pathname + url.search + url.hash);
}

function stop() {
  write(null);
  dropParam();
  render(null);
  const head = document.querySelector(".brand");
  if (head) head.focus({ preventScroll: true });
}

// ---------- Ход маршрута внизу экрана ----------
function capsule(route, at, state) {
  const n = route.steps.length;
  const onStep = at >= 0;
  const dots = el(
    "ol",
    { class: "rb-dots" },
    ...route.steps.map((s, k) =>
      el(
        "li",
        { class: k === at ? "now" : state.seen.includes(s.page) ? "done" : "" },
        el("a", { href: s.href, "aria-label": `Шаг ${k + 1} из ${n}: ${title(s)}`, "aria-current": k === at ? "step" : null }),
      ),
    ),
  );
  let action;
  if (onStep && at === n - 1) {
    action = el("a", { class: "rb-next finish", href: pagerBox ? "#pager" : `index.html#route-${route.id}` }, el("span", { class: "rb-verb" }, "Маршрут пройден"));
  } else {
    const next = onStep ? route.steps[at + 1] : route.steps.find((s) => !state.seen.includes(s.page)) || route.steps[0];
    const verb = onStep ? "Дальше" : "Продолжить";
    action = el(
      "a",
      { class: "rb-next", href: next.href, "aria-label": `${verb}: ${title(next)}` },
      el("span", { class: "rb-verb" }, verb, el("span", { class: "rb-to" }, `: ${title(next)}`)),
      arrow("right"),
    );
  }
  return el(
    "aside",
    { class: "routebar", "aria-label": `Маршрут «${route.title}»` },
    el("a", { class: "rb-name", href: `index.html#route-${route.id}`, title: route.title }, route.short),
    dots,
    el("span", { class: "rb-pos" }, onStep ? `${at + 1} из ${n}` : "вне маршрута"),
    action,
    el("button", { class: "rb-close", type: "button", "aria-label": "Закрыть маршрут", onclick: stop }, cross()),
  );
}

// ---------- «Назад» и «Дальше» внизу страницы по шагам маршрута ----------
function card(kind, href, dir, name, sub, icon) {
  return el(
    "a",
    { class: `pager-card ${kind}`, href, rel: kind },
    icon ? el("img", { class: "pager-art", src: icon, width: 160, height: 160, alt: "", loading: "lazy", decoding: "async" }) : null,
    el("span", { class: "pager-dir" }, kind === "prev" ? arrow("left") : null, dir, kind === "next" ? arrow("right") : null),
    el("b", {}, name),
    el("span", { class: "pager-sub" }, sub),
  );
}

function routePager(route, at) {
  const n = route.steps.length;
  const track = el(
    "ol",
    { class: "pager-track" },
    ...route.steps.map((s, k) =>
      el(
        "li",
        { class: k < at ? "done" : k === at ? "now" : "" },
        el("a", { href: s.href, title: title(s), "aria-current": k === at ? "step" : null }, el("span", { class: "sr-only" }, `${k + 1}. ${title(s)}`)),
      ),
    ),
  );
  const before = route.steps[at - 1];
  const after = route.steps[at + 1];
  const prev = before
    ? card("prev", before.href, "Назад", title(before), before.text)
    : card("prev", `index.html#route-${route.id}`, "Назад", "Все маршруты", "Пять маршрутов по ролям на главной");
  const next = after
    ? card("next", after.href, "Дальше по маршруту", title(after), after.text, site.pages[after.page].icon_src)
    : el(
        "div",
        { class: "pager-card next finish" },
        el("span", { class: "pager-dir" }, "Маршрут пройден"),
        el("b", {}, "Глубже — документы"),
        el("ul", { class: "finish-docs" }, ...route.docs.map((d) => el("li", {}, el("a", { href: d.href }, d.text)))),
      );
  pagerBox.classList.add("on-route");
  pagerBox.replaceChildren(
    el("div", { class: "pager-head" }, el("p", { class: "pager-pos" }, `Маршрут «${route.title}» · шаг ${at + 1} из ${n}`), track),
    el("div", { class: "pager-cards" }, prev, next),
    el(
      "p",
      { class: "pager-all" },
      el("button", { class: "pager-stop", type: "button", onclick: stop }, "Выйти из маршрута"),
      el("a", { href: "index.html#routes" }, "Другие маршруты"),
    ),
  );
}

function render(state) {
  const route = state ? routeOf(state.id) : null;
  const at = route ? route.steps.findIndex((s) => s.page === here) : -1;
  if (route && at >= 0 && !state.seen.includes(here)) state.seen.push(here);
  if (route) write(state);
  document.documentElement.classList.toggle("on-route", Boolean(route));
  for (const a of document.querySelectorAll("[data-route]")) a.classList.toggle("is-on", Boolean(route) && a.dataset.route === route.id);
  if (bar) bar.remove();
  bar = route ? capsule(route, at, state) : null;
  if (bar) document.body.append(bar);
  if (pagerBox) {
    pagerBox.classList.remove("on-route");
    if (route && at >= 0) routePager(route, at);
    else pagerBox.replaceChildren(...original.map((n) => n.cloneNode(true)));
  }
  // Когда «Дальше» внизу страницы уже на экране, ход маршрута уступает ему место.
  if (watcher) watcher.disconnect();
  if (bar && pagerBox && "IntersectionObserver" in window) {
    watcher = new IntersectionObserver(([entry]) => bar && bar.classList.toggle("is-away", entry.isIntersecting), { threshold: 0.2 });
    watcher.observe(pagerBox);
  }
}

// Ссылка маршрута на эту же страницу не перезагружает её: маршрут включается на месте.
function joinHere(event) {
  const a = event.target.closest("a[href*='route=']");
  if (!a || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const url = new URL(a.href, location.href);
  if (url.pathname !== location.pathname || url.origin !== location.origin) return;
  const id = url.searchParams.get("route");
  if (!routeOf(id)) return;
  event.preventDefault();
  history.replaceState(history.state, "", url.pathname + url.search + (url.hash || location.hash));
  const now = read();
  render(now && now.id === id ? now : { id, seen: [] });
  if (url.hash) document.getElementById(decodeURIComponent(url.hash.slice(1)))?.scrollIntoView({ block: "start" });
}

function start() {
  menu();
  if (!site) return;
  render(read());
  document.addEventListener("click", joinHere);
  // Возврат кнопкой «Назад» достаёт страницу из кэша: маршрут могли закрыть на другой странице.
  addEventListener("pageshow", (event) => {
    if (event.persisted) render(read());
  });
}

// Страница, подготовленная заранее (Speculation Rules), не трогает маршрут, пока её не открыли.
if (document.prerendering) document.addEventListener("prerenderingchange", start, { once: true });
else start();
