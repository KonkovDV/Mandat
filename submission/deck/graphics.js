// Графика презентации в фирменной палитре ВТБ: фоны, знак «Мандата», иллюстрация графа полномочий, значки.
// Всё рисуется кодом в SVG; исходники пишутся в assets/svg/, их можно перетащить в Figma как вектор.
const fs = require("fs");
const path = require("path");
const sharp = require("sharp");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const lu = require("react-icons/lu");

// Палитра ВТБ: тёмно-синий знака, кобальт и голубой полос; служебные цвета статусов.
const BRAND = {
  navy: "#0A2973",
  navyDeep: "#061A4A",
  cobalt: "#004CBC",
  sky: "#00AAFF",
  ink: "#0B1F44",
  ice: "#CFE3FF",
  ok: "#1A9F68",
  no: "#E5484D",
  rv: "#F5A300",
  nd: "#6B7A99",
};

const FONT = "Golos Text";
const SVG_DIR = path.join(__dirname, "assets", "svg");

function save(name, svg) {
  fs.mkdirSync(SVG_DIR, { recursive: true });
  fs.writeFileSync(path.join(SVG_DIR, `${name}.svg`), svg);
}

async function png(svg, w, h) {
  const buf = await sharp(Buffer.from(svg), { density: 144 }).resize(w, h).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}

// Детерминированный генератор: фон одинаков при каждой сборке.
function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

function lucideInner(name) {
  const markup = ReactDOMServer.renderToStaticMarkup(React.createElement(lu[`Lu${name}`], { size: 24 }));
  return markup.replace(/^<svg[^>]*>/, "").replace(/<\/svg>$/, "");
}

function lucide(name, color, stroke = 2) {
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 24 24" fill="none" ` +
    `stroke="${color}" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round">${lucideInner(name)}</svg>`
  );
}

async function icon(name, color, stroke = 2) {
  return png(lucide(name, color, stroke), 256, 256);
}

function network(rand, w, h, n, opacity) {
  const pts = Array.from({ length: n }, () => [rand() * w, rand() * h]);
  let out = "";
  for (let i = 0; i < pts.length; i++) {
    for (let j = i + 1; j < pts.length; j++) {
      const d = Math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]);
      if (d < w * 0.16) {
        out += `<line x1="${pts[i][0].toFixed(1)}" y1="${pts[i][1].toFixed(1)}" x2="${pts[j][0].toFixed(1)}" y2="${pts[j][1].toFixed(1)}" stroke="#FFFFFF" stroke-opacity="${(opacity * (1 - d / (w * 0.16))).toFixed(3)}" stroke-width="1.4"/>`;
      }
    }
  }
  for (const [x, y] of pts) out += `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" fill="#FFFFFF" fill-opacity="${(opacity * 2).toFixed(3)}"/>`;
  return out;
}

function bgDark() {
  const w = 2560, h = 1440;
  const rand = rng(20261027);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
  <defs>
    <linearGradient id="base" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0B2F8C"/><stop offset="0.5" stop-color="${BRAND.navy}"/><stop offset="1" stop-color="${BRAND.navyDeep}"/>
    </linearGradient>
    <radialGradient id="glowSky" cx="0.80" cy="0.30" r="0.55">
      <stop offset="0" stop-color="${BRAND.sky}" stop-opacity="0.42"/><stop offset="1" stop-color="${BRAND.sky}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="glowCobalt" cx="0.05" cy="1.05" r="0.65">
      <stop offset="0" stop-color="${BRAND.cobalt}" stop-opacity="0.65"/><stop offset="1" stop-color="${BRAND.cobalt}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="${w}" height="${h}" fill="url(#base)"/>
  <rect width="${w}" height="${h}" fill="url(#glowCobalt)"/>
  <rect width="${w}" height="${h}" fill="url(#glowSky)"/>
  <g>${network(rand, w, h, 70, 0.07)}</g>
</svg>`;
}

// Знак «Мандата»: изометрическая «М», правая диагональ — голубой акцент, как на логотипе проекта.
function mark(onDark = false) {
  const pillar = onDark ? "#FFFFFF" : BRAND.navy;
  const inner = onDark ? "#CFE3FF" : "#1B3F9A";
  return `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="360" viewBox="0 0 100 90">
  <defs><linearGradient id="acc" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="${BRAND.cobalt}"/><stop offset="1" stop-color="${BRAND.sky}"/></linearGradient></defs>
  <polygon points="6,20 26,8 26,82 6,70" fill="${pillar}"/>
  <polygon points="26,8 50,34 50,56 26,30" fill="${inner}"/>
  <polygon points="50,34 74,8 74,30 50,56" fill="url(#acc)"/>
  <polygon points="74,8 94,20 94,70 74,82" fill="${pillar}"/>
</svg>`;
}

// Иллюстрация: шесть источников сходятся в один вердикт на дату операции.
function heroGraph() {
  const w = 1400, h = 1240, cx = 700, cy = 600, R = 430;
  const nodes = [
    { label: "ЕГРЮЛ на дату", icon: "Landmark", a: -90, st: BRAND.ok },
    { label: "МЧД в XML", icon: "FileText", a: -30, st: BRAND.ok },
    { label: "Реестр МЧД ФНС", icon: "Database", a: 30, st: BRAND.ok },
    { label: "Передоверие", icon: "GitBranch", a: 90, st: BRAND.no },
    { label: "Устав", icon: "ScrollText", a: 150, st: BRAND.rv },
    { label: "События", icon: "BellRing", a: 210, st: BRAND.sky },
  ];
  const pos = nodes.map((n) => {
    const r = (n.a * Math.PI) / 180;
    return { ...n, x: cx + R * Math.cos(r), y: cy + R * Math.sin(r) };
  });
  let edges = "", sats = "";
  for (const n of pos) {
    const mx = cx + (n.x - cx) * 0.52, my = cy + (n.y - cy) * 0.52;
    edges += `<line x1="${n.x.toFixed(1)}" y1="${n.y.toFixed(1)}" x2="${cx}" y2="${cy}" stroke="${BRAND.sky}" stroke-opacity="0.55" stroke-width="3"/>`;
    edges += `<circle cx="${mx.toFixed(1)}" cy="${my.toFixed(1)}" r="13" fill="${n.st}" stroke="${BRAND.navyDeep}" stroke-width="5"/>`;
    sats += `<circle cx="${n.x.toFixed(1)}" cy="${n.y.toFixed(1)}" r="86" fill="#FFFFFF" fill-opacity="0.07" stroke="${BRAND.sky}" stroke-opacity="0.55" stroke-width="2.5"/>`;
    sats += `<circle cx="${n.x.toFixed(1)}" cy="${n.y.toFixed(1)}" r="64" fill="#0E3796"/>`;
    sats += `<svg x="${(n.x - 34).toFixed(1)}" y="${(n.y - 34).toFixed(1)}" width="68" height="68" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${lucideInner(n.icon)}</svg>`;
    const ly = n.a === 90 ? n.y + 130 : n.a === -90 ? n.y - 112 : n.y + 128;
    sats += `<text x="${n.x.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="middle" font-family="${FONT}" font-size="34" font-weight="500" fill="#FFFFFF" fill-opacity="0.92">${n.label}</text>`;
  }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
  <defs>
    <radialGradient id="halo" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="${BRAND.sky}" stop-opacity="0.55"/><stop offset="1" stop-color="${BRAND.sky}" stop-opacity="0"/></radialGradient>
    <linearGradient id="core" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="${BRAND.cobalt}"/><stop offset="1" stop-color="${BRAND.sky}"/></linearGradient>
  </defs>
  <circle cx="${cx}" cy="${cy}" r="${R}" fill="none" stroke="#FFFFFF" stroke-opacity="0.16" stroke-width="2" stroke-dasharray="4 14"/>
  <circle cx="${cx}" cy="${cy}" r="${R * 0.62}" fill="none" stroke="#FFFFFF" stroke-opacity="0.10" stroke-width="2"/>
  ${edges}
  <circle cx="${cx}" cy="${cy}" r="230" fill="url(#halo)"/>
  <circle cx="${cx}" cy="${cy}" r="150" fill="url(#core)"/>
  <circle cx="${cx}" cy="${cy}" r="150" fill="none" stroke="#FFFFFF" stroke-opacity="0.55" stroke-width="3"/>
  <svg x="${cx - 54}" y="${cy - 100}" width="108" height="108" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">${lucideInner("ShieldCheck")}</svg>
  <text x="${cx}" y="${cy + 46}" text-anchor="middle" font-family="${FONT}" font-size="32" font-weight="700" fill="#FFFFFF">вердикт</text>
  <text x="${cx}" y="${cy + 86}" text-anchor="middle" font-family="${FONT}" font-size="30" font-weight="500" fill="#FFFFFF" fill-opacity="0.9">на дату t</text>
  ${sats}
</svg>`;
  return { svg, w, h };
}

// Полоса-градиент для акцентной плашки (кобальт → голубой).
function accentBand() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="2400" height="200" viewBox="0 0 2400 200">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="${BRAND.navy}"/><stop offset="0.6" stop-color="${BRAND.cobalt}"/><stop offset="1" stop-color="#0A8FE0"/></linearGradient></defs>
  <rect width="2400" height="200" fill="url(#g)"/>
</svg>`;
}

// Окно терминала с настоящим выводом `./run-demo.sh` и команд демо; строки размечены ролью.
const TERM_COLORS = { prompt: "#7FD3FF", cmd: "#FFFFFF", out: "#C9D6EE", ok: "#5FD39A", warn: "#FFC35A", err: "#FF8A8E", dim: "#7F8FB0" };
function esc(t) {
  return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function terminal(lines, title) {
  const w = 900, lh = 33, top = 78, pad = 30;
  const h = top + lines.length * lh + 30;
  let body = "";
  lines.forEach(([role, text], i) => {
    const y = top + 24 + i * lh;
    if (role === "cmd") {
      body += `<text x="${pad}" y="${y}" font-family="DejaVu Sans Mono" font-size="22" xml:space="preserve"><tspan fill="${TERM_COLORS.prompt}">$ </tspan><tspan fill="${TERM_COLORS.cmd}" font-weight="bold">${esc(text)}</tspan></text>`;
    } else {
      body += `<text x="${pad}" y="${y}" font-family="DejaVu Sans Mono" font-size="22" fill="${TERM_COLORS[role]}" xml:space="preserve">${esc(text)}</text>`;
    }
  });
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
  <rect x="0" y="0" width="${w}" height="${h}" rx="22" fill="#081B4D"/>
  <path d="M22 0 H${w - 22} A22 22 0 0 1 ${w} 22 V52 H0 V22 A22 22 0 0 1 22 0 Z" fill="#0E2A6B"/>
  <circle cx="34" cy="26" r="8" fill="#FF6B6B"/><circle cx="62" cy="26" r="8" fill="#FFC35A"/><circle cx="90" cy="26" r="8" fill="#5FD39A"/>
  <text x="${w / 2}" y="34" text-anchor="middle" font-family="${FONT}" font-size="21" fill="#C9D6EE">${esc(title)}</text>
  ${body}
</svg>`;
  return { svg, w, h };
}

async function buildGraphics(opts = {}) {
  const out = {};
  const bg = bgDark();
  save("background-dark", bg);
  out.bgDark = "image/jpeg;base64," + (await darkBackground()).toString("base64");

  const hero = heroGraph();
  save("authority-graph", hero.svg);
  out.hero = await png(hero.svg, hero.w, hero.h);
  out.heroRatio = hero.h / hero.w;

  save("mandat-mark", mark(false));
  save("mandat-mark-on-dark", mark(true));
  out.mark = await png(mark(false), 400, 360);
  out.markDark = await png(mark(true), 400, 360);

  // Прозрачная заглушка для зон-ссылок на слайдах.
  out.clear = await sharp({ create: { width: 4, height: 4, channels: 4, background: { r: 255, g: 255, b: 255, alpha: 0 } } }).png().toBuffer().then((b) => "image/png;base64," + b.toString("base64"));

  const band = accentBand();
  save("accent-band", band);
  out.band = await png(band, 2400, 200);

  if (opts.terminal) {
    const term = terminal(opts.terminal.lines, opts.terminal.title);
    save("demo-terminal", term.svg);
    out.terminal = await png(term.svg, term.w, term.h);
    out.terminalRatio = term.h / term.w;
  }
  if (opts.qrSvg) {
    const qr = fs.readFileSync(opts.qrSvg, "utf8");
    save("qr-github", qr);
    out.qr = await png(qr, 600, 600);
  }
  if (opts.qrSiteSvg) {
    const qr = fs.readFileSync(opts.qrSiteSvg, "utf8");
    save("qr-site", qr);
    out.qrSite = await png(qr, 600, 600);
  }

  // Значки Lucide в два цвета: кобальт на светлой плитке и белый на тёмной или цветной.
  const names = [
    "Landmark", "Building2", "FileText", "FileCheck", "FileX", "FileSearch", "FileQuestion", "Scale", "ShieldCheck",
    "ShieldAlert", "UserCheck", "UserSearch", "Users", "Search", "Clock", "Bell", "BellRing", "Link", "Unlink", "Server",
    "Database", "Cpu", "Bot", "Filter", "Gavel", "Lock", "KeyRound", "Eye", "Fingerprint", "Network", "GitBranch",
    "Workflow", "ChartColumn", "Coins", "Route", "BadgeCheck", "Flag", "TriangleAlert", "Check", "X", "ArrowRight",
    "Globe", "BookOpen", "ListChecks", "Timer", "Handshake", "Target", "Stamp", "CalendarClock", "History", "Radar",
    "Webhook", "ScrollText", "Briefcase", "Mail", "Phone", "Github", "Quote", "Layers", "Siren", "Ban", "SearchCheck",
    "ClipboardCheck", "Banknote", "IdCard", "Activity", "GitCompare", "Award", "Puzzle", "Plug", "Container", "Gauge",
    "Microscope", "UserRound", "CircleHelp", "Waypoints", "ScanSearch", "PenLine", "Signature", "Rocket",
  ];
  out.icon = {};
  out.iconLight = {};
  out.iconSky = {};
  for (const n of names) {
    out.icon[n] = await icon(n, BRAND.cobalt);
    out.iconLight[n] = await icon(n, "#FFFFFF");
    out.iconSky[n] = await icon(n, "#7FD3FF");
    if (["ShieldCheck", "Landmark", "FileText", "Database", "GitBranch", "ScrollText", "BellRing", "Scale", "UserSearch"].includes(n)) {
      save(`icon-${n}`, lucide(n, BRAND.cobalt));
    }
  }
  return out;
}

// ---------- Фото и 3D-значки (исходники — assets/visuals, их пишет import_visuals.py) ----------
const SLIDE_W = 12192000 / 914400; // LAYOUT_WIDE: 13,333 дюйма
const BG_W = 2560, BG_H = 1440, BG_PX = BG_W / SLIDE_W; // фон тёмных слайдов: 192 точки на дюйм
const jpeg = (buf) => "image/jpeg;base64," + buf.toString("base64");

// Фон тёмных слайдов (обложка, раздел, финал). Если есть visuals/bg-lux.jpg из Figma — атласные волны с золотыми
// нитями; левая половина притемнена под белый текст. Без файла — сетка, нарисованная кодом (bgDark).
const LUX = path.join(__dirname, "assets", "visuals", "bg-lux.jpg");
let BG = null;
async function darkBackground() {
  if (BG) return BG;
  if (!fs.existsSync(LUX)) {
    BG = await sharp(Buffer.from(bgDark()), { density: 144 }).resize(BG_W, BG_H).jpeg({ quality: 92 }).toBuffer();
    return BG;
  }
  const veil = `<svg xmlns="http://www.w3.org/2000/svg" width="${BG_W}" height="${BG_H}"><defs>
    <linearGradient id="l" x1="0" x2="1"><stop offset="0" stop-color="${BRAND.navyDeep}" stop-opacity="0.62"/><stop offset="0.58" stop-color="${BRAND.navyDeep}" stop-opacity="0"/></linearGradient>
    <linearGradient id="b" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="${BRAND.navyDeep}" stop-opacity="0.5"/><stop offset="0.35" stop-color="${BRAND.navyDeep}" stop-opacity="0"/></linearGradient></defs>
    <rect width="${BG_W}" height="${BG_H}" fill="url(#l)"/><rect width="${BG_W}" height="${BG_H}" fill="url(#b)"/></svg>`;
  BG = await sharp(LUX).resize(BG_W, BG_H, { fit: "cover" }).composite([{ input: Buffer.from(veil) }]).jpeg({ quality: 90, chromaSubsampling: "4:4:4" }).toBuffer();
  return BG;
}

// Кадр на тёмном слайде: занимает полосу от xIn до правого края и слева уходит в фон.
// Под кадр подкладывается тот же кусок фона, что у макета, поэтому шва нет и прозрачность не нужна.
async function photoPanel(file, xIn, opts = {}) {
  const left = Math.round(xIn * BG_PX), width = BG_W - left;
  const bg = await sharp(await darkBackground()).extract({ left, top: 0, width, height: BG_H }).png().toBuffer();
  const photo = await sharp(file).resize(width, BG_H, { fit: "cover", position: opts.position ?? "top" }).png().toBuffer();
  const stop = (opts.fade ?? 0.28).toFixed(3);
  const mask = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${BG_H}"><defs><linearGradient id="f" x1="0" x2="1">
    <stop offset="0" stop-color="#FFF" stop-opacity="0"/><stop offset="${stop}" stop-color="#FFF" stop-opacity="1"/></linearGradient></defs>
    <rect width="${width}" height="${BG_H}" fill="url(#f)"/></svg>`;
  const faded = await sharp(photo).ensureAlpha().composite([{ input: Buffer.from(mask), blend: "dest-in" }]).png().toBuffer();
  const out = await sharp(bg).composite([{ input: faded }]).jpeg({ quality: 92, chromaSubsampling: "4:4:4" }).toBuffer();
  return { data: jpeg(out), x: xIn, w: SLIDE_W - xIn, h: 7.5 };
}

// Фото со скруглёнными углами на светлом слайде: углы закрашены цветом фона, файл остаётся JPEG.
async function roundedPhoto(file, wIn, hIn, rIn, bgHex, opts = {}) {
  const k = 192, w = Math.round(wIn * k), h = Math.round(hIn * k), r = Math.round(rIn * k);
  const photo = await sharp(file).resize(w, h, { fit: "cover", position: opts.position ?? "centre" }).png().toBuffer();
  const mask = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><rect width="${w}" height="${h}" rx="${r}" fill="#FFF"/></svg>`;
  const clipped = await sharp(photo).ensureAlpha().composite([{ input: Buffer.from(mask), blend: "dest-in" }]).png().toBuffer();
  const out = await sharp({ create: { width: w, height: h, channels: 3, background: `#${bgHex}` } })
    .composite([{ input: clipped }]).jpeg({ quality: 90, chromaSubsampling: "4:4:4" }).toBuffer();
  return jpeg(out);
}

// Портрет для круга: квадрат по лицу из исходного кадра (box — левый верхний угол и сторона в точках файла).
async function squarePhoto(file, box, size = 900) {
  const out = await sharp(file).extract({ left: box.x, top: box.y, width: box.side, height: box.side })
    .resize(size, size, { kernel: "lanczos3" }).sharpen({ sigma: 0.6 }).jpeg({ quality: 92, chromaSubsampling: "4:4:4" }).toBuffer();
  return jpeg(out);
}

// 3D-значок с прозрачным фоном, уменьшенный под слайд.
async function icon3d(file, size = 256) {
  const out = await sharp(file).resize(size, size).png({ compressionLevel: 9 }).toBuffer();
  return "image/png;base64," + out.toString("base64");
}

module.exports = { BRAND, FONT, buildGraphics, photoPanel, roundedPhoto, squarePhoto, icon3d };
