// Презентация «Мандата» как код: все числа о продукте берутся из evidence.json.
//
//   node build_deck.js ../../evidence.json ../slides/Mandat_VTB.pptx
//   python finish_deck.py ../slides/Mandat_VTB.pptx      # цвета темы, alt-текст, метаданные
//
// Оформление — в палитре ВТБ (тёмно-синий, кобальт, голубой) со шрифтом Golos Text (ParaType, OFL):
// фирменный VTB Group UI лицензирован только для Группы ВТБ. Графика рисуется в graphics.js.
// Числа рынка и эффекта — из открытых источников и допущений команды; каждое из них
// записано в CLAIMS_REGISTRY.md вместе с источником или командой воспроизведения.
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");
const { buildGraphics } = require("./graphics");

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
    coveragePct: comma(ev.tests.coverage_percent),
    cases: sc.cases,
    kinds: sc.defect_types,
    agree: Math.round(sc.cases * sc.agreement),
    expected,
    kindsBy: { ok: kinds("confirmed"), no: kinds("refuted"), rv: kinds("needs_review"), nd: kinds("insufficient_data") },
    negatives: sc.cases - expected.confirmed,
    falseConfirmed: sc.false_confirmed,
    fcUpper: pct(sc.false_confirmed_rate_upper95),
    fcUpperByType: pct(sc.false_confirmed_rate_upper95_by_defect_type),
    negativeTypes: sc.negative_defect_types,
    recallLow: pct(sc.refute_recall_wilson95[0]),
    coverage: pct(sc.coverage),
    autoRiskUpper: pct(sc.auto_risk_upper95),
    toHuman: `${Math.round(100 * sc.abstention_rate)}%`,
    strictCoverage: pct(ev.synthetic_corpus_strict.coverage),
    holdoutMismatches: ev.synthetic_corpus_holdout.mismatches,
    nRecall: sc.n_for_recall_0_9_pm_0_05,
    chains: ev.subdelegation_property.chains,
    medianMs: `≈${comma(ev.latency.median_ms)} мс`,
    p95Ms: `${comma(ev.latency.p95_ms)} мс`,
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
const DEMOS = `${N.demo} ${plural(N.demo, "демо-кейс", "демо-кейса", "демо-кейсов")}`;
const FUZZ = (() => {
  const text = fs.readFileSync(path.join(__dirname, "..", "..", "tests", "test_redteam_fuzz.py"), "utf8");
  const m = text.match(/assert len\(fuzz_rows\) == (\d+)/);
  if (!m) throw new Error("в tests/test_redteam_fuzz.py не найдено закреплённое число мутаций");
  return Number(m[1]);
})();
const FUZZ_TXT = FUZZ.toLocaleString("ru-RU").replace(/\s/g, " ");

// ---------- Эффект: параметры — src/mandat/data/effect_scenarios.json, их же читает scripts/effect_model.py --scenarios ----------
const EFFECT_RAW = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "src", "mandat", "data", "effect_scenarios.json"), "utf8"));
const EFFECT = { ...EFFECT_RAW, lossPerIncidentMln: EFFECT_RAW.lossPerQuarterMln / EFFECT_RAW.incidentsPerQuarter };
const FTE = [0, 1, 2].map((i) => Math.round((EFFECT.clients * EFFECT.eventShare[i] * EFFECT.minutesSaved[i]) / 60 / EFFECT.hoursPerFte));
const LOSS = [0, 1, 2].map((i) => Math.round(EFFECT.incidentsPerQuarter * 4 * EFFECT.vtbShare[i] * EFFECT.authorityShare[i] * EFFECT.lossPerIncidentMln));
const shares = (arr) => arr.map((x) => Math.round(100 * x)).join(" / ");

const THEME = JSON.parse(fs.readFileSync(path.join(__dirname, "theme.json"), "utf8"));
const HEX = THEME.colors;

// Цвета без слота в теме: оттенки плиток, текст второго плана, статусы вердиктов.
const TINT = { blue: "E6F0FF", blueLine: "C9DCFA", line: "DCE4F0", soft: "F3F6FB", text2: "42557A", muted: "62718F", ice: "CFE3FF" };
const ST = {
  ok: { label: "ПОДТВЕРЖДЕНО", base: HEX.accent3, tint: "E2F4EB", text: "0B6B44", icon: "Check" },
  no: { label: "ОПРОВЕРГНУТО", base: HEX.accent5, tint: "FDE6E7", text: "A3161B", icon: "X" },
  rv: { label: "НА РАЗБОР", base: HEX.accent4, tint: "FFF1D4", text: "7A4E00", icon: "UserSearch" },
  nd: { label: "НЕДОСТАТОЧНО ДАННЫХ", base: HEX.accent6, tint: "E8ECF3", text: "3B4864", icon: "CircleHelp" },
};

(async () => {
  const G = await buildGraphics();
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
  pres.author = "Команда «Мандат»";
  pres.company = "Команда «Мандат»";
  pres.title = "Мандат — единое окно полномочий для бизнес-клиентов банка";
  pres.subject = "Академия Финтеха 2026, трек ВТБ «Финтех без границ»";
  pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
  const C = pres.SchemeColor;

  const L = 0.6, W = 12.133, R = L + W, TOP = 1.72, BOTTOM = 6.72;

  // ---------- Макеты ----------
  pres.defineSlideMaster({
    title: "Обложка",
    background: { data: G.bgDark },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: 1.72, y: 1.5, w: 5.4, h: 1.0, fontSize: 56, bold: true, color: C.background1, align: "left", valign: "middle", margin: 0 }, text: "" } },
    ],
  });
  pres.defineSlideMaster({
    title: "Контент",
    background: { color: HEX.lt2 },
    margin: [0.5, 0.6, 0.6, 0.6],
    objects: [
      { image: { x: 11.66, y: 0.3, w: 0.36, h: 0.324, data: G.mark, altText: "Знак «Мандата»" } },
      { text: { text: "Мандат", options: { x: 12.06, y: 0.28, w: 0.67, h: 0.36, fontSize: 12, bold: true, color: C.text2, align: "right", valign: "middle", margin: 0 } } },
      { placeholder: { options: { name: "kicker", type: "body", x: L, y: 0.3, w: 9.0, h: 0.3, fontSize: 11, bold: true, color: C.accent1, charSpacing: 1.5, align: "left", valign: "middle", margin: 0 }, text: "" } },
      { placeholder: { options: { name: "title", type: "title", x: L, y: 0.64, w: W, h: 0.92, fontSize: 26, bold: true, color: C.text2, align: "left", valign: "top", margin: 0, lineSpacingMultiple: 0.95 }, text: "" } },
      { placeholder: { options: { name: "source", type: "body", x: L, y: 6.86, w: 11.25, h: 0.46, fontSize: 10, color: TINT.muted, align: "left", valign: "middle", margin: 0 }, text: "" } },
    ],
    slideNumber: { x: 12.13, y: 6.95, w: 0.6, h: 0.3, fontSize: 10, color: TINT.muted, align: "right" },
  });
  pres.defineSlideMaster({
    title: "Раздел",
    background: { data: G.bgDark },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: L, y: 2.45, w: 11.5, h: 1.1, fontSize: 40, bold: true, color: C.background1, align: "left", valign: "middle", margin: 0 }, text: "" } },
      { placeholder: { options: { name: "body", type: "body", x: L, y: 3.75, w: 10.5, h: 1.4, fontSize: 18, color: TINT.ice, align: "left", valign: "top", margin: 0 }, text: "" } },
    ],
  });
  pres.defineSlideMaster({
    title: "Финал",
    background: { data: G.bgDark },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: L, y: 1.55, w: 7.0, h: 1.3, fontSize: 36, bold: true, color: C.background1, align: "left", valign: "top", margin: 0 }, text: "" } },
    ],
  });

  // ---------- Помощники ----------
  const shadow = () => ({ type: "outer", color: HEX.dk2, opacity: 0.1, blur: 10, offset: 2, angle: 90 });
  const T = (s, text, x, y, w, h, o = {}) =>
    s.addText(text, {
      x, y, w, h, isTextBox: true, margin: o.margin ?? 0, fontSize: o.size ?? 14, color: o.color ?? C.text1,
      bold: o.bold ?? false, italic: o.italic ?? false, align: o.align ?? "left", valign: o.valign ?? "top",
      paraSpaceAfter: o.psa, lineSpacingMultiple: o.lsm ?? 1.0, charSpacing: o.cs, objectName: o.name,
    });
  function card(s, x, y, w, h, o = {}) {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y, w, h, rectRadius: o.r ?? 0.12,
      fill: { color: o.fill ?? C.background1, transparency: o.transparency },
      line: o.line === null ? { type: "none" } : { color: o.line ?? TINT.line, width: o.lw ?? 0.75 },
      shadow: o.shadow === false ? undefined : shadow(), objectName: o.name ?? "Карточка",
    });
  }
  function tile(s, x, y, d, name, o = {}) {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y, w: d, h: d, rectRadius: d * 0.28, fill: { color: o.fill ?? TINT.blue }, line: { type: "none" }, objectName: "Плитка значка",
    });
    const p = d * 0.22;
    const set = o.icon === "light" ? G.iconLight : o.icon === "sky" ? G.iconSky : G.icon;
    s.addImage({ data: set[name], x: x + p, y: y + p, w: d - 2 * p, h: d - 2 * p, objectName: "Значок" });
  }
  function dot(s, x, y, d, fill, name, iconSet = "light") {
    s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: fill }, line: { type: "none" }, objectName: "Круг значка" });
    const p = d * 0.22;
    const set = iconSet === "light" ? G.iconLight : G.icon;
    s.addImage({ data: set[name], x: x + p, y: y + p, w: d - 2 * p, h: d - 2 * p, objectName: "Значок" });
  }
  // Ширина надписи по метрике Golos Text Bold (em на знак), с запасом на другой рендер.
  const pillTextW = (label, size) =>
    ([...label].reduce((a, ch) => a + (ch === " " ? 0.28 : ch === "·" ? 0.3 : /[0-9]/.test(ch) ? 0.62 : /[A-Z]/.test(ch) ? 0.66 : 0.74), 0) * size) / 72;
  const pillW = (label, size = 10) => 0.38 + pillTextW(label, size) * 1.08 + 0.14;
  function pill(s, x, y, key, o = {}) {
    const st = ST[key];
    const label = o.label ?? st.label;
    const size = o.size ?? 10;
    const h = o.h ?? 0.32;
    const w = o.w ?? pillW(label, size);
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: h / 2, fill: { color: st.tint }, line: { type: "none" }, objectName: `Вердикт: ${st.label}` });
    const d = 0.1;
    s.addShape(pres.shapes.OVAL, { x: x + 0.15, y: y + (h - d) / 2, w: d, h: d, fill: { color: st.base }, line: { type: "none" }, objectName: "Метка статуса" });
    T(s, label, x + 0.33, y, w - 0.4, h, { size, bold: true, color: st.text, valign: "middle", align: o.w ? "left" : "left" });
    return w;
  }
  function arrow(s, x, y, w, color = TINT.blueLine, width = 1.75) {
    s.addShape(pres.shapes.LINE, { x, y, w, h: 0, line: { color, width, endArrowType: "triangle" }, objectName: "Стрелка" });
  }
  function vline(s, x, y, h, color = TINT.blueLine, width = 1.5, dash) {
    s.addShape(pres.shapes.LINE, { x, y, w: 0, h, line: { color, width, dashType: dash }, objectName: "Линия" });
  }
  function bullets(s, items, x, y, w, h, o = {}) {
    s.addText(
      items.map((t, i) => ({ text: t, options: { bullet: { indent: 12 }, breakLine: i < items.length - 1 } })),
      { x, y, w, h, isTextBox: true, margin: 0, fontSize: o.size ?? 13, color: o.color ?? C.text1, paraSpaceAfter: o.psa ?? 5, valign: "top", lineSpacingMultiple: 1.0, objectName: o.name ?? "Список" },
    );
  }
  function band(s, x, y, w, h, text, o = {}) {
    card(s, x, y, w, h, { fill: o.fill ?? C.text2, line: null, shadow: o.shadow ?? true, name: "Плашка вывода" });
    let tx = x + 0.3;
    if (o.icon) {
      const lightTile = (o.tile ?? "1D46A8") === "FFFFFF";
      tile(s, x + 0.22, y + (h - 0.5) / 2, 0.5, o.icon, { fill: o.tile ?? "1D46A8", icon: lightTile ? "cobalt" : "light" });
      tx = x + 0.92;
    }
    T(s, text, tx, y, w - (tx - x) - 0.3, h, { size: o.size ?? 15, bold: o.bold ?? false, color: o.color ?? C.background1, valign: "middle" });
  }
  function slide(master, section, kicker, title, source) {
    const s = pres.addSlide({ masterName: master, sectionTitle: section });
    if (kicker) s.addText(kicker, { placeholder: "kicker" });
    if (title) s.addText(title, { placeholder: "title" });
    if (source) s.addText(source, { placeholder: "source" });
    return s;
  }
  function stat(s, x, y, w, value, label, o = {}) {
    T(s, value, x, y, w, o.vh ?? 0.7, { size: o.size ?? 40, bold: true, color: o.color ?? C.accent1, valign: "bottom" });
    T(s, label, x, y + (o.vh ?? 0.7) + 0.06, w, o.lh ?? 0.7, { size: o.lsize ?? 12, color: o.lcolor ?? TINT.text2 });
  }
  // Таблица приложения: шапка тёмно-синяя, строки с чередованием, один столбец можно выделить.
  function table(s, header, rows, x, y, w, colW, o = {}) {
    const size = o.size ?? 11;
    const hl = o.highlight;
    const head = header.map((t, i) => ({
      text: t,
      options: { bold: true, color: "FFFFFF", fill: { color: i === hl ? HEX.accent1 : HEX.dk2 }, fontSize: size, valign: "middle" },
    }));
    const body = rows.map((r, ri) =>
      r.map((t, i) => ({
        text: t,
        options: {
          fontSize: size, valign: "middle", color: i === hl ? HEX.accent1 : i === 0 ? HEX.dk2 : HEX.dk1,
          bold: i === 0 || i === hl, fill: { color: i === hl ? TINT.blue : ri % 2 ? TINT.soft : "FFFFFF" },
        },
      })),
    );
    const rowH = o.headH ? [o.headH, ...rows.map(() => o.rowH)] : o.rowH;
    s.addTable([head, ...body], {
      x, y, w, colW, border: { type: "solid", pt: 0.75, color: TINT.line }, margin: [3, 6, 3, 6],
      rowH, autoPage: false, objectName: o.name ?? "Таблица",
    });
  }

  // =====================================================================
  // ПИТЧ
  // =====================================================================
  pres.addSection({ title: "Питч" });

  // ---------- 1. Обложка ----------
  {
    const s = pres.addSlide({ masterName: "Обложка", sectionTitle: "Питч" });
    T(s, "Академия Финтеха 2026 · трек ВТБ «Финтех без границ» · задача «Онбординг и проверка полномочий»", L, 0.55, 7.0, 0.55, { size: 12, color: TINT.ice });
    s.addImage({ data: G.markDark, x: L, y: 1.6, w: 0.92, h: 0.83, altText: "Знак «Мандата»" });
    s.addText("Мандат", { placeholder: "title" });
    T(s, "Единое окно полномочий для бизнес-клиентов банка", L, 2.75, 6.6, 0.95, { size: 24, bold: true, color: C.background1 });
    T(s, "Отвечает, вправе ли человек провести операцию от имени компании в этот день, — за миллисекунды и с доказательством до поля документа", L, 3.8, 6.3, 1.0, { size: 15, color: TINT.ice, lsm: 1.1 });
    let px = L;
    for (const k of ["ok", "no", "rv", "nd"]) px += pill(s, px, 5.0, k, { size: 9.5, h: 0.34 }) + 0.12;
    T(s, "Коньков Дмитрий", L, 6.12, 4.0, 0.34, { size: 14, bold: true, color: C.background1 });
    T(s, "капитан команды «Мандат»", L, 6.46, 4.0, 0.3, { size: 12, color: TINT.ice });
    T(s, "Онлайн-питч · ноябрь 2026", 9.0, 6.46, 3.73, 0.3, { size: 12, color: TINT.ice, align: "right" });
    const hw = 5.55;
    s.addImage({ data: G.hero, x: 7.3, y: 0.62, w: hw, h: hw * G.heroRatio, altText: "Схема: шесть источников — ЕГРЮЛ, МЧД, реестр ФНС, передоверие, устав и события — сходятся в один вердикт на дату операции" });
    s.addNotes("[0:00–0:15] Здравствуйте, мы — команда «Мандат». В задаче трека ВТБ «Финтех без границ» первым направлением для бизнеса названы онбординг и проверка полномочий. Мы сделали движок, который за миллисекунды отвечает, вправе ли человек провести операцию от имени компании в конкретный день, и показывает доказательство до поля документа.");
  }

  // ---------- 2. Цена ошибки ----------
  {
    const s = slide("Контент", "Питч", "ЦЕНА ОШИБКИ", "Компании теряют редко, но крупно: ≈0,7 млн ₽ на одну операцию против юрлица",
      "Банк России: обзоры операций без согласия клиентов за I кв. 2026 (cbr.ru, 14.05.2026) и II кв. 2026 (cbr.ru, 04.08.2026; сумма и доля юрлиц — по Интерфаксу и Smart-Lab); fintechacademy.ru — задача трека ВТБ");
    // A. Средняя операция
    card(s, L, TOP, 4.75, 3.78);
    T(s, "I квартал 2026 · операции без согласия клиента", L + 0.3, TOP + 0.22, 4.2, 0.3, { size: 12, bold: true, color: TINT.text2 });
    T(s, "≈0,7 млн ₽", L + 0.3, TOP + 0.55, 4.2, 0.85, { size: 46, bold: true, color: C.accent1, valign: "middle" });
    T(s, "средняя операция против юрлица: 818 операций на 573 млн ₽", L + 0.3, TOP + 1.42, 4.2, 0.5, { size: 12, color: TINT.text2 });
    const bx = L + 0.3, bw = 4.15;
    T(s, "юрлица · ≈0,7 млн ₽", bx, TOP + 2.02, bw, 0.26, { size: 11, bold: true, color: C.text2 });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: bx, y: TOP + 2.3, w: bw, h: 0.24, rectRadius: 0.12, fill: { color: HEX.accent1 }, line: { type: "none" }, objectName: "Столбик: юрлица" });
    T(s, "все клиенты · ≈15 тыс. ₽ — в ≈47 раз меньше", bx, TOP + 2.66, bw, 0.26, { size: 11, bold: true, color: C.text2 });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: bx, y: TOP + 2.94, w: 0.1, h: 0.24, rectRadius: 0.05, fill: { color: TINT.blueLine }, line: { type: "none" }, objectName: "Столбик: все клиенты" });
    T(s, "495,8 тыс. операций на 7,4 млрд ₽", bx, TOP + 3.3, bw, 0.28, { size: 11, color: TINT.muted });
    // B. Рост потерь юрлиц
    const bX = L + 4.75 + 0.25, bW = 3.85;
    card(s, bX, TOP, bW, 3.78);
    T(s, "Потери юрлиц растут, млн ₽", bX + 0.3, TOP + 0.22, bW - 0.6, 0.3, { size: 12, bold: true, color: TINT.text2 });
    s.addChart(pres.charts.BAR, [{ name: "Похищено у юрлиц, млн ₽", labels: ["I кв. 2026", "II кв. 2026"], values: [573, 830] }], {
      x: bX + 0.2, y: TOP + 0.5, w: bW - 0.4, h: 1.95, barDir: "col", barGapWidthPct: 55,
      chartColors: ["9DB8E8", HEX.accent1], showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 14, dataLabelFontBold: true,
      dataLabelColor: HEX.dk2, dataLabelFontFace: THEME.bodyFontFace, dataLabelFormatCode: "#,##0",
      catAxisLabelColor: TINT.text2, catAxisLabelFontSize: 11, catAxisLabelFontFace: THEME.bodyFontFace, valAxisLabelFontFace: THEME.bodyFontFace, catAxisLineShow: false,
      valAxisHidden: true, valAxisMinVal: 0, valAxisMaxVal: 1000, valGridLine: { style: "none" }, catGridLine: { style: "none" },
      showLegend: false, showTitle: false, objectName: "Диаграмма: похищено у юрлиц по кварталам",
    });
    T(s, "доля юрлиц в потерях", bX + 0.3, TOP + 2.5, bW - 0.6, 0.26, { size: 11, color: TINT.text2 });
    T(s, "7,8% → 11,3%", bX + 0.3, TOP + 2.76, bW - 0.6, 0.46, { size: 22, bold: true, color: C.text2, valign: "middle" });
    T(s, "II кв.: «свыше 830 млн ₽», в основном со счетов", bX + 0.3, TOP + 3.26, bW - 0.6, 0.4, { size: 11, color: TINT.muted });
    // C. Возмещение и задача трека
    const cX = bX + bW + 0.25, cW = R - cX;
    card(s, cX, TOP, cW, 1.78);
    T(s, "15,9% и 2,4%", cX + 0.3, TOP + 0.2, cW - 0.6, 0.6, { size: 26, bold: true, color: HEX.accent5, valign: "middle" });
    T(s, "столько возмещено компаниям по счетам и через СБП в I кв.", cX + 0.3, TOP + 0.85, cW - 0.6, 0.7, { size: 12, color: TINT.text2 });
    card(s, cX, TOP + 2.0, cW, 1.78, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
    tile(s, cX + 0.3, TOP + 2.2, 0.46, "Landmark", { fill: "FFFFFF" });
    T(s, "Задача трека ВТБ", cX + 0.88, TOP + 2.2, cW - 1.1, 0.46, { size: 13, bold: true, color: C.text2, valign: "middle" });
    T(s, "«Онбординг и проверка полномочий» — первое направление для бизнеса в задаче «Финтех без границ»", cX + 0.3, TOP + 2.78, cW - 0.6, 0.9, { size: 12, color: C.text1 });
    band(s, L, 5.72, W, 0.95, "Право подписи складывается из трёх источников — МЧД, выписки ЕГРЮЛ и устава — и меняется со временем. Сегодня их сводит вручную сотрудник банка.", { icon: "Layers", size: 15 });
    s.addNotes("[0:15–0:40] Почему это важно банку. По данным Банка России, в первом квартале 2026 года у юрлиц было 818 операций без согласия на 573 миллиона рублей — около 0,7 миллиона на операцию, почти в 50 раз больше средней по всем клиентам. Во втором квартале у компаний похищено уже свыше 830 миллионов, их доля в потерях выросла с 7,8 до 11,3 процента. Возмещают компаниям мало: 15,9 процента по счетам и 2,4 через СБП. А ответ на вопрос о полномочиях складывается из трёх документов и меняется со временем.");
  }

  // ---------- 3. Сценарий захвата ----------
  {
    const s = slide("Контент", "Питч", "СЦЕНАРИЙ ЗАХВАТА", "Захвату хватает пяти рабочих дней, а сверка «на сегодня» не видит событий между обращениями",
      "129-ФЗ, ст. 8 (срок регистрации); ГК РФ, ст. 188; разъяснения ФНС; Forbes и ABN по данным РБК, 22.10.2026 — случай «ЭОС» в изложении компании; Банк России, I кв. 2026");
    const steps = [
      ["День 0", "Заявление в ФНС о смене директора — нередко по подделке"],
      ["+5 рабочих дней", "В ЕГРЮЛ — новый директор"],
      ["Затем", "Новая МЧД на «своего» представителя"],
      ["Затем", "Заявка в банк: доступ к счёту, смена реквизитов"],
      ["Финал", "Платёж, который редко удаётся вернуть"],
    ];
    const gap = 0.22, sw = (W - gap * 4) / 5, sh = 1.55;
    s.addShape(pres.shapes.LINE, { x: L + 0.2, y: TOP + sh / 2, w: W - 0.4, h: 0, line: { color: TINT.blueLine, width: 2 }, objectName: "Линия времени" });
    steps.forEach(([k, t], i) => {
      const x = L + i * (sw + gap);
      const last = i === steps.length - 1;
      card(s, x, TOP, sw, sh, last ? { fill: ST.no.tint, line: "F6B9BC" } : {});
      T(s, k, x + 0.2, TOP + 0.16, sw - 0.4, 0.3, { size: 12, bold: true, color: last ? ST.no.text : C.accent1 });
      T(s, t, x + 0.2, TOP + 0.48, sw - 0.4, sh - 0.55, { size: 12.5, color: last ? ST.no.text : C.text1, bold: last });
    });
    // Реальный случай
    const y2 = TOP + sh + 0.22, h2 = 2.3;
    card(s, L, y2, 5.95, h2);
    tile(s, L + 0.3, y2 + 0.25, 0.5, "Siren", { fill: ST.no.tint });
    T(s, "Реальный случай · октябрь 2026", L + 0.95, y2 + 0.22, 4.7, 0.24, { size: 11, bold: true, color: ST.no.text });
    T(s, "«ЭОС»: 10 дней с чужим гендиректором", L + 0.95, y2 + 0.46, 4.8, 0.32, { size: 14, bold: true, color: C.text2 });
    T(s, "09.10 в ЕГРЮЛ появился неизвестный компании руководитель — по версии компании, через поддельные нотариальные документы и доверенности от имени участников. 19.10 запись исправили. Нотариальная форма решения с 01.09.2024 подделку не исключает.", L + 0.3, y2 + 0.9, 5.35, h2 - 0.98, { size: 12, color: C.text1 });
    // Ловушка
    const tx = L + 5.95 + 0.23, tw = R - tx;
    card(s, tx, y2, tw, h2);
    tile(s, tx + 0.3, y2 + 0.25, 0.5, "Scale");
    T(s, "Ловушка в обе стороны", tx + 0.95, y2 + 0.25, tw - 1.2, 0.26, { size: 14, bold: true, color: C.text2 });
    T(s, "Смена директора не прекращает МЧД — п. 1 ст. 188 ГК РФ, позиция ФНС", tx + 0.95, y2 + 0.52, tw - 1.2, 0.3, { size: 12, bold: true, color: C.accent1 });
    dot(s, tx + 0.36, y2 + 1.08, 0.38, HEX.accent5, "X");
    T(s, "Откажете по старой МЧД — потеряете клиента и день его работы", tx + 0.95, y2 + 1.02, tw - 1.25, 0.5, { size: 13, valign: "middle" });
    dot(s, tx + 0.36, y2 + 1.7, 0.38, HEX.accent4, "TriangleAlert");
    T(s, "Пропустите свежую смену — откроете дверь захвату", tx + 0.95, y2 + 1.64, tw - 1.25, 0.5, { size: 13, valign: "middle" });
    band(s, L, 5.9, W, 0.8, "Нужен ответ на дату операции — с историей событий ЕГРЮЛ и реестра МЧД, а не снимок «на сегодня»", { icon: "History", size: 15, bold: true });
    s.addNotes("[0:40–1:05] Как работает захват. Мошенник подаёт заявление о смене директора, через пять рабочих дней в ЕГРЮЛ новый директор, дальше новая доверенность, заявка в банк и платёж. Это не теория: в октябре у агентства «ЭОС» в ЕГРЮЛ на десять дней появился чужой гендиректор — по версии компании, по поддельным нотариальным документам. Сверка «на сегодня» не видит, что происходит между обращениями клиента. И есть ловушка: смена директора по закону доверенность не прекращает. Откажете по старой МЧД — потеряете клиента, пропустите свежую смену — откроете дверь захвату. Нужен ответ на дату и с контекстом событий.");
  }

  // ---------- 4. Решение ----------
  {
    const s = slide("Контент", "Питч", "РЕШЕНИЕ", "Один вопрос — четыре ответа, и неизвестное никогда не становится «да»",
      "«Мандат» проверяет полномочия, а не личность: идентификацию по 115-ФЗ проводят существующие каналы банка (КЭП, ЕБС, офис)");
    card(s, L, TOP, W, 0.72, { fill: C.text2, line: null });
    const q = (t, hl) => ({ text: t, options: hl ? { color: HEX.accent2, bold: true } : {} });
    s.addText([q("Вправе ли "), q("X", 1), q(" совершить операцию "), q("O", 1), q(" от имени компании "), q("Y", 1), q(" на дату "), q("t", 1), q("?")],
      { x: L, y: TOP, w: W, h: 0.72, isTextBox: true, margin: 0, fontSize: 21, bold: true, color: C.background1, align: "center", valign: "middle", objectName: "Вопрос" });
    const items = [
      ["ok", "Все обязательные проверки пройдены", "рекомендацию роли и подписанное удостоверение"],
      ["no", "Найдено нарушение", "звено цепочки, норму закона и что запросить у клиента"],
      ["rv", "Спорная ситуация", "досье для сотрудника, приоритет P1–P3 и срок разбора"],
      ["nd", "Источник не ответил или нет документа", "список недостающего — вместо «да»"],
    ];
    const gap = 0.25, cw = (W - gap * 3) / 4, cy = TOP + 0.95, ch = 2.66;
    items.forEach(([k, head, gets], i) => {
      const x = L + i * (cw + gap);
      card(s, x, cy, cw, ch);
      dot(s, x + 0.25, cy + 0.22, 0.5, ST[k].base, ST[k].icon);
      pill(s, x + 0.25, cy + 0.86, k, { size: 9.5, h: 0.3 });
      T(s, head, x + 0.25, cy + 1.26, cw - 0.5, 0.56, { size: 14, bold: true, color: C.text2 });
      T(s, "Банк получает", x + 0.25, cy + 1.84, cw - 0.5, 0.22, { size: 10.5, bold: true, color: TINT.muted });
      T(s, gets, x + 0.25, cy + 2.06, cw - 0.5, 0.56, { size: 12, color: C.text1 });
    });
    const steps = [
      ["01", "Принять", "МЧД XML, ЕГРЮЛ, устав, бумажная доверенность"],
      ["02", "Проверить", `граф полномочий на дату, ${RULES} с нормой`],
      ["03", "Решить", "вердикт, доказательства, следующий шаг"],
      ["04", "Следить", "события ЕГРЮЛ и реестра МЧД"],
    ];
    const py = cy + ch + 0.25, ph = BOTTOM - py;
    steps.forEach(([n, head, t], i) => {
      const x = L + i * (cw + gap);
      card(s, x, py, cw, ph, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
      T(s, n, x + 0.22, py + 0.1, 0.5, 0.34, { size: 17, bold: true, color: C.accent1 });
      T(s, head, x + 0.72, py + 0.1, cw - 0.9, 0.34, { size: 14, bold: true, color: C.text2, valign: "middle" });
      T(s, t, x + 0.22, py + 0.48, cw - 0.4, ph - 0.52, { size: 12, color: C.text1 });
      if (i < steps.length - 1) arrow(s, x + cw + 0.03, py + ph / 2, gap - 0.06, HEX.accent1, 1.5);
    });
    s.addNotes("[1:05–1:30] Решение — один вопрос и четыре ответа. Подтверждено — с рекомендацией роли. Опровергнуто — с нормой и звеном цепочки. На разбор — человеку с готовым досье и сроком. Недостаточно данных — список недостающего. Главное правило: неизвестное никогда не становится «да». Недоступный реестр — это не «действует», а «недостаточно данных».");
  }

  // ---------- 5. Доказательность ----------
  {
    const s = slide("Контент", "Питч", "ДОКАЗАТЕЛЬНОСТЬ", "Каждый вердикт доказан: поле МЧД, запись ЕГРЮЛ, норма закона",
      "Демо-кейсы 14, 05 и 11 (mandat demo); данные вымышлены, отметка юриста об уставе в кейсе 14 синтетическая. Реестр МЧД и проверка КЭП на стенде — заглушки, и это написано в каждом отчёте");
    const cw = 7.45, ch = BOTTOM - TOP;
    card(s, L, TOP, cw, ch);
    T(s, "Кейс 14 · синтетическая отметка юриста об уставе", L + 0.3, TOP + 0.25, 4.8, 0.28, { size: 11, bold: true, color: TINT.muted });
    pill(s, L + cw - 0.3 - pillW("ПОДТВЕРЖДЕНО"), TOP + 0.22, "ok");
    T(s, "Платёжное поручение на 480 000 ₽ · 20.10.2026", L + 0.3, TOP + 0.62, cw - 0.6, 0.34, { size: 16, bold: true, color: C.text2 });
    T(s, "Представитель Лебедева И. П. по МЧД от директора Петрова П. П.", L + 0.3, TOP + 0.98, cw - 0.6, 0.3, { size: 12, color: TINT.text2 });
    const rows = [
      ["ok", "Check", "МЧД подписал директор по ЕГРЮЛ на дату выдачи", "ЕГРЮЛ · ГРН 2247700123456"],
      ["ok", "Check", "МЧД действует в реестре, дата операции в сроке", "XPath …/СвДов/@СрокДейст"],
      ["ok", "Check", "Полномочие покрывает подписание платёжных поручений", "XPath …/СвПолн/МашПолн[1]"],
      ["rv", "Flag", "Директор сменился 01.05.2026 — сигнал, а не отказ: МЧД не отменена", "п. 1 ст. 188 ГК РФ"],
    ];
    rows.forEach(([k, ic, t, ref], i) => {
      const y = TOP + 1.48 + i * 0.7;
      s.addShape(pres.shapes.LINE, { x: L + 0.3, y: y - 0.08, w: cw - 0.6, h: 0, line: { color: TINT.line, width: 0.75 }, objectName: "Разделитель" });
      dot(s, L + 0.3, y + 0.08, 0.4, ST[k].base, ic);
      T(s, t, L + 0.88, y, 3.85, 0.56, { size: 13, color: C.text1, valign: "middle" });
      card(s, L + 4.82, y + 0.1, cw - 5.12, 0.38, { fill: TINT.blue, line: null, shadow: false, r: 0.08, name: "Ссылка на доказательство" });
      T(s, ref, L + 4.92, y + 0.1, cw - 5.3, 0.38, { size: 10, bold: true, color: C.accent1, valign: "middle" });
    });
    T(s, "Хеш набора правил и входов — в отчёте: одинаковые документы дают одинаковый хеш. Решение пишется в журнал.", L + 0.3, TOP + 4.32, cw - 0.6, 0.55, { size: 11.5, color: TINT.muted });
    const rx = L + cw + 0.28, rw = R - rx;
    T(s, "Та же компания — другие факты", rx, TOP, rw, 0.32, { size: 14, bold: true, color: C.text2 });
    const alt = [
      ["rv", "НА РАЗБОР · P1 · 4 Ч", "Директор сменился 19 дней назад", "Свежая смена — первый шаг возможного захвата: уточнить у нового директора, при признаках — в антифрод"],
      ["no", "ОПРОВЕРГНУТО", "Основную МЧД отменили, осталось передоверие", "С прекращением доверенности теряет силу и передоверие — п. 3 ст. 188 ГК РФ"],
    ];
    alt.forEach(([k, lab, head, t], i) => {
      const y = TOP + 0.45 + i * 2.27, h = 2.08;
      card(s, rx, y, rw, h);
      T(s, head, rx + 0.28, y + 0.22, rw - 0.56, 0.6, { size: 14, bold: true, color: C.text2 });
      pill(s, rx + 0.28, y + 0.84, k, { label: lab, size: 9.5 });
      T(s, t, rx + 0.28, y + 1.28, rw - 0.56, 0.75, { size: 12, color: C.text1 });
    });
    s.addNotes("[1:30–2:00] Так выглядит вердикт. Директор, выдавший доверенность, сменился почти полгода назад, но доверенность не отменена — по ГК она действует. Когда юрист отметил, что устав операцию не ограничивает, «Мандат» подтверждает и ставит сигнал, а не отказывает; в демо эта отметка синтетическая, без неё тот же кейс уходит на разбор. Каждая строка — с доказательством: ГРН записи ЕГРЮЛ, путь до поля МЧД, норма. Та же компания, но директор сменился 19 дней назад, — на разбор с приоритетом P1 и сроком четыре часа. А если отменили основную доверенность, передоверие теряет силу — опровергнуто.");
  }

  // ---------- 6. Мониторинг ----------
  {
    const s = slide("Контент", "Питч", "НЕПРЕРЫВНЫЙ КОНТРОЛЬ", "После выдачи прав «Мандат» продолжает следить: признаки захвата видны до платежа",
      "Сценарии examples/events/takeover.json и poa_revoked.json; команды mandat monitor и mandat attest-verify; статусы — W3C Bitstring Status List v1.0; события — Standard Webhooks");
    const lw = 6.55, h = BOTTOM - TOP;
    card(s, L, TOP, lw, h);
    tile(s, L + 0.3, TOP + 0.25, 0.5, "Radar");
    T(s, "Демо: три события за 2 ч 55 мин", L + 0.95, TOP + 0.25, lw - 1.2, 0.5, { size: 15, bold: true, color: C.text2, valign: "middle" });
    const ev3 = [["09:10", "в ЕГРЮЛ — новый директор"], ["11:40", "новая МЧД на нового представителя"], ["12:05", "заявка на смену контактного телефона"]];
    vline(s, L + 0.72, TOP + 1.0, 1.1, TINT.blueLine, 2);
    ev3.forEach(([t, e], i) => {
      const y = TOP + 0.88 + i * 0.55;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: L + 0.3, y, w: 0.84, h: 0.36, rectRadius: 0.18, fill: { color: HEX.dk2 }, line: { type: "none" }, objectName: "Время события" });
      T(s, t, L + 0.3, y, 0.84, 0.36, { size: 12, bold: true, color: C.background1, align: "center", valign: "middle" });
      T(s, e, L + 1.35, y - 0.02, lw - 1.6, 0.4, { size: 13.5, valign: "middle" });
    });
    const ay = TOP + 2.55, ah = h - 2.8;
    card(s, L + 0.3, ay, lw - 0.6, ah, { fill: ST.no.tint, line: "F6B9BC", shadow: false });
    dot(s, L + 0.5, ay + 0.2, 0.42, HEX.accent5, "Siren");
    T(s, "Тревога «возможный захват» · высокий риск", L + 1.08, ay + 0.2, lw - 1.6, 0.42, { size: 14, bold: true, color: ST.no.text, valign: "middle" });
    bullets(s, [
      "банку — рекомендация приостановить автоматическую выдачу прав по компании",
      "передать кейс в антифрод и уведомить прежних подписантов",
      "удостоверения компании — в статус «приостановлено»",
    ], L + 0.55, ay + 0.78, lw - 1.1, ah - 0.88, { size: 12.5, color: C.text1, psa: 4 });
    const rx = L + lw + 0.25, rw = R - rx, gap = 0.18, ch = (h - 2 * gap) / 3;
    const cards = [
      ["Unlink", "Отменили основную МЧД", "удостоверения по всей цепочке передоверия — «отозвано»; банку — рекомендация приостановить права (п. 3 ст. 188 ГК РФ)"],
      ["UserRound", "Сменился руководитель", "его собственные удостоверения без МЧД — «отозвано»; права по МЧД остаются: смена директора их не прекращает"],
      ["ShieldCheck", "Проверка без доступа к базе", "интернет-банк и антифрод сами видят статус удостоверения; права в ДБО меняет банк, а не «Мандат»"],
    ];
    cards.forEach(([ic, head, t], i) => {
      const y = TOP + i * (ch + gap);
      card(s, rx, y, rw, ch);
      tile(s, rx + 0.25, y + 0.22, 0.46, ic);
      T(s, head, rx + 0.85, y + 0.22, rw - 1.05, 0.46, { size: 14, bold: true, color: C.text2, valign: "middle" });
      T(s, t, rx + 0.25, y + 0.78, rw - 0.5, ch - 0.85, { size: 12, color: C.text1 });
    });
    s.addNotes("[2:00–2:30] Вторая половина продукта — постоянный контроль. Демо: за три часа смена директора, новая доверенность и заявка на смену контактного телефона. «Мандат» связывает три события в тревогу «возможный захват»: рекомендует приостановить автоматическую выдачу прав и передать кейс в антифрод, а сам переводит удостоверения компании в статус «приостановлено». Если отменили основную доверенность, удостоверения по всей цепочке отзываются. Права в интернет-банке меняет банк по своей политике. Здесь включаем запись демо.");
  }

  // ---------- 7. Качество ----------
  {
    const s = slide("Контент", "Питч", "КАЧЕСТВО", `Ноль ложных «подтверждено» на ${N.negatives} кейсах, где подтверждать нельзя`,
      `Воспроизводится: mandat eval --per-defect 6; pytest tests/test_properties.py tests/test_cedar.py tests/test_redteam_fuzz.py; python scripts/bench.py → evidence.json от ${N.asOf}, v${N.version}: ${TESTS} проходят`);
    const sw = 5.35, rows = [
      [`${N.falseConfirmed} из ${N.negatives}`, `ложных «подтверждено»; верхняя граница 95% — ${N.fcUpper}`],
      [`${N.agree} / ${N.cases}`, `совпадение с ожидаемым вердиктом, ${N.kinds} видов ситуаций; на отложенном seed — ${N.holdoutMismatches} расхождений`],
      [FUZZ_TXT, "мутаций сценариев: ни одного ложного «подтверждено» и ни одного падения"],
      [String(N.chains), "цепочки передоверия: решения совпали с независимой реализацией на Cedar"],
      [N.medianMs, `медиана на кейс с разбором документов, без сети; 95% кейсов — быстрее ${N.p95Ms}`],
    ];
    const rh = 0.7, rg = 0.12;
    rows.forEach(([v, t], i) => {
      const y = TOP + i * (rh + rg);
      card(s, L, y, sw, rh);
      T(s, v, L + 0.22, y, 1.75, rh, { size: 22, bold: true, color: i === 0 ? HEX.accent3 : C.accent1, valign: "middle" });
      T(s, t, L + 2.05, y, sw - 2.25, rh, { size: 11.5, color: C.text1, valign: "middle" });
    });
    const cx = L + sw + 0.25, cw = R - cx;
    card(s, cx, TOP, cw, 2.86);
    T(s, `Ожидаемые вердикты ${N.cases} проверочных кейсов`, cx + 0.3, TOP + 0.18, cw - 0.6, 0.3, { size: 12, bold: true, color: TINT.text2 });
    s.addChart(pres.charts.BAR, [{ name: "Кейсов", labels: ["ПОДТВЕРЖДЕНО", "ОПРОВЕРГНУТО", "НА РАЗБОР", "НЕДОСТ. ДАННЫХ"], values: [N.expected.confirmed, N.expected.refuted, N.expected.needs_review, N.expected.insufficient_data] }], {
      x: cx + 0.15, y: TOP + 0.5, w: cw - 0.3, h: 2.28, barDir: "bar", barGapWidthPct: 45,
      chartColors: [HEX.accent3, HEX.accent5, HEX.accent4, HEX.accent6], showValue: true, dataLabelPosition: "outEnd",
      dataLabelFontSize: 12, dataLabelFontBold: true, dataLabelColor: HEX.dk1, dataLabelFontFace: THEME.bodyFontFace,
      catAxisLabelColor: TINT.text2, catAxisLabelFontSize: 10.5, catAxisLabelFontFace: THEME.bodyFontFace, valAxisLabelFontFace: THEME.bodyFontFace, catAxisOrientation: "maxMin", catAxisLineShow: false,
      valAxisHidden: true, valAxisMinVal: 0, valAxisMaxVal: 150, valGridLine: { style: "none" }, catGridLine: { style: "none" },
      showLegend: false, showTitle: false, objectName: "Диаграмма: ожидаемые вердикты корпуса",
    });
    const ey = TOP + 2.86 + 0.2, eh = 1.04;
    card(s, cx, ey, cw, eh, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
    tile(s, cx + 0.25, ey + 0.27, 0.5, "Microscope", { fill: "FFFFFF" });
    T(s, [
      { text: "Сверка с внешними эталонами, 27.10.2026: ", options: { bold: true, color: HEX.dk2 } },
      { text: "каноническая запись JCS (RFC 8785, 20 000 документов), дерево Меркла (RFC 6962), подпись — векторы W3C eddsa-jcs-2022: совпало" },
    ], cx + 0.9, ey, cw - 1.1, eh, { size: 12, valign: "middle" });
    band(s, L, 5.92, W, 0.8, `Это синтетический корпус: проверка логики правил, а не точность на документах банка. ${N.toHuman} кейсов уходит человеку — без проверки КЭП и разметки устава стенд не говорит «да». Реальная доля ошибок — стоп-критерий пилота.`, { icon: "TriangleAlert", size: 13 });
    s.addNotes(`[2:30–3:00] Качество. Главная ошибка, которую нельзя допускать, — ложное «подтверждено». На ${N.negatives} синтетических кейсах, где подтверждать нельзя, таких ошибок ноль, верхняя граница 95% — ${N.fcUpper.replace("%", " процента")}. ${FUZZ} мутаций сценариев тоже не дали ни одного ложного «да». Ключевое свойство — передоверие не расширяет полномочия — проверено на ${N.chains} цепочках и совпало с независимой реализацией на Cedar. Реализацию стандартов сверили с внешними эталонами. Это синтетика, проверка логики правил. Точность на реальных пакетах измерит пилот.`);
  }

  // ---------- 8. ИИ под контролем ----------
  {
    const s = slide("Контент", "Питч", "ИИ ПОД КОНТРОЛЕМ", "ИИ советует, но не решает: вердикт выносят правила, спорное — человек",
      "Кодекс этики в сфере ИИ на финансовом рынке (информационное письмо Банка России № ИН-016-13/91 от 09.07.2025); CNews, 21.08.2026; 243-ФЗ от 26.07.2026, ст. 5 и 13; ADR-001, ADR-005; OWASP Top 10 for LLM Applications 2025");
    const flow = [
      ["FileText", "Документ", "устав, текстовое полномочие"],
      ["Cpu", "Модель в контуре банка", "предлагает факт с дословной цитатой"],
      ["Filter", "Шлюз цитат", "нет цитаты в документе — нет факта"],
      ["Gavel", `${RULES} с нормой`, "выносят вердикт"],
      ["UserCheck", "Человек", "разбирает спорное; клиент может оспорить"],
    ];
    const gap = 0.3, fw = (W - gap * 4) / 5, fh = 2.0;
    flow.forEach(([ic, head, t], i) => {
      const x = L + i * (fw + gap);
      const hl = i === 3;
      card(s, x, TOP, fw, fh, hl ? { fill: C.text2, line: null } : {});
      tile(s, x + 0.2, TOP + 0.2, 0.48, ic, hl ? { fill: "1D46A8", icon: "light" } : {});
      T(s, head, x + 0.2, TOP + 0.76, fw - 0.36, 0.5, { size: 13.5, bold: true, color: hl ? C.background1 : C.text2 });
      T(s, t, x + 0.2, TOP + 1.28, fw - 0.36, 0.68, { size: 12, color: hl ? TINT.ice : C.text1 });
      if (i < flow.length - 1) arrow(s, x + fw + 0.04, TOP + fh / 2, gap - 0.08, HEX.accent1, 1.75);
    });
    const by = TOP + fh + 0.25;
    card(s, L, by, W, 0.62, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
    T(s, [
      { text: "В логике Кодекса этики в сфере ИИ на финансовом рынке (Банк России, 2025). ", options: { bold: true, color: HEX.dk2 } },
      { text: "По сообщениям СМИ, ВТБ присоединился к Кодексу в августе 2026" },
    ], L + 0.3, by, W - 0.6, 0.62, { size: 13, valign: "middle" });
    const py = by + 0.62 + 0.25, ph = BOTTOM - py, pg = 0.25, pw = (W - pg * 3) / 4;
    const pr = [
      ["Eye", "Прозрачность", "у каждого вывода — норма и ссылка на поле документа; отчёт сам пишет, где на стенде заглушка"],
      ["Users", "Человек в контуре", "человекоцентричность: спорное решает сотрудник, автоматический вердикт клиент может оспорить у человека"],
      ["Lock", "Безопасность", "модель on-prem и по умолчанию выключена — без неё продукт работает полностью; ПДн псевдонимизированы"],
      ["Landmark", "Суверенность", "провайдер сменный: по 243-ФЗ с 01.03.2027 банкам могут предписать национальные модели"],
    ];
    pr.forEach(([ic, head, t], i) => {
      const x = L + i * (pw + pg);
      card(s, x, py, pw, ph);
      tile(s, x + 0.2, py + 0.16, 0.4, ic);
      T(s, head, x + 0.7, py + 0.16, pw - 0.85, 0.4, { size: 13.5, bold: true, color: C.text2, valign: "middle" });
      T(s, t, x + 0.2, py + 0.66, pw - 0.38, ph - 0.7, { size: 12, color: C.text1 });
    });
    s.addNotes("[3:00–3:20] Про ИИ. Модель только предлагает факт из устава, и только с дословной цитатой. Нет цитаты — нет факта. Вердикт выносят правила со ссылкой на норму, спорное решает человек, а клиент может оспорить автоматический вердикт. Модель работает в контуре банка, по умолчанию выключена, провайдер сменный — это важно с учётом закона об ИИ. Это логика Кодекса этики в сфере ИИ на финансовом рынке; по сообщениям СМИ, ВТБ присоединился к нему в августе.");
  }

  // ---------- 9. Эффект ----------
  {
    const s = slide("Контент", "Питч", "ЭФФЕКТ ДЛЯ ВТБ", "Эффект в двух валютах: время сотрудников и предотвращённый ущерб",
      "ВТБ, МСФО за 1П 2026 (≈1,8 млн предприятий и предпринимателей; пересказ пресс-релиза, altapress.ru; первичный источник не открыт); Банк России, I–II кв. 2026. Доли и минуты — допущения команды; расчёт: python scripts/effect_model.py --scenarios");
    const gap = 0.25, cw = (W - gap) / 2, ch = 3.95;
    const charts = [
      ["Users", "Высвобождение нагрузки, ставок в год", FTE, `≈${comma(EFFECT.clients / 1e6)} млн предприятий и ИП × доля с событием представительства (${shares(EFFECT.eventShare)}%) × минуты на кейс с разбором (${EFFECT.minutesSaved.join(" / ")}) ÷ 60 ÷ ${EFFECT.hoursPerFte.toLocaleString("ru-RU")} ч на ставку`, HEX.accent1],
      ["ShieldCheck", "Предотвращённый ущерб, млн ₽ в год", LOSS, `${EFFECT.incidentsPerQuarter} операций против юрлиц за I кв. × 4 × доля ВТБ (${shares(EFFECT.vtbShare)}%) × доля случаев с полномочиями (${shares(EFFECT.authorityShare)}%) × ${comma(EFFECT.lossPerIncidentMln)} млн ₽`, HEX.accent3],
    ];
    charts.forEach(([ic, head, vals, formula, color], i) => {
      const x = L + i * (cw + gap);
      card(s, x, TOP, cw, ch);
      tile(s, x + 0.28, TOP + 0.22, 0.46, ic);
      T(s, head, x + 0.88, TOP + 0.22, cw - 1.1, 0.46, { size: 14, bold: true, color: C.text2, valign: "middle" });
      s.addChart(pres.charts.BAR, [{ name: head, labels: EFFECT.names, values: vals }], {
        x: x + 0.25, y: TOP + 0.75, w: cw - 0.5, h: 2.15, barDir: "col", barGapWidthPct: 70,
        chartColors: [color], showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 14, dataLabelFontBold: true,
        dataLabelColor: HEX.dk2, dataLabelFontFace: THEME.bodyFontFace,
        catAxisLabelColor: TINT.text2, catAxisLabelFontSize: 11, catAxisLabelFontFace: THEME.bodyFontFace, valAxisLabelFontFace: THEME.bodyFontFace, catAxisLineShow: false,
        valAxisHidden: true, valAxisMinVal: 0, valAxisMaxVal: Math.ceil((Math.max(...vals) * 1.22) / 10) * 10,
        valGridLine: { style: "none" }, catGridLine: { style: "none" }, showLegend: false, showTitle: false,
        objectName: `Диаграмма: ${head}`,
      });
      T(s, formula, x + 0.28, TOP + 2.98, cw - 0.56, 0.9, { size: 11, color: TINT.text2 });
    });
    band(s, L, TOP + ch + 0.25, W, BOTTOM - (TOP + ch + 0.25), "Сценарии, а не прогноз: доли и минуты — гипотезы команды, первые две недели пилота заменят их фактами ВТБ. База ущерба консервативна: во II кв. 2026 у юрлиц похищено свыше 830 млн ₽ против 573 млн ₽ в I кв.", { icon: "Target", size: 13.5 });
    s.addNotes(`[3:20–3:45] Эффект в двух валютах. Операционный: при 1,8 миллиона клиентов ВТБ — предприятий и предпринимателей — от ${FTE[0]} до ${FTE[2]} ставок в год. Риск: от ${LOSS[0]} до ${LOSS[2]} миллионов рублей предотвращённого ущерба в год. Обе формулы на слайде, все допущения подписаны. Это сценарии, а не обещание: первые две недели пилота заменяют допущения фактами банка. И база консервативна: во втором квартале потери компаний выросли.`);
  }

  // ---------- 10. Почему сейчас ----------
  {
    const s = slide("Контент", "Питч", "ПОЧЕМУ СЕЙЧАС", "МЧД стало втрое больше, а правила для банков меняются каждые полгода",
      "63-ФЗ в ред. 476-ФЗ; Приказ Банка России № ОД-2506; Приказ Минцифры № 1001; ФНС, 27.07.2026; emchd.ru, 10.2024; 243-ФЗ; 210-ФЗ от 26.06.2026; Указание Банка России № 7220-У; 315-ФЗ; CNews, 12.02.2026 — комплект МЧД и КЭП");
    const lw = 7.7, h = BOTTOM - TOP;
    card(s, L, TOP, lw, h);
    const ev8 = [
      ["01.09.2024", "МЧД обязательна для представителей юрлиц", 0],
      ["01.01.2026", "12 признаков операций без согласия вместо 6 — Приказ № ОД-2506", 0],
      ["01.02.2026", "новые реквизиты МЧД: СНИЛС и ИНН представителя — Приказ № 1001", 0],
      ["01.03.2026", "реестр МЧД ищет по номеру и ИНН доверителя и представителя", 0],
      ["27.07.2026", "в реестре ФНС зарегистрирована пятимиллионная МЧД", 0],
      ["01.09.2026", "в силе закон об ИИ (243-ФЗ) и первые нормы антифрод-пакета (210-ФЗ)", 0],
      ["01.10.2026", "изменения 821-П о защите информации при переводах (№ 7220-У)", 0],
      ["01.09.2027", "единая форма МЧД становится основной (315-ФЗ)", 1],
    ];
    const lx = L + 1.62, step = (h - 0.5) / ev8.length;
    vline(s, lx + 0.07, TOP + 0.38, step * (ev8.length - 1), TINT.blueLine, 2);
    ev8.forEach(([d, t, fut], i) => {
      const y = TOP + 0.25 + i * step;
      T(s, d, L + 0.25, y, 1.25, 0.28, { size: 12, bold: true, color: fut ? HEX.accent2 : C.accent1, align: "right", valign: "middle" });
      s.addShape(pres.shapes.OVAL, { x: lx, y: y + 0.07, w: 0.14, h: 0.14, fill: { color: fut ? "FFFFFF" : HEX.accent1 }, line: { color: fut ? HEX.accent2 : HEX.accent1, width: 1.5 }, objectName: "Точка шкалы" });
      T(s, t, lx + 0.35, y - 0.04, lw - (lx - L) - 0.55, 0.36, { size: 12.5, color: C.text1, valign: "middle", bold: fut === 1 });
    });
    const rx = L + lw + 0.25, rw = R - rx, gap = 0.2, ch = (h - 2 * gap) / 3;
    const st = [
      ["×3", "МЧД в реестре ФНС за два года: ≈1,6 млн (10.2024) → более 5 млн (07.2026)"],
      ["1,8 млн", "предприятий и предпринимателей у ВТБ — у каждого есть подписанты, которые меняются"],
      ["10 МЧД + КЭП", "комплект для представителя в «ВТБ Бизнес» с февраля 2026: поток доверенностей в банк растёт"],
    ];
    st.forEach(([v, t], i) => {
      const y = TOP + i * (ch + gap);
      card(s, rx, y, rw, ch);
      T(s, v, rx + 0.28, y + 0.12, rw - 0.56, 0.62, { size: i === 2 ? 26 : 34, bold: true, color: C.accent1, valign: "middle" });
      T(s, t, rx + 0.28, y + 0.76, rw - 0.56, ch - 0.82, { size: 11.5, color: TINT.text2 });
    });
    s.addNotes("[3:45–4:00] Почему сейчас. МЧД стали обязательными в сентябре 2024 года, и за два года их стало втрое больше — уже более пяти миллионов. С февраля действуют новые реквизиты, с марта реестр ищет доверенность только по трём параметрам, Банк России расширил признаки операций без согласия, в сентябре вступили в силу закон об ИИ и первые нормы нового антифрод-пакета, а с сентября 2027 года единая форма МЧД станет основной. Банку нужен движок, в котором нормы меняются данными, а не релизом.");
  }

  // ---------- 11. Мировая практика ----------
  {
    const s = slide("Контент", "Питч", "МИРОВАЯ ПРАКТИКА", "Мир движется к той же модели: полномочие представителя проверяют на дату и с доказательством",
      "FATF, Рекомендация 10, п. 4 пояснительной записки (cfatf-gafic.org); Регламент (ЕС) 2024/1624, ст. 22 и 90; AMLA, финальный отчёт по RTS к ст. 28(1) AMLR, 30.09.2026; Директива (ЕС) 2025/25 (EUR-Lex); W3C, 15.05.2025; RFC 9162; CNCF; gleif.org");
    const lw = 5.9, hy = TOP, gy = TOP + 0.42;
    T(s, "Требования к банкам", L, hy, lw, 0.32, { size: 13, bold: true, color: TINT.text2 });
    const reqs = [
      ["FATF · Рекомендация 10", "проверить, что лицо, действующее от имени клиента, уполномочено, и установить его личность"],
      ["ЕС · Регламент 2024/1624", "с 10.07.2027 — сведения о любом лице, действующем от имени клиента; финальный проект RTS AMLA по CDD передан Еврокомиссии 01.10.2026"],
      ["ЕС · Директива 2025/25", "выписка о компании ЕС: кто представляет компанию и действуют ли по одному или совместно; цифровая доверенность ЕС — с 31.07.2028"],
    ];
    const rh = 1.22, rg = 0.1;
    reqs.forEach(([head, t], i) => {
      const y = gy + i * (rh + rg);
      card(s, L, y, lw, rh);
      tile(s, L + 0.22, y + 0.22, 0.46, "Globe");
      T(s, head, L + 0.82, y + 0.18, lw - 1.0, 0.3, { size: 13, bold: true, color: C.text2 });
      T(s, t, L + 0.82, y + 0.48, lw - 1.0, rh - 0.52, { size: 11.5, color: C.text1 });
    });
    const rx = L + lw + 0.25, rw = R - rx;
    T(s, "Открытые стандарты в основе «Мандата»", rx, hy, rw, 0.32, { size: 13, bold: true, color: TINT.text2 });
    const std = [
      ["BadgeCheck", "W3C VC 2.0", "удостоверение проверки со списком статусов W3C"],
      ["Layers", "RFC 9162", "журнал: доказательство одной записи без выдачи журнала"],
      ["GitCompare", "Cedar · CNCF", "вторая независимая реализация правил передоверия"],
      ["Webhook", "Standard Webhooks", "подписанные события для антифрода банка"],
      ["IdCard", "GLEIF vLEI", "ориентир: роль представителя как удостоверение (ISO 17442-3)"],
      ["ShieldAlert", "OWASP LLM Top 10", "риски советника-ИИ разобраны по списку 2025 года"],
    ];
    const tg = 0.15, tw = (rw - tg) / 2;
    std.forEach(([ic, head, t], i) => {
      const x = rx + (i % 2) * (tw + tg), y = gy + Math.floor(i / 2) * (rh + rg);
      card(s, x, y, tw, rh);
      tile(s, x + 0.2, y + 0.2, 0.42, ic);
      T(s, head, x + 0.72, y + 0.2, tw - 0.85, 0.42, { size: 12.5, bold: true, color: C.text2, valign: "middle" });
      T(s, t, x + 0.2, y + 0.68, tw - 0.38, rh - 0.72, { size: 11, color: C.text1 });
    });
    const by = gy + 3 * rh + 2 * rg + 0.15;
    band(s, L, by, W, BOTTOM - by, "Ось «по одному или совместно» из Директивы 2025/25 — правило R-JNT-001; «объём полномочия» из проекта RTS AMLA — правила покрытия, устава и совместных полномочий", { icon: "Waypoints", size: 12.5, fill: TINT.blue, color: C.text2, tile: "FFFFFF", shadow: false });
    s.addNotes("[4:00–4:15] Мировая практика. FATF и новый регламент ЕС требуют проверять, что представитель клиента уполномочен, а проект стандартов AMLA — ещё и объём полномочия. ЕС вводит выписку о компании, где видно, действуют ли представители по одному или совместно, и цифровую доверенность. «Мандат» отвечает именно на этот вопрос и построен на открытых стандартах: удостоверения в модели W3C, журнал по RFC 9162, независимая проверка на Cedar.");
  }

  // ---------- 12. Встройка ----------
  {
    const s = slide("Контент", "Питч", "ВСТРОЙКА", "Встаёт в контур ВТБ on-prem: ядро готово, источники подключаются через порты",
      `vtb.ru — «Комплект МЧД и КЭП» в «ВТБ Бизнес»; репозиторий «Мандата» v${N.version}, evidence.json от ${N.asOf}; docs/ARCHITECTURE.md, docs/PILOT.md`);
    const cols = [
      ["Database", "Источники", ["МЧД в XML и бумажная доверенность", "ЕГРЮЛ на дату", "реестр МЧД ФНС: номер + ИНН доверителя и представителя", "ЕФРСБ и устав"], 0],
      ["Cpu", "Движок «Мандата»", ["граф полномочий на дату", `${RULES} с нормой и тестом`, "журнал с деревом Меркла", "без внешней сети"], 1],
      ["BadgeCheck", "Результат", ["вердикт и доказательства", "рекомендация роли в «ВТБ Бизнес»", "удостоверение со статусом", "событие для антифрода"], 0],
    ];
    const gap = 0.45, cw = (W - 2 * gap) / 3, ch = 2.45;
    cols.forEach(([ic, head, items, hl], i) => {
      const x = L + i * (cw + gap);
      card(s, x, TOP, cw, ch, hl ? { fill: C.text2, line: null } : {});
      tile(s, x + 0.25, TOP + 0.22, 0.5, ic, hl ? { fill: "1D46A8", icon: "light" } : {});
      T(s, head, x + 0.9, TOP + 0.22, cw - 1.1, 0.5, { size: 15, bold: true, color: hl ? C.background1 : C.text2, valign: "middle" });
      bullets(s, items, x + 0.3, TOP + 0.9, cw - 0.55, ch - 1.0, { size: 12.5, color: hl ? C.background1 : C.text1, psa: 4 });
      if (i < 2) arrow(s, x + cw + 0.06, TOP + ch / 2, gap - 0.12, HEX.accent1, 2);
    });
    const y2 = TOP + ch + 0.25, h2 = BOTTOM - y2, w2 = (W - 0.25) / 2;
    const parts = [
      ["Check", HEX.accent3, "Работает сегодня", [`${RULES}, ${DEMOS}, CLI и HTTP API с ролями`, "разбор человеком: очередь P1–P3 и сроки", "мониторинг событий, статусы удостоверений, журнал", `${TESTS} проходят; SBOM и подписанное происхождение сборки`]],
      ["Plug", HEX.accent1, "Подключаем к пилоту", ["реестр МЧД, ЕГРЮЛ, ЕФРСБ — адаптеры банка вместо заглушек", "проверка КЭП и ГОСТ-подпись — сертифицированные СКЗИ банка", "ключи — HSM по PKCS#11; вход — OIDC банка", "коды полномочий — выгрузка классификатора ЕСНСИ"]],
    ];
    parts.forEach(([ic, col, head, items], i) => {
      const x = L + i * (w2 + 0.25);
      card(s, x, y2, w2, h2);
      dot(s, x + 0.25, y2 + 0.22, 0.42, col, ic);
      T(s, head, x + 0.82, y2 + 0.22, w2 - 1.0, 0.42, { size: 15, bold: true, color: C.text2, valign: "middle" });
      bullets(s, items, x + 0.3, y2 + 0.8, w2 - 0.55, h2 - 0.86, { size: 12, psa: 4 });
    });
    s.addNotes("[4:15–4:30] Встройка. ВТБ уже продаёт комплект МЧД и КЭП в «ВТБ Бизнес», значит, поток представителей по доверенностям уже есть. «Мандат» встаёт в контур on-prem: ядро, правила, журнал и мониторинг работают сегодня, а реестр, ЕГРЮЛ, ЕФРСБ и проверку подписи подключаем к пилоту через готовые порты.");
  }

  // ---------- 13. Пилот ----------
  {
    const s = slide("Контент", "Питч", "ПРЕДЛОЖЕНИЕ ВТБ", "Предлагаем 6-недельный теневой пилот на праве подписи платёжных поручений по МЧД",
      "Теневой режим: «Мандат» проверяет те же пакеты, что и сотрудники, и не влияет на клиентов и выдачу прав. Формат совпадает с пилотами Акселератора ВТБ. docs/PILOT.md");
    const weeks = [
      ["Неделя 1", "протокол, регламент операции, коды полномочий, пороги приёмки, куратор от владельца процесса"],
      ["Недели 2–5", "параллельная проверка тех же пакетов, журнал расхождений с решениями сотрудников"],
      ["Неделя 6", "независимая разметка расхождений, замер, решение: продолжать или остановить"],
    ];
    const gap = 0.35, ww = (W - 2 * gap) / 3, wh = 1.3;
    weeks.forEach(([k, t], i) => {
      const x = L + i * (ww + gap);
      card(s, x, TOP, ww, wh, i === 1 ? { fill: TINT.blue, line: TINT.blueLine } : {});
      T(s, k, x + 0.25, TOP + 0.16, ww - 0.5, 0.32, { size: 14, bold: true, color: C.accent1 });
      T(s, t, x + 0.25, TOP + 0.52, ww - 0.5, wh - 0.6, { size: 12.5, color: C.text1 });
      if (i < 2) arrow(s, x + ww + 0.05, TOP + wh / 2, gap - 0.1, HEX.accent1, 2);
    });
    const y2 = TOP + wh + 0.25, h2 = 2.55, w2 = (W - 0.25) / 2;
    const parts = [
      ["Target", "Критерии: продолжать или остановить", ["0 ложных «подтверждено» на всех кейсах — стоп-критерий", "доля «на разбор» не выше согласованного порога", "согласие с экспертами (κ Коэна) не ниже межэкспертного", "все расхождения разобраны; проверка быстрее ручной"]],
      ["Handshake", "Что нужно от ВТБ", ["владелец процесса и куратор пилота", "регламент одной операции и типовые отказы", "50–100 обезличенных пакетов или тестовый контур", "юрист для правил; базовые цифры для расчёта эффекта"]],
    ];
    parts.forEach(([ic, head, items], i) => {
      const x = L + i * (w2 + 0.25);
      card(s, x, y2, w2, h2);
      tile(s, x + 0.25, y2 + 0.22, 0.46, ic);
      T(s, head, x + 0.85, y2 + 0.22, w2 - 1.05, 0.46, { size: 15, bold: true, color: C.text2, valign: "middle" });
      bullets(s, items, x + 0.3, y2 + 0.85, w2 - 0.55, h2 - 0.95, { size: 13, psa: 5 });
    });
    const cy = y2 + h2 + 0.25, chh = BOTTOM - cy;
    s.addImage({ data: G.band, x: L, y: cy, w: W, h: chh, altText: "" , rounding: false, objectName: "Плашка запроса" });
    tile(s, L + 0.25, cy + (chh - 0.5) / 2, 0.5, "Rocket", { fill: "1D46A8", icon: "light" });
    T(s, "Наш запрос: теневой пилот через Акселератор ВТБ с метриками, согласованными до старта", L + 0.95, cy, W - 1.2, chh, { size: 16, bold: true, color: C.background1, valign: "middle" });
    s.addNotes("[4:30–4:50] Наш запрос конкретный: шестинедельный теневой пилот на одной операции — праве подписи платёжных поручений по МЧД. «Мандат» работает рядом с сотрудниками и ни на что не влияет. Стоп-критерий — ноль ложных подтверждений на всех кейсах пилота. От банка — владелец процесса, регламент, обезличенные пакеты и юрист.");
  }

  // ---------- 14. Команда ----------
  {
    const s = slide("Контент", "Питч", "КОМАНДА", "Команда: архитектура правил и измерение качества", "github.com/KonkovDV/Mandat · собственный код под лицензией MIT");
    const people = [
      ["dmitrij.jpg", "Коньков Дмитрий", "Капитан · архитектура и правила", "Автор открытых проектов AeroBIM и SynAPS. Детерминированное ядро, граф полномочий, границы заявлений"],
      ["elena.jpg", "Конькова Елена", "Системный инженер · измерение и качество", "Методика оценки, корпус и разметка, воспроизводимость сборки, протокол пилота"],
    ];
    const gap = 0.25, pw = (W - gap) / 2, ph = 2.75;
    people.forEach(([img, name, role, desc], i) => {
      const x = L + i * (pw + gap);
      card(s, x, TOP, pw, ph);
      s.addShape(pres.shapes.OVAL, { x: x + 0.3, y: TOP + 0.38, w: 1.98, h: 1.98, fill: { color: TINT.blue }, line: { color: HEX.accent2, width: 2.5 }, objectName: "Рамка фото" });
      s.addImage({ path: path.join(ASSETS, img), x: x + 0.39, y: TOP + 0.47, w: 1.8, h: 1.8, rounding: true, altText: `Фото: ${name}` });
      T(s, name, x + 2.6, TOP + 0.45, pw - 2.85, 0.42, { size: 19, bold: true, color: C.text2 });
      T(s, role, x + 2.6, TOP + 0.9, pw - 2.85, 0.5, { size: 13, bold: true, color: C.accent1 });
      T(s, desc, x + 2.6, TOP + 1.42, pw - 2.85, 1.15, { size: 13, color: C.text1 });
    });
    const y2 = TOP + ph + 0.25, h2 = BOTTOM - y2, w2 = (W - gap) / 2;
    card(s, L, y2, w2, h2, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
    tile(s, L + 0.25, y2 + 0.25, 0.5, "Award", { fill: "FFFFFF" });
    T(s, "Что получает банк", L + 0.9, y2 + 0.25, w2 - 1.1, 0.5, { size: 15, bold: true, color: C.text2, valign: "middle" });
    T(s, "проверенный набор правил, протокол измерений, итоги пилота и эталонную реализацию под MIT для передачи команде банка", L + 0.3, y2 + 0.9, w2 - 0.6, h2 - 1.0, { size: 13 });
    const x2 = L + w2 + gap;
    card(s, x2, y2, w2, h2, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
    tile(s, x2 + 0.25, y2 + 0.25, 0.5, "Search", { fill: "FFFFFF" });
    T(s, "Кого ищем в ВТБ", x2 + 0.9, y2 + 0.25, w2 - 1.1, 0.5, { size: 15, bold: true, color: C.text2, valign: "middle" });
    T(s, "владельца процесса добавления представителей юрлиц и юриста, который утвердит правила и окно «свежей» смены руководителя", x2 + 0.3, y2 + 0.9, w2 - 0.6, h2 - 1.0, { size: 13 });
    s.addNotes("[4:50–5:00] Команда: я отвечаю за архитектуру и правила, Елена — за методику измерения и качество. Юридическую проверку правил планируем с юристом банка на пилоте. Спасибо, готовы к вопросам.");
  }

  // ---------- 15. Контакты ----------
  {
    const s = pres.addSlide({ masterName: "Финал", sectionTitle: "Питч" });
    s.addImage({ data: G.markDark, x: L, y: 0.6, w: 0.62, h: 0.56, altText: "Знак «Мандата»" });
    T(s, "Команда «Мандат»", L + 0.8, 0.6, 5.0, 0.56, { size: 16, bold: true, color: C.background1, valign: "middle" });
    s.addText("Спасибо! Проверим гипотезы на данных ВТБ", { placeholder: "title" });
    const rows = [
      ["Phone", "Коньков Дмитрий, капитан", "+7 915 452-88-64 · konkov-dmitrij@yandex.ru"],
      ["Mail", "Конькова Елена, измерение и качество", "konkovaelena@yandex.ru"],
      ["Github", "Код и документация", "github.com/KonkovDV/Mandat · собственный код под MIT"],
    ];
    rows.forEach(([ic, who, t], i) => {
      const y = 3.0 + i * 0.72;
      tile(s, L, y + 0.06, 0.46, ic, { fill: "1D46A8", icon: "light" });
      T(s, who, L + 0.65, y, 6.5, 0.3, { size: 14, bold: true, color: C.background1, valign: "middle" });
      T(s, t, L + 0.65, y + 0.3, 6.5, 0.3, { size: 13, color: TINT.ice, valign: "middle" });
    });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: L, y: 5.35, w: 6.7, h: 0.95, rectRadius: 0.12, fill: { color: "FFFFFF", transparency: 90 }, line: { color: HEX.accent2, width: 1.5 }, objectName: "Плашка просьбы" });
    T(s, "Ищем в ВТБ владельца процесса добавления представителей юрлиц, чтобы сверить гипотезы до финала 11 декабря", L + 0.3, 5.35, 6.1, 0.95, { size: 14, bold: true, color: C.background1, valign: "middle" });
    const hw = 4.9;
    s.addImage({ data: G.hero, x: 7.85, y: 1.05, w: hw, h: hw * G.heroRatio, altText: "Схема: источники сходятся в один вердикт на дату операции" });
    s.addNotes("Спасибо. Сейчас для нас важнее всего поговорить с теми, кто в ВТБ отвечает за добавление представителей юрлиц: это сделает гипотезы проверяемыми уже в первом спринте.");
  }

  // =====================================================================
  // ПРИЛОЖЕНИЕ
  // =====================================================================
  pres.addSection({ title: "Приложение" });
  {
    const s = pres.addSlide({ masterName: "Раздел", sectionTitle: "Приложение" });
    s.addImage({ data: G.markDark, x: L, y: 1.45, w: 0.8, h: 0.72, altText: "Знак «Мандата»" });
    s.addText("Приложение: ответы на вопросы жюри", { placeholder: "title" });
    s.addText("Семь взглядов · критерии жюри · рынок · правила · право · доверие без базы · качество · ИБ · риски · расчёт эффекта · трудные вопросы · нормы · дорожная карта", { placeholder: "body" });
  }

  // ---------- Семь взглядов ----------
  {
    const s = slide("Контент", "Приложение", "ВРАЖДЕБНЫЙ РАЗБОР", "Семь взглядов на «Мандат»: главное возражение каждой роли и наш ответ",
      "docs/audit/MURDER_BOARD-2026-10-20.md (банкир, юрист, правовед, безопасник, ИБ, пользователь, Red Team); docs/audit/AUDIT-2026-10-27-DIFFERENTIAL.md; docs/JURY.md");
    const roles = [
      ["Briefcase", "Банкир", "«Сколько экономит и не вырастет ли очередь?»", "Эффект — сценарии на допущениях, пилот заменит их фактами. Очередь P1–P3 со сроками; порог доли «на разбор» — критерий пилота"],
      ["Scale", "Юрист банка", "«Все правила — черновики»", "В строгом профиле черновик не даёт ни «да», ни «нет». Утверждение правил юристом — первая неделя пилота"],
      ["BookOpen", "Правовед", "«Полномочие — правоотношение на дату»", "Так и смоделировано: граф на дату операции (ст. 185–189 ГК РФ). Полномочие из обстановки (ст. 182) вне модели: без документа «да» нет"],
      ["Lock", "ИБ банка", "«Ed25519, а не ГОСТ; root перепишет журнал»", "Ключ — в HSM, Vault или KMS; ГОСТ — через СКЗИ банка. Без внешнего якоря журнала pilot и prod не стартуют"],
      ["ShieldAlert", "Антифрод", "«Подставной представитель с действующей МЧД»", "Проверяем полномочие, а не намерение. Признаки ОД-2506 — в антифроде банка; ему уходят подписанные события"],
      ["UserRound", "Сотрудник и клиент", "«Всё сходится — почему „на разбор“?»", "Карточка показывает сильнейшее возражение и следующий шаг; клиент получает список недостающего и может оспорить ответ"],
      ["Award", "Жюри", "«0 ложных „да“ — это точность 100%?»", `Нет: корпус синтетический, это проверка логики, граница ${N.fcUpper}. Реальная доля — стоп-критерий пилота`],
    ];
    const gap = 0.2, cw = (W - 3 * gap) / 4, ch = (BOTTOM - TOP - gap) / 2;
    roles.forEach(([ic, role, obj, ans], i) => {
      const x = L + (i % 4) * (cw + gap), y = TOP + Math.floor(i / 4) * (ch + gap);
      card(s, x, y, cw, ch);
      tile(s, x + 0.2, y + 0.18, 0.42, ic);
      T(s, role, x + 0.72, y + 0.18, cw - 0.9, 0.42, { size: 13.5, bold: true, color: C.text2, valign: "middle" });
      T(s, obj, x + 0.2, y + 0.7, cw - 0.4, 0.5, { size: 11.5, italic: true, color: C.accent1 });
      T(s, ans, x + 0.2, y + 1.24, cw - 0.4, ch - 1.3, { size: 11, color: C.text1 });
    });
    const x = L + 3 * (cw + gap), y = TOP + ch + gap;
    card(s, x, y, cw, ch, { fill: C.text2, line: null });
    tile(s, x + 0.2, y + 0.2, 0.42, "ListChecks", { fill: "1D46A8", icon: "light" });
    T(s, "Полный разбор", x + 0.72, y + 0.2, cw - 0.9, 0.42, { size: 13.5, bold: true, color: C.background1, valign: "middle" });
    T(s, "Позиции, находки, статусы и тесты, которые их держат, — в docs/audit. Числа — только из реестра утверждений с командой воспроизведения", x + 0.2, y + 0.75, cw - 0.4, ch - 0.85, { size: 11.5, color: TINT.ice });
  }

  // ---------- Критерии жюри ----------
  {
    const s = slide("Контент", "Приложение", "КРИТЕРИИ", "Пять критериев жюри — где в проекте ответ на каждый",
      "Критерии трека MVP по Положению «Академии Финтеха» (шкала 0–2); редакцию мы видели только в выдаче поиска и уточняем у организаторов. docs/research/REQUIREMENTS_MATRIX.md");
    table(s, ["Критерий", "Что показываем", "Чем проверить"], [
      ["Готовность MVP", `демо одной командой, ${DEMOS}, CLI, HTTP API, Docker`, "./run-demo.sh; docker compose в CI"],
      ["Техническая реализация", `${RULES} с нормой и тестом, журнал Меркла, удостоверения со статусом, Cedar, сверка с внешними эталонами`, `${TESTS}; evidence.json; AUDIT-2026-10-27-DIFFERENTIAL`],
      ["Потенциал пилота в ВТБ", "ВТБ сам продаёт комплект МЧД и КЭП; порты под реестр, ЕГРЮЛ, ЕФРСБ и СКЗИ", "docs/PILOT.md: теневой режим 6 недель"],
      ["Бизнес-модель", "лицензия в контуре банка и годовое обновление правил при смене норм и формата МЧД", "docs/ECONOMICS.md; сценарии C-15, C-16"],
      ["Презентация", "питч 5 минут, демо в 4 шага, трудные вопросы с честными ответами", "docs/JURY.md; числа из evidence.json"],
    ], L, TOP, W, [2.6, 5.6, 3.933], { size: 13, headH: 0.48, rowH: 0.8 });
  }

  // ---------- Конкуренты ----------
  {
    const s = slide("Контент", "Приложение", "РЫНОК", "Реестр и ЭДО отвечают «действует ли МЧД», «Мандат» — «можно ли эту операцию сегодня»",
      "«Не заявлено» — нет в открытых описаниях продукта на 14.10.2026; «не проверяли» — открытых данных недостаточно. Источники: vtb.ru/malyj-biznes/ukep/mchd; i.vkontur.ru/news/3157; developer.kontur.ru; docs/COMPETITORS.md");
    table(s, ["Что сравниваем", "Сервис МЧД «ВТБ Бизнес»", "Контур.Диадок, 1С-ЭДО", "Реестр МЧД ФНС", "«Мандат»"], [
      ["Объект проверки", "выпуск исходящих МЧД", "даты МЧД и сертификат под документом ЭДО", "действует ли МЧД", "операция банка и права в ДБО на дату"],
      ["Полномочие подписанта", "не заявлено", "«Диадок не может» (Контур, по выдаче поиска)", "нет", `${RULES} с нормой`],
      ["Передоверие", "только признак", "не проверяли", "хранит сведения", "сроки, сужение, каскад отмены"],
      ["ЕГРЮЛ на дату и устав", "не заявлено", "не заявлено", "нет", "в одной проверке"],
      ["Смена директора", "не заявлено", "не проверяли", "нет", "сигнал или разбор по окну свежести"],
      ["Доказательства и аудит", "не заявлено", "не заявлено", "статус в реестре", "XPath, ГРН, норма; журнал; удостоверение со статусом"],
    ], L, TOP, W, [2.25, 2.3, 2.75, 2.0, 2.833], { size: 12, highlight: 4, headH: 0.5, rowH: 0.55 });
    band(s, L, 5.95, W, 0.75, "Не конкурируем с выпуском МЧД — берём его результат на вход. Операторы ЭДО и реестр — источники данных для «Мандата».", { icon: "Puzzle", size: 13.5, fill: TINT.blue, color: C.text2, tile: "FFFFFF", shadow: false });
  }

  // ---------- 39 правил ----------
  {
    const s = slide("Контент", "Приложение", "ПРАВИЛА", `${RULES} со ссылкой на норму: что они ловят`,
      "Полная таблица «правило → норма → эффект → тест» — docs/RULES.md. Все правила в статусе draft до проверки юристом банка; в строгом профиле правило-черновик не опровергает и не подтверждает");
    table(s, ["Группа", "Правила", "Что ловят"], [
      ["Формат", "R-FMT-001…005", "битый XML и XML-бомбы, чужой формат, реквизиты по Приказу № 1001, безотзывная доверенность, повтор документов и полей"],
      ["Стороны", "R-ID-001…005", "контрольные числа, чужая компания, чужой представитель, наименование не по ЕГРЮЛ, ИНН и СНИЛС разных людей"],
      ["Реестр и срок", "R-REG-001, R-TERM-001…002", "отмена и истечение, недоступный реестр, срок по закону — год со дня совершения"],
      ["Руководитель", "R-EIO-001…006, R-BNK-001", "подписант не директор на дату, смена директора (сигнал или разбор), недостоверность, ликвидация и банкротство"],
      ["Покрытие", "R-COV-001…002, R-JNT-001", "нет полномочия на операцию, код исключён из классификатора, совместные полномочия"],
      ["Передоверие", "R-SUB-001…010", "запрет, однократность, расширение, срок, пропущенное звено, каскад отмены, утрата полномочий, длинная цепочка, нотариальная форма"],
      ["Устав и подпись", "R-CHR-001…002, R-TXT-001, R-PAP-001, R-SIG-001…002", "порог одобрения, неразобранный устав, текстовые полномочия, бумажная доверенность, КЭП и подпись распоряжения руководителя"],
    ], L, TOP, W, [1.9, 3.35, 6.883], { size: 12, headH: 0.42, rowH: 0.62 });
  }

  // ---------- Пять норм ----------
  {
    const s = slide("Контент", "Приложение", "ПРАВО", "Пять норм, на которых ошибается ручная сверка",
      "ГК РФ, ст. 186–188; 127-ФЗ, ст. 64, 94, 126; разъяснения ФНС о смене руководителя; описание схемы МЧД (ПрУтрПолн). Ссылки сверить с юристом банка перед пилотом — docs/REGULATORY.md");
    const norms = [
      ["Смена директора", "п. 1 ст. 188 ГК РФ", "МЧД прежнего директора действует, пока её не отменили", "ok", "давняя смена — сигнал; свежая — на разбор"],
      ["Отмена основной МЧД", "п. 3 ст. 188 ГК РФ", "вместе с ней теряет силу передоверие", "no", "каскад по всей цепочке"],
      ["Срок не указан", "п. 1 ст. 186 ГК РФ", "МЧД действует год со дня совершения", "rv", "истёк год — опровергнуто"],
      ["Передоверие с утратой", "признак ПрУтрПолн = 2", "передоверитель больше не вправе действовать", "no", "действует только новый представитель"],
      ["Банкротство", "подп. 7 п. 1 ст. 188 ГК РФ; 127-ФЗ", "конкурсное производство и внешнее управление прекращают МЧД", "no", "наблюдение — на разбор"],
    ];
    const gap = 0.2, cw = (W - 4 * gap) / 5, ch = 3.95;
    norms.forEach(([head, norm, t, k, res], i) => {
      const x = L + i * (cw + gap);
      card(s, x, TOP, cw, ch);
      T(s, head, x + 0.2, TOP + 0.2, cw - 0.4, 0.6, { size: 14, bold: true, color: C.text2 });
      T(s, norm, x + 0.2, TOP + 0.85, cw - 0.4, 0.5, { size: 11, bold: true, color: C.accent1 });
      T(s, t, x + 0.2, TOP + 1.38, cw - 0.4, 1.1, { size: 12, color: C.text1 });
      pill(s, x + 0.2, TOP + 2.62, k, { size: 9, w: cw - 0.4 });
      T(s, res, x + 0.2, TOP + 3.05, cw - 0.4, 0.8, { size: 11.5, color: TINT.text2 });
    });
    band(s, L, TOP + ch + 0.25, W, BOTTOM - (TOP + ch + 0.25), "Ручная сверка «на сегодня» ошибается в обе стороны: отказывает по действующей МЧД и пропускает оборванную цепочку", { icon: "Scale", size: 14 });
  }

  // ---------- Доверие без доступа к базе ----------
  {
    const s = slide("Контент", "Приложение", "ДОВЕРИЕ БЕЗ БАЗЫ", "Доверие без доступа к базе: подписанное удостоверение, статус и журнал",
      "Стандарты: W3C Verifiable Credentials 2.0 и Bitstring Status List v1.0 (Рекомендации W3C, 15.05.2025); RFC 6962 / RFC 9162. На стенде подпись Ed25519, к пилоту — ГОСТ через СКЗИ банка");
    const cols = [
      ["BadgeCheck", "Удостоверение", ["вердикт, операция, хеш набора правил и входов", "номера МЧД цепочки", "без ФИО, только хеш ИНН представителя", "привязка к сумме, получателю и одноразовому nonce"]],
      ["Bell", "Статус", ["списки «отозвано» и «приостановлено»", "мониторинг меняет статус по событию", "проверяющий видит статус сам и отвергает подложенный старый список"]],
      ["Lock", "Журнал решений", ["хеш-цепочка и HMAC: правка записи видна", "дерево Меркла: доказательство одной записи", "аудитор не получает весь журнал", "внешний якорь сегментов в контуре банка"]],
    ];
    const gap = 0.25, cw = (W - 2 * gap) / 3, ch = 3.4;
    cols.forEach(([ic, head, items], i) => {
      const x = L + i * (cw + gap);
      card(s, x, TOP, cw, ch);
      tile(s, x + 0.25, TOP + 0.22, 0.52, ic);
      T(s, head, x + 0.92, TOP + 0.22, cw - 1.1, 0.52, { size: 16, bold: true, color: C.text2, valign: "middle" });
      bullets(s, items, x + 0.3, TOP + 0.95, cw - 0.55, ch - 1.05, { size: 13, psa: 6 });
    });
    const y2 = TOP + ch + 0.25;
    card(s, L, y2, W, BOTTOM - y2, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
    T(s, "Сценарий", L + 0.3, y2 + 0.2, 2.0, 0.3, { size: 12, bold: true, color: C.accent1, cs: 0.5 });
    const flow = ["проверка", "удостоверение уходит в «ВТБ Бизнес»", "основную МЧД отменили", "мониторинг ставит «отозвано»", "интернет-банк видит статус сам"];
    const fy = y2 + 0.62, fh = BOTTOM - y2 - 0.85, fg = 0.3, fw = (W - 0.6 - 4 * fg) / 5;
    flow.forEach((t, i) => {
      const x = L + 0.3 + i * (fw + fg);
      card(s, x, fy, fw, fh, { fill: "FFFFFF", line: TINT.blueLine, shadow: false, r: 0.1 });
      T(s, t, x + 0.12, fy, fw - 0.24, fh, { size: 12.5, bold: i === 4, color: C.text2, align: "center", valign: "middle" });
      if (i < 4) arrow(s, x + fw + 0.03, fy + fh / 2, fg - 0.06, HEX.accent1, 1.5);
    });
  }

  // ---------- Методика качества ----------
  {
    const s = slide("Контент", "Приложение", "ИЗМЕРЕНИЕ", "Как мы измеряем качество — и что изменится на реальных данных",
      "Источники: evidence.json, docs/EVALUATION.md, docs/audit/AUDIT-2026-10-27-DIFFERENTIAL.md; Brown, Cai, DasGupta (2001) — интервал Вилсона; Geifman, El-Yaniv (2017) — «риск — покрытие»");
    table(s, ["Что", "Сейчас: синтетика", "На пилоте"], [
      ["Корпус", `${N.cases} кейса, ${N.kinds} видов: ${N.kindsBy.ok} зелёный, ${N.kindsBy.no} опровергающих, ${N.kindsBy.rv} на разбор, ${N.kindsBy.nd} без данных`, "50–100 обезличенных пакетов"],
      ["Ложные «подтверждено»", `${N.falseConfirmed} из ${N.negatives}, верхняя граница 95% — ${N.fcUpper}; по видам дефектов 0 из ${N.negativeTypes}, граница ${N.fcUpperByType}`, "стоп-критерий: 0 на всех кейсах пилота"],
      ["Полнота", `все опровергающие найдены, нижняя граница 95% — ${N.recallLow}`, `не меньше ${N.nRecall} дефектных случаев для ±5%`],
      ["Покрытие", `${N.coverage} автоматических вердиктов в профиле demo, риск среди них ≤ ${N.autoRiskUpper}; ${N.strictCoverage} в strict`, "порог согласуем с банком до старта"],
      ["Устойчивость", `${FUZZ_TXT} мутаций сценариев без ложного «да»; отложенный seed без расхождений`, "отдельная попытка построить ложное «да»"],
      ["Независимая проверка", `${N.chains} цепочки: «Мандат» и Cedar совпали; JCS, Меркл и подпись сверены с внешними эталонами`, "символьное доказательство свойства на Cedar SymCC"],
      ["Разметка", "ожидаемый вердикт задан генератором", "два независимых эксперта, κ Коэна"],
    ], L, TOP, W, [2.5, 6.0, 3.633], { size: 12, headH: 0.42, rowH: 0.6 });
  }

  // ---------- ИБ ----------
  {
    const s = slide("Контент", "Приложение", "ИНФОРМАЦИОННАЯ БЕЗОПАСНОСТЬ", "Всё в контуре банка: данные, модель и решения",
      "Это описание проектных решений, а не оценка соответствия: её проводят ИБ и юристы банка. 821-П, 851-П и ГОСТ Р 57580.1 — меры стенда и пробелы перечислены в docs/REGULATORY.md. docs/DATA_PROTECTION.md, docs/THREAT_MODEL.md");
    const tiles = [
      ["Server", "On-prem без сети", "ядро не делает внешних запросов; модель — только хосты из белого списка, по умолчанию выключена"],
      ["Fingerprint", "Персональные данные", "псевдонимизация ИНН, СНИЛС, паспорта, телефона, почты; в модель — только строки о полномочиях"],
      ["FileX", "Вредоносные файлы", "защита от XML-бомб и внешних сущностей, строгий JSON на всех внешних входах, лимиты размера и глубины"],
      ["KeyRound", "API и роли", "оператор, рецензент, аудитор; чужой кейс не виден; токены — только хеши, лимит частоты и тела"],
      ["Scale", "152-ФЗ и 115-ФЗ", "спроектировано с учётом 152-ФЗ; идентификацию по 115-ФЗ не подменяем — она за каналами банка"],
      ["Container", "Подпись и сборка", "КЭП — через СКЗИ банка, ключ — HSM, Vault или KMS; в CI закреплённые версии, pip-audit, CodeQL, SBOM"],
    ];
    const gap = 0.25, cw = (W - 2 * gap) / 3, ch = (BOTTOM - TOP - gap) / 2;
    tiles.forEach(([ic, head, t], i) => {
      const x = L + (i % 3) * (cw + gap), y = TOP + Math.floor(i / 3) * (ch + gap);
      card(s, x, y, cw, ch);
      tile(s, x + 0.25, y + 0.25, 0.52, ic);
      T(s, head, x + 0.92, y + 0.25, cw - 1.1, 0.52, { size: 15, bold: true, color: C.text2, valign: "middle" });
      T(s, t, x + 0.25, y + 0.95, cw - 0.5, ch - 1.05, { size: 13, color: C.text1 });
    });
  }

  // ---------- Риски ----------
  {
    const s = slide("Контент", "Приложение", "РИСКИ", "Риски, которые банк увидит первыми, — и как мы их снимаем",
      "LIMITS.md — полный список границ; docs/AI_RISKS.md — матрица рисков ИИ; docs/audit — Red Team допуска операции");
    table(s, ["Риск", "Как снимаем", "Чем контролируем"], [
      ["Ложное подтверждение", "неизвестное не становится «да», обязательные группы проверок", "0 ложных «подтверждено» — стоп-критерий"],
      ["Удостоверение на чужую дату или компанию", "сверка дня по Москве и ИНН организации; срок не длиннее 30 дней и сроков цепочки", "тесты привязки к запросу; Red Team допуска"],
      ["Ошибка в норме", "норма у каждого правила, статус draft/approved, юрист банка", "доля правил, утверждённых юристом"],
      ["Ошибка модели", "нет цитаты — нет факта; вердикт модель не меняет", "тест «модель не меняет вердикт»"],
      ["Формат МЧД изменится", "теги — в файле соответствия, формы ЦБ по 315-ФЗ — через адаптер", "пересборка примеров одной командой"],
      ["Нагрузка «на разбор»", "сигнал вместо разбора там, где закон позволяет", "доля «на разбор» с порогом банка"],
      ["Интеграция с ДБО", "на пилоте — только рекомендация, без изменений в ДБО", "сценарий пилота без доработок ДБО"],
    ], L, TOP, W, [3.0, 5.4, 3.733], { size: 12, headH: 0.42, rowH: 0.62 });
  }

  // ---------- Расчёт эффекта ----------
  {
    const s = slide("Контент", "Приложение", "ЭКОНОМИКА", "Расчёт эффекта: формулы, допущения и мировой ориентир",
      "Источники: Банк России, I–II кв. 2026; ВТБ, МСФО за 1П 2026 (вторичный источник); Fenergo — отраслевые опросы 2022 и 2025 гг. (данные поставщика ПО)");
    const gap = 0.25, cw = (W - gap) / 2, ch = 3.55;
    card(s, L, TOP, cw, ch);
    tile(s, L + 0.25, TOP + 0.22, 0.46, "Coins");
    T(s, "Формулы «Мандата»", L + 0.85, TOP + 0.22, cw - 1.1, 0.46, { size: 15, bold: true, color: C.text2, valign: "middle" });
    bullets(s, [
      "Ставки = клиенты × доля с событием представительства × минуты на кейс (с разбором) ÷ 60 ÷ 1 800 ч",
      "Ущерб = 818 × 4 × доля ВТБ × доля случаев с полномочиями × 0,7 млн ₽",
      "Годовой объём — I кв. 2026 × 4; во II кв. потери юрлиц выше, поэтому база консервативна",
      "Все доли и минуты — гипотезы: их измеряет пилот; расчёт — python scripts/effect_model.py --scenarios",
    ], L + 0.3, TOP + 0.9, cw - 0.55, ch - 1.0, { size: 12.5, psa: 6 });
    const x2 = L + cw + gap;
    card(s, x2, TOP, cw, ch);
    tile(s, x2 + 0.25, TOP + 0.22, 0.46, "Globe");
    T(s, "Мировой ориентир — не наша экономия", x2 + 0.85, TOP + 0.22, cw - 1.1, 0.46, { size: 15, bold: true, color: C.text2, valign: "middle" });
    bullets(s, [
      "1 500–3 000 долл. США — столько больше половины корпоративных банков тратят на одну KYC-проверку клиента (опрос Fenergo, 2022)",
      "70% финансовых организаций за год теряли клиентов из-за медленного подключения (опрос Fenergo, 2025)",
      "KYC шире проверки полномочий, а масштаб западных корпоративных банков несопоставим со сменой подписанта в МСБ",
    ], x2 + 0.3, TOP + 0.9, cw - 0.55, ch - 1.0, { size: 12.5, psa: 6 });
    band(s, L, TOP + ch + 0.25, W, BOTTOM - (TOP + ch + 0.25), "Что заменит допущения фактами: число событий представительства в год, минуты на кейс по хронометражу 30–50 проверок, доля инцидентов, связанных с полномочиями", { icon: "Timer", size: 14 });
  }

  // ---------- Трудные вопросы ----------
  const hard = [
    [
      ["Чем вы лучше проверки статуса МЧД в реестре ФНС?", `Реестр отвечает «действует или отменена». Мы — покрывает ли МЧД эту операцию у этой компании на эту дату, с ЕГРЮЛ, уставом, передоверием и событиями. Статус реестра — одно из ${RULES}`],
      ["Откуда «0 ложных», если данные синтетические?", `Это проверка логики правил. Верхняя граница — ${N.fcUpper}. Реальная доля — стоп-критерий пилота`],
      ["Кто отвечает, если «подтверждено», а это мошенник?", "Решение остаётся за банком. В пилоте теневой режим; мы даём доказательства и журнал, права не выдаём"],
      ["Почему правила, а не машинное обучение?", "Полномочия — вопрос права, а не вероятности. Правила объяснимы и проверяемы; машинное обучение уместно для сигналов риска"],
      ["Зачем тогда ИИ?", "Извлечь положения устава и текстовые полномочия с дословной цитатой. Без модели продукт работает полностью"],
      [`Откуда ${LOSS[0]}–${LOSS[2]} млн ₽?`, "Формула на данных Банка России с тремя явными допущениями. Пилот их измерит"],
      ["Всё на заглушках — что реально работает?", "Ядро, правила, журнал, мониторинг (Standard Webhooks), роли API, SBOM. Заглушки — реестр ФНС, ЕФРСБ, КЭП"],
      ["Почему почти нет «подтверждено»?", `«Да» — только если закрыт и устав; без разметки устава ${N.toHuman} корпуса уходит человеку`],
    ],
    [
      ["Почему смена директора не прекращает МЧД?", "Перечень оснований в п. 1 ст. 188 ГК РФ закрытый, ФНС это подтверждает. Новый директор может отменить МЧД"],
      ["Что при конфликте ЕГРЮЛ и МЧД?", "Корневую МЧД подписал не директор по ЕГРЮЛ на дату выдачи — «опровергнуто» (R-EIO-001). Сверить нельзя — «на разбор». Свежая смена директора — всегда разбор"],
      ["Как с ИБ и ПДн?", "On-prem, без сети, псевдонимизация, удостоверение без ФИО, роли в API. Оценку соответствия проводит банк"],
      ["Сколько стоит внедрение?", "Пилот — одна операция, 6 недель. Цену назовём после замеров; бизнес-модель — лицензия на контур банка и годовое обновление правил"],
      ["Это требование только российское?", "Нет. FATF требует проверять, что представитель уполномочен; в ЕС с 10.07.2027 — Регламент 2024/1624 и стандарты AMLA"],
      ["Если запрос придёт от ИИ-агента?", "Представитель по ГК РФ — лицо, а не программа. «Мандат» проверяет полномочие человека, от чьего имени действует агент, на конкретную операцию"],
      ["Почему вам можно доверять код?", `${TESTS} проходят (v${N.version}, evidence.json), независимая проверка на Cedar, сверка с внешними эталонами, реестр утверждений`],
      ["Есть ли юрлицо для пилота?", "Для конкурса не нужно. К пилоту через Акселератор ВТБ — регистрация компании или партнёр-интегратор"],
    ],
  ];
  hard.forEach((rows, i) => {
    const s = slide("Контент", "Приложение", "ТРУДНЫЕ ВОПРОСЫ", `Трудные вопросы жюри (${i + 1}/2)`,
      "Ответы опираются на CLAIMS_REGISTRY.md и LIMITS.md: каждое число — с командой воспроизведения и границей");
    table(s, ["Вопрос", "Ответ"], rows, L, TOP, W, [4.0, 8.133], { size: 11, headH: 0.4, rowH: 0.56 });
  });

  // ---------- Нормативная база ----------
  {
    const s = slide("Контент", "Приложение", "НОРМАТИВНАЯ БАЗА", "Нормативная база на октябрь 2026 и что она меняет в «Мандате»",
      "Ревизия 27.10.2026 по открытым источникам; это не юридическое заключение — каждую строку проверяет юрист банка. 210-ФЗ — по обзорам pgplaw.ru и vc.ru, текст закона не открыт. docs/REGULATORY.md, docs/SOURCES.md");
    table(s, ["Акт", "С какой даты", "Что меняет"], [
      ["63-ФЗ в ред. 476-ФЗ", "01.09.2024", "представитель юрлица подписывает своей КЭП вместе с МЧД"],
      ["ГК РФ, ст. 185–189", "действует", "срок по умолчанию — год; передоверие; закрытый перечень прекращения; каскад"],
      ["127-ФЗ; подп. 7 п. 1 ст. 188 ГК РФ", "действует", "конкурсное производство и внешнее управление прекращают МЧД"],
      ["115-ФЗ, ст. 7", "действует", "идентификация представителя и документ-основание: номер, дата, срок"],
      ["Приказ Банка России № ОД-2506", "01.01.2026", "12 признаков операций без согласия вместо 6; признак из ГИС — с 01.03.2026"],
      ["Приказ Минцифры № 1001", "01.02.2026", "СНИЛС и ИНН представителя, КПП, наименование полномочия рядом с кодом"],
      ["Реестр МЧД ФНС", "01.03.2026", "поиск МЧД по номеру, ИНН доверителя и ИНН представителя"],
      ["243-ФЗ о технологиях ИИ", "01.09.2026; ст. 5 — 01.03.2027", "национальные модели — по решению Правительства, для банков — с Банком России"],
      ["210-ФЗ, антифрод-пакет", "поэтапно с 01.09.2026", "обязанности банков по защите и возмещению; применимость к юрлицам — юристу"],
      ["821-П в ред. № 7220-У", "01.10.2026", "защита информации при переводах; соответствие не заявляем"],
      ["Кодекс этики ИИ; 3-МР", "2025; 16.06.2026", "прозрачность, человек в контуре, подтверждение человеком при высоком риске"],
      ["315-ФЗ", "01.09.2027", "единая форма МЧД основная; формы Банка России — дополнительные"],
    ], L, TOP, W, [3.15, 2.5, 6.483], { size: 10.5, headH: 0.36, rowH: 0.37 });
  }

  // ---------- Дорожная карта ----------
  {
    const s = slide("Контент", "Приложение", "ДОРОЖНАЯ КАРТА", "Дорожная карта: от онлайн-питча до решения о пилоте", "docs/ROADMAP.md, docs/MVP.md, docs/HANDOFF_NEXT_QUOTA.md");
    const cols = [
      ["До 10.11", "Онлайн-питч", ["сверка формата с официальной XSD ФНС", "8–12 интервью и опрос", "видео-демо, критерии трека"]],
      ["16–29.11", "Спринт 2", ["коды из выгрузки ЕСНСИ", "корпус от 300 пакетов, κ Коэна", "прототип проверки КЭП через СКЗИ", "финансовая модель на фактах"]],
      ["03–08.12", "Спринт 3", ["символьная проверка на Cedar SymCC", "ГОСТ-подпись удостоверений", "контракт интеграции с ДБО (OpenAPI)", "маршрут допуска операции"]],
      ["После 11.12", "Пилот", ["6 недель в теневом режиме", "одна операция", "решение: продолжать или остановить"]],
    ];
    const gap = 0.25, cw = (W - 3 * gap) / 4, ch = 3.8;
    cols.forEach(([d, head, items], i) => {
      const x = L + i * (cw + gap);
      const hl = i === 3;
      card(s, x, TOP, cw, ch, hl ? { fill: C.text2, line: null } : {});
      T(s, d, x + 0.25, TOP + 0.2, cw - 0.5, 0.3, { size: 12, bold: true, color: hl ? HEX.accent2 : C.accent1 });
      T(s, head, x + 0.25, TOP + 0.52, cw - 0.5, 0.42, { size: 18, bold: true, color: hl ? C.background1 : C.text2 });
      bullets(s, items, x + 0.3, TOP + 1.1, cw - 0.55, ch - 1.2, { size: 13, psa: 6, color: hl ? C.background1 : C.text1 });
    });
    band(s, L, TOP + ch + 0.25, W, BOTTOM - (TOP + ch + 0.25), "Финал 11.12.2026: живое демо и, если банк даст данные, результаты на обезличенных кейсах вместо синтетики", { icon: "Flag", size: 15, bold: true });
  }

  await pres.writeFile({ fileName: OUT });
  console.log(`готово: ${OUT}`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
