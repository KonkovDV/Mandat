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

async function buildGraphics() {
  const out = {};
  const bg = bgDark();
  save("background-dark", bg);
  out.bgDark = await png(bg, 2560, 1440);

  const hero = heroGraph();
  save("authority-graph", hero.svg);
  out.hero = await png(hero.svg, hero.w, hero.h);
  out.heroRatio = hero.h / hero.w;

  save("mandat-mark", mark(false));
  save("mandat-mark-on-dark", mark(true));
  out.mark = await png(mark(false), 400, 360);
  out.markDark = await png(mark(true), 400, 360);

  const band = accentBand();
  save("accent-band", band);
  out.band = await png(band, 2400, 200);

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

module.exports = { BRAND, FONT, buildGraphics };
