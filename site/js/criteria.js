// Критерии: вкладки этапов и поиск по всем критериям сразу; адрес #s1, #s2, #s3 или #c открывает вкладку.
import "./common.js";
import { tabs } from "./tabs.js";

const list = document.getElementById("tabs");
const wanted = location.hash.slice(1);
let ready = false;
const control = tabs(list, (tab) => ready && history.replaceState(null, "", `#${tab.id.slice(2)}`));
if (["s1", "s2", "s3", "c"].includes(wanted)) control.select(`t-${wanted}`);
ready = true;

const q = document.getElementById("q");
const found = document.getElementById("found");
q.hidden = false;
q.addEventListener("input", () => {
  const text = q.value.trim().toLowerCase();
  const panels = [...document.querySelectorAll(".panel")];
  if (!text) {
    for (const li of document.querySelectorAll(".crit > li")) li.hidden = false;
    for (const panel of panels) panel.classList.remove("searching");
    for (const fold of document.querySelectorAll(".panel details")) fold.open = false;
    found.textContent = "";
    control.select(list.querySelector('[aria-selected="true"]').id);
    return;
  }
  let hits = 0;
  for (const panel of panels) {
    let inPanel = 0;
    for (const li of panel.querySelectorAll(".crit > li")) {
      const on = li.textContent.toLowerCase().includes(text);
      li.hidden = !on;
      inPanel += on ? 1 : 0;
    }
    panel.hidden = inPanel === 0;
    // Сводка спринта на время поиска уходит, свёрнутые доказательства с находками раскрываются.
    panel.classList.add("searching");
    for (const fold of panel.querySelectorAll("details")) fold.open = inPanel > 0;
    hits += inPanel;
  }
  found.textContent = hits ? `найдено: ${hits}` : "ничего не нашлось";
});
