// Проверка страницы проекта и демо-отчётов на матрице устройств, как у тестировщика веб-студии.
//
//   python scripts/build_site.py --out _site
//   node scripts/check_site_devices.js _site [каталог-для-снимков]
//
// Нужен Node.js и Playwright с Chromium (как для видео-демо). Для каждого устройства в светлой и тёмной теме
// страница открывается как с телефона, планшета или компьютера: ширина, плотность пикселей, касания.
// Проверяется: страница не уезжает вбок, нет неудачных запросов и ошибок в консоли, шрифт Golos загружен,
// якоря меню ведут на существующие разделы, у картинок есть alt, кнопки и пункты меню удобны пальцу (не ниже
// 40 px), все 14 демо-отчётов открываются без горизонтальной прокрутки. Код выхода 1 — есть замечания.
// Движок — только Chromium: Safari и Firefox на этой машине не проверяются, их держат стили с запасом.
"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");
const { chromium } = require("playwright");

const TYPES = {
  ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
  ".png": "image/png", ".jpg": "image/jpeg", ".woff2": "font/woff2", ".pdf": "application/pdf",
  ".webmanifest": "application/manifest+json", ".txt": "text/plain; charset=utf-8",
};

// Страница проверяется так, как её отдаст GitHub Pages: по HTTP, с типами файлов, а не с диска.
function serve(root) {
  const server = http.createServer((req, res) => {
    const rel = decodeURIComponent(new URL(req.url, "http://localhost").pathname).replace(/\/$/, "/index.html");
    const file = path.join(root, rel);
    if (!file.startsWith(root) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404).end();
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

async function checkPage(page, url, problems, label, { report = false } = {}) {
  const failed = [];
  const errors = [];
  page.on("requestfailed", (r) => failed.push(r.url()));
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await page.goto(url, { waitUntil: "load" });
  await page.evaluate(() => document.fonts.ready);
  const r = await page.evaluate((isReport) => {
    const doc = document.documentElement;
    const out = { overflow: doc.scrollWidth - doc.clientWidth };
    if (isReport) return out;
    out.font = document.fonts.check('16px "Golos Text"');
    out.badAnchors = [...document.querySelectorAll('a[href^="#"]')]
      .map((a) => a.getAttribute("href").slice(1))
      .filter((id) => id && !document.getElementById(id));
    out.noAlt = [...document.querySelectorAll("img")].filter((i) => !i.hasAttribute("alt")).length;
    const small = [];
    for (const el of document.querySelectorAll(".btn, nav a")) {
      const box = el.getBoundingClientRect();
      if (box.width > 0 && box.height > 0 && box.height < 40 && getComputedStyle(el).display !== "none") small.push(el.textContent.trim());
    }
    out.small = small;
    return out;
  }, report);
  if (r.overflow > 0) problems.push(`${label}: страница шире экрана на ${r.overflow} px`);
  if (failed.length) problems.push(`${label}: неудачные запросы ${failed.slice(0, 3).join(", ")}`);
  if (errors.length) problems.push(`${label}: ошибки ${errors.slice(0, 2).join("; ")}`);
  if (!report) {
    if (!r.font) problems.push(`${label}: шрифт Golos Text не загружен`);
    if (r.badAnchors.length) problems.push(`${label}: якоря без разделов ${r.badAnchors.join(", ")}`);
    if (r.noAlt) problems.push(`${label}: картинок без alt — ${r.noAlt}`);
    if (r.small.length) problems.push(`${label}: мелкие цели касания: ${[...new Set(r.small)].join(", ")}`);
  }
}

(async () => {
  const site = path.resolve(process.argv[2] || "_site");
  const shots = process.argv[3] ? path.resolve(process.argv[3]) : null;
  if (shots) fs.mkdirSync(shots, { recursive: true });
  const reports = fs.readdirSync(path.join(site, "reports")).filter((f) => f.endsWith(".html")).sort();
  const server = await serve(site);
  const base = `http://127.0.0.1:${server.address().port}/`;
  const browser = await chromium.launch();
  const problems = [];
  let pages = 0;
  for (const [name, width, height, scale, mobile] of DEVICES) {
    for (const scheme of ["light", "dark"]) {
      const context = await browser.newContext({
        viewport: { width, height },
        deviceScaleFactor: scale,
        isMobile: mobile && width < 1024,
        hasTouch: mobile,
        colorScheme: scheme,
        reducedMotion: shots ? "reduce" : "no-preference",
      });
      const page = await context.newPage();
      const label = `${name} ${width}×${height} ${scheme === "dark" ? "тёмная" : "светлая"}`;
      await checkPage(page, base, problems, label);
      pages += 1;
      if (shots) await page.screenshot({ path: path.join(shots, `${width}x${height}-${scheme}.png`), fullPage: true });
      if (scheme === "light") {
        for (const report of reports) {
          const rp = await context.newPage();
          await checkPage(rp, `${base}reports/${report}`, problems, `${label} · ${report}`, { report: true });
          pages += 1;
          await rp.close();
        }
      }
      await context.close();
    }
  }
  await browser.close();
  server.close();
  console.log(`проверено страниц: ${pages} (${DEVICES.length} устройств × 2 темы, отчёты — в светлой)`);
  if (problems.length) {
    console.log(problems.map((p) => "— " + p).join("\n"));
    process.exit(1);
  }
  console.log("замечаний нет");
})();
