// Презентация «Мандата» как код: все числа о продукте берутся из evidence.json.
//
//   node build_deck.js ../../evidence.json ../../Mandat_VTB.pptx
//   python finish_deck.py ../../Mandat_VTB.pptx      # цвета темы, alt-текст, метаданные
//
// Числа рынка и эффекта — из открытых источников и допущений команды; каждое из них
// записано в CLAIMS_REGISTRY.md вместе с источником или командой воспроизведения.
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");
const sharp = require("sharp");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const fa = require("react-icons/fa");

const EVIDENCE = process.argv[2] || path.join(__dirname, "..", "..", "evidence.json");
const OUT = process.argv[3] || path.join(__dirname, "..", "..", "Mandat_VTB.pptx");
const ASSETS = path.join(__dirname, "assets");

// ---------- Числа из evidence.json ----------
const ev = JSON.parse(fs.readFileSync(EVIDENCE, "utf8"));
const comma = (x, digits = 1) => x.toFixed(digits).replace(".", ",");
const pct = (x, digits = 1) => `${comma(100 * x, digits)}%`;
const ruDate = (iso) => iso.slice(0, 10).split("-").reverse().join(".");
const N = (() => {
  const sc = ev.synthetic_corpus;
  const expected = { confirmed: 0, refuted: 0, needs_review: 0, insufficient_data: 0 };
  for (const [pair, count] of Object.entries(sc.confusion)) expected[pair.split("->")[0]] += count;
  const perKind = sc.cases / sc.defect_types;
  const kinds = (k) => (Number.isInteger(expected[k] / perKind) ? expected[k] / perKind : null);
  return {
    version: ev.engine.split("/")[1],
    asOf: ruDate(ev.generated_at),
    rules: ev.rule_pack.rules,
    demo: Object.keys(ev.demo_cases.results).length,
    tests: ev.tests.passed,
    cases: sc.cases,
    kinds: sc.defect_types,
    agree: Math.round(sc.cases * sc.agreement),
    expected,
    kindsBy: { ok: kinds("confirmed"), no: kinds("refuted"), rv: kinds("needs_review"), nd: kinds("insufficient_data") },
    negatives: sc.cases - expected.confirmed,
    falseConfirmed: sc.false_confirmed,
    fcUpper: pct(sc.false_confirmed_rate_upper95),
    recallLow: pct(sc.refute_recall_wilson95[0]),
    coverage: pct(sc.coverage),
    toHuman: `${Math.round(100 * sc.abstention_rate)}%`,
    strictCoverage: pct(ev.synthetic_corpus_strict.coverage),
    nRecall: sc.n_for_recall_0_9_pm_0_05,
    chains: ev.subdelegation_property.chains,
    medianMs: `≈${Math.max(1, Math.round(ev.latency.median_ms))} мс`,
  };
})();
const plural = (n, one, few, many) => {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
};
const RULES = `${N.rules} ${plural(N.rules, "правило", "правила", "правил")}`;
const TESTS = `${N.tests} ${plural(N.tests, "автотест", "автотеста", "автотестов")}`;

// ---------- Эффект: формулы слайда 9 и приложения (параметры — src/mandat/data/effect_scenarios.json, их же читает scripts/effect_model.py --scenarios) ----------
const EFFECT_RAW = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "src", "mandat", "data", "effect_scenarios.json"), "utf8"));
const EFFECT = { ...EFFECT_RAW, lossPerIncidentMln: EFFECT_RAW.lossPerQuarterMln / EFFECT_RAW.incidentsPerQuarter };
const FTE = [0, 1, 2].map((i) => Math.round((EFFECT.clients * EFFECT.eventShare[i] * EFFECT.minutesSaved[i]) / 60 / EFFECT.hoursPerFte));
const LOSS = [0, 1, 2].map((i) => Math.round(EFFECT.incidentsPerQuarter * 4 * EFFECT.vtbShare[i] * EFFECT.authorityShare[i] * EFFECT.lossPerIncidentMln));

const THEME = JSON.parse(fs.readFileSync(path.join(__dirname, "theme.json"), "utf8"));
const HEX = THEME.colors;

async function svgPng(svg, w, h) {
  const buf = await sharp(Buffer.from(svg)).resize(w, h).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}
async function icon(name, color) {
  const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(fa[name], { size: 256, color }));
  return svgPng(svg, 256, 256);
}

(async () => {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
  pres.author = "Команда «Мандат»";
  pres.company = "Команда «Мандат»";
  pres.title = "Мандат — единое окно полномочий для бизнес-клиентов банка";
  pres.subject = "Академия Финтеха 2026, трек ВТБ «Финтех без границ»";
  pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
  const C = pres.SchemeColor;

  const L = 0.6, W = 12.13, TOP = 1.45;

  const bgDark = await svgPng(`<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0B1622"/><stop offset="0.55" stop-color="#13263A"/><stop offset="1" stop-color="#0E1C2B"/></linearGradient></defs><rect width="1920" height="1080" fill="url(#g)"/></svg>`, 1920, 1080);
  const bgCover = await svgPng(`<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080"><defs><radialGradient id="r" cx="0.74" cy="0.45" r="0.75"><stop offset="0" stop-color="#1E3550"/><stop offset="0.45" stop-color="#12233A"/><stop offset="1" stop-color="#0A1420"/></radialGradient></defs><rect width="1920" height="1080" fill="url(#r)"/></svg>`, 1920, 1080);

  const ICON = {};
  const dark = "#0F1B2A";
  for (const n of ["FaBalanceScale", "FaUserSecret", "FaSearch", "FaClock", "FaUniversity", "FaExclamationTriangle", "FaCheck", "FaTimes", "FaUserTie", "FaQuestion", "FaFileAlt", "FaRobot", "FaFilter", "FaGavel", "FaUsers", "FaShieldAlt", "FaBell", "FaLink", "FaServer", "FaDatabase", "FaCogs", "FaClipboardCheck", "FaHandshake", "FaFlag", "FaLock", "FaChartLine", "FaCoins", "FaRoute", "FaCertificate", "FaEye"]) {
    ICON[n] = await icon(n, dark);
  }
  const ICON_LIGHT = {};
  for (const n of ["FaCheck", "FaTimes", "FaFlag", "FaArrowRight"]) ICON_LIGHT[n] = await icon(n, "#F3F6F9");

  // ---------- Layouts ----------
  pres.defineSlideMaster({ title: "COVER", background: { data: bgCover }, objects: [] });
  pres.defineSlideMaster({
    title: "CONTENT",
    background: { data: bgDark },
    margin: [0.5, 0.6, 0.6, 0.6],
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: L, y: 0.32, w: W, h: 0.95, fontSize: 28, bold: true, color: C.background1, align: "left", valign: "middle", margin: 0 }, text: "" } },
      { placeholder: { options: { name: "source", type: "body", x: L, y: 6.98, w: 11.4, h: 0.34, fontSize: 10, color: C.accent4, align: "left", valign: "middle", margin: 0 }, text: "" } },
    ],
    slideNumber: { x: 12.23, y: 6.98, w: 0.5, h: 0.34, fontSize: 10, color: C.accent4, align: "right" },
  });
  pres.defineSlideMaster({
    title: "DIVIDER",
    background: { data: bgCover },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: L, y: 2.5, w: 12.0, h: 1.0, fontSize: 40, bold: true, color: C.background1, align: "left", valign: "middle", margin: 0 }, text: "" } },
      { placeholder: { options: { name: "body", type: "body", x: L, y: 3.7, w: 10.5, h: 1.2, fontSize: 18, color: C.accent4, align: "left", valign: "top", margin: 0 }, text: "" } },
    ],
  });

  // ---------- helpers ----------
  const shadow = () => ({ type: "outer", color: "000000", opacity: 0.28, blur: 6, offset: 2, angle: 90 });
  function card(s, x, y, w, h, o = {}) {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y, w, h, rectRadius: 0.08,
      fill: { color: o.fill || C.accent6 }, line: { color: o.line || C.text2, width: o.lineW || 1 },
      shadow: o.noShadow ? undefined : shadow(), objectName: o.name || "card",
    });
  }
  function iconCircle(s, x, y, d, iconData, fill = C.accent1) {
    s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: fill }, line: { color: fill, width: 0 }, objectName: "icon-circle" });
    const p = d * 0.24;
    s.addImage({ data: iconData, x: x + p, y: y + p, w: d - 2 * p, h: d - 2 * p, objectName: "icon" });
  }
  function text(s, t, x, y, w, h, o = {}) {
    s.addText(t, { x, y, w, h, isTextBox: true, margin: o.margin ?? 0, fontSize: o.size || 15, color: o.color || C.background1, bold: o.bold || false, italic: o.italic || false, align: o.align || "left", valign: o.valign || "top", paraSpaceAfter: o.psa, lineSpacingMultiple: o.lsm, fit: undefined, objectName: o.name });
  }
  function bullets(s, items, x, y, w, h, o = {}) {
    const arr = items.map((t, i) => ({ text: t, options: { bullet: { indent: 14 }, breakLine: i < items.length - 1 } }));
    s.addText(arr, { x, y, w, h, isTextBox: true, margin: 0, fontSize: o.size || 15, color: o.color || C.background1, paraSpaceAfter: o.psa ?? 6, valign: "top" });
  }
  function pill(s, label, x, y, w, h, fill, color = C.text1, size = 13) {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: h / 2, fill: { color: fill }, line: { color: fill, width: 0 }, objectName: "pill" });
    s.addText(label, { x, y, w, h, isTextBox: true, margin: 0, fontSize: size, bold: true, color, align: "center", valign: "middle" });
  }
  function content(title, source, section) {
    const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: section });
    s.addText(title, { placeholder: "title" });
    if (source) s.addText(source, { placeholder: "source" });
    return s;
  }
  const V = {
    ok: { label: "ПОДТВЕРЖДЕНО", fill: C.accent3 },
    no: { label: "ОПРОВЕРГНУТО", fill: C.accent5 },
    rv: { label: "НА РАЗБОР", fill: C.accent1 },
    nd: { label: "НЕДОСТАТОЧНО ДАННЫХ", fill: C.accent2 },
  };

  const MAIN = "Питч · 5 минут";
  const APP = "Приложение для вопросов жюри";

  // ===== 1. Обложка =====
  pres.addSection({ title: MAIN });
  {
    const s = pres.addSlide({ masterName: "COVER", sectionTitle: MAIN });
    text(s, "Академия Финтеха 2026 · трек ВТБ «Финтех без границ» · «Онбординг и проверка полномочий»", L, 0.55, 8.4, 0.4, { size: 14, color: C.accent1, bold: true });
    text(s, "Мандат", L, 1.35, 7.5, 1.2, { size: 66, bold: true });
    text(s, "Единое окно полномочий для бизнес-клиентов банка", L, 2.55, 8.0, 0.55, { size: 24 });
    text(s, "Отвечает, вправе ли человек провести операцию от имени компании на дату, — за миллисекунды и с доказательством до поля документа", L, 3.3, 7.6, 1.0, { size: 18, color: C.accent4 });
    const vx = [L, L + 1.95, L + 3.9, L + 5.4];
    pill(s, "ПОДТВЕРЖДЕНО", vx[0], 4.55, 1.8, 0.42, C.accent3, C.text1, 12);
    pill(s, "ОПРОВЕРГНУТО", vx[1], 4.55, 1.8, 0.42, C.accent5, C.text1, 12);
    pill(s, "НА РАЗБОР", vx[2], 4.55, 1.35, 0.42, C.accent1, C.text1, 12);
    pill(s, "НЕДОСТАТОЧНО ДАННЫХ", vx[3], 4.55, 2.35, 0.42, C.accent2, C.text1, 12);
    text(s, "Коньков Дмитрий", L, 5.55, 6, 0.4, { size: 18, bold: true });
    text(s, "Капитан команды «Мандат»", L, 5.95, 6, 0.35, { size: 14, color: C.accent4 });
    s.addImage({ path: path.join(ASSETS, "graph.png"), x: 8.75, y: 1.45, w: 3.9, h: 3.9, objectName: "graph", altText: "Схема: граф полномочий «Мандата»" });
    s.addNotes("[0:00–0:15] Здравствуйте, мы — команда «Мандат». В задаче трека ВТБ «Финтех без границ» первым направлением для бизнеса названы онбординг и проверка полномочий. Мы сделали движок, который за миллисекунды отвечает, вправе ли человек провести операцию от имени компании на конкретную дату, и показывает доказательство до поля документа.");
  }

  // ===== 2. Проблема стоит денег =====
  {
    const s = content("Ошибка в полномочиях стоит дорого: ≈0,7 млн ₽ на одну операцию против компании",
      "Источники: Банк России, обзор операций без согласия клиентов, I кв. 2026 (по expert.ru, 14.05.2026; RTVI, 21.05.2026); fintechacademy.ru — задача трека ВТБ, 10.2026", MAIN);
    card(s, L, TOP, 3.7, 5.25);
    iconCircle(s, L + 0.3, TOP + 0.3, 0.7, ICON.FaUniversity);
    text(s, "Задача трека ВТБ", L + 1.15, TOP + 0.42, 2.4, 0.45, { size: 18, bold: true });
    text(s, "«Онбординг и проверка полномочий» — первое направление для бизнеса в задаче трека «Финтех без границ».", L + 0.3, TOP + 1.25, 3.15, 1.5, { size: 16 });
    text(s, "Ответ на вопрос «вправе ли этот человек подписать за компанию» складывается из трёх источников — МЧД, выписки ЕГРЮЛ и устава — и меняется со временем.", L + 0.3, TOP + 2.85, 3.15, 2.1, { size: 15, color: C.accent4 });

    const X = L + 4.0, CWd = 8.13;
    card(s, X, TOP, CWd, 3.35);
    text(s, "Операции без согласия клиента, I квартал 2026", X + 0.35, TOP + 0.25, 7.4, 0.4, { size: 16, bold: true, color: C.accent4 });
    text(s, "≈0,7 млн ₽", X + 0.35, TOP + 0.7, 3.6, 0.95, { size: 54, bold: true, color: C.accent1 });
    text(s, "средняя операция против юрлица: 818 операций на 573 млн ₽", X + 4.35, TOP + 0.85, 3.5, 0.8, { size: 15 });
    s.addShape(pres.shapes.RECTANGLE, { x: X + 0.35, y: TOP + 1.8, w: 7.35, h: 0.38, fill: { color: C.accent1 }, line: { color: C.accent1, width: 0 }, objectName: "bar-legal" });
    text(s, "≈15 тыс. ₽", X + 0.35, TOP + 2.35, 3.0, 0.5, { size: 26, bold: true, color: C.accent4 });
    s.addShape(pres.shapes.RECTANGLE, { x: X + 0.35, y: TOP + 2.92, w: 0.16, h: 0.26, fill: { color: C.accent4 }, line: { color: C.accent4, width: 0 }, objectName: "bar-all" });
    text(s, "средняя по всем клиентам: 495,8 тыс. операций на 7,4 млрд ₽", X + 0.7, TOP + 2.9, 7.0, 0.32, { size: 14, color: C.accent4 });

    card(s, X, TOP + 3.55, 3.95, 1.7);
    text(s, "в десятки раз", X + 0.3, TOP + 3.72, 3.5, 0.55, { size: 26, bold: true, color: C.accent1 });
    text(s, "дороже средней кражи: компании теряют редко, но крупно", X + 0.3, TOP + 4.3, 3.45, 0.85, { size: 15 });
    card(s, X + 4.18, TOP + 3.55, 3.95, 1.7);
    text(s, "15,9% и 2,4%", X + 4.48, TOP + 3.72, 3.5, 0.55, { size: 26, bold: true, color: C.accent1 });
    text(s, "столько возмещено компаниям по счетам и через СБП", X + 4.48, TOP + 4.3, 3.45, 0.85, { size: 15 });
    s.addNotes("[0:15–0:40] Почему это важно банку. По данным Банка России, в первом квартале 2026 года у юрлиц было 818 операций без согласия на 573 миллиона рублей — в среднем около 0,7 миллиона на операцию. Средняя операция по всем клиентам — около 15 тысяч. Компании теряют редко, но крупно, а возмещают им лишь 15,9 процента по счетам и 2,4 процента через СБП. При этом ответ на вопрос о полномочиях складывается из трёх документов и меняется со временем.");
  }

  // ===== 3. Механика захвата =====
  {
    const s = content("Мошеннику хватает пяти рабочих дней, а сверка «на сегодня» не видит событий между обращениями",
      "Источники: HFLabs, блог «Как банку вычислить фейкового директора»; vtb.ru/r/terms_mchd (условия сервиса МЧД «ВТБ Бизнес»); 129-ФЗ, ст. 8 (срок регистрации); ГК РФ, ст. 188; разъяснения ФНС", MAIN);
    const steps = [
      ["День 0", "Заявление о смене директора в ФНС, часто с украденной подписью"],
      ["5 рабочих дней", "В ЕГРЮЛ — новый директор"],
      ["Затем", "Новая МЧД на «своего» представителя"],
      ["Затем", "Заявка в банк: доступ к счёту, смена реквизитов"],
      ["Финал", "Платёж, который редко удаётся вернуть"],
    ];
    const sw = 2.25, gap = 0.22;
    steps.forEach(([when, what], i) => {
      const x = L + i * (sw + gap);
      card(s, x, TOP, sw, 2.05, { fill: i === 4 ? C.accent5 : C.accent6, line: i === 4 ? C.accent5 : C.text2 });
      text(s, when, x + 0.2, TOP + 0.18, sw - 0.4, 0.4, { size: 16, bold: true, color: i === 4 ? C.text1 : C.accent1 });
      text(s, what, x + 0.2, TOP + 0.65, sw - 0.4, 1.3, { size: 15, color: i === 4 ? C.text1 : C.background1 });
      if (i < 4) s.addImage({ data: ICON_LIGHT.FaArrowRight, x: x + sw + 0.02, y: TOP + 0.9, w: 0.18, h: 0.18, objectName: "arrow" });
    });
    card(s, L, TOP + 2.35, 5.95, 2.9);
    iconCircle(s, L + 0.3, TOP + 2.6, 0.6, ICON.FaSearch);
    text(s, "Где ломается ручная сверка", L + 1.05, TOP + 2.68, 4.6, 0.45, { size: 18, bold: true });
    bullets(s, [
      "Сверка МЧД, выписки ЕГРЮЛ и устава отвечает на вопрос «на сегодня»",
      "В сервисе МЧД «ВТБ Бизнес» представителя, которого нет среди абонентов, добавляют в офисе",
      "Между обращениями клиента смену директора и отмену МЧД нужно отслеживать отдельно",
    ], L + 0.3, TOP + 3.4, 5.4, 1.8, { size: 15 });
    card(s, L + 6.18, TOP + 2.35, 5.95, 2.9);
    iconCircle(s, L + 6.48, TOP + 2.6, 0.6, ICON.FaBalanceScale);
    text(s, "Ловушка в обе стороны", L + 7.23, TOP + 2.68, 4.6, 0.45, { size: 18, bold: true });
    text(s, "Смена директора не прекращает МЧД (п. 1 ст. 188 ГК РФ, позиция ФНС).", L + 6.48, TOP + 3.4, 5.4, 0.7, { size: 16, bold: true, color: C.accent1 });
    bullets(s, [
      "Откажете по старой МЧД — потеряете клиента и день его работы",
      "Пропустите свежую смену директора — откроете дверь захвату",
    ], L + 6.48, TOP + 4.15, 5.4, 1.0, { size: 15 });
    s.addNotes("[0:40–1:05] Как работает захват. Мошенник подаёт заявление о смене директора, через пять рабочих дней в ЕГРЮЛ новый директор, дальше новая доверенность, заявка в банк и платёж. Сверка «на сегодня» не видит, что происходит между обращениями клиента. И есть ловушка: смена директора по закону доверенность не прекращает. Откажете по старой МЧД — потеряете клиента, пропустите свежую смену — откроете дверь захвату. Нужен ответ на дату и с контекстом событий.");
  }

  // ===== 4. Решение =====
  {
    const s = content("Один вопрос — четыре ответа, и неизвестное никогда не становится «да»",
      "«Мандат» проверяет полномочия, а не личность: идентификацию проводят существующие каналы банка (КЭП, ЕБС, офис)", MAIN);
    card(s, L, TOP, W, 0.85, { fill: C.text2, line: C.text2 });
    text(s, [
      { text: "Вправе ли ", options: {} },
      { text: "X", options: { color: C.accent1 } },
      { text: " совершить операцию ", options: {} },
      { text: "O", options: { color: C.accent1 } },
      { text: " от имени компании ", options: {} },
      { text: "Y", options: { color: C.accent1 } },
      { text: " на дату ", options: {} },
      { text: "t", options: { color: C.accent1 } },
      { text: "?", options: {} },
    ], L, TOP, W, 0.85, { size: 24, bold: true, align: "center", valign: "middle" });
    const vs = [
      [V.ok, ICON.FaCheck, "Все обязательные проверки пройдены", "Рекомендация роли в интернет-банке и подписанное удостоверение результата"],
      [V.no, ICON.FaTimes, "Найдено нарушение", "Звено цепочки, норма закона и что запросить у клиента"],
      [V.rv, ICON.FaUserTie, "Спорная ситуация", "Сотруднику — готовое досье, приоритет и срок разбора"],
      [V.nd, ICON.FaQuestion, "Реестр не ответил или нет документа", "Список недостающего вместо «да»"],
    ];
    const cw = 2.85, cg = (W - 4 * cw) / 3;
    vs.forEach(([v, ic, head, body], i) => {
      const x = L + i * (cw + cg), y = TOP + 1.1;
      card(s, x, y, cw, 2.9);
      pill(s, v.label, x + 0.2, y + 0.25, cw - 0.4, 0.42, v.fill, C.text1, 12);
      iconCircle(s, x + 0.2, y + 0.9, 0.55, ic, v.fill);
      text(s, head, x + 0.9, y + 0.9, cw - 1.1, 0.6, { size: 15, bold: true, valign: "middle" });
      text(s, body, x + 0.2, y + 1.65, cw - 0.4, 1.15, { size: 14, color: C.accent4 });
    });
    const flow = [["Принять", "МЧД XML, ЕГРЮЛ, устав, бумажная доверенность"], ["Проверить", `граф полномочий на дату, ${RULES} с нормой`], ["Решить", "вердикт, доказательства, следующий шаг"], ["Следить", "события ЕГРЮЛ и реестра МЧД"]];
    flow.forEach(([h, b], i) => {
      const x = L + i * (cw + cg), y = TOP + 4.2;
      text(s, `0${i + 1} · ${h}`, x, y, cw, 0.4, { size: 15, bold: true, color: C.accent1 });
      text(s, b, x, y + 0.42, cw, 0.75, { size: 14 });
    });
    s.addNotes("[1:05–1:30] Решение — один вопрос и четыре ответа. Подтверждено — с рекомендацией роли. Опровергнуто — с нормой и звеном цепочки. На разбор — человеку с готовым досье. Недостаточно данных — список недостающего. Главное правило: неизвестное никогда не становится «да». Недоступный реестр — это не «действует», а «недостаточно данных».");
  }

  // ===== 5. Доказательства =====
  {
    const s = content("Каждый вердикт доказан: поле МЧД, запись ЕГРЮЛ, норма закона",
      "Демо-кейсы 10, 05 и 11 из репозитория «Мандата»; данные вымышлены. Реестр МЧД и проверка КЭП в стенде — заглушки, это видно в каждом отчёте", MAIN);
    const X = L, Wd = 7.45;
    card(s, X, TOP, Wd, 5.3, { fill: C.background2, line: C.background2 });
    text(s, "Кейс 10 · ООО «Ясная поляна» · синтетические данные", X + 0.35, TOP + 0.25, 4.9, 0.35, { size: 13, color: C.text2, bold: true });
    pill(s, "ПОДТВЕРЖДЕНО", X + Wd - 2.25, TOP + 0.2, 1.9, 0.42, C.accent3, C.text1, 12);
    text(s, "Подписание платёжного поручения на 480 000 ₽ · 20.10.2026 · представитель Лебедева И. П. по МЧД от директора Петрова П. П.", X + 0.35, TOP + 0.72, Wd - 0.7, 0.65, { size: 15, color: C.text1, bold: true });
    const rows = [
      [true, "МЧД подписал директор по ЕГРЮЛ на дату выдачи", "ЕГРЮЛ · ГРН 2247700123456"],
      [true, "МЧД действует в реестре, дата операции в сроке", "XPath …/СвДов/@СрокДейст"],
      [true, "Полномочие покрывает подписание платёжных поручений", "XPath …/СвПолн/МашПолн[1]"],
      [false, "Директор сменился 01.05.2026 — сигнал, а не отказ: МЧД не отменена", "п. 1 ст. 188 ГК РФ"],
    ];
    rows.forEach(([ok, what, ref], i) => {
      const y = TOP + 1.55 + i * 0.9;
      s.addShape(pres.shapes.OVAL, { x: X + 0.35, y: y + 0.05, w: 0.42, h: 0.42, fill: { color: ok ? "2E7D4F" : "B04A0C" }, line: { color: ok ? "2E7D4F" : "B04A0C", width: 0 }, objectName: "check" });
      s.addImage({ data: ok ? ICON_LIGHT.FaCheck : ICON_LIGHT.FaFlag, x: X + 0.45, y: y + 0.15, w: 0.22, h: 0.22, objectName: "check-icon" });
      text(s, what, X + 0.95, y, 4.2, 0.75, { size: 15, color: C.text1, valign: "top" });
      text(s, ref, X + 5.2, y + 0.02, 1.95, 0.75, { size: 12, color: C.text2, valign: "top" });
    });
    const RX = L + 7.75, RW = W - 7.75;
    text(s, "Та же компания — другие факты", RX, TOP, RW, 0.4, { size: 17, bold: true, color: C.accent4 });
    card(s, RX, TOP + 0.5, RW, 2.25);
    text(s, "Директор сменился 19 дней назад", RX + 0.3, TOP + 0.7, RW - 0.6, 0.4, { size: 16, bold: true });
    pill(s, "НА РАЗБОР · P1 · 4 ч", RX + 0.3, TOP + 1.22, 2.6, 0.4, C.accent1, C.text1, 12);
    text(s, "Свежая смена — первый шаг возможного захвата: уточнить у нового директора, при признаках — в антифрод", RX + 0.3, TOP + 1.75, RW - 0.6, 0.95, { size: 14, color: C.accent4 });
    card(s, RX, TOP + 3.0, RW, 2.3);
    text(s, "Основную МЧД отменили, осталось передоверие", RX + 0.3, TOP + 3.2, RW - 0.6, 0.7, { size: 16, bold: true });
    pill(s, "ОПРОВЕРГНУТО", RX + 0.3, TOP + 3.95, 2.0, 0.4, C.accent5, C.text1, 12);
    text(s, "С прекращением доверенности теряет силу передоверие — п. 3 ст. 188 ГК РФ", RX + 0.3, TOP + 4.48, RW - 0.6, 0.75, { size: 14, color: C.accent4 });
    s.addNotes("[1:30–2:05] Так выглядит вердикт. Директор, выдавший доверенность, сменился полгода назад, но доверенность не отменена — по ГК она действует. «Мандат» подтверждает и ставит сигнал, а не отказывает. Каждая строка — с доказательством: ГРН записи ЕГРЮЛ, путь до поля МЧД, норма. Та же компания, но директор сменился 19 дней назад, — на разбор с приоритетом P1 и сроком четыре часа. А если отменили основную доверенность, передоверие теряет силу — опровергнуто.");
  }

  // ===== 6. Мониторинг =====
  {
    const s = content("После выдачи прав «Мандат» продолжает следить: признаки захвата видны до платежа",
      "Сценарии examples/events/takeover.json и poa_revoked.json; команды mandat monitor и mandat attest-verify; статусы — по W3C Bitstring Status List v1.0", MAIN);
    card(s, L, TOP, 6.6, 5.3);
    text(s, "Демо: признаки захвата за 3 часа", L + 0.35, TOP + 0.25, 6, 0.45, { size: 18, bold: true });
    const ev = [["09:10", "в ЕГРЮЛ новый директор"], ["11:40", "новая МЧД на нового представителя"], ["12:05", "заявка на смену реквизитов"]];
    ev.forEach(([t, d], i) => {
      const y = TOP + 0.9 + i * 0.62;
      pill(s, t, L + 0.35, y, 0.95, 0.42, C.text2, C.background1, 13);
      text(s, d, L + 1.5, y, 4.9, 0.42, { size: 15, valign: "middle" });
    });
    card(s, L + 0.35, TOP + 2.85, 5.9, 2.2, { fill: "3A1F17", line: C.accent5, noShadow: true });
    text(s, "Тревога «возможный захват» · высокий риск", L + 0.6, TOP + 3.0, 5.5, 0.4, { size: 16, bold: true, color: C.accent5 });
    bullets(s, [
      "банку — рекомендация приостановить автоматическую выдачу прав по компании",
      "рекомендация передать кейс в антифрод и уведомить прежних подписантов",
      "удостоверения компании — в статус «приостановлено»",
    ], L + 0.6, TOP + 3.5, 5.45, 1.5, { size: 15 });
    const RX = L + 6.85, RW = W - 6.85;
    card(s, RX, TOP, RW, 2.5);
    iconCircle(s, RX + 0.3, TOP + 0.3, 0.6, ICON.FaLink);
    text(s, "Отменили основную МЧД", RX + 1.05, TOP + 0.38, RW - 1.3, 0.45, { size: 17, bold: true });
    text(s, "Удостоверения по всей цепочке передоверия — в статус «отозвано», банку — рекомендация приостановить права по этой цепочке. Каскад по п. 3 ст. 188 ГК РФ.", RX + 0.3, TOP + 1.1, RW - 0.6, 1.3, { size: 15 });
    card(s, RX, TOP + 2.8, RW, 2.5);
    iconCircle(s, RX + 0.3, TOP + 3.1, 0.6, ICON.FaCertificate);
    text(s, "Проверка без доступа к базе", RX + 1.05, TOP + 3.18, RW - 1.3, 0.45, { size: 17, bold: true });
    text(s, "Интернет-банк и антифрод проверяют подпись и статус удостоверения сами. Права в ДБО «Мандат» не отзывает: политику задаёт банк.", RX + 0.3, TOP + 3.9, RW - 0.6, 1.35, { size: 15 });
    s.addNotes("[2:05–2:40] Вторая половина продукта — постоянный контроль. Демо: за три часа смена директора, новая доверенность и заявка на смену реквизитов. «Мандат» связывает три события в тревогу «возможный захват»: рекомендует приостановить автоматическую выдачу прав и передать кейс в антифрод, а сам переводит удостоверения компании в статус «приостановлено». Если отменили основную доверенность, удостоверения по всей цепочке отзываются, а банк получает рекомендацию приостановить права. Здесь включаем запись демо.");
  }

  // ===== 7. Качество =====
  {
    const fcTitle = N.falseConfirmed === 0 ? "Ноль ложных «подтверждено»" : `${N.falseConfirmed} ложных «подтверждено»`;
    const s = content(`${fcTitle} на ${N.negatives} кейсах, где подтверждать нельзя`,
      `Воспроизводится: mandat eval --per-defect 6; python -m pytest tests/test_properties.py tests/test_cedar.py; python scripts/bench.py → evidence.json (${N.asOf}, v${N.version})`, MAIN);
    const stats = [
      [`${N.falseConfirmed} из ${N.negatives}`, `ложных «подтверждено»; верхняя граница 95% — ${N.fcUpper}`],
      [`${N.agree} / ${N.cases}`, `совпадение с ожидаемым вердиктом, ${N.kinds} ${plural(N.kinds, "вид", "вида", "видов")} ситуаций`],
      [`${N.chains}`, "цепочки передоверия: решения совпали с независимой проверкой на Cedar"],
      [N.medianMs, "медиана на кейс вместе с разбором документов, без сети"],
    ];
    stats.forEach(([n, d], i) => {
      const y = TOP + i * 1.1;
      card(s, L, y, 5.6, 0.95);
      text(s, n, L + 0.25, y, 2.2, 0.95, { size: 30, bold: true, color: C.accent1, valign: "middle" });
      text(s, d, L + 2.5, y, 2.95, 0.95, { size: 14, valign: "middle" });
    });
    card(s, L + 5.85, TOP, W - 5.85, 4.3);
    const E = N.expected;
    s.addChart(pres.charts.BAR, [{ name: "Кейсов", labels: ["ПОДТВЕРЖДЕНО", "ОПРОВЕРГНУТО", "НА РАЗБОР", "НЕДОСТ. ДАННЫХ"], values: [E.confirmed, E.refuted, E.needs_review, E.insufficient_data] }], {
      x: L + 6.05, y: TOP + 0.1, w: W - 6.25, h: 4.1, barDir: "bar",
      showTitle: true, title: `Ожидаемые вердикты ${N.cases} проверочных кейсов`, titleColor: HEX.lt1, titleFontSize: 15, titleFontFace: "+mn-lt",
      chartColors: [HEX.accent3, HEX.accent5, HEX.accent1, HEX.accent2], varyColors: true,
      showValue: true, dataLabelPosition: "outEnd", dataLabelColor: HEX.lt1, dataLabelFontSize: 14, dataLabelFontFace: "+mn-lt",
      catAxisLabelColor: HEX.accent4, catAxisLabelFontSize: 13, catAxisLabelFontFace: "+mn-lt", catAxisLineShow: false,
      valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" }, showLegend: false, barGapWidthPct: 60, catAxisOrientation: "maxMin",
    });
    card(s, L, TOP + 4.5, W, 0.8, { fill: C.text2, line: C.text2 });
    text(s, `Это синтетический корпус: проверка логики правил, а не точность на документах банка. ${N.toHuman} кейсов уходят человеку — так задумано. Реальная доля ошибок — главный стоп-критерий пилота.`, L + 0.3, TOP + 4.5, W - 0.6, 0.8, { size: 15, valign: "middle" });
    s.addNotes(`[2:40–3:10] Качество. Главная ошибка, которую нельзя допускать, — ложное «подтверждено». На ${N.negatives} синтетических кейсах, где подтверждать нельзя, таких ошибок ${N.falseConfirmed === 0 ? "ноль" : N.falseConfirmed}, верхняя граница 95% — ${N.fcUpper}. Ключевое свойство — передоверие не расширяет полномочия — проверено на ${N.chains} цепочках и совпало с независимой реализацией на Cedar. Это синтетика, проверка логики правил. Точность на реальных пакетах измерит пилот.`);
  }

  // ===== 8. ИИ советует =====
  {
    const s = content("ИИ советует, но не решает: вердикт выносят правила, спорное — человек",
      "Источники: Кодекс этики в сфере ИИ на финансовом рынке (Банк России, 2025); CNews, 21.08.2026 — ВТБ присоединился к Кодексу; архитектурное решение ADR-001 «Мандата»", MAIN);
    const flow = [
      [ICON.FaFileAlt, "Документ", "устав, текстовое полномочие"],
      [ICON.FaRobot, "Модель в контуре банка", "предлагает факт с дословной цитатой"],
      [ICON.FaFilter, "Шлюз цитат", "нет цитаты в документе — нет факта"],
      [ICON.FaGavel, `${RULES} с нормой`, "выносят вердикт"],
      [ICON.FaUsers, "Человек", "разбирает спорное; клиент может оспорить"],
    ];
    const fw = 2.25, fg = 0.22;
    flow.forEach(([ic, h, b], i) => {
      const x = L + i * (fw + fg);
      card(s, x, TOP, fw, 2.3, { fill: i === 3 ? C.text2 : C.accent6 });
      iconCircle(s, x + 0.2, TOP + 0.22, 0.6, ic, i === 1 ? C.accent4 : C.accent1);
      text(s, h, x + 0.2, TOP + 0.95, fw - 0.4, 0.62, { size: 15, bold: true });
      text(s, b, x + 0.2, TOP + 1.55, fw - 0.4, 0.7, { size: 13, color: C.accent4 });
      if (i < 4) s.addImage({ data: ICON_LIGHT.FaArrowRight, x: x + fw + 0.02, y: TOP + 1.05, w: 0.18, h: 0.18, objectName: "arrow" });
    });
    card(s, L, TOP + 2.6, W, 2.7);
    text(s, "«Мандат» спроектирован в логике Кодекса этики в сфере ИИ на финансовом рынке; по данным CNews, ВТБ присоединился к нему 21.08.2026", L + 0.35, TOP + 2.8, W - 0.7, 0.75, { size: 17, bold: true, color: C.accent1 });
    const pr = [
      ["Прозрачность", "у каждого вывода — норма и ссылка на поле документа; отчёт сам пишет, что в стенде заглушка"],
      ["Человекоцентричность", "спорное решает сотрудник, автоматический вердикт клиент может оспорить у человека"],
      ["Безопасность", "модель on-prem, ПДн псевдонимизированы; без модели продукт работает полностью"],
    ];
    pr.forEach(([h, b], i) => {
      const x = L + 0.35 + i * 3.9;
      text(s, h, x, TOP + 3.7, 3.6, 0.4, { size: 16, bold: true });
      text(s, b, x, TOP + 4.12, 3.6, 1.1, { size: 14, color: C.accent4 });
    });
    s.addNotes("[3:10–3:35] Про ИИ. Модель только предлагает факт из устава, и только с дословной цитатой. Нет цитаты — нет факта. Вердикт выносят правила со ссылкой на норму, спорное решает человек, а клиент может оспорить автоматический вердикт. Это логика Кодекса этики в сфере ИИ на финансовом рынке; по данным CNews, ВТБ присоединился к нему в августе.");
  }

  // ===== 9. Эффект =====
  {
    const s = content("Эффект для ВТБ в двух валютах: ставки сотрудников и предотвращённый ущерб",
      "Источники: ВТБ, МСФО за 1П 2026 (≈1,8 млн предприятий и предпринимателей; пересказ пресс-релиза, altapress.ru, 29.07.2026; первичный источник не открыт); Банк России, I кв. 2026. Доли и минуты — допущения команды; расчёт: python scripts/effect_model.py --scenarios", MAIN);
    const half = (W - 0.25) / 2;
    card(s, L, TOP, half, 4.15);
    s.addChart(pres.charts.BAR, [{ name: "Ставок в год", labels: ["Консервативный", "Базовый", "Оптимистичный"], values: FTE }], {
      x: L + 0.15, y: TOP + 0.1, w: half - 0.3, h: 2.9, barDir: "col",
      showTitle: true, title: "Высвобождение нагрузки, ставок в год", titleColor: HEX.lt1, titleFontSize: 15, titleFontFace: "+mn-lt",
      chartColors: [HEX.accent1], showValue: true, dataLabelPosition: "outEnd", dataLabelColor: HEX.lt1, dataLabelFontSize: 14, dataLabelFontFace: "+mn-lt",
      catAxisLabelColor: HEX.accent4, catAxisLabelFontSize: 12, catAxisLabelFontFace: "+mn-lt", catAxisLineShow: false,
      valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" }, showLegend: false, barGapWidthPct: 70, valAxisMaxVal: 200,
    });
    text(s, "≈1,8 млн предприятий и ИП × доля с событием представительства (10 / 15 / 25%) × сэкономленные минуты на кейс, включая разбор (20 / 30 / 40) ÷ 60 ÷ 1 800 ч на ставку", L + 0.3, TOP + 3.1, half - 0.6, 1.0, { size: 13, color: C.accent4 });
    const RX = L + half + 0.25;
    card(s, RX, TOP, half, 4.15);
    s.addChart(pres.charts.BAR, [{ name: "Млн ₽ в год", labels: ["Консервативный", "Базовый", "Оптимистичный"], values: LOSS }], {
      x: RX + 0.15, y: TOP + 0.1, w: half - 0.3, h: 2.9, barDir: "col",
      showTitle: true, title: "Предотвращённый ущерб, млн ₽ в год", titleColor: HEX.lt1, titleFontSize: 15, titleFontFace: "+mn-lt",
      chartColors: [HEX.accent3], showValue: true, dataLabelPosition: "outEnd", dataLabelColor: HEX.lt1, dataLabelFontSize: 14, dataLabelFontFace: "+mn-lt",
      catAxisLabelColor: HEX.accent4, catAxisLabelFontSize: 12, catAxisLabelFontFace: "+mn-lt", catAxisLineShow: false,
      valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" }, showLegend: false, barGapWidthPct: 70, valAxisMaxVal: 270,
    });
    text(s, "818 операций против юрлиц за квартал × 4 × доля ВТБ (15 / 20 / 25%) × доля случаев с полномочиями, которые перехватит «Мандат» (10 / 25 / 40%) × 0,7 млн ₽", RX + 0.3, TOP + 3.1, half - 0.6, 1.0, { size: 13, color: C.accent4 });
    card(s, L, TOP + 4.4, W, 0.9, { fill: C.text2, line: C.text2 });
    text(s, [
      { text: "Сценарии, а не прогноз. ", options: { bold: true, color: C.accent1 } },
      { text: "Доли и минуты — гипотезы команды; первые две недели пилота заменяют их фактами ВТБ. Плюс цель пилота: права в день обращения для типовых случаев.", options: {} },
    ], L + 0.3, TOP + 4.4, W - 0.6, 0.9, { size: 15, valign: "middle" });
    s.addNotes(`[3:35–4:00] Эффект в двух валютах. Операционный: при 1,8 миллиона корпоративных клиентов ВТБ — от ${FTE[0]} до ${FTE[2]} ставок в год. Риск: от ${LOSS[0]} до ${LOSS[2]} миллионов рублей предотвращённого ущерба в год.` + " Обе формулы на слайде, все допущения подписаны. Это сценарии, а не обещание: первые две недели пилота заменяют допущения фактами банка.");
  }

  // ===== 10. Почему сейчас =====
  {
    const s = content("Почему сейчас: МЧД втрое больше, а правила меняются каждые полгода",
      "Источники: 63-ФЗ в ред. 476-ФЗ; emchd.ru, 10.2024; Приказ Банка России № ОД-2506 от 05.11.2025; Приказ Минцифры № 1001; CNews, 21.08.2026; портал МЧД ФНС по selsup.ru, 09.2026; 315-ФЗ", MAIN);
    const tl = [
      ["01.09.2024", "МЧД обязательна для представителей юрлиц"],
      ["10.2024", "≈1,6 млн МЧД в реестре ФНС"],
      ["01.01.2026", "Приказ Банка России № ОД-2506: новые признаки операций без согласия"],
      ["01.02.2026", "новые реквизиты МЧД: Приказ Минцифры № 1001"],
      ["21.08.2026", "ВТБ присоединился к Кодексу этики в сфере ИИ (по данным CNews)"],
      ["09.2026", "более 5,5 млн МЧД в реестре ФНС"],
      ["01.09.2027", "единая форма МЧД — универсальная (315-ФЗ)"],
    ];
    const tw = 7.3;
    card(s, L, TOP, tw + 0.2, 5.3);
    s.addShape(pres.shapes.LINE, { x: L + 1.95, y: TOP + 0.45, w: 0, h: 4.45, line: { color: C.text2, width: 2 }, objectName: "timeline" });
    tl.forEach(([d, t], i) => {
      const y = TOP + 0.3 + i * 0.68;
      const hot = i === 5 || i === 6;
      s.addShape(pres.shapes.OVAL, { x: L + 1.84, y: y + 0.08, w: 0.22, h: 0.22, fill: { color: hot ? C.accent1 : C.accent4 }, line: { color: hot ? C.accent1 : C.accent4, width: 0 }, objectName: "dot" });
      text(s, d, L + 0.25, y, 1.45, 0.4, { size: 14, bold: true, color: hot ? C.accent1 : C.accent4, align: "right" });
      text(s, t, L + 2.3, y, tw - 2.3, 0.4, { size: 15, bold: hot });
    });
    const RX = L + tw + 0.45, RW = W - tw - 0.45;
    card(s, RX, TOP, RW, 2.5);
    text(s, "×3", RX + 0.3, TOP + 0.2, RW - 0.6, 0.9, { size: 54, bold: true, color: C.accent1 });
    text(s, "рост числа МЧД за неполные два года: поток входящих доверенностей в банк только растёт", RX + 0.3, TOP + 1.15, RW - 0.6, 1.2, { size: 15 });
    card(s, RX, TOP + 2.8, RW, 2.5);
    text(s, "1,8 млн", RX + 0.3, TOP + 3.0, RW - 0.6, 0.9, { size: 46, bold: true, color: C.accent1 });
    text(s, "предприятий и предпринимателей у ВТБ (по пресс-релизу к МСФО за 1П 2026) — у каждого есть подписанты и представители, которые меняются", RX + 0.3, TOP + 3.95, RW - 0.6, 1.2, { size: 15 });
    s.addNotes("[4:00–4:20] Почему сейчас. МЧД стали обязательными в сентябре 2024 года, и за неполные два года их стало втрое больше — уже более пяти с половиной миллионов. С февраля действуют новые реквизиты, Банк России обновил признаки операций без согласия клиента, а с сентября 2027 года единая форма МЧД станет универсальной. Банку нужен движок, в котором нормы меняются данными, а не релизом.");
  }

  // ===== 11. Встройка в контур ВТБ =====
  {
    const s = content("Встаёт в контур ВТБ on-prem: ядро готово, источники подключаются через порты",
      `Источники: vtb.ru — «Комплект МЧД и КЭП» в «ВТБ Бизнес», 10.2026; репозиторий «Мандата» v${N.version}: ${TESTS}, evidence.json от ${N.asOf}`, MAIN);
    const boxes = [
      [ICON.FaDatabase, "Источники", ["МЧД в XML", "ЕГРЮЛ на дату", "реестр МЧД ФНС", "устав и бумага"]],
      [ICON.FaCogs, "Движок «Мандата»", ["граф полномочий на дату", `${RULES} с нормой`, "журнал с деревом Меркла", "без внешней сети"]],
      [ICON.FaRoute, "Результат", ["вердикт и доказательства", "рекомендация роли в «ВТБ Бизнес»", "удостоверение со статусом", "сигнал антифроду"]],
    ];
    const bw = 3.75, bg = (W - 3 * bw) / 2;
    boxes.forEach(([ic, h, items], i) => {
      const x = L + i * (bw + bg);
      card(s, x, TOP, bw, 2.75, { fill: i === 1 ? C.text2 : C.accent6 });
      iconCircle(s, x + 0.25, TOP + 0.25, 0.6, ic);
      text(s, h, x + 1.0, TOP + 0.33, bw - 1.2, 0.45, { size: 17, bold: true });
      bullets(s, items, x + 0.3, TOP + 1.05, bw - 0.55, 1.65, { size: 14, psa: 3 });
      if (i < 2) s.addImage({ data: ICON_LIGHT.FaArrowRight, x: x + bw + 0.06, y: TOP + 1.25, w: 0.24, h: 0.24, objectName: "arrow" });
    });
    const hw = (W - 0.25) / 2;
    card(s, L, TOP + 3.0, hw, 2.3);
    text(s, "Работает сегодня", L + 0.3, TOP + 3.15, hw - 0.6, 0.4, { size: 16, bold: true, color: C.accent3 });
    bullets(s, [`${RULES}, ${N.demo} демо-кейсов, CLI и HTTP API`, "мониторинг событий, статусы удостоверений", `${TESTS} проходят, каждое число воспроизводится командой`], L + 0.3, TOP + 3.65, hw - 0.6, 1.6, { size: 15 });
    card(s, L + hw + 0.25, TOP + 3.0, hw, 2.3);
    text(s, "Подключаем к пилоту", L + hw + 0.55, TOP + 3.15, hw - 0.6, 0.4, { size: 16, bold: true, color: C.accent1 });
    bullets(s, ["реестр МЧД ФНС, ЕГРЮЛ — адаптеры банка вместо заглушек", "проверка КЭП — сертифицированные СКЗИ банка", "коды полномочий — выгрузка классификатора ЕСНСИ"], L + hw + 0.55, TOP + 3.65, hw - 0.6, 1.6, { size: 15 });
    s.addNotes("[4:20–4:35] Встройка. ВТБ уже продаёт комплект МЧД и КЭП в «ВТБ Бизнес», значит, поток представителей по доверенностям уже есть. «Мандат» встаёт в контур on-prem: ядро, правила, журнал и мониторинг работают сегодня, а реестр, ЕГРЮЛ и проверку подписи подключаем к пилоту через готовые порты.");
  }

  // ===== 12. Пилот =====
  {
    const s = content("Предлагаем 6-недельный теневой пилот на праве подписи платёжных поручений по МЧД",
      "Теневой режим: «Мандат» проверяет те же пакеты, что и сотрудники, и не влияет на клиентов и выдачу прав. Формат совпадает с пилотами Акселератора ВТБ", MAIN);
    const wk = [["Неделя 1", "Протокол, регламент операции, пороги приёмки, куратор от владельца процесса"], ["Недели 2–5", "Параллельная проверка тех же пакетов, журнал расхождений"], ["Неделя 6", "Независимая разметка расхождений, решение: продолжать или остановить"]];
    const ww = 3.95, wg = (W - 3 * ww) / 2;
    wk.forEach(([h, b], i) => {
      const x = L + i * (ww + wg);
      card(s, x, TOP, ww, 1.4);
      text(s, h, x + 0.3, TOP + 0.15, ww - 0.6, 0.4, { size: 17, bold: true, color: C.accent1 });
      text(s, b, x + 0.3, TOP + 0.6, ww - 0.6, 0.75, { size: 14 });
    });
    const hw = (W - 0.25) / 2;
    card(s, L, TOP + 1.65, hw, 2.6);
    iconCircle(s, L + 0.3, TOP + 1.85, 0.55, ICON.FaClipboardCheck);
    text(s, "Критерии: продолжать или остановить", L + 1.0, TOP + 1.92, hw - 1.2, 0.42, { size: 17, bold: true });
    bullets(s, ["0 ложных «подтверждено» на всех кейсах пилота — стоп-критерий", "доля «на разбор» не выше порога, согласованного до старта", "согласие с экспертами (κ Коэна) не ниже, чем между экспертами", "каждое расхождение разобрано, проверка быстрее ручной"], L + 0.3, TOP + 2.55, hw - 0.55, 1.65, { size: 14, psa: 3 });
    card(s, L + hw + 0.25, TOP + 1.65, hw, 2.6);
    iconCircle(s, L + hw + 0.55, TOP + 1.85, 0.55, ICON.FaHandshake);
    text(s, "Что нужно от ВТБ", L + hw + 1.25, TOP + 1.92, hw - 1.2, 0.42, { size: 17, bold: true });
    bullets(s, ["владелец процесса и куратор пилота от него", "регламент одной операции и типовые отказы", "50–100 обезличенных пакетов или тестовый контур", "юрист для проверки правил, базовые цифры"], L + hw + 0.55, TOP + 2.55, hw - 0.55, 1.65, { size: 14, psa: 3 });
    card(s, L, TOP + 4.5, W, 0.8, { fill: C.accent1, line: C.accent1 });
    text(s, "Наш запрос: теневой пилот через Акселератор ВТБ с метриками, согласованными до старта", L + 0.3, TOP + 4.5, W - 0.6, 0.8, { size: 18, bold: true, color: C.text1, valign: "middle" });
    s.addNotes("[4:35–4:50] Наш запрос конкретный: шестинедельный теневой пилот на одной операции — праве подписи платёжных поручений по МЧД. «Мандат» работает рядом с сотрудниками и ни на что не влияет. Стоп-критерий — ноль ложных подтверждений на всех кейсах пилота. От банка — владелец процесса, регламент, обезличенные пакеты и юрист.");
  }

  // ===== 13. Команда =====
  {
    const s = content("Команда: правила, измерение, процесс и право",
      "Результат для банка — проверенный набор правил, протокол измерений, итоги пилота и эталонная реализация под MIT для передачи команде банка", MAIN);
    const team = [
      ["dmitrij.jpg", "Коньков Дмитрий", "Капитан · архитектура и правила", "Автор открытых проектов AeroBIM и SynAPS. Детерминированное ядро, граф полномочий, границы заявлений"],
      ["elena.jpg", "Конькова Елена", "Системный инженер · измерение и качество", "Методика оценки, корпус и разметка, воспроизводимость сборки, протокол пилота"],
      ["nikolaj.jpg", "Ляхов Николай", "Владелец продукта и исследователь", "Интервью, опрос, карта процесса, питч"],
      ["aleksej.jpg", "Кузнецов Алексей", "Юрист", "Правила и нормы, разметка корпуса, юридические эффекты"],
    ];
    const hw = (W - 0.25) / 2, hh = 2.5;
    team.forEach(([img, name, role, desc], i) => {
      const x = L + (i % 2) * (hw + 0.25), y = TOP + Math.floor(i / 2) * (hh + 0.3);
      card(s, x, y, hw, hh);
      s.addImage({ path: path.join(ASSETS, img), x: x + 0.3, y: y + 0.3, w: 1.9, h: 1.9, rounding: false, objectName: "photo", altText: `Фото: ${name}` });
      text(s, name, x + 2.5, y + 0.3, hw - 2.8, 0.45, { size: 19, bold: true });
      text(s, role, x + 2.5, y + 0.78, hw - 2.8, 0.6, { size: 14, bold: true, color: C.accent1 });
      text(s, desc, x + 2.5, y + 1.4, hw - 2.8, 1.0, { size: 14, color: C.accent4 });
    });
    s.addNotes("[4:50–5:00] Команда: я отвечаю за архитектуру и правила, Елена — за методику измерения и качество, Николай — за исследование и процесс, Алексей — за правовые нормы. Спасибо, готовы к вопросам.");
  }

  // ===== 14. Контакты =====
  {
    const s = pres.addSlide({ masterName: "COVER", sectionTitle: MAIN });
    text(s, "Контакты", L, 0.9, 7, 0.9, { size: 44, bold: true, color: C.accent1 });
    text(s, "Команда «Мандат»", L, 1.95, 7, 0.5, { size: 24, bold: true });
    text(s, [
      { text: "Коньков Дмитрий, капитан · +7 915 452-88-64 · konkov-dmitrij@yandex.ru", options: { breakLine: true } },
      { text: "Конькова Елена, измерение и качество · konkovaelena@yandex.ru", options: { breakLine: true } },
      { text: "Код: ", options: {} },
      { text: "github.com/KonkovDV/Mandat", options: { hyperlink: { url: "https://github.com/KonkovDV/Mandat" }, color: C.accent1 } },
      { text: " · собственный код под MIT", options: {} },
    ], L, 2.65, 7.6, 1.5, { size: 16, psa: 8 });
    card(s, L, 4.55, 7.4, 1.0, { fill: C.accent6, line: C.accent1 });
    text(s, "Ищем в ВТБ владельца процесса добавления представителей юрлиц, чтобы сверить гипотезы до 18 октября", L + 0.3, 4.55, 6.8, 1.0, { size: 16, valign: "middle" });
    s.addImage({ path: path.join(ASSETS, "graph.png"), x: 8.75, y: 1.45, w: 3.9, h: 3.9, objectName: "graph", altText: "Схема: граф полномочий «Мандата»" });
    s.addNotes("Спасибо. Сейчас для нас важнее всего поговорить с теми, кто в ВТБ отвечает за добавление представителей юрлиц: это сделает гипотезы проверяемыми уже в первом спринте.");
  }

  // ===================== ПРИЛОЖЕНИЕ =====================
  pres.addSection({ title: APP });
  {
    const s = pres.addSlide({ masterName: "DIVIDER", sectionTitle: APP });
    s.addText("Приложение: ответы на вопросы жюри", { placeholder: "title" });
    s.addText("Конкуренты · правила · право · доверие без базы · качество · ИБ · риски · расчёт эффекта · трудные вопросы · нормы · дорожная карта", { placeholder: "body" });
  }

  let TSIZE = 14;
  const TH = (t) => ({ text: t, options: { bold: true, color: HEX.lt1, fill: { color: HEX.dk2 }, fontSize: TSIZE, valign: "middle" } });
  const TD = (t, o = {}) => ({ text: t, options: { color: o.color || HEX.lt1, fill: { color: o.fill || HEX.accent6 }, fontSize: o.size || TSIZE, bold: o.bold || false, valign: "middle" } });
  const tableOpts = (colW, y = TOP, rowH) => ({ x: L, y, w: W, colW, border: { type: "solid", pt: 0.75, color: HEX.dk2 }, fontFace: "Calibri", margin: [4, 6, 4, 6], rowH });

  // A1 Конкуренты
  {
    const s = content("Реестр и ЭДО отвечают «действует ли МЧД», «Мандат» — «можно ли эту операцию сегодня»",
      "«Не заявлено» — нет в открытых описаниях продукта на 12.10.2026; «не проверяли» — открытых данных недостаточно для сравнения. Источники: vtb.ru/r/terms_mchd; kontur.ru/mchd; docs/COMPETITORS.md", APP);
    const M = (t) => TD(t, { color: HEX.dk1, fill: HEX.accent1, bold: true });
    const rows = [
      [TH("Что сравниваем"), TH("Сервис МЧД «ВТБ Бизнес»"), TH("1С-ЭДО, Контур.Доверенность"), TH("Реестр МЧД ФНС"), TH("«Мандат»")],
      [TD("Объект проверки", { bold: true }), TD("выпуск исходящих МЧД"), TD("МЧД под документ ЭДО"), TD("действует ли МЧД"), M("операция банка и права в ДБО на дату")],
      [TD("Передоверие", { bold: true }), TD("только признак"), TD("не проверяли"), TD("хранит сведения"), M("сроки, сужение, каскад отмены")],
      [TD("ЕГРЮЛ на дату и устав", { bold: true }), TD("не заявлено"), TD("не заявлено"), TD("нет"), M("в одной проверке")],
      [TD("Смена директора", { bold: true }), TD("не заявлено"), TD("не проверяли"), TD("нет"), M("сигнал или разбор по окну свежести")],
      [TD("Доказательства и аудит", { bold: true }), TD("не заявлено"), TD("не заявлено"), TD("статус в реестре"), M("XPath, ГРН, норма; журнал; удостоверение со статусом")],
    ];
    s.addTable(rows, tableOpts([2.3, 2.3, 2.55, 1.95, 3.03], TOP, 0.66));
    card(s, L, TOP + 4.35, W, 0.95, { fill: C.text2, line: C.text2 });
    text(s, "Не конкурируем с выпуском МЧД — берём его результат на вход. Операторы ЭДО и реестр — источники данных для «Мандата».", L + 0.3, TOP + 4.35, W - 0.6, 0.95, { size: 15, valign: "middle" });
  }

  // A2 Правила
  {
    const s = content(`${RULES} со ссылкой на норму: что они ловят`,
      "Полная таблица «правило → норма → эффект → тест» — docs/RULES.md. Все правила в статусе draft до проверки юристом банка; в строгом профиле неутверждённое правило не опровергает", APP);
    const rows = [
      [TH("Группа"), TH("Правила"), TH("Что ловят")],
      [TD("Формат", { bold: true }), TD("R-FMT-001…004"), TD("битый XML и XML-бомбы, чужой формат, реквизиты по Приказу № 1001, признаки безотзывной доверенности")],
      [TD("Стороны", { bold: true }), TD("R-ID-001…004"), TD("контрольные числа, чужая компания, чужой представитель, наименование не по ЕГРЮЛ")],
      [TD("Реестр и срок", { bold: true }), TD("R-REG-001, R-TERM-001…002"), TD("отмена и истечение, недоступный реестр, срок по закону — год со дня совершения")],
      [TD("Руководитель", { bold: true }), TD("R-EIO-001…005"), TD("подписант не директор на дату, смена директора (сигнал или разбор), недостоверность, ликвидация")],
      [TD("Покрытие", { bold: true }), TD("R-COV-001…002, R-JNT-001"), TD("нет полномочия на операцию, код исключён из классификатора, совместные полномочия")],
      [TD("Передоверие", { bold: true }), TD("R-SUB-001…008"), TD("запрет, однократность, расширение, срок, пропущенное звено, каскад отмены, утрата полномочий, длинная цепочка")],
      [TD("Устав и подпись", { bold: true }), TD("R-CHR, R-TXT, R-PAP, R-SIG"), TD("порог одобрения, текстовые полномочия, бумажная доверенность, КЭП")],
    ];
    TSIZE = 13;
    const rows2 = rows.map((r) => r.map((c) => ({ text: c.text, options: { ...c.options, fontSize: 13 } })));
    s.addTable(rows2, tableOpts([2.1, 3.1, 6.93], TOP, 0.6));
    TSIZE = 14;
  }

  // A3 Право
  {
    const s = content("Четыре нормы, на которых ошибается ручная сверка",
      "Источники: ГК РФ, ст. 186–188; разъяснения ФНС о смене руководителя; описание схемы МЧД (ПрУтрПолн). Ссылки сверить с юристом банка перед пилотом — docs/REGULATORY.md", APP);
    const items = [
      ["Смена директора", "п. 1 ст. 188 ГК РФ", "МЧД прежнего директора действует, пока её не отменили", V.ok, "давняя смена — сигнал; свежая — на разбор"],
      ["Отмена основной МЧД", "п. 3 ст. 188 ГК РФ", "вместе с ней теряет силу передоверие", V.no, "каскад по всей цепочке"],
      ["Срок не указан", "п. 1 ст. 186 ГК РФ", "МЧД действует год со дня совершения", V.rv, "истёк год — опровергнуто"],
      ["Передоверие с утратой", "признак ПрУтрПолн = 2", "передоверитель больше не вправе действовать", V.no, "действует только новый представитель"],
    ];
    const cw = (W - 3 * 0.22) / 4;
    items.forEach(([h, norm, rule, v, note], i) => {
      const x = L + i * (cw + 0.22);
      card(s, x, TOP, cw, 4.15);
      text(s, h, x + 0.25, TOP + 0.25, cw - 0.5, 0.75, { size: 18, bold: true });
      text(s, norm, x + 0.25, TOP + 1.05, cw - 0.5, 0.4, { size: 14, bold: true, color: C.accent1 });
      text(s, rule, x + 0.25, TOP + 1.6, cw - 0.5, 1.5, { size: 15 });
      pill(s, v.label, x + 0.25, TOP + 2.75, cw - 0.5, 0.42, v.fill, C.text1, 12);
      text(s, note, x + 0.25, TOP + 3.3, cw - 0.5, 0.8, { size: 14, color: C.accent4 });
    });
    card(s, L, TOP + 4.4, W, 0.9, { fill: C.text2, line: C.text2 });
    text(s, "Ручная сверка «на сегодня» ошибается в обе стороны: отказывает по действующей МЧД и пропускает оборванную цепочку", L + 0.3, TOP + 4.4, W - 0.6, 0.9, { size: 15, valign: "middle" });
  }

  // A4 Доверие без базы
  {
    const s = content("Доверие без доступа к базе: подписанное удостоверение, статус и журнал",
      "Стандарты: W3C Verifiable Credentials 2.0 и Bitstring Status List v1.0 (Рекомендации W3C, 15.05.2025); RFC 6962 / RFC 9162. В стенде подпись Ed25519, к пилоту — ГОСТ через СКЗИ банка", APP);
    const cols = [
      [ICON.FaCertificate, "Удостоверение", ["вердикт, операция, хеш набора правил и входов", "номера МЧД цепочки", "без ФИО, только хеш ИНН представителя"]],
      [ICON.FaBell, "Статус", ["списки «отозвано» и «приостановлено»", "мониторинг меняет статус по событию", "проверяющий видит актуальный статус сам"]],
      [ICON.FaLock, "Журнал решений", ["хеш-цепочка и HMAC: правка записи видна", "дерево Меркла: доказательство одной записи", "аудитор не получает весь журнал"]],
    ];
    const cw = (W - 2 * 0.25) / 3;
    cols.forEach(([ic, h, items], i) => {
      const x = L + i * (cw + 0.25);
      card(s, x, TOP, cw, 3.65);
      iconCircle(s, x + 0.3, TOP + 0.3, 0.65, ic);
      text(s, h, x + 1.1, TOP + 0.4, cw - 1.3, 0.5, { size: 19, bold: true });
      bullets(s, items, x + 0.3, TOP + 1.3, cw - 0.6, 2.25, { size: 15, psa: 10 });
    });
    card(s, L, TOP + 3.9, W, 1.4, { fill: C.text2, line: C.text2 });
    text(s, [
      { text: "Сценарий: ", options: { bold: true, color: C.accent1 } },
      { text: "проверка → удостоверение уходит в «ВТБ Бизнес» → основную МЧД отменили → мониторинг ставит «отозвано» → интернет-банк видит статус сам, без запроса к «Мандату».", options: {} },
    ], L + 0.3, TOP + 3.9, W - 0.6, 1.4, { size: 16, valign: "middle" });
  }

  // A5 Методика качества
  {
    const s = content("Как мы измеряем качество — и что изменится на реальных данных",
      "Источники: evidence.json, docs/EVALUATION.md; Brown, Cai, DasGupta (2001) — интервал Вилсона; Geifman, El-Yaniv (2017) — «риск — покрытие»", APP);
    const rows = [
      [TH("Что"), TH("Сейчас: синтетика"), TH("На пилоте")],
      [TD("Корпус", { bold: true }), TD(`${N.cases} ${plural(N.cases, "кейс", "кейса", "кейсов")}, ${N.kinds} ${plural(N.kinds, "вид", "вида", "видов")}` + (N.kindsBy.ok !== null ? `: ${N.kindsBy.ok} зелёных, ${N.kindsBy.no} опровергающих, ${N.kindsBy.rv} на разбор, ${N.kindsBy.nd} без данных` : "")), TD("50–100 обезличенных пакетов")],
      [TD("Ложные «подтверждено»", { bold: true }), TD(`${N.falseConfirmed} из ${N.negatives}, верхняя граница 95% — ${N.fcUpper}`), TD("стоп-критерий: 0 на всех кейсах пилота")],
      [TD("Полнота", { bold: true }), TD(`все опровергающие найдены, нижняя граница 95% — ${N.recallLow}`), TD(`не меньше ${N.nRecall} дефектных случаев для ±5%`)],
      [TD("Покрытие", { bold: true }), TD(`${N.coverage} автоматических вердиктов в профиле demo; ${N.strictCoverage} в strict`), TD("порог согласуем с банком до старта")],
      [TD("Разметка", { bold: true }), TD("ожидаемый вердикт задан генератором"), TD("два независимых эксперта, κ Коэна")],
      [TD("Независимая проверка", { bold: true }), TD(`${N.chains} цепочки: «Мандат» и Cedar совпали`), TD("символьное доказательство свойства — спринт 3")],
    ];
    s.addTable(rows, tableOpts([2.6, 4.9, 4.63], TOP, 0.66));
  }

  // A6 ИБ и ПДн
  {
    const s = content("Всё в контуре банка: данные, модель и решения",
      "Это описание проектных решений, а не оценка соответствия: её проводят ИБ и юристы банка. docs/DATA_PROTECTION.md, docs/THREAT_MODEL.md", APP);
    const items = [
      [ICON.FaServer, "On-prem без сети", "Ядро не делает внешних запросов; модель — только хосты из белого списка, по умолчанию выключена"],
      [ICON.FaUserSecret, "ПДн", "Псевдонимизация ИНН, СНИЛС, паспорта, телефона, почты; удостоверение без ФИО"],
      [ICON.FaShieldAlt, "Вредоносные файлы", "Защита от XML-бомб и внешних сущностей, лимиты размера и глубины"],
      [ICON.FaLock, "API", "Закрыт без токена: 503, неверный токен — 401; лимит тела, CSP на отчёте"],
      [ICON.FaEye, "152-ФЗ и 115-ФЗ", "Спроектировано с учётом 152-ФЗ; идентификацию по 115-ФЗ не подменяем"],
      [ICON.FaCertificate, "Подпись", "КЭП проверяют сертифицированные СКЗИ банка через порт"],
    ];
    const cw = (W - 2 * 0.25) / 3, ch = 2.35;
    items.forEach(([ic, h, b], i) => {
      const x = L + (i % 3) * (cw + 0.25), y = TOP + Math.floor(i / 3) * (ch + 0.25);
      card(s, x, y, cw, ch);
      iconCircle(s, x + 0.3, y + 0.3, 0.6, ic);
      text(s, h, x + 1.05, y + 0.38, cw - 1.25, 0.45, { size: 17, bold: true });
      text(s, b, x + 0.3, y + 1.1, cw - 0.6, 1.15, { size: 15, color: C.accent4 });
    });
  }

  // A7 Риски
  {
    const s = content("Риски, которые банк увидит первыми, — и как мы их снимаем", "LIMITS.md — полный список границ; docs/AI_RISKS.md — матрица рисков ИИ", APP);
    const rows = [
      [TH("Риск"), TH("Как снимаем"), TH("Чем контролируем")],
      [TD("Ложное подтверждение", { bold: true }), TD("неизвестное не становится «да», обязательные группы проверок"), TD("0 ложных «подтверждено» — стоп-критерий")],
      [TD("Ошибка в норме", { bold: true }), TD("норма у каждого правила, статус draft/approved, юрист банка"), TD("доля правил, утверждённых юристом")],
      [TD("Ошибка модели", { bold: true }), TD("нет цитаты — нет факта; вердикт модель не меняет"), TD("тест «модель не меняет вердикт»")],
      [TD("Формат МЧД изменится", { bold: true }), TD("теги — в файле соответствия, формы ЦБ по 315-ФЗ — через адаптер"), TD("пересборка примеров одной командой")],
      [TD("Нагрузка «на разбор»", { bold: true }), TD("сигнал вместо разбора там, где закон позволяет"), TD("доля «на разбор» с порогом банка")],
      [TD("Интеграция с ДБО", { bold: true }), TD("на пилоте — только рекомендация, без изменений в ДБО"), TD("сценарий пилота без доработок ДБО")],
    ];
    s.addTable(rows, tableOpts([2.8, 5.2, 4.13], TOP, 0.66));
  }

  // A8 Расчёт эффекта
  {
    const s = content("Расчёт эффекта: формулы, допущения и мировой ориентир",
      "Источники: Банк России, I кв. 2026; ВТБ, МСФО за 1П 2026; Fenergo — опросы банков 2022 и 2025 (данные поставщика ПО)", APP);
    const hw = (W - 0.25) / 2;
    card(s, L, TOP, hw, 4.0);
    text(s, "Формулы «Мандата»", L + 0.3, TOP + 0.2, hw - 0.6, 0.45, { size: 18, bold: true, color: C.accent1 });
    bullets(s, [
      "Ставки = клиенты × доля с событием представительства × сэкономленные минуты на кейс (включая разбор) ÷ 60 ÷ 1 800 ч",
      "Ущерб = 818 × 4 × доля ВТБ × доля случаев с полномочиями × 0,7 млн ₽",
      "Годовой объём — I кв. 2026 × 4; статистика Банка России не выделяет случаи с полномочиями",
      "Все доли и минуты — гипотезы: их измеряет пилот; расчёт — python scripts/effect_model.py --scenarios",
    ], L + 0.3, TOP + 0.8, hw - 0.6, 3.1, { size: 15, psa: 10 });
    card(s, L + hw + 0.25, TOP, hw, 4.0);
    text(s, "Мировой ориентир — не наша экономия", L + hw + 0.55, TOP + 0.2, hw - 0.6, 0.45, { size: 18, bold: true, color: C.accent4 });
    bullets(s, [
      "1 500–3 000 долл. США — стоимость проверки корпоративного клиента у 54% банков (опрос Fenergo, 2022; данные поставщика ПО)",
      "70% банков теряли клиентов из-за медленного подключения в 2025 году (опрос Fenergo; данные поставщика ПО)",
      "Масштаб западных корпоративных банков несопоставим со сменой подписанта в малом и среднем бизнесе",
    ], L + hw + 0.55, TOP + 0.8, hw - 0.6, 3.1, { size: 15, psa: 10 });
    card(s, L, TOP + 4.25, W, 1.05, { fill: C.text2, line: C.text2 });
    text(s, [
      { text: "Что заменит допущения фактами: ", options: { bold: true, color: C.accent1 } },
      { text: "число событий представительства в год, минуты на кейс по хронометражу 30–50 проверок, доля инцидентов, связанных с полномочиями.", options: {} },
    ], L + 0.3, TOP + 4.25, W - 0.6, 1.05, { size: 15, valign: "middle" });
  }

  // A9–A10 Трудные вопросы жюри
  const QA1 = [
    ["Чем вы лучше проверки статуса МЧД в реестре ФНС?", `Реестр отвечает «действует или отменена». Мы — покрывает ли МЧД эту операцию у этой компании на эту дату, с ЕГРЮЛ, уставом, передоверием и событиями. Статус реестра — одно из ${N.rules} правил`],
    ["Откуда «0 ложных», если данные синтетические?", `Это проверка логики правил. Верхняя граница — ${N.fcUpper}. Реальная доля — стоп-критерий пилота`],
    ["Кто отвечает, если «подтверждено», а это мошенник?", "Решение остаётся за банком. В пилоте теневой режим; мы даём доказательства и журнал, права не выдаём"],
    ["Почему правила, а не машинное обучение?", "Полномочия — вопрос права, а не вероятности. Правила объяснимы и проверяемы; машинное обучение уместно для сигналов риска"],
    ["Зачем тогда ИИ?", "Извлечь положения устава и текстовые полномочия с дословной цитатой. Без модели продукт работает полностью"],
    [`Откуда ${LOSS[0]}–${LOSS[2]} млн ₽?`, "Формула на данных Банка России с тремя явными допущениями. Пилот их измерит"],
    ["Всё на заглушках — что реально работает?", `Ядро, ${RULES}, журнал, мониторинг, удостоверения. Заглушки — источники данных; каждый отчёт сам это показывает`],
    ["Сколько «на разбор» получат сотрудники?", `На корпусе ${N.toHuman}, на реальном потоке — вторая метрика пилота с порогом банка`],
  ];
  const QA2 = [
    ["Почему смена директора не прекращает МЧД?", "Перечень оснований в п. 1 ст. 188 ГК РФ исчерпывающий, ФНС это подтверждает. Новый директор может отменить МЧД"],
    ["Что при конфликте ЕГРЮЛ и МЧД?", "Корневую МЧД подписал не директор по ЕГРЮЛ на дату выдачи — «опровергнуто» (R-EIO-001). Сверить нельзя или наименование расходится — «на разбор». Свежая смена директора — всегда разбор"],
    ["Как с ИБ и ПДн?", "On-prem, без сети, псевдонимизация, удостоверение без ФИО. Оценку соответствия проводит банк"],
    ["Сколько стоит внедрение?", "Пилот — одна операция, 6 недель. Цену назовём после замеров; бизнес-модель — лицензия на контур банка и годовое обновление правил"],
    ["Как масштабируется?", "Новая операция — новые правила и коды полномочий, ядро то же. Формы Банка России по 315-ФЗ — новый адаптер"],
    ["Почему вам можно доверять код?", `${TESTS} проходят (v${N.version}, evidence.json), независимая проверка на Cedar, реестр утверждений: каждое число воспроизводится командой`],
    ["Есть ли юрлицо для пилота?", "Для конкурса не нужно. К пилоту через Акселератор ВТБ — регистрация компании или партнёр-интегратор"],
  ];
  TSIZE = 13;
  [[QA1, "Трудные вопросы жюри (1/2)"], [QA2, "Трудные вопросы жюри (2/2)"]].forEach(([qa, title]) => {
    const s = content(title, "Ответы опираются на CLAIMS_REGISTRY.md и LIMITS.md: каждое число — с командой воспроизведения и границей", APP);
    const rows = [[TH("Вопрос"), TH("Ответ")], ...qa.map(([q, a]) => [TD(q, { bold: true }), TD(a)])];
    s.addTable(rows, tableOpts([4.0, 8.13], TOP, 0.6));
  });
  TSIZE = 14;

  // A11 Нормы
  {
    const s = content("Нормативная база на октябрь 2026 и что она меняет в «Мандате»",
      "Ревизия 12.10.2026 по открытым источникам; это не юридическое заключение — каждую строку проверяет юрист банка. docs/REGULATORY.md, docs/SOURCES.md", APP);
    const rows = [
      [TH("Акт"), TH("С какой даты"), TH("Что меняет")],
      [TD("63-ФЗ в ред. 476-ФЗ", { bold: true }), TD("01.09.2024"), TD("представитель юрлица подписывает своей КЭП и МЧД")],
      [TD("ГК РФ, ст. 186–188", { bold: true }), TD("действует"), TD("срок по умолчанию — год; передоверие; исчерпывающие основания прекращения; каскад")],
      [TD("Приказ Банка России № ОД-2506 от 05.11.2025", { bold: true }), TD("01.01.2026"), TD("обновлённый перечень признаков операций без согласия клиента")],
      [TD("Приказ Минцифры № 1001", { bold: true }), TD("01.02.2026"), TD("СНИЛС и ИНН представителя, КПП, наименование полномочия рядом с кодом")],
      [TD("Кодекс этики в сфере ИИ на финансовом рынке", { bold: true }), TD("2025; ВТБ — 21.08.2026 (CNews)"), TD("прозрачность, человек в контуре, право на пересмотр решения")],
      [TD("315-ФЗ", { bold: true }), TD("01.09.2027"), TD("единая форма МЧД универсальна; Банк России вправе утверждать свои формы")],
    ];
    s.addTable(rows, tableOpts([3.2, 2.6, 6.33], TOP, 0.66));
  }

  // A12 Дорожная карта
  {
    const s = content("Дорожная карта: от онлайн-питча до решения о пилоте", "docs/ROADMAP.md, docs/MVP.md", APP);
    const st = [
      ["До 10.11", "Онлайн-питч", ["сверка формата с официальной XSD", "8–12 интервью и опрос", "видео-демо, критерии трека"]],
      ["16–29.11", "Спринт 2", ["коды из выгрузки ЕСНСИ", "корпус от 300 пакетов, κ Коэна", "прототип проверки КЭП через СКЗИ", "финансовая модель на фактах"]],
      ["03–08.12", "Спринт 3", ["символьная проверка на Cedar", "ГОСТ-подпись удостоверений", "контракт интеграции с ДБО"]],
      ["После 11.12", "Пилот", ["6 недель в теневом режиме", "одна операция", "решение: продолжать или остановить"]],
    ];
    const cw = (W - 3 * 0.22) / 4;
    st.forEach(([d, h, items], i) => {
      const x = L + i * (cw + 0.22);
      card(s, x, TOP, cw, 4.0, { fill: i === 3 ? C.text2 : C.accent6 });
      text(s, d, x + 0.25, TOP + 0.25, cw - 0.5, 0.45, { size: 16, bold: true, color: C.accent1 });
      text(s, h, x + 0.25, TOP + 0.75, cw - 0.5, 0.5, { size: 20, bold: true });
      bullets(s, items, x + 0.25, TOP + 1.45, cw - 0.5, 2.45, { size: 15, psa: 8 });
    });
    card(s, L, TOP + 4.25, W, 1.05, { fill: C.accent1, line: C.accent1 });
    text(s, "Финал 11.12.2026: живое демо и, если банк даст данные, результаты на обезличенных кейсах вместо синтетики", L + 0.3, TOP + 4.25, W - 0.6, 1.05, { size: 17, bold: true, color: C.text1, valign: "middle" });
  }

  await pres.writeFile({ fileName: OUT });
  console.log(`записано: ${OUT} · v${N.version} · ${RULES} · ${TESTS} · ${N.cases} ${plural(N.cases, "кейс", "кейса", "кейсов")} корпуса`);
})().catch((e) => { console.error(e); process.exit(1); });
