// Вкладки по шаблону WAI-ARIA: role="tablist", у вкладки aria-controls на свою панель.
// Без скриптов все панели видны подряд; скрипт оставляет одну и даёт листать стрелками.
export function tabs(list, onChange) {
  const items = [...list.querySelectorAll('[role="tab"]')];
  const panel = (tab) => document.getElementById(tab.getAttribute("aria-controls"));
  const select = (tab, focus) => {
    for (const t of items) {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      const p = panel(t);
      if (p) p.hidden = !on;
    }
    if (focus) tab.focus();
    if (onChange) onChange(tab);
  };
  list.addEventListener("click", (event) => {
    const tab = event.target.closest('[role="tab"]');
    if (tab) select(tab, false);
  });
  list.addEventListener("keydown", (event) => {
    const i = items.indexOf(document.activeElement);
    if (i < 0) return;
    const next = { ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1, Home: 0, End: items.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    select(items[(next + items.length) % items.length], true);
  });
  const initial = items.find((t) => t.getAttribute("aria-selected") === "true") || items[0];
  select(initial, false);
  return { select: (id) => { const t = items.find((x) => x.id === id || x.getAttribute("aria-controls") === id); if (t) select(t, false); } };
}
