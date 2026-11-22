// «Мандат» на четырёх экранах: живые страницы собранного сайта в рамках монитора, ноутбука, планшета и телефона.
//
//   python scripts/build_site.py --out _site      # из корня репозитория
//   node submission/deck/device_art.js _site       # Playwright с Chromium; sharp — из submission/deck
//
// Снимки делаются с настоящих страниц при каждой сборке картинки: что на экранах — то и есть на сайте этого коммита.
// Рамки устройств нарисованы здесь же векторами, без чужих макетов и логотипов производителей.
// Пишет site/img/devices-1200 и devices-2400 в WebP и AVIF — прозрачный фон, годится для светлой и тёмной темы.
"use strict";
const fs = require("fs");
const http = require("http");
const path = require("path");
const sharp = require("sharp");
const { chromium } = require("playwright");

const ROOT = path.join(__dirname, "..", "..");
const SITE = path.resolve(process.argv[2] || path.join(ROOT, "_site"));
const OUT = path.join(ROOT, "site", "img");
const TYPES = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg", ".woff2": "font/woff2", ".json": "application/json", ".webmanifest": "application/manifest+json" };

// Холст и устройства, в пикселях холста. Экран — место под снимок; viewport — окно браузера в CSS-пикселях.
const W = 2400, H = 1400;
const DEV = {
  display: { screen: { x: 560, y: 40, w: 1280, h: 720 }, viewport: { width: 1600, height: 900 }, page: "index.html", theme: "light" },
  laptop: { screen: { x: 150, y: 690, w: 860, h: 558 }, viewport: { width: 1512, height: 982 }, page: "cases.html#14_old_director_charter_cleared", theme: "dark", scrollTo: "#explorer, .explorer" },
  tablet: { screen: { x: 1846, y: 600, w: 452, h: 648 }, viewport: { width: 834, height: 1194 }, page: "verify.html", theme: "light", click: "#check-sig", scrollTo: ".sealed" },
  phone: { screen: { x: 1534, y: 806, w: 246, h: 534 }, viewport: { width: 393, height: 852 }, page: "index.html", theme: "dark", scrollTo: "#scrub" },
};

function serve(root) {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      let p = decodeURIComponent(req.url.split("?")[0].split("#")[0]);
      if (p.endsWith("/")) p += "index.html";
      const file = path.join(root, p);
      if (!file.startsWith(root)) { res.writeHead(403); res.end(); return; }
      fs.readFile(file, (err, body) => {
        if (err) { res.writeHead(404); res.end(); return; }
        res.writeHead(200, { "content-type": TYPES[path.extname(file)] || "application/octet-stream" });
        res.end(body);
      });
    });
    srv.listen(0, "127.0.0.1", () => resolve(srv));
  });
}

async function shoot(browser, base, d) {
  const ctx = await browser.newContext({ viewport: d.viewport, deviceScaleFactor: 2, colorScheme: d.theme, reducedMotion: "reduce", locale: "ru-RU" });
  await ctx.addInitScript((theme) => { try { localStorage.setItem("mandat-theme", theme); } catch {} }, d.theme);
  const page = await ctx.newPage();
  await page.goto(base + d.page, { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  if (d.click) { await page.click(d.click); await page.waitForTimeout(400); }
  if (d.scrollTo) {
    await page.evaluate((sel) => {
      const el = document.querySelector(sel);
      if (!el) return;
      const head = document.querySelector("header")?.getBoundingClientRect().height || 0;
      window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - head - 12);
    }, d.scrollTo);
  }
  await page.waitForTimeout(500);
  const png = await page.screenshot({ type: "png" });
  await ctx.close();
  return png;
}

const img = (buf) => `data:image/png;base64,${buf.toString("base64")}`;

// Рамки: тёмный экранный бортик, алюминиевая или титановая кромка, мягкая тень под устройством.
function display(s, shot) {
  const b = 16, r = 22, x = s.x - b, y = s.y - b, w = s.w + 2 * b, h = s.h + 2 * b;
  const cx = s.x + s.w / 2, neckTop = y + h - 6, neckW = 250, baseY = neckTop + 196;
  return `
  <ellipse cx="${cx}" cy="${baseY + 22}" rx="330" ry="16" fill="#0b1f44" opacity=".16" filter="url(#soft)"/>
  <path d="M${cx - neckW / 2} ${neckTop} h${neckW} l14 ${baseY - neckTop} h${-neckW - 28} z" fill="url(#alu-neck)"/>
  <rect x="${cx - 215}" y="${baseY}" width="430" height="16" rx="6" fill="url(#alu-base)"/>
  <rect x="${x - 2}" y="${y - 2}" width="${w + 4}" height="${h + 4}" rx="${r + 2}" fill="url(#alu-edge)"/>
  <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="#0c0d10"/>
  <clipPath id="c-display"><rect x="${s.x}" y="${s.y}" width="${s.w}" height="${s.h}" rx="4"/></clipPath>
  <image href="${img(shot)}" x="${s.x}" y="${s.y}" width="${s.w}" height="${s.h}" preserveAspectRatio="xMidYMin slice" clip-path="url(#c-display)"/>`;
}

function laptop(s, shot) {
  const b = 14, top = 18, x = s.x - b, y = s.y - top, w = s.w + 2 * b, h = s.h + top + b;
  const baseW = w * 1.16, bx = x + w / 2 - baseW / 2, by = y + h;
  return `
  <ellipse cx="${x + w / 2}" cy="${by + 30}" rx="${baseW / 2}" ry="18" fill="#0b1f44" opacity=".22" filter="url(#soft)"/>
  <path d="M${x + 24} ${y} h${w - 48} a24 24 0 0 1 24 24 v${h - 30} a6 6 0 0 1 -6 6 h${-w + 12} a6 6 0 0 1 -6 -6 v${-h + 30} a24 24 0 0 1 24 -24 z" fill="url(#alu-edge)"/>
  <path d="M${x + 25.5} ${y + 1.5} h${w - 51} a22.5 22.5 0 0 1 22.5 22.5 v${h - 31} h${-w + 3} v${-h + 31} a22.5 22.5 0 0 1 22.5 -22.5 z" fill="#0c0d10"/>
  <clipPath id="c-laptop"><rect x="${s.x}" y="${s.y}" width="${s.w}" height="${s.h}" rx="8"/></clipPath>
  <image href="${img(shot)}" x="${s.x}" y="${s.y}" width="${s.w}" height="${s.h}" preserveAspectRatio="xMidYMin slice" clip-path="url(#c-laptop)"/>
  <rect x="${s.x + s.w / 2 - 52}" y="${s.y - 1}" width="104" height="17" rx="8" fill="#0c0d10"/>
  <path d="M${bx} ${by} h${baseW} v8 q0 14 -26 16 h${-baseW + 52} q-26 -2 -26 -16 z" fill="url(#alu-base)"/>
  <rect x="${x + w / 2 - 80}" y="${by}" width="160" height="7" rx="3.5" fill="#9fa3a9" opacity=".55"/>`;
}

function tablet(s, shot) {
  const b = 18, r = 38, x = s.x - b, y = s.y - b, w = s.w + 2 * b, h = s.h + 2 * b;
  return `
  <ellipse cx="${x + w / 2}" cy="${y + h + 12}" rx="${w / 2 + 10}" ry="14" fill="#0b1f44" opacity=".2" filter="url(#soft)"/>
  <rect x="${x - 3}" y="${y - 3}" width="${w + 6}" height="${h + 6}" rx="${r + 3}" fill="url(#alu-edge)"/>
  <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="#0c0d10"/>
  <clipPath id="c-tablet"><rect x="${s.x}" y="${s.y}" width="${s.w}" height="${s.h}" rx="20"/></clipPath>
  <image href="${img(shot)}" x="${s.x}" y="${s.y}" width="${s.w}" height="${s.h}" preserveAspectRatio="xMidYMin slice" clip-path="url(#c-tablet)"/>`;
}

function phone(s, shot) {
  const b = 10, f = 5, r = 46, x = s.x - b - f, y = s.y - b - f, w = s.w + 2 * (b + f), h = s.h + 2 * (b + f);
  return `
  <ellipse cx="${x + w / 2}" cy="${y + h + 8}" rx="${w / 2 + 6}" ry="11" fill="#0b1f44" opacity=".26" filter="url(#soft)"/>
  <rect x="${x - 3}" y="${y + 150}" width="4" height="56" rx="2" fill="#7d8087"/>
  <rect x="${x + w - 1}" y="${y + 170}" width="4" height="84" rx="2" fill="#7d8087"/>
  <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="url(#ti-edge)"/>
  <rect x="${x + f}" y="${y + f}" width="${w - 2 * f}" height="${h - 2 * f}" rx="${r - f}" fill="#0c0d10"/>
  <clipPath id="c-phone"><rect x="${s.x}" y="${s.y}" width="${s.w}" height="${s.h}" rx="${r - f - b + 2}"/></clipPath>
  <image href="${img(shot)}" x="${s.x}" y="${s.y}" width="${s.w}" height="${s.h}" preserveAspectRatio="xMidYMin slice" clip-path="url(#c-phone)"/>
  <rect x="${s.x + s.w / 2 - 40}" y="${s.y + 10}" width="80" height="24" rx="12" fill="#0c0d10"/>`;
}

(async () => {
  if (!fs.existsSync(path.join(SITE, "index.html"))) throw new Error(`нет собранного сайта в ${SITE}: python scripts/build_site.py --out _site`);
  const srv = await serve(SITE);
  const base = `http://127.0.0.1:${srv.address().port}/`;
  const browser = await chromium.launch();
  const shots = {};
  try {
    for (const [name, d] of Object.entries(DEV)) {
      const raw = await shoot(browser, base, d);
      // Снимок заранее ужимается до места на холсте в 2×: SVG-движок не пересчитывает огромные картинки.
      shots[name] = await sharp(raw).resize(d.screen.w * 2, d.screen.h * 2, { fit: "cover", position: "top" }).png().toBuffer();
    }
  } finally {
    await browser.close();
    srv.close();
  }
  const defs = `
  <defs>
    <filter id="soft" x="-30%" y="-200%" width="160%" height="500%"><feGaussianBlur stdDeviation="14"/></filter>
    <linearGradient id="alu-edge" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#dfe1e5"/><stop offset=".5" stop-color="#b9bcc2"/><stop offset="1" stop-color="#8f939a"/></linearGradient>
    <linearGradient id="ti-edge" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#a7a9ae"/><stop offset=".5" stop-color="#6d7077"/><stop offset="1" stop-color="#9a9da3"/></linearGradient>
    <linearGradient id="alu-neck" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#a9adb3"/><stop offset=".5" stop-color="#e6e8eb"/><stop offset="1" stop-color="#a3a7ad"/></linearGradient>
    <linearGradient id="alu-base" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#eceef1"/><stop offset=".55" stop-color="#c3c6cb"/><stop offset="1" stop-color="#8e9298"/></linearGradient>
  </defs>`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W * 2}" height="${H * 2}" viewBox="0 0 ${W} ${H}">${defs}
  ${display(DEV.display.screen, shots.display)}
  ${tablet(DEV.tablet.screen, shots.tablet)}
  ${laptop(DEV.laptop.screen, shots.laptop)}
  ${phone(DEV.phone.screen, shots.phone)}
  </svg>`;
  const master = await sharp(Buffer.from(svg)).png().toBuffer();
  const trimmed = await sharp(master).trim({ threshold: 1 }).toBuffer({ resolveWithObject: true });
  for (const width of [1200, 2400]) {
    const sized = await sharp(trimmed.data).resize({ width }).png().toBuffer();
    // AVIF — для браузеров, которые его понимают (почти вдвое легче), WebP — для остальных.
    for (const [ext, encode] of [["webp", (s) => s.webp({ quality: 84, alphaQuality: 90, effort: 6 })], ["avif", (s) => s.avif({ quality: 58, effort: 7 })]]) {
      const file = path.join(OUT, `devices-${width}.${ext}`);
      await encode(sharp(sized)).toFile(file);
      const meta = await sharp(file).metadata();
      console.log(`${path.relative(ROOT, file)}: ${meta.width}×${meta.height}, ${Math.round(fs.statSync(file).size / 1024)} КБ`);
    }
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
