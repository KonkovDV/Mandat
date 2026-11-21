// Общее для всех страниц: кнопка темы, чтение данных страницы и безопасная сборка DOM.
// Данные приходят из блока <script type="application/json">, который собрал scripts/build_site.py.
// Текст из данных вставляется только через textContent: разметку из данных страница не исполняет.

const SVG_NS = "http://www.w3.org/2000/svg";

export function data(id) {
  const node = document.getElementById(id);
  return node ? JSON.parse(node.textContent) : null;
}

/** Элемент с атрибутами и детьми; строки становятся текстовыми узлами. */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  setAttrs(node, attrs);
  append(node, children);
  return node;
}

export function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  setAttrs(node, attrs);
  append(node, children);
  return node;
}

function setAttrs(node, attrs) {
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") node.textContent = String(value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
}

function append(node, children) {
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

const NBSP = " ";

/** Число по-русски, как _num в scripts/build_site.py: узкий неразрывный пробел между разрядами, запятая. */
export function num(value, digits = 0) {
  const fixed = Math.abs(value).toFixed(digits);
  const [int, frac] = fixed.split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  const sign = value < 0 && Number(fixed) !== 0 ? "−" : "";
  return sign + grouped + (frac ? "," + frac : "");
}

export function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

export function ruDate(iso) {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}.${m}.${y}`;
}

export function rub(value, digits = 0) {
  return `${num(value, digits)}${NBSP}₽`;
}

/** Кнопка темы: светлая ↔ тёмная, выбор запоминается. */
function themeButton() {
  const button = document.querySelector(".theme");
  if (!button) return;
  const root = document.documentElement;
  const dark = () => root.dataset.theme === "dark" || (!root.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  const label = () => button.setAttribute("aria-label", dark() ? "Включить светлую тему" : "Включить тёмную тему");
  label();
  button.addEventListener("click", () => {
    const next = dark() ? "light" : "dark";
    root.dataset.theme = next;
    try {
      localStorage.setItem("mandat-theme", next);
    } catch (e) {
      /* без хранилища тема держится до перезагрузки */
    }
    label();
    document.dispatchEvent(new CustomEvent("themechange"));
  });
}

/** Копирует текст в буфер; на старом браузере выделяет его в поле для ручного копирования. */
export async function copy(text, button) {
  const old = button.textContent;
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = "Скопировано";
  } catch (e) {
    window.prompt("Скопируйте вручную:", text);
  }
  setTimeout(() => (button.textContent = old), 1600);
}

/** Подсказка у графика: одна на контейнер, позиция — у указателя или у элемента с фокусом. */
export function tooltip(container) {
  const tip = el("div", { class: "tip", role: "status", hidden: true });
  container.append(tip);
  return {
    show(target, title, lines, event) {
      tip.replaceChildren(el("b", { text: title }), ...lines.map((line) => el("div", { text: line })));
      tip.hidden = false;
      const box = container.getBoundingClientRect();
      const at = event && "clientX" in event && event.clientX ? { x: event.clientX, y: event.clientY } : (() => {
        const r = target.getBoundingClientRect();
        return { x: r.left + r.width / 2, y: r.top };
      })();
      const x = Math.min(Math.max(at.x - box.left + 12, 4), box.width - tip.offsetWidth - 4);
      const y = Math.max(at.y - box.top - tip.offsetHeight - 10, 4);
      tip.style.left = `${x}px`;
      tip.style.top = `${y}px`;
    },
    hide() {
      tip.hidden = true;
    },
  };
}

export function reducedMotion() {
  return matchMedia("(prefers-reduced-motion: reduce)").matches;
}

themeButton();
