// Проверка сайта и демо-отчётов в браузере: матрица устройств и сценарии взаимодействия.
//
//   python scripts/build_site.py --out _site
//   node scripts/check_site_devices.js _site [каталог-для-снимков]
//
// Нужен Node.js и Playwright с Chromium (как для видео-демо). Код выхода 1 — есть замечания.
//
// 1. Девять устройств в светлой и тёмной теме, все страницы сайта и 14 отчётов: страница не уезжает вбок и ни один
//    блок не вылезает за экран, нет неудачных запросов и ошибок в консоли, шрифт Golos загружен, якоря ведут на
//    разделы, у картинок есть alt, кнопки и пункты меню не ниже 40 px, остальные цели касания не меньше 24 × 24 px
//    (WCAG 2.2, 2.5.8), в тексте нет NaN, undefined и Infinity.
// 2. Сценарии на компьютере: шкала даты и «что если» дают ответ ядра, подпись удостоверения сходится и ломается
//    от подмены поля, статус после захвата — «приостановлено», правка записи журнала даёт другой корень; калькулятор
//    сверяется с независимой эталонной моделью на случайных допущениях и не ломается от испорченного адреса;
//    слайды листаются стрелками, Home и End, адрес #N открывает слайд, F включает весь экран, а Ctrl+F — нет.
// 3. axe-core из scripts/package-lock.json (cd scripts && npm ci) проверяет каждую страницу на WCAG 2.2 AA;
//    в CI то же делает задача devices в .github/workflows/pages.yml.
// Движок — Chromium: Safari и Firefox на этой машине не проверяются, их держат стили с запасом.
"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const { chromium } = require("playwright");

const TYPES = {
  ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".js": "text/javascript",
  ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".woff2": "font/woff2", ".pdf": "application/pdf",
  ".webmanifest": "application/manifest+json", ".txt": "text/plain; charset=utf-8", ".json": "application/json",
  ".mp4": "video/mp4", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};
const PAGES = ["index", "cases", "showcase", "verify", "economics", "pilot", "law", "criteria", "pitch"];

// Страница проверяется так, как её отдаст GitHub Pages: по HTTP, под /Mandat/, с типами файлов и своей 404.
function serve(root) {
  const server = http.createServer((req, res) => {
    let rel = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
    if (!rel.startsWith("/Mandat/")) { res.writeHead(404).end(); return; }
    rel = rel.slice("/Mandat".length).replace(/\/$/, "/index.html");
    // Путь из адреса не выходит за корень сайта: сравнение с нормализованным корнем и разделителем.
    const base = path.resolve(root);
    const file = path.resolve(base, "." + rel);
    if (!file.startsWith(base + path.sep) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404, { "Content-Type": TYPES[".html"] });
      fs.createReadStream(path.join(root, "404.html")).pipe(res);
      return;
    }
    res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

const DEVICES = [
  ["Galaxy Z Fold, сложен", 280, 653, 3, true],
  ["iPhone SE", 375, 667, 2, true],
  ["iPhone 15 Pro", 393, 852, 3, true],
  ["Pixel 7", 412, 915, 2.625, true],
  ["iPad mini", 768, 1024, 2, true],
  ["iPad Pro 12.9, альбом", 1366, 1024, 2, true],
  ["Ноутбук 1366", 1366, 768, 1, false],
  ["Монитор Full HD", 1920, 1080, 1, false],
  ["Монитор 2560", 2560, 1440, 1, false],
];

function watch(page) {
  const out = [];
  page.on("requestfailed", (r) => out.push("запрос не прошёл: " + r.url()));
  page.on("response", (r) => r.status() >= 400 && out.push(`${r.status()} ${r.url()}`));
  page.on("pageerror", (e) => out.push("исключение: " + String(e)));
  page.on("console", (m) => m.type() === "error" && out.push("консоль: " + m.text()));
  page.on("dialog", (d) => { out.push("диалог: " + d.message()); d.dismiss(); });
  return out;
}

async function checkLayout(page, url, problems, label, { report = false } = {}) {
  const errors = watch(page);
  await page.goto(url, { waitUntil: "load" });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(120);
  const r = await page.evaluate((isReport) => {
    const doc = document.documentElement, vw = doc.clientWidth;
    const out = { overflow: doc.scrollWidth - vw };
    out.wide = [...document.querySelectorAll("body *")].filter((el) => {
      if (el.closest(".sr-only,[aria-hidden='true'],svg,.table-wrap,.thumbs,.case-list,.nav") || ["fixed", "absolute"].includes(getComputedStyle(el).position)) return false;
      const b = el.getBoundingClientRect();
      return b.width > 0 && b.right > vw + 1;
    }).slice(0, 4).map((el) => el.tagName.toLowerCase() + "." + [...el.classList].join("."));
    // Слово, которое не влезло в строку и разорвано без дефиса («ДОКАЗАТЕЛЬСТ / ВА»). Пути, хеши и код
    // (с «/», «_», «.», «:», «=», «@» или цифрами) рвать можно, обычные слова — нет.
    out.broken = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node && out.broken.length < 4; node = walker.nextNode()) {
      const host = node.parentElement;
      if (!host || host.closest("code, pre, kbd, samp, svg, .hash, .loc, .sr-only, [hidden], [aria-hidden='true'], script, style")) continue;
      if (!host.getClientRects().length) continue;
      // Части составного слова проверяются по отдельности: перенос после дефиса допустим.
      for (const m of node.data.matchAll(/[^\s  \-‐‑–—]+/g)) {
        const word = m[0];
        const token = node.data.slice(0, m.index).split(/[\s  ]/).pop() + node.data.slice(m.index).split(/[\s  ]/)[0];
        if (word.length < 4 || /[\/_.:=@\d]/.test(token)) continue;
        const range = document.createRange();
        range.setStart(node, m.index);
        range.setEnd(node, m.index + word.length);
        const tops = new Set([...range.getClientRects()].filter((r) => r.width > 0).map((r) => Math.round(r.top / 4)));
        if (tops.size > 1) { out.broken.push(word); break; }
      }
    }
    if (isReport) return out;
    out.font = document.fonts.check('16px "Golos Text"');
    out.badAnchors = [...document.querySelectorAll('a[href^="#"]')].map((a) => a.getAttribute("href").slice(1)).filter((id) => id && !/^\d+$/.test(id) && !document.getElementById(decodeURIComponent(id)));
    out.noAlt = [...document.querySelectorAll("img")].filter((i) => !i.hasAttribute("alt")).length;
    const small = [], tiny = [];
    for (const el of document.querySelectorAll("button, a, input[type=range], input[type=search], summary")) {
      const box = el.getBoundingClientRect(), s = getComputedStyle(el);
      if (!box.width || !box.height || s.visibility === "hidden" || el.closest("[hidden]")) continue;
      const big = el.matches(".btn, .nav a");
      if (big && box.height < 40) small.push(el.textContent.trim().slice(0, 30));
      const inline = el.matches("a") && s.display === "inline";
      if (!big && !inline && (box.width < 24 || box.height < 24)) tiny.push((el.getAttribute("aria-label") || el.textContent).trim().slice(0, 30));
    }
    out.small = small;
    out.tiny = tiny;
    out.junk = /\b(NaN|undefined|Infinity|null)\b/.test(document.body.innerText);
    return out;
  }, report);
  if (r.overflow > 0) problems.push(`${label}: страница шире экрана на ${r.overflow} px`);
  if (r.wide.length) problems.push(`${label}: блоки за краем экрана: ${r.wide.join(", ")}`);
  if (r.broken.length) problems.push(`${label}: слово разорвано между строками: ${r.broken.map((w) => `«${w}»`).join(", ")}`);
  if (errors.length) problems.push(`${label}: ${errors.slice(0, 2).join("; ")}`);
  if (!report) {
    if (!r.font) problems.push(`${label}: шрифт Golos Text не загружен`);
    if (r.badAnchors.length) problems.push(`${label}: якоря без разделов ${r.badAnchors.join(", ")}`);
    if (r.noAlt) problems.push(`${label}: картинок без alt — ${r.noAlt}`);
    if (r.small.length) problems.push(`${label}: кнопки ниже 40 px: ${[...new Set(r.small)].join(", ")}`);
    if (r.tiny.length) problems.push(`${label}: цели касания меньше 24 px: ${[...new Set(r.tiny)].slice(0, 4).join(", ")}`);
    if (r.junk) problems.push(`${label}: в тексте страницы NaN, undefined, Infinity или null`);
  }
}

// ---------- Эталонная модель калькулятора: написана заново, не из кода страницы ----------
function reference(p) {
  const fte = (p.clients * 1e6 * p.event_share * p.minutes) / 60 / p.hours_fte;
  const costFte = (p.salary_ops * 1000 * 12 * (1 + p.bank_contrib) * (1 + p.overhead)) / 1e6;
  const prevented = p.incidents_q * 4 * p.vtb_share * p.authority_share * (p.loss_q / p.incidents_q);
  const effect = fte * costFte * p.realization + prevented * p.bank_loss_share;
  const oneOff = p.impl_t1 * (1 + p.vat) + p.bank_integration_py * p.bank_it_cost + p.bank_infra + p.bank_security;
  const annual = p.fixed_t1 + p.clients * p.price_client + p.bank_support_py * p.bank_it_cost;
  const y1 = effect * p.ramp - annual, y2 = effect - annual;
  const payback = y1 >= oneOff ? (12 * oneOff) / y1 : y2 <= 0 ? Infinity : 12 + (12 * (oneOff - y1)) / y2;
  return { fte, prevented, payback };
}

function months(m) {
  if (!Number.isFinite(m)) return "не окупается";
  const v = m >= 12 ? Math.round(m) : Math.round(m * 10) / 10;
  const n = Math.abs(v) % 100, k = n % 10;
  const word = n > 10 && n < 20 ? "месяцев" : k === 1 ? "месяц" : k > 1 && k < 5 ? "месяца" : "месяцев";
  return Number.isInteger(v) ? `${v} ${word}` : `${String(v).replace(".", ",")} месяца`;
}

function rng(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
}

async function scenarios(context, base, problems) {
  const page = await context.newPage();
  const errors = watch(page);
  const expect = (ok, text) => ok || problems.push("сценарий: " + text);

  // Главная: шкала даты — ответ ядра на день операции кейса и опровержение до выдачи МЧД.
  await page.goto(base + "index.html");
  const scrub = await page.evaluate(() => JSON.parse(document.getElementById("page-data").textContent));
  expect(/подтверждено/.test(await page.locator("#scrub-card").innerText()), "шкала: на дату кейса не «подтверждено»");
  await page.locator("#scrub-day").fill("10");
  expect(/опровергнуто/.test(await page.locator("#scrub-card").innerText()), "шкала: до выдачи МЧД не «опровергнуто»");
  expect(scrub.segments.length >= 5, "шкала: меньше пяти отрезков");

  // Кейсы: «что если» и адрес кейса.
  await page.goto(base + "cases.html#11_parent_revoked");
  expect(/Основная МЧД отменена/.test(await page.locator("#panel h2").innerText()), "кейсы: адрес #11 не открыл кейс 11");
  await page.locator('#lab-registry button[data-v="revoked"]').click();
  expect(/опровергнуто/.test(await page.locator("#lab-card").innerText()), "что если: отменённая МЧД не опровергнута");
  await page.locator('#lab-registry button[data-v="active"]').click();
  await page.locator('#lab-toggles button[data-t="fresh"]').click();
  expect(/на разбор/.test(await page.locator("#lab-card").innerText()), "что если: свежая смена директора не на разбор");

  // Проверка в браузере: подпись, подмена, статус после захвата, журнал.
  await page.goto(base + "verify.html");
  await page.waitForSelector("#sig-result.ok, #sig-result.no", { timeout: 8000 });
  expect(await page.locator("#sig-result.ok").count() === 1, "проверка: подпись удостоверения не сошлась в браузере");
  await page.locator('#tamper button[data-field="amount"]').click();
  await page.waitForSelector("#sig-result.no", { timeout: 8000 });
  await page.locator('#tamper button[data-field="amount"]').click();
  await page.waitForSelector("#sig-result.ok", { timeout: 8000 });
  await page.locator('#moment button[data-moment="after"]').click();
  await page.waitForFunction(() => /Приостановлено/.test(document.getElementById("status-result").textContent), null, { timeout: 8000 }).catch(() => problems.push("проверка: после захвата статус не «приостановлено»"));
  await page.waitForSelector("#journal-result.ok", { timeout: 8000 }).catch(() => problems.push("проверка: запись журнала не сошлась с корнем"));
  await page.locator("#flip-verdict").click();
  await page.waitForSelector("#journal-result.no", { timeout: 8000 }).catch(() => problems.push("проверка: правка записи журнала не замечена"));

  // Калькулятор: эталонная модель на случайных допущениях и испорченные адреса.
  await page.goto(base + "economics.html");
  const model = await page.evaluate(() => JSON.parse(document.getElementById("page-data").textContent));
  const random = rng(20261121);
  for (let run = 0; run < 25; run++) {
    const s = Math.floor(random() * 3);
    const p = Object.fromEntries(Object.entries(model.inputs).map(([k, v]) => [k, v[s]]));
    const pick = (min, max, step) => Math.round((min + random() * (max - min)) / step) * step;
    Object.assign(p, {
      event_share: +pick(0.05, 0.4, 0.01).toFixed(2), minutes: pick(10, 60, 5), salary_ops: pick(60, 160, 5),
      vtb_share: +pick(0.1, 0.35, 0.01).toFixed(2), authority_share: +pick(0.05, 0.5, 0.01).toFixed(2), realization: +pick(0.2, 0.8, 0.05).toFixed(2),
    });
    const hash = `s=${s}&` + ["event_share", "minutes", "salary_ops", "vtb_share", "authority_share", "realization"].map((k) => `${k}=${p[k]}`).join("&");
    // Смена одного якоря страницу не перезагружает: калькулятор читает адрес при загрузке.
    await page.goto("about:blank");
    await page.goto(base + "economics.html#" + hash, { waitUntil: "load" });
    const shown = await page.locator("#outs li b").allInnerTexts();
    const want = reference(p);
    const fte = Number(shown[1].replace(/\D/g, ""));
    if (shown[0] !== months(want.payback) || fte !== Math.round(want.fte)) {
      problems.push(`калькулятор: ${hash} — показано ${shown[0]}, ${fte}; эталон ${months(want.payback)}, ${Math.round(want.fte)}`);
      break;
    }
  }
  for (const bad of ["s=9&event_share=NaN", "minutes=<script>alert(1)</script>", "salary_ops=-5e9&realization=Infinity", "s=1.5&vtb_share=1e400"]) {
    await page.goto(base + "economics.html#" + encodeURIComponent(bad).replace(/%3D/g, "=").replace(/%26/g, "&"));
    await page.reload();
    const text = await page.locator("#calc").innerText();
    expect(!/NaN|undefined|Infinity/.test(text), `калькулятор: адрес «${bad}» дал NaN или Infinity`);
  }

  // Слайды: клавиатура, адрес, полный экран.
  await page.goto(base + "pitch.html");
  const total = await page.evaluate(() => JSON.parse(document.getElementById("page-data").textContent).slides.length);
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  expect((await page.locator("#cnt").innerText()) === `3 / ${total}`, "слайды: стрелка вправо не листает");
  await page.keyboard.press("End");
  expect((await page.locator("#cnt").innerText()) === `${total} / ${total}`, "слайды: End не открыл последний");
  await page.keyboard.press("Home");
  expect((await page.locator("#cnt").innerText()) === `1 / ${total}`, "слайды: Home не открыл первый");
  await page.goto(base + "pitch.html#7");
  expect((await page.locator("#cnt").innerText()) === `7 / ${total}`, "слайды: адрес #7 не открыл слайд 7");
  await page.evaluate(() => { window.__fs = 0; HTMLElement.prototype.requestFullscreen = function () { window.__fs++; return Promise.resolve(); }; });
  await page.keyboard.press("Control+f");
  await page.keyboard.press("f");
  expect((await page.evaluate(() => window.__fs)) === 1, "слайды: F не включает весь экран или Ctrl+F перехвачен");

  // Остальное: сценарий захвата, закон, пилот, критерии.
  await page.goto(base + "showcase.html");
  await page.locator("#reset").click();
  expect((await page.locator("#pos").innerText()) === "Шаг 1 из 7", "захват: «Сначала» не вернул к шагу 1");
  await page.goto(base + "law.html#on=2028-03-02");
  expect(/до пересверки/.test(await page.locator("#law-sum").innerText()), "закон: на 02.03.2028 нет правил на разбор");
  await page.goto(base + "pilot.html#G2");
  expect(await page.locator("#gate-G2:not([hidden])").count() === 1, "пилот: адрес #G2 не открыл ворота G2");
  expect((await page.locator("#plan li").count()) === 4, "пилот: календарь не из четырёх строк");
  await page.goto(base + "criteria.html");
  await page.locator("#q").fill("защит");
  expect(/найдено/.test(await page.locator("#found").innerText()), "критерии: поиск ничего не нашёл");

  if (errors.length) problems.push("сценарии: " + errors.slice(0, 3).join("; "));
  await page.close();

  // Своя страница 404 со ссылками на материалы: ответ 404 здесь ожидаем, поэтому страница без слежения за ошибками.
  const lost = await context.newPage();
  const missing = await lost.goto(base + "nichego-net/");
  expect(missing.status() === 404 && /Такой страницы здесь нет/.test(await lost.locator("h1").innerText()), "нет своей страницы 404");
  await lost.close();
}

async function axe(context, base, problems) {
  let axePath;
  try {
    axePath = require.resolve("axe-core/axe.min.js");
  } catch {
    console.log("axe-core не установлен: проверка WCAG пропущена (npm i axe-core рядом со скриптом)");
    return;
  }
  const page = await context.newPage();
  for (const name of PAGES) {
    await page.goto(base + name + ".html");
    await page.addScriptTag({ path: axePath });
    const found = await page.evaluate(async () => {
      const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] } });
      return r.violations.map((v) => `${v.id} ×${v.nodes.length}: ${v.nodes.slice(0, 2).map((n) => n.target.join(" ")).join(" | ")}`);
    });
    for (const v of found) problems.push(`axe ${name}: ${v}`);
  }
  await page.close();
}

(async () => {
  const site = path.resolve(process.argv[2] || "_site");
  const shots = process.argv[3] ? path.resolve(process.argv[3]) : null;
  if (shots) fs.mkdirSync(shots, { recursive: true });
  const reports = fs.readdirSync(path.join(site, "reports")).filter((f) => f.endsWith(".html")).sort();
  const server = await serve(site);
  const base = `http://127.0.0.1:${server.address().port}/Mandat/`;
  const browser = await chromium.launch();
  const problems = [];
  let pages = 0;
  for (const [name, width, height, scale, mobile] of DEVICES) {
    for (const scheme of ["light", "dark"]) {
      const context = await browser.newContext({
        viewport: { width, height }, deviceScaleFactor: scale, isMobile: mobile && width < 1024, hasTouch: mobile,
        colorScheme: scheme, reducedMotion: shots ? "reduce" : "no-preference", locale: "ru-RU", timezoneId: "Europe/Moscow",
      });
      for (const p of PAGES) {
        const page = await context.newPage();
        const label = `${name} ${width}×${height} ${scheme === "dark" ? "тёмная" : "светлая"} · ${p}`;
        await checkLayout(page, `${base}${p}.html`, problems, label);
        pages += 1;
        if (shots && p === "index") await page.screenshot({ path: path.join(shots, `${width}x${height}-${scheme}.png`), fullPage: true });
        await page.close();
      }
      if (scheme === "light") {
        for (const report of reports) {
          const rp = await context.newPage();
          await checkLayout(rp, `${base}reports/${report}`, problems, `${name} ${width}×${height} · ${report}`, { report: true });
          pages += 1;
          await rp.close();
        }
      }
      await context.close();
    }
  }
  const desk = await browser.newContext({ viewport: { width: 1366, height: 900 }, locale: "ru-RU", timezoneId: "Europe/Moscow" });
  await scenarios(desk, base, problems);
  await desk.close();
  // axe встраивается скриптом: политика CSP страницы его не пустит, поэтому для этой проверки она отключена.
  const audit = await browser.newContext({ viewport: { width: 1366, height: 900 }, bypassCSP: true, reducedMotion: "reduce" });
  await axe(audit, base, problems);
  await audit.close();
  await browser.close();
  server.close();
  console.log(`проверено страниц: ${pages} (${DEVICES.length} устройств × 2 темы × ${PAGES.length} страниц, отчёты — в светлой) и сценарии взаимодействия`);
  if (problems.length) {
    console.log(problems.map((p) => "— " + p).join("\n"));
    process.exit(1);
  }
  console.log("замечаний нет");
})();
