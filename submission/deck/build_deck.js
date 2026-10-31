// Презентация «Мандата» как код: все числа о продукте берутся из evidence.json.
//
//   node build_deck.js ../../evidence.json ../slides/Mandat_VTB.pptx
//   python finish_deck.py ../slides/Mandat_VTB.pptx      # цвета темы, язык, alt-текст, метаданные
//
// Бриф колоды — PROMPT.md: драматургия, дизайн-система, правила данных и чек-лист приёмки.
// Оформление — палитра ВТБ и шрифт Golos Text (ParaType, OFL): фирменный VTB Group UI лицензирован
// только для Группы ВТБ. Графика рисуется в graphics.js. Числа рынка и эффекта — из открытых источников
// и допущений команды; каждое записано в CLAIMS_REGISTRY.md с источником или командой воспроизведения.
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");
const { buildGraphics } = require("./graphics");

const EVIDENCE = process.argv[2] || path.join(__dirname, "..", "..", "evidence.json");
const OUT = process.argv[3] || path.join(__dirname, "..", "..", "Mandat_VTB.pptx");
const ASSETS = path.join(__dirname, "assets");
const ROOT = path.join(__dirname, "..", "..");

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
  const text = fs.readFileSync(path.join(ROOT, "tests", "test_redteam_fuzz.py"), "utf8");
  const m = text.match(/assert len\(fuzz_rows\) == (\d+)/);
  if (!m) throw new Error("в tests/test_redteam_fuzz.py не найдено закреплённое число мутаций");
  return Number(m[1]);
})();
const FUZZ_TXT = FUZZ.toLocaleString("ru-RU").replace(/\s/g, " ");

// ---------- Эффект: параметры — src/mandat/data/effect_scenarios.json, их же читает scripts/effect_model.py --scenarios ----------
const EFFECT_RAW = JSON.parse(fs.readFileSync(path.join(ROOT, "src", "mandat", "data", "effect_scenarios.json"), "utf8"));
const EFFECT = { ...EFFECT_RAW, lossPerIncidentMln: EFFECT_RAW.lossPerQuarterMln / EFFECT_RAW.incidentsPerQuarter };
const FTE = [0, 1, 2].map((i) => Math.round((EFFECT.clients * EFFECT.eventShare[i] * EFFECT.minutesSaved[i]) / 60 / EFFECT.hoursPerFte));
const LOSS = [0, 1, 2].map((i) => Math.round(EFFECT.incidentsPerQuarter * 4 * EFFECT.vtbShare[i] * EFFECT.authorityShare[i] * EFFECT.lossPerIncidentMln));
const shares = (arr) => arr.map((x) => Math.round(100 * x)).join(" / ");

// ---------- Банк России: операции без согласия у юрлиц в I полугодии 2026 (M-07, M-15) ----------
// II кв. — сумма двух строк таблицы обзора: счета (707 операций, 796,5 млн ₽) и СБП (73 операции, 34,9 млн ₽).
const CBR = {
  q1: { ops: 818, lossMln: 573, shareAll: 7.8 },
  q2: { ops: 780, accounts: 707, sbp: 73, lossMln: 831.4, refundAccounts: 19.5, refundSbp: 0.5, allOps: 458655, allLossMln: 7350.7, shareAll: 11.3 },
};
CBR.q2.avgMln = CBR.q2.lossMln / CBR.q2.ops;
CBR.q2.allAvgK = (CBR.q2.allLossMln * 1000) / CBR.q2.allOps;
CBR.q2.ratio = Math.round((CBR.q2.avgMln * 1000) / CBR.q2.allAvgK);
CBR.q2.growth = Math.round((100 * (CBR.q2.lossMln - CBR.q1.lossMln)) / CBR.q1.lossMln);
if ((CBR.q1.ops + CBR.q2.ops) / 2 !== EFFECT.incidentsPerQuarter || Math.abs((CBR.q1.lossMln + CBR.q2.lossMln) / 2 - EFFECT.lossPerQuarterMln) > 0.05) {
  throw new Error("effect_scenarios.json расходится с данными Банка России за I полугодие 2026");
}
const AVG_Q2 = `≈${comma(CBR.q2.avgMln, 2)} млн ₽`;

// ---------- Деньги и окупаемость для ВТБ: из пересчитанной финансовой модели пакета аналитики ----------
// Порядок сборки: build_model.py → build_deck.js → build_docs.py. Модель читает тот же effect_scenarios.json;
// если её ставки или ущерб расходятся с колодой, модель устарела, и сборка останавливается.
const MODEL = JSON.parse(fs.readFileSync(path.join(ROOT, "submission", "analytics", "out", "model_values.json"), "utf8"));
const BANK = MODEL.bank;
if (BANK.prevented_round.some((v, i) => v !== LOSS[i]) || BANK.fte_round.some((v, i) => v !== FTE[i])) {
  throw new Error(`модель устарела: ущерб ${BANK.prevented_round}, ставки ${BANK.fte_round}; в колоде ${LOSS} и ${FTE}. Пересоберите submission/analytics/build_model.py`);
}
// До года — с десятыми, как в пакете аналитики (build_docs.py, months): «7,5 месяца».
const months = (m) => {
  const v = m >= 12 ? Math.round(m) : Math.round(m * 10) / 10;
  return Number.isInteger(v) ? `${v} ${plural(v, "месяц", "месяца", "месяцев")}` : `${comma(v, 1)} месяца`;
};
const PAYBACK = months(BANK.payback_cash[1]);
const ROI = comma(BANK.roi_cash[1], 1);

// ---------- Демо-кейс 14 для оси времени: даты берутся из файлов кейса ----------
const CASE14 = path.join(ROOT, "examples", "cases", "14_old_director_charter_cleared");
const C14 = (() => {
  const egrul = JSON.parse(fs.readFileSync(path.join(CASE14, "egrul.json"), "utf8"));
  const kase = JSON.parse(fs.readFileSync(path.join(CASE14, "case.json"), "utf8"));
  const poa = fs.readFileSync(path.join(CASE14, "poa-root.xml"), "utf8");
  const attr = (name) => (poa.match(new RegExp(`${name}="([^"]+)"`)) || [])[1];
  const [prev, next] = egrul.executives;
  return { prev, next, issued: attr("ДатаВыдДовер"), until: attr("СрокДейст"), op: kase.operation, poa: Object.keys(kase.registry)[0] };
})();

const THEME = JSON.parse(fs.readFileSync(path.join(__dirname, "theme.json"), "utf8"));
const HEX = THEME.colors;

// Цвета без слота в теме: оттенки плиток, текст второго плана, статусы вердиктов.
const TINT = { blue: "E6F0FF", blueLine: "C9DCFA", line: "DCE4F0", soft: "F3F6FB", text2: "42557A", muted: "5E6D8C", ice: "CFE3FF", navyTile: "1D46A8" };
const ST = {
  ok: { label: "ПОДТВЕРЖДЕНО", base: HEX.accent3, tint: "E2F4EB", text: "0B6B44", icon: "Check" },
  no: { label: "ОПРОВЕРГНУТО", base: HEX.accent5, tint: "FDE6E7", text: "A3161B", icon: "X" },
  rv: { label: "НА РАЗБОР", base: HEX.accent4, tint: "FFF1D4", text: "7A4E00", icon: "UserSearch" },
  nd: { label: "НЕДОСТАТОЧНО ДАННЫХ", base: HEX.accent6, tint: "E8ECF3", text: "3B4864", icon: "CircleHelp" },
};

// ---------- Русская типографика: неразрывные пробелы (PROMPT.md, раздел 6) ----------
const NB = " ";
const LETTER = "А-Яа-яЁёA-Za-z";
function typo(input) {
  if (typeof input !== "string") return input;
  let s = input;
  for (let i = 0; i < 2; i++) {
    // Просмотр назад, а не захват: цепочка коротких слов («и с доказательством») связывается за один проход.
    s = s.replace(new RegExp(`(?<=^|[\\s«„(\\/])([${LETTER}]{1,2})\\s+(?=[${LETTER}0-9«„(№])`, "g"), `$1${NB}`);
  }
  s = s.replace(/\s+—/g, `${NB}—`);
  s = s.replace(/(?<![\d.])(\d+(?:,\d+)?)\s+(?=[а-яё₽%]|ФЗ|МЧД)/g, `$1${NB}`);
  s = s.replace(/\s+(?=₽)/g, NB);
  s = s.replace(/([≈≤≥×÷])\s+(?=\d)/g, `$1${NB}`);
  s = s.replace(/(\d)\s+\/\s+(?=\d)/g, `$1${NB}/${NB}`);
  s = s.replace(/\s+(?=[№§])/g, NB).replace(/([№§])\s+/g, `$1${NB}`);
  s = s.replace(/(RFC|ISO|ГОСТ Р)\s+(?=\d)/g, `$1${NB}`);
  s = s.replace(/«ВТБ\s+Бизнес»/g, `«ВТБ${NB}Бизнес»`);
  // Неразрывный дефис в коротких русских составных словах и кодах актов: «243‑ФЗ», «ОД‑2506», «стоп‑критерий».
  // Технические строки (пути, команды, латиница) не трогаются: их должно быть можно скопировать как есть.
  s = s.replace(/(^|[\s«(„])([А-ЯЁа-яёA-Z0-9]{1,6})-(?=[А-ЯЁа-яё0-9]+(?=[\s,.;:»)“]|$))/g, (m, pre, left, offset, all) => {
    const right = all.slice(offset + m.length).match(/^[А-ЯЁа-яё0-9]+/)[0];
    return /[А-ЯЁа-яё]/.test(left + right) ? `${pre}${left}‑` : m;
  });
  s = s.replace(new RegExp(`(?<![${LETTER}])(ст|п|подп|ч|гл|г|кв)\\.\\s+(?=[0-9IVX])`, "g"), `$1.${NB}`);
  s = s.replace(/([А-ЯЁ]\.)\s+(?=[А-ЯЁ]\.)/g, `$1${NB}`);
  s = s.replace(/([А-ЯЁ][а-яё]+)\s+(?=[А-ЯЁ]\.\s?[А-ЯЁ]\.)/g, `$1${NB}`);
  s = s.replace(/ГК\s+РФ/g, `ГК${NB}РФ`);
  // Коды строк реестра («M-07», «C-15»), номера судебных дел и стандартов не рвутся на дефисе.
  s = s.replace(/(?<![\w-])([MCRS])-(\d{2,3})(?![\w-])/g, "$1‑$2");
  s = s.replace(/(\d{3})-([А-ЯЁ]{2}\d{2})-(\d+)/g, "$1‑$2‑$3");
  s = s.replace(/((?:ISO|ГОСТ Р)\s\d+)-(\d+)/g, "$1‑$2");
  s = s.replace(/(\d)\s(?=\d{3}(?!\d))/g, `$1${NB}`);
  return s;
}
const typoRuns = (t) => (Array.isArray(t) ? t.map((r) => ({ ...r, text: typo(r.text) })) : typo(t));

// ---------- Приложение: порядок и номера слайдов для оглавления со ссылками ----------
const MAIN_SLIDES = 19;
const APPX = [
  ["A1", "Семь взглядов на «Мандат»"],
  ["A2", "Критерии жюри"],
  ["A3", "Прослеживаемость чисел"],
  ["A4", "Путь сотрудника и клиента"],
  ["A5", "Рынок и конкуренты"],
  [`A6`, `${RULES} с нормой`],
  ["A7", "Пять норм ручной сверки"],
  ["A8", "Доверие без доступа к базе"],
  ["A9", "Методика измерения качества"],
  ["A10", "Информационная безопасность"],
  ["A11", "Риски и как их снимаем"],
  ["A12", "Расчёт эффекта"],
  ["A13", "Реальные кейсы"],
  ["A14", "Открытые стандарты и SOTA 2026"],
  ["A15", "Трудные вопросы (1/2)"],
  ["A16", "Трудные вопросы (2/2)"],
  ["A17", "Нормативная база"],
  ["A18", "Дорожная карта"],
];
const INDEX_SLIDE = MAIN_SLIDES + 1;
const appxPage = (id) => INDEX_SLIDE + 1 + APPX.findIndex(([a]) => a === id);

// Настоящий вывод `./run-demo.sh` и команд демо (docs/DEMO.md): строки дословно, столбцы таблицы сужены под ширину
// окна; «…» — пропущенные строки и аргументы, «# код» — код выхода команды. Полный вывод снимает
// submission/video/capture.py в submission/video/out/outputs.json.
const TERMINAL = {
  title: "mandat · демо на стенде",
  lines: [
    ["cmd", "mandat demo"],
    ["dim", "кейс                             ожидалось  получено"],
    ["dim", "…"],
    ["out", "11_parent_revoked                refuted    ОПРОВЕРГНУТО"],
    ["dim", "…"],
    ["out", "14_old_director_charter_cleared  confirmed  ПОДТВЕРЖДЕНО"],
    ["ok", "журнал: 14 записей, целостность OK"],
    ["cmd", "mandat attest-verify var/demo/attestations/01_ok.json …"],
    ["ok", "статус удостоверения: действует           # код 0"],
    ["cmd", "mandat monitor examples/events/takeover.json …"],
    ["warn", '  "kind": "possible_takeover",'],
    ["warn", '  "risk": "high",   …   "attestation_action": "suspension",'],
    ["cmd", "mandat attest-verify var/demo/attestations/01_ok.json …"],
    ["err", "не принимается: статус удостоверения: приостановлено  # код 3"],
  ],
};

(async () => {
  const G = await buildGraphics({ terminal: TERMINAL, qrSvg: path.join(ASSETS, "qr-github.svg") });
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
  pres.author = "Команда «Мандат»";
  pres.company = "Команда «Мандат»";
  pres.title = "Мандат — единое окно полномочий для бизнес-клиентов банка";
  pres.subject = "Академия Финтеха 2026, трек ВТБ «Финтех без границ»: онбординг и проверка полномочий";
  pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
  const C = pres.SchemeColor;
  const FONT = THEME.bodyFontFace;

  const L = 0.6, W = 12.133, R = L + W, TOP = 1.72, BOTTOM = 6.72, GAP = 0.25;

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
      { placeholder: { options: { name: "kicker", type: "body", x: L, y: 0.3, w: 8.5, h: 0.3, fontSize: 11, bold: true, color: C.accent1, charSpacing: 1.5, align: "left", valign: "middle", margin: 0 }, text: "" } },
      { placeholder: { options: { name: "title", type: "title", x: L, y: 0.64, w: W, h: 0.92, fontSize: 26, bold: true, color: C.text2, align: "left", valign: "top", margin: 0, lineSpacingMultiple: 0.95 }, text: "" } },
      { placeholder: { options: { name: "source", type: "body", x: L, y: 6.86, w: 11.25, h: 0.46, fontSize: 10, color: TINT.muted, align: "left", valign: "middle", margin: 0 }, text: "" } },
    ],
    slideNumber: { x: 12.13, y: 6.95, w: 0.6, h: 0.3, fontSize: 10, color: TINT.muted, align: "right" },
  });
  pres.defineSlideMaster({
    title: "Раздел",
    background: { data: G.bgDark },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: L, y: 0.62, w: 11.0, h: 0.9, fontSize: 34, bold: true, color: C.background1, align: "left", valign: "middle", margin: 0 }, text: "" } },
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
    s.addText(typoRuns(text), {
      x, y, w, h, isTextBox: true, margin: o.margin ?? 0, fontSize: o.size ?? 14, color: o.color ?? C.text1,
      bold: o.bold ?? false, italic: o.italic ?? false, align: o.align ?? "left", valign: o.valign ?? "top",
      paraSpaceAfter: o.psa, lineSpacingMultiple: o.lsm ?? 1.0, charSpacing: o.cs, objectName: o.name,
      fontFace: o.font, hyperlink: o.link,
    });
  function card(s, x, y, w, h, o = {}) {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y, w, h, rectRadius: o.r ?? 0.12,
      fill: { color: o.fill ?? C.background1, transparency: o.transparency },
      line: o.line === null ? { type: "none" } : { color: o.line ?? TINT.line, width: o.lw ?? 0.75, dashType: o.dash },
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
  function dot(s, x, y, d, fill, name) {
    s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: fill }, line: { type: "none" }, objectName: "Круг значка" });
    const p = d * 0.22;
    s.addImage({ data: G.iconLight[name], x: x + p, y: y + p, w: d - 2 * p, h: d - 2 * p, objectName: "Значок" });
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
    T(s, label, x + 0.33, y, w - 0.4, h, { size, bold: true, color: st.text, valign: "middle" });
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
      items.map((t, i) => ({ text: typo(t), options: { bullet: { indent: 12 }, breakLine: i < items.length - 1 } })),
      { x, y, w, h, isTextBox: true, margin: 0, fontSize: o.size ?? 13, color: o.color ?? C.text1, paraSpaceAfter: o.psa ?? 5, valign: "top", lineSpacingMultiple: 1.0, objectName: o.name ?? "Список" },
    );
  }
  function band(s, x, y, w, h, text, o = {}) {
    card(s, x, y, w, h, { fill: o.fill ?? C.text2, line: null, shadow: o.shadow ?? true, name: "Плашка вывода" });
    let tx = x + 0.3;
    if (o.icon) {
      const lightTile = (o.tile ?? TINT.navyTile) === "FFFFFF";
      tile(s, x + 0.22, y + (h - 0.5) / 2, 0.5, o.icon, { fill: o.tile ?? TINT.navyTile, icon: lightTile ? "cobalt" : "light" });
      tx = x + 0.92;
    }
    T(s, text, tx, y, w - (tx - x) - 0.3, h, { size: o.size ?? 15, bold: o.bold ?? false, color: o.color ?? C.background1, valign: "middle" });
  }
  const pages = () => pres.slides.length;
  // Ссылка — прозрачная зона поверх текста: PowerPoint не перекрашивает текст в цвет гиперссылки темы.
  function link(s, x, y, w, h, slideNo, tooltip) {
    s.addImage({ data: G.clear, x, y, w, h, hyperlink: { slide: slideNo, tooltip }, altText: `Переход: ${tooltip}`, objectName: `Ссылка на слайд ${slideNo}` });
  }
  function slide(master, section, kicker, title, source, o = {}) {
    const s = pres.addSlide({ masterName: master, sectionTitle: section });
    if (kicker) s.addText(typo(kicker), { placeholder: "kicker" });
    if (title) s.addText(typo(title), { placeholder: "title" });
    if (source) s.addText(typo(source), { placeholder: "source" });
    if (o.appx) {
      if (pages() !== appxPage(o.appx)) throw new Error(`слайд ${o.appx}: номер ${pages()}, а в оглавлении ${appxPage(o.appx)}`);
      T(s, "← к оглавлению приложения", 8.6, 0.3, 2.9, 0.3, { size: 10.5, bold: true, color: C.accent1, align: "right", valign: "middle", name: "Ссылка на оглавление" });
      link(s, 8.6, 0.28, 2.9, 0.34, INDEX_SLIDE, "оглавление приложения");
    }
    return s;
  }
  // Таблица приложения: шапка тёмно-синяя, строки с чередованием, один столбец можно выделить.
  function table(s, header, rows, x, y, w, colW, o = {}) {
    const size = o.size ?? 11;
    const hl = o.highlight;
    const head = header.map((t, i) => ({
      text: typo(t),
      options: { bold: true, color: "FFFFFF", fill: { color: i === hl ? HEX.accent1 : HEX.dk2 }, fontSize: size, valign: "middle", fontFace: FONT },
    }));
    const body = rows.map((r, ri) =>
      r.map((t, i) => ({
        text: typo(t),
        options: {
          fontSize: size, valign: "middle", fontFace: FONT, color: i === hl ? HEX.accent1 : i === 0 ? HEX.dk2 : HEX.dk1,
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
  const chartBase = () => ({
    dataLabelFontFace: FONT, catAxisLabelFontFace: FONT, valAxisLabelFontFace: FONT,
    catAxisLabelColor: TINT.text2, catAxisLineShow: false, valAxisHidden: true, valAxisMinVal: 0,
    valGridLine: { style: "none" }, catGridLine: { style: "none" }, showLegend: false, showTitle: false,
    showValue: true, dataLabelPosition: "outEnd", dataLabelFontBold: true, dataLabelColor: HEX.dk2,
  });

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
    T(s, [{ text: "Единое окно полномочий", options: { breakLine: true } }, { text: "для бизнес-клиентов банка" }], L, 2.75, 6.6, 0.95, { size: 24, bold: true, color: C.background1 });
    T(s, "Отвечает, вправе ли человек провести операцию от имени компании в этот день, — за миллисекунды и с доказательством до поля документа", L, 3.8, 6.3, 1.0, { size: 15, color: TINT.ice, lsm: 1.1 });
    let px = L;
    for (const k of ["ok", "no", "rv", "nd"]) px += pill(s, px, 5.0, k, { size: 9.5, h: 0.34 }) + 0.12;
    T(s, "Коньков Дмитрий", L, 6.12, 4.0, 0.34, { size: 14, bold: true, color: C.background1 });
    T(s, "капитан команды «Мандат»", L, 6.46, 4.0, 0.3, { size: 12, color: TINT.ice });
    T(s, "Онлайн-питч · 10–15 ноября 2026", 8.5, 6.46, 4.23, 0.3, { size: 12, color: TINT.ice, align: "right" });
    const hw = 5.55;
    s.addImage({ data: G.hero, x: 7.3, y: 0.62, w: hw, h: hw * G.heroRatio, altText: "Схема: шесть источников — ЕГРЮЛ, МЧД, реестр ФНС, передоверие, устав и события — сходятся в один вердикт на дату операции" });
    s.addNotes("[0:00–0:10] Здравствуйте, мы — команда «Мандат». Мы отвечаем, вправе ли человек провести операцию от имени компании в конкретный день, и доказываем ответ. Суть — за двадцать секунд.");
  }

  // ---------- 2. Коротко ----------
  {
    const s = slide("Контент", "Питч", "КОРОТКО", "Проверка полномочий на дату с доказательством — и шесть недель теневого пилота, чтобы измерить её на данных ВТБ",
      `Банк России, II кв. 2026 (M-15); evidence.json от ${N.asOf} (C-03, C-13, C-17); финансовая модель пакета аналитики; docs/PILOT.md`);
    const cols = [
      ["ПРОБЛЕМА", "TriangleAlert", AVG_Q2, `средняя операция без согласия против юрлица — в ${CBR.q2.ratio} раз крупнее средней; захват компании — за пять рабочих дней`, null],
      ["РЕШЕНИЕ", "Scale", "4 ответа", "на вопрос «вправе ли X провести операцию O за компанию Y на дату t» — с нормой и полем документа", null],
      ["ДОКАЗАТЕЛЬСТВА", "BadgeCheck", `${N.falseConfirmed} из ${N.negatives}`, `ложных «да» на кейсах-ловушках ${N.negativeTypes} видов; ${FUZZ_TXT} мутаций без сбоя; ${TESTS}`, null],
      ["ЗАПРОС", "Rocket", "6 недель", `теневой пилот на праве подписи платёжных поручений по МЧД; для ВТБ окупаемость — ${PAYBACK}`, 1],
    ];
    const more = [["подробнее: слайды 3–5", 3], ["подробнее: слайды 6–9", 6], ["подробнее: слайд 10 и A3", 10], ["подробнее: слайд 17", 17]];
    const cw = (W - 3 * GAP) / 4, ch = 3.85;
    cols.forEach(([k, ic, v, t, hl], i) => {
      const x = L + i * (cw + GAP);
      card(s, x, TOP, cw, ch, hl ? { fill: C.text2, line: null } : {});
      T(s, k, x + 0.28, TOP + 0.28, cw - 1.0, 0.3, { size: 11, bold: true, color: hl ? HEX.accent2 : C.accent1, cs: 1.2 });
      tile(s, x + cw - 0.78, TOP + 0.2, 0.5, ic, hl ? { fill: TINT.navyTile, icon: "light" } : {});
      T(s, v, x + 0.28, TOP + 0.95, cw - 0.36, 0.8, { size: i === 0 ? 26 : 30, bold: true, color: hl ? C.background1 : C.accent1, valign: "middle" });
      T(s, t, x + 0.28, TOP + 1.85, cw - 0.56, 1.45, { size: 13, color: hl ? TINT.ice : C.text1 });
      const [label, target] = more[i];
      s.addShape(pres.shapes.LINE, { x: x + 0.28, y: TOP + ch - 0.52, w: cw - 0.56, h: 0, line: { color: hl ? "2E4F9E" : TINT.line, width: 0.75 }, objectName: "Разделитель" });
      T(s, `${label} →`, x + 0.28, TOP + ch - 0.46, cw - 0.56, 0.32, { size: 11, bold: true, color: hl ? HEX.accent2 : C.accent1, valign: "middle" });
      link(s, x, TOP + ch - 0.5, cw, 0.42, target, label);
    });
    band(s, L, TOP + ch + GAP, W, BOTTOM - (TOP + ch + GAP), "Главное правило «Мандата»: неизвестное никогда не становится «да». Молчание реестра — это «недостаточно данных», а не «действует».", { icon: "ShieldCheck", size: 15, bold: true });
    s.addNotes(`[0:10–0:28] Коротко. Одна операция без согласия против компании стоит в среднем больше миллиона рублей, а захват через смену директора занимает пять рабочих дней. Мы отвечаем на один вопрос четырьмя вердиктами и доказываем каждый. На ${N.negatives} кейсах-ловушках — ноль ложных «да». Просим шесть недель теневого пилота. Почему это важно?`);
  }

  // ---------- 3. Цена ошибки ----------
  {
    const s = slide("Контент", "Питч", "ЦЕНА ОШИБКИ", `Компании теряют редко, но крупно: ${AVG_Q2} на одну операцию против юрлица`,
      "Банк России: обзоры отчётности об инцидентах ИБ при переводе денежных средств за I кв. (cbr.ru, 14.05.2026) и II кв. 2026 (04.08.2026), таблицы по юрлицам (M-07, M-15); fintechacademy.ru — задача трека ВТБ");
    card(s, L, TOP, 4.75, 3.78);
    T(s, "II квартал 2026 · операции без согласия клиента", L + 0.3, TOP + 0.22, 4.2, 0.3, { size: 12, bold: true, color: TINT.text2 });
    T(s, AVG_Q2, L + 0.3, TOP + 0.55, 4.2, 0.85, { size: 46, bold: true, color: C.accent1, valign: "middle" });
    T(s, `средняя операция против юрлица: ${CBR.q2.ops} операций на ${Math.round(CBR.q2.lossMln)} млн ₽`, L + 0.3, TOP + 1.42, 4.2, 0.5, { size: 12, color: TINT.text2 });
    const bx = L + 0.3, bw = 4.15;
    T(s, `юрлица · ${AVG_Q2}`, bx, TOP + 2.02, bw, 0.26, { size: 11, bold: true, color: C.text2 });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: bx, y: TOP + 2.3, w: bw, h: 0.24, rectRadius: 0.12, fill: { color: HEX.accent1 }, line: { type: "none" }, objectName: "Столбик: юрлица" });
    T(s, `все клиенты · ≈${Math.round(CBR.q2.allAvgK)} тыс. ₽ — в ${CBR.q2.ratio} раз меньше`, bx, TOP + 2.66, bw, 0.26, { size: 11, bold: true, color: C.text2 });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: bx, y: TOP + 2.94, w: 0.1, h: 0.24, rectRadius: 0.05, fill: { color: TINT.blueLine }, line: { type: "none" }, objectName: "Столбик: все клиенты" });
    T(s, `${comma(CBR.q2.allOps / 1000, 1)} тыс. операций на ${comma(CBR.q2.allLossMln / 1000, 2)} млрд ₽`, bx, TOP + 3.3, bw, 0.28, { size: 11, color: TINT.muted });
    const bX = L + 4.75 + GAP, bW = 3.85;
    card(s, bX, TOP, bW, 3.78);
    T(s, "Потери юрлиц растут, млн ₽", bX + 0.3, TOP + 0.22, bW - 0.6, 0.3, { size: 12, bold: true, color: TINT.text2 });
    s.addChart(pres.charts.BAR, [{ name: "Похищено у юрлиц, млн ₽", labels: ["I кв. 2026", "II кв. 2026"], values: [CBR.q1.lossMln, Math.round(CBR.q2.lossMln)] }], {
      ...chartBase(), x: bX + 0.2, y: TOP + 0.5, w: bW - 0.4, h: 1.95, barDir: "col", barGapWidthPct: 55,
      chartColors: ["9DB8E8", HEX.accent1], dataLabelFontSize: 14, dataLabelFormatCode: "#,##0", catAxisLabelFontSize: 11,
      valAxisMaxVal: 1000, objectName: "Диаграмма: похищено у юрлиц по кварталам",
    });
    T(s, "доля юрлиц в потерях", bX + 0.3, TOP + 2.5, bW - 0.6, 0.26, { size: 11, color: TINT.text2 });
    T(s, `${comma(CBR.q1.shareAll, 1)}% → ${comma(CBR.q2.shareAll, 1)}%`, bX + 0.3, TOP + 2.76, bW - 0.6, 0.46, { size: 22, bold: true, color: C.text2, valign: "middle" });
    T(s, `+${CBR.q2.growth}% за квартал; ${CBR.q2.accounts} из ${CBR.q2.ops} операций — со счетов`, bX + 0.3, TOP + 3.26, bW - 0.6, 0.4, { size: 11, color: TINT.muted });
    const cX = bX + bW + GAP, cW = R - cX;
    card(s, cX, TOP, cW, 1.78);
    T(s, `${comma(CBR.q2.refundAccounts, 1)}% и ${comma(CBR.q2.refundSbp, 1)}%`, cX + 0.3, TOP + 0.2, cW - 0.6, 0.6, { size: 26, bold: true, color: HEX.accent5, valign: "middle" });
    T(s, "столько возмещено компаниям во II кв. по счетам и через СБП — остальное теряет бизнес", cX + 0.3, TOP + 0.85, cW - 0.6, 0.8, { size: 12, color: TINT.text2 });
    card(s, cX, TOP + 2.0, cW, 1.78, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
    tile(s, cX + 0.3, TOP + 2.2, 0.46, "Landmark", { fill: "FFFFFF" });
    T(s, "Задача трека ВТБ", cX + 0.88, TOP + 2.2, cW - 1.1, 0.46, { size: 13, bold: true, color: C.text2, valign: "middle" });
    T(s, "«Онбординг и проверка полномочий» — первое направление для бизнеса", cX + 0.3, TOP + 2.78, cW - 0.6, 0.9, { size: 12, color: C.text1 });
    band(s, L, 5.72, W, 0.95, "Право подписи складывается из трёх источников — МЧД, выписки ЕГРЮЛ и устава — и меняется со временем. Сегодня их сводит вручную сотрудник банка.", { icon: "Layers", size: 15 });
    s.addNotes(`[0:28–0:46] По данным Банка России, во втором квартале у компаний ${CBR.q2.ops} операций без согласия на ${Math.round(CBR.q2.lossMln)} миллион рублей — больше миллиона на операцию, в ${CBR.q2.ratio} раз больше средней. Возвращают компаниям меньше пятой части. А право подписи сотрудник сводит вручную из трёх документов. Как этим пользуются мошенники?`);
  }

  // ---------- 4. Сценарий захвата ----------
  {
    const s = slide("Контент", "Питч", "СЦЕНАРИЙ ЗАХВАТА", "Захвату хватает пяти рабочих дней, а сверка «на сегодня» не видит событий между обращениями",
      "129-ФЗ, ст. 8 (срок регистрации); ГК РФ, ст. 188; разъяснения ФНС; Forbes и АБН по данным РБК, 22.10.2026 — случай «ЭОС» по версии компании (M-17); ВС РФ, Определение СКЭС от 02.05.2023 № 308-ЭС22-17199 (M-35)");
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
    const y2 = TOP + sh + 0.22, h2 = 2.3;
    card(s, L, y2, 5.95, h2);
    tile(s, L + 0.3, y2 + 0.25, 0.5, "Siren", { fill: ST.no.tint });
    T(s, "Реальный случай · октябрь 2026", L + 0.95, y2 + 0.22, 4.7, 0.24, { size: 11, bold: true, color: ST.no.text });
    T(s, "«ЭОС»: 10 дней с чужим гендиректором", L + 0.95, y2 + 0.46, 4.8, 0.32, { size: 14, bold: true, color: C.text2 });
    T(s, "09.10 в ЕГРЮЛ внесли неизвестного компании гендиректора — по версии компании, по поддельным документам, в том числе доверенностям от имени участников. Прежних руководителей восстановили только 19.10: десять дней реестр показывал банкам чужого руководителя.", L + 0.3, y2 + 0.9, 5.35, h2 - 0.98, { size: 12, color: C.text1 });
    const tx = L + 5.95 + 0.23, tw = R - tx;
    card(s, tx, y2, tw, h2);
    tile(s, tx + 0.3, y2 + 0.25, 0.5, "Scale");
    T(s, "Ловушка в обе стороны", tx + 0.95, y2 + 0.25, tw - 1.2, 0.26, { size: 14, bold: true, color: C.text2 });
    T(s, "Смена директора не прекращает МЧД — п. 1 ст. 188 ГК РФ, позиция ФНС", tx + 0.95, y2 + 0.52, tw - 1.2, 0.3, { size: 12, bold: true, color: C.accent1 });
    dot(s, tx + 0.36, y2 + 1.14, 0.38, HEX.accent5, "X");
    T(s, "Отказ законному руководителю — нарушение банка: суд обязал провести платёж на 6,5 млн ₽, ВС РФ оставил это в силе (№ 308-ЭС22-17199)", tx + 0.95, y2 + 1.0, tw - 1.25, 0.66, { size: 12, valign: "middle" });
    dot(s, tx + 0.36, y2 + 1.8, 0.38, HEX.accent4, "TriangleAlert");
    T(s, "Пропустите свежую смену — откроете дверь захвату", tx + 0.95, y2 + 1.72, tw - 1.25, 0.54, { size: 12, valign: "middle" });
    band(s, L, 5.9, W, 0.8, "Нужен ответ на дату операции — с историей событий ЕГРЮЛ и реестра МЧД, а не снимок «на сегодня»", { icon: "History", size: 15, bold: true });
    s.addNotes("[0:46–1:04] Захват выглядит так: заявление о смене директора, через пять рабочих дней новый директор в ЕГРЮЛ, новая доверенность, заявка в банк и платёж. Это не теория: в октябре у агентства «ЭОС» десять дней значился чужой гендиректор. И ловушка в обе стороны: откажете законному руководителю — суд обяжет банк провести платёж, и Верховный Суд такое решение оставил в силе; пропустите свежую смену — откроете дверь захвату.");
  }

  // ---------- 5. Ось времени ----------
  {
    const s = slide("Контент", "Питч", "КЛЮЧЕВАЯ ИДЕЯ", "Полномочие — это пересечение интервалов на дату операции, а не статус документа",
      `Демо-кейс 14 (examples/cases/14_old_director_charter_cleared): даты — из egrul.json и poa-root.xml; данные вымышлены, отметка юриста об уставе синтетическая; окно «свежей» смены — 30 дней (ADR-002)`);
    const d0 = Date.UTC(2026, 0, 1), d1 = Date.UTC(2027, 2, 31);
    const lx = L + 2.5, rx = R - 0.15;
    const X = (iso) => lx + ((Date.parse(iso) - d0) / (d1 - d0)) * (rx - lx);
    const ch = 4.1;
    card(s, L, TOP, W, ch);
    const ticks = [["2026-01-01", "янв 2026"], ["2026-04-01", "апр"], ["2026-07-01", "июл"], ["2026-10-01", "окт"], ["2027-01-01", "янв 2027"]];
    const laneTop = TOP + 0.55, laneH = 0.5, laneGap = 0.14;
    const lanes = 5, lanesBottom = laneTop + lanes * laneH + (lanes - 1) * laneGap;
    ticks.forEach(([d, label]) => {
      vline(s, X(d), laneTop - 0.08, lanesBottom - laneTop + 0.1, "E3E9F2", 0.75);
      T(s, label, X(d) - 0.6, TOP + 0.16, 1.2, 0.26, { size: 10, color: TINT.muted, align: "center" });
    });
    const laneY = (i) => laneTop + i * (laneH + laneGap);
    const labels = ["Директор по ЕГРЮЛ", `МЧД ${C14.poa.slice(0, 8)}…`, "Реестр МЧД ФНС", "Смена руководителя", "Устав"];
    labels.forEach((t, i) => T(s, t, L + 0.25, laneY(i), 2.15, laneH, { size: 12, bold: true, color: C.text2, valign: "middle" }));
    const bar = (i, from, to, fill, text, color, o = {}) => {
      const x0 = Math.max(lx, X(from)), x1 = Math.min(rx, X(to));
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x0, y: laneY(i) + 0.06, w: x1 - x0, h: laneH - 0.12, rectRadius: 0.08, fill: { color: fill }, line: o.line ? { color: o.line, width: 1, dashType: o.dash } : { type: "none" }, objectName: `Интервал: ${text}` });
      T(s, text, x0 + 0.1, laneY(i) + 0.06, x1 - x0 - 0.2, laneH - 0.12, { size: 10.5, bold: true, color, valign: "middle" });
    };
    const fmt = (iso) => ruDate(iso);
    bar(0, "2025-12-01", C14.prev.valid_to, "DCE7FA", `← ${C14.prev.full_name.replace(/^(\S+) (\S)\S* (\S)\S*$/, "$1 $2. $3.")} · с ${C14.prev.valid_from.slice(0, 4)} до ${fmt(C14.prev.valid_to)}`, HEX.dk2);
    bar(0, C14.next.valid_from, "2027-05-01", "BCD3F6", `${C14.next.full_name.replace(/^(\S+) (\S)\S* (\S)\S*$/, "$1 $2. $3.")} · с ${fmt(C14.next.valid_from)}`, HEX.dk2);
    bar(1, C14.issued, C14.until, HEX.accent1, `выдана ${fmt(C14.issued)} · до ${fmt(C14.until)}`, "FFFFFF");
    bar(2, C14.issued, "2027-05-01", "E2F4EB", "статус: действует (на стенде — из пакета кейса)", ST.ok.text);
    const tISO = C14.op.date;
    const winFrom = new Date(Date.parse(tISO) - 30 * 86400000).toISOString().slice(0, 10);
    s.addShape(pres.shapes.RECTANGLE, { x: X(winFrom), y: laneY(3) + 0.04, w: X(tISO) - X(winFrom), h: laneH - 0.08, fill: { color: "FFF1D4" }, line: { color: HEX.accent4, width: 1, dashType: "dash" }, objectName: "Окно свежей смены" });
    T(s, "← окно «свежей» смены: 30 дней до операции", X(tISO) + 0.12, laneY(3), rx - X(tISO) - 0.15, laneH, { size: 10, bold: true, color: ST.rv.text, valign: "middle" });
    const ex = X(C14.next.valid_from);
    s.addShape(pres.shapes.DIAMOND, { x: ex - 0.14, y: laneY(3) + laneH / 2 - 0.14, w: 0.28, h: 0.28, fill: { color: HEX.accent4 }, line: { color: "FFFFFF", width: 1.5 }, objectName: "Событие: смена руководителя" });
    T(s, `${fmt(C14.next.valid_from)}: МЧД не прекращается (п. 1 ст. 188 ГК РФ)`, ex + 0.22, laneY(3), X(winFrom) - ex - 0.35, laneH, { size: 10, bold: true, color: ST.rv.text, valign: "middle" });
    bar(4, "2025-12-01", "2027-05-01", "EEF1F6", "ограничений для операции нет — синтетическая отметка юриста", TINT.text2);
    const tx = X(tISO);
    vline(s, tx, laneTop - 0.12, lanesBottom - laneTop + 0.2, HEX.dk2, 2, "dash");
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: tx - 1.15, y: lanesBottom + 0.12, w: 2.3, h: 0.34, rectRadius: 0.17, fill: { color: HEX.dk2 }, line: { type: "none" }, objectName: "Дата операции" });
    T(s, `t = ${fmt(tISO)} · ${C14.op.amount_rub.toLocaleString("ru-RU")} ₽`, tx - 1.15, lanesBottom + 0.12, 2.3, 0.34, { size: 10.5, bold: true, color: "FFFFFF", align: "center", valign: "middle" });
    const y2 = TOP + ch + GAP, h2 = BOTTOM - y2, w2 = (W - GAP) / 2;
    card(s, L, y2, w2, h2, { fill: ST.no.tint, line: "F6B9BC", shadow: false });
    dot(s, L + 0.22, y2 + (h2 - 0.4) / 2, 0.4, HEX.accent5, "X");
    T(s, [{ text: "Сверка «на сегодня»: ", options: { bold: true } }, { text: "директор сейчас другой, чем подписал МЧД, — соблазн отказать по действующей доверенности" }], L + 0.78, y2, w2 - 1.0, h2, { size: 12, color: ST.no.text, valign: "middle" });
    card(s, L + w2 + GAP, y2, w2, h2, { fill: ST.ok.tint, line: "A9DDC3", shadow: false });
    dot(s, L + w2 + GAP + 0.22, y2 + (h2 - 0.4) / 2, 0.4, HEX.accent3, "Check");
    T(s, [{ text: "Проверка на дату t: ", options: { bold: true } }, { text: "МЧД подписал директор на дату выдачи, она в сроке и не отменена, смена давняя → ПОДТВЕРЖДЕНО с сигналом" }], L + w2 + GAP + 0.78, y2, w2 - 1.0, h2, { size: 12, color: ST.ok.text, valign: "middle" });
    s.addNotes("[1:04–1:22] Ключевая идея: полномочие — это пересечение интервалов на дату операции. Реальный демо-кейс: директор сменился в мае, доверенность выдал прежний директор в марте, она действует до февраля. Сверка «на сегодня» видит другого директора и отказывает. Проверка на дату видит действующую доверенность от законного директора — подтверждено, с сигналом.");
  }

  // ---------- 6. Решение ----------
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
    const cw = (W - GAP * 3) / 4, cy = TOP + 0.95, ch = 2.78;
    items.forEach(([k, head, gets], i) => {
      const x = L + i * (cw + GAP);
      card(s, x, cy, cw, ch);
      dot(s, x + 0.25, cy + 0.22, 0.5, ST[k].base, ST[k].icon);
      pill(s, x + 0.25, cy + 0.86, k, { size: 9.5, h: 0.3 });
      T(s, head, x + 0.25, cy + 1.26, cw - 0.5, 0.56, { size: 14, bold: true, color: C.text2 });
      T(s, "Банк получает", x + 0.25, cy + 1.84, cw - 0.5, 0.22, { size: 10.5, bold: true, color: TINT.muted });
      T(s, gets, x + 0.25, cy + 2.06, cw - 0.5, 0.66, { size: 12, color: C.text1 });
    });
    const steps = [
      ["01", "Принять", "МЧД XML, ЕГРЮЛ, устав, бумажная доверенность"],
      ["02", "Проверить", `граф полномочий на дату, ${RULES} с нормой`],
      ["03", "Решить", "вердикт, доказательства, следующий шаг"],
      ["04", "Следить", "события ЕГРЮЛ и реестра МЧД"],
    ];
    const py = cy + ch + GAP, ph = BOTTOM - py;
    steps.forEach(([n, head, t], i) => {
      const x = L + i * (cw + GAP);
      card(s, x, py, cw, ph, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
      T(s, n, x + 0.22, py + 0.1, 0.5, 0.34, { size: 17, bold: true, color: C.accent1 });
      T(s, head, x + 0.72, py + 0.1, cw - 0.9, 0.34, { size: 14, bold: true, color: C.text2, valign: "middle" });
      T(s, t, x + 0.22, py + 0.48, cw - 0.4, ph - 0.52, { size: 12, color: C.text1 });
      if (i < steps.length - 1) arrow(s, x + cw + 0.03, py + ph / 2, GAP - 0.06, HEX.accent1, 1.5);
    });
    s.addNotes("[1:22–1:36] Решение — один вопрос и четыре ответа. Подтверждено — с рекомендацией роли. Опровергнуто — с нормой и звеном цепочки. На разбор — человеку с досье и сроком. Недостаточно данных — список недостающего. Неизвестное никогда не становится «да».");
  }

  // ---------- 7. Доказательность ----------
  {
    const s = slide("Контент", "Питч", "ДОКАЗАТЕЛЬНОСТЬ", "Каждый вердикт доказан: поле МЧД, запись ЕГРЮЛ, норма закона",
      "Демо-кейсы 14, 05 и 11 (mandat demo); данные демо вымышлены, отметка юриста об уставе в кейсе 14 синтетическая. Источники — реестр МЧД, ЕГРЮЛ, КЭП — подключаются через порты; отчёт называет источник каждого факта");
    const cw = 7.45, ch = BOTTOM - TOP;
    card(s, L, TOP, cw, ch);
    T(s, "Кейс 14 · синтетическая отметка юриста об уставе", L + 0.3, TOP + 0.25, 4.8, 0.28, { size: 11, bold: true, color: TINT.muted });
    pill(s, L + cw - 0.3 - pillW("ПОДТВЕРЖДЕНО"), TOP + 0.22, "ok");
    T(s, "Платёжное поручение на 480 000 ₽ · 20.10.2026", L + 0.3, TOP + 0.62, cw - 0.6, 0.34, { size: 16, bold: true, color: C.text2 });
    T(s, "Представитель Лебедева И. П. по МЧД от директора Петрова П. П.", L + 0.3, TOP + 0.98, cw - 0.6, 0.3, { size: 12, color: TINT.text2 });
    const rows = [
      ["ok", "Check", "МЧД подписал директор по ЕГРЮЛ на дату выдачи", `ЕГРЮЛ · ГРН ${C14.prev.grn}`],
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
    s.addNotes("[1:36–1:54] Каждая строка вердикта — с доказательством: ГРН записи ЕГРЮЛ, путь до поля МЧД, норма. Та же компания, но директор сменился 19 дней назад, — на разбор с приоритетом P1 и сроком четыре часа. А если отменили основную доверенность, передоверие теряет силу — опровергнуто. Это не макет — покажу живой вывод.");
  }

  // ---------- 8. Продукт работает ----------
  {
    const s = slide("Контент", "Питч", "ПРОДУКТ СЕГОДНЯ", `Работает сегодня: ${DEMOS}, удостоверения и журнал — одной командой`,
      `Скриншот и вывод — настоящие, сняты с ./run-demo.sh при сборке колоды (capture_demo.js); «…» — пропущенные строки. Сценарий показа — docs/DEMO.md. v${N.version}, evidence.json от ${N.asOf}`);
    const shot = { w: 1576, h: 1082 };
    const fw = 6.3, barH = 0.34, imgW = fw - 0.12, imgH = imgW * (shot.h / shot.w), fh = barH + imgH + 0.1;
    card(s, L, TOP, fw, fh, { r: 0.1, name: "Окно браузера" });
    s.addShape(pres.shapes.RECTANGLE, { x: L + 0.02, y: TOP + 0.02, w: fw - 0.04, h: barH - 0.02, fill: { color: "EEF2F8" }, line: { type: "none" }, objectName: "Панель браузера" });
    ["FF6B6B", "FFC35A", "5FD39A"].forEach((c, i) => s.addShape(pres.shapes.OVAL, { x: L + 0.16 + i * 0.18, y: TOP + 0.11, w: 0.12, h: 0.12, fill: { color: c }, line: { type: "none" }, objectName: "Кнопка окна" }));
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: L + 0.8, y: TOP + 0.06, w: fw - 1.0, h: 0.22, rectRadius: 0.11, fill: { color: "FFFFFF" }, line: { type: "none" }, objectName: "Адресная строка" });
    T(s, "var/demo/14_old_director_charter_cleared.html", L + 0.92, TOP + 0.06, fw - 1.2, 0.22, { size: 9, color: TINT.text2, valign: "middle" });
    s.addImage({ path: path.join(ASSETS, "demo-card-14.jpg"), x: L + 0.06, y: TOP + barH + 0.04, w: imgW, h: imgH, altText: "Скриншот карточки вердикта демо-кейса 14: «ПОДТВЕРЖДЕНО», сильнейшее возражение — смена руководителя с нормой п. 1 ст. 188 ГК РФ, операция на 480 000 ₽ 20.10.2026, организация и сигнал мониторинга" });
    T(s, "Карточка сотрудника: вердикт, сильнейшее возражение с нормой, операция и сигнал мониторинга — без ручной сводки", L, TOP + fh + 0.1, fw, BOTTOM - TOP - fh - 0.1, { size: 11.5, color: TINT.text2, valign: "middle" });
    const rx = L + fw + GAP, rw = R - rx;
    const th = rw * G.terminalRatio;
    s.addImage({ data: G.terminal, x: rx, y: TOP, w: rw, h: th, altText: "Вывод терминала: mandat demo — 14 кейсов совпали с ожиданием, журнал цел; удостоверение действует; после событий захвата — тревога possible_takeover и статус «приостановлено», код 3" });
    const ly = TOP + th + 0.2, lh = BOTTOM - ly;
    card(s, rx, ly, rw, lh);
    T(s, "Демо в 4 шага", rx + 0.25, ly + 0.14, rw - 0.5, 0.3, { size: 13, bold: true, color: C.text2 });
    const demo = [
      "01_ok: устав не разобран — на разбор",
      "10 и 05: давняя смена — сигнал, свежая — разбор",
      "отмена МЧД — «отозвано», захват — «приостановлено»",
      "журнал: доказательство одной записи",
    ];
    s.addText(demo.map((t, i) => ({ text: typo(t), options: { bullet: { type: "number" }, breakLine: i < demo.length - 1 } })),
      { x: rx + 0.25, y: ly + 0.46, w: rw - 0.45, h: lh - 0.5, isTextBox: true, margin: 0, fontSize: 11.5, color: C.text1, paraSpaceAfter: 2, valign: "top", objectName: "Шаги демо" });
    s.addNotes("[1:54–2:20] Продукт работает сегодня, одной командой. Слева — настоящая карточка кейса: вердикт, сильнейшее возражение с нормой и сигнал мониторинга. Справа — терминал: все 14 демо-кейсов дают ожидаемый вердикт, журнал цел. Удостоверение действует; после событий захвата монитор поднимает тревогу, и то же удостоверение уже приостановлено. Если есть минута — покажу вживую.");
  }

  // ---------- 9. Мониторинг ----------
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
    const rx = L + lw + GAP, rw = R - rx, gap = 0.18, ch = (h - 2 * gap) / 3;
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
    s.addNotes("[2:20–2:34] Это и есть постоянный контроль. За три часа смена директора, новая доверенность и смена телефона складываются в тревогу «возможный захват»: удостоверения компании приостановлены, кейс — в антифрод. Отмена основной доверенности отзывает удостоверения по цепочке. Права в интернет-банке меняет банк.");
  }

  // ---------- 10. Качество ----------
  {
    const s = slide("Контент", "Питч", "КАЧЕСТВО", `Ноль ложных «подтверждено» на ${N.negatives} кейсах, где подтверждать нельзя`,
      `Корпус: ${N.cases} ${plural(N.cases, "сгенерированный кейс", "сгенерированных кейса", "сгенерированных кейсов")}, ${N.kinds} видов ситуаций по нормам ГК РФ и формату МЧД. Воспроизводится: mandat eval --per-defect 6; pytest tests/test_properties.py tests/test_cedar.py tests/test_redteam_fuzz.py; python scripts/bench.py → evidence.json от ${N.asOf}, v${N.version}`);
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
      T(s, v, L + 0.22, y, 1.75, rh, { size: 22, bold: true, color: i === 0 ? ST.ok.text : C.accent1, valign: "middle" });
      T(s, t, L + 2.05, y, sw - 2.25, rh, { size: 11.5, color: C.text1, valign: "middle" });
    });
    const cx = L + sw + GAP, cw = R - cx;
    card(s, cx, TOP, cw, 2.86);
    T(s, `Ожидаемые вердикты ${N.cases} проверочных кейсов`, cx + 0.3, TOP + 0.18, cw - 0.6, 0.3, { size: 12, bold: true, color: TINT.text2 });
    s.addChart(pres.charts.BAR, [{ name: "Кейсов", labels: ["ПОДТВЕРЖДЕНО", "ОПРОВЕРГНУТО", "НА РАЗБОР", "НЕДОСТ. ДАННЫХ"], values: [N.expected.confirmed, N.expected.refuted, N.expected.needs_review, N.expected.insufficient_data] }], {
      ...chartBase(), x: cx + 0.15, y: TOP + 0.5, w: cw - 0.3, h: 2.28, barDir: "bar", barGapWidthPct: 45,
      chartColors: [HEX.accent3, HEX.accent5, HEX.accent4, HEX.accent6], dataLabelFontSize: 12, dataLabelColor: HEX.dk1,
      catAxisLabelFontSize: 10.5, catAxisOrientation: "maxMin", valAxisMaxVal: 150, objectName: "Диаграмма: ожидаемые вердикты корпуса",
    });
    const ey = TOP + 2.86 + 0.2, eh = 1.04;
    card(s, cx, ey, cw, eh, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
    tile(s, cx + 0.25, ey + 0.27, 0.5, "Microscope", { fill: "FFFFFF" });
    T(s, [
      { text: "Сверка с внешними эталонами, 27.10.2026: ", options: { bold: true, color: HEX.dk2 } },
      { text: "каноническая запись JCS (RFC 8785, 20 000 документов), дерево Меркла (RFC 6962), подпись — векторы W3C eddsa-jcs-2022: совпало" },
    ], cx + 0.9, ey, cw - 1.1, eh, { size: 12, valign: "middle" });
    const auto = N.expected.confirmed + N.expected.refuted, human = N.expected.needs_review + N.expected.insufficient_data;
    band(s, L, 5.92, W, 0.8, `Однозначное решается само, спорное — человеком: все ${auto} однозначных кейсов закрыты автоматически без единой ошибки, все ${human} спорных и неполных — у сотрудника с готовым досье. Пилот переносит эти метрики на данные ВТБ.`, { icon: "Route", size: 13 });
    s.addNotes(`[2:34–2:52] Качество. Недопустимая ошибка — ложное «подтверждено». На ${N.negatives} кейсах-ловушках их ноль, верхняя граница — ${N.fcUpper.replace("%", " процента")}. ${FUZZ} мутаций тоже не дали ложного «да». Передоверие сверено с независимой реализацией на Cedar. Всё однозначное закрывается автоматически, спорное идёт к сотруднику с досье. Пилот переносит эти метрики на данные ВТБ.`);
  }

  // ---------- 11. ИИ под контролем ----------
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
      tile(s, x + 0.2, TOP + 0.2, 0.48, ic, hl ? { fill: TINT.navyTile, icon: "light" } : {});
      T(s, head, x + 0.2, TOP + 0.76, fw - 0.36, 0.5, { size: 13.5, bold: true, color: hl ? C.background1 : C.text2 });
      T(s, t, x + 0.2, TOP + 1.28, fw - 0.36, 0.68, { size: 12, color: hl ? TINT.ice : C.text1 });
      if (i < flow.length - 1) arrow(s, x + fw + 0.04, TOP + fh / 2, gap - 0.08, HEX.accent1, 1.75);
    });
    const by = TOP + fh + GAP;
    card(s, L, by, W, 0.62, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
    T(s, [
      { text: "В логике Кодекса этики в сфере ИИ на финансовом рынке (Банк России, 2025). ", options: { bold: true, color: HEX.dk2 } },
      { text: "По сообщениям СМИ, ВТБ присоединился к Кодексу в августе 2026" },
    ], L + 0.3, by, W - 0.6, 0.62, { size: 13, valign: "middle" });
    const py = by + 0.62 + GAP, ph = BOTTOM - py, pw = (W - GAP * 3) / 4;
    const pr = [
      ["Eye", "Прозрачность", "у каждого вывода — норма и ссылка на поле документа; отчёт называет источник каждого факта"],
      ["Users", "Человек в контуре", "человекоцентричность: спорное решает сотрудник, автоматический вердикт клиент может оспорить у человека"],
      ["Lock", "Безопасность", "модель on-prem и по умолчанию выключена — без неё продукт работает полностью; ПДн псевдонимизированы"],
      ["Landmark", "Суверенность", "провайдер сменный: по 243-ФЗ с 01.03.2027 банкам могут предписать национальные модели"],
    ];
    pr.forEach(([ic, head, t], i) => {
      const x = L + i * (pw + GAP);
      card(s, x, py, pw, ph);
      tile(s, x + 0.2, py + 0.16, 0.4, ic);
      T(s, head, x + 0.7, py + 0.16, pw - 0.85, 0.4, { size: 13.5, bold: true, color: C.text2, valign: "middle" });
      T(s, t, x + 0.2, py + 0.66, pw - 0.38, ph - 0.7, { size: 12, color: C.text1 });
    });
    s.addNotes("[2:52–3:05] Про ИИ. Модель только предлагает факт из устава с дословной цитатой: нет цитаты — нет факта. Вердикт выносят правила, спорное решает человек, клиент может оспорить. Модель в контуре банка, по умолчанию выключена, провайдер сменный — с учётом закона об ИИ.");
  }

  // ---------- 12. Эффект и бизнес-модель ----------
  {
    const s = slide("Контент", "Питч", "ЭФФЕКТ ДЛЯ ВТБ", "Эффект в двух валютах: время сотрудников и предотвращённый ущерб",
      "ВТБ, пресс-релиз к МСФО за 1П 2026: ≈1,8 млн предприятий и предпринимателей (M-09); Банк России, I–II кв. 2026 (M-07, M-15). Расчёт: python scripts/effect_model.py --scenarios; деньги и окупаемость — финансовая модель пакета аналитики");
    const cw = (W - GAP) / 2, ch = 3.75;
    const charts = [
      ["Users", "Высвобождение нагрузки, ставок в год", FTE, `≈${comma(EFFECT.clients / 1e6)} млн предприятий и ИП × доля с событием представительства (${shares(EFFECT.eventShare)}%) × минуты на кейс с разбором (${EFFECT.minutesSaved.join(" / ")}) ÷ 60 ÷ ${EFFECT.hoursPerFte.toLocaleString("ru-RU")} ч на ставку`, HEX.accent1],
      ["ShieldCheck", "Предотвращённый ущерб, млн ₽ в год", LOSS, `${EFFECT.incidentsPerQuarter} операций против юрлиц в среднем за квартал I пол. 2026 × 4 × доля ВТБ (${shares(EFFECT.vtbShare)}%) × доля случаев с полномочиями (${shares(EFFECT.authorityShare)}%) × ${comma(EFFECT.lossPerIncidentMln, 2)} млн ₽`, HEX.accent3],
    ];
    charts.forEach(([ic, head, vals, formula, color], i) => {
      const x = L + i * (cw + GAP);
      card(s, x, TOP, cw, ch);
      tile(s, x + 0.28, TOP + 0.22, 0.46, ic);
      T(s, head, x + 0.88, TOP + 0.22, cw - 1.1, 0.46, { size: 14, bold: true, color: C.text2, valign: "middle" });
      s.addChart(pres.charts.BAR, [{ name: head, labels: EFFECT.names, values: vals }], {
        ...chartBase(), x: x + 0.25, y: TOP + 0.72, w: cw - 0.5, h: 2.0, barDir: "col", barGapWidthPct: 70,
        chartColors: [color], dataLabelFontSize: 14, catAxisLabelFontSize: 11,
        valAxisMaxVal: Math.ceil((Math.max(...vals) * 1.22) / 10) * 10, objectName: `Диаграмма: ${head}`,
      });
      T(s, formula, x + 0.28, TOP + 2.78, cw - 0.56, 0.92, { size: 11, color: TINT.text2 });
    });
    const y2 = TOP + ch + GAP, h2 = BOTTOM - y2;
    card(s, L, y2, cw, h2, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
    tile(s, L + 0.22, y2 + (h2 - 0.5) / 2, 0.5, "Briefcase", { fill: "FFFFFF" });
    T(s, [{ text: "Бизнес-модель: ", options: { bold: true, color: HEX.dk2 } }, { text: "лицензия в контуре банка и годовая подписка на сопровождение правил при смене норм, формата МЧД и классификатора" }], L + 0.92, y2, cw - 1.15, h2, { size: 12.5, valign: "middle" });
    band(s, L + cw + GAP, y2, cw, h2, `Для ВТБ, базовый сценарий: разовые затраты окупаются за ${PAYBACK}, чистая отдача — ${ROI} ₽ на рубль затрат за три года`, { icon: "Target", size: 12.5, bold: true });
    s.addNotes(`[3:05–3:24] Эффект в двух валютах. Время: при 1,8 миллиона бизнес-клиентов — от ${FTE[0]} до ${FTE[2]} ставок в год. Риск: от ${LOSS[0]} до ${LOSS[2]} ${plural(LOSS[2], "миллиона", "миллионов", "миллионов")} рублей предотвращённого ущерба в год — на данных Банка России за полугодие. Для ВТБ в базовом сценарии затраты окупаются за ${PAYBACK}. Модель — лицензия и подписка на правила: нормы меняются каждые полгода.`);
  }

  // ---------- 13. Почему сейчас ----------
  {
    const s = slide("Контент", "Питч", "ПОЧЕМУ СЕЙЧАС", "МЧД стало втрое больше, а правила для банков меняются каждые полгода",
      "63-ФЗ в ред. 476-ФЗ; Приказ Банка России № ОД-2506; Приказ Минцифры № 1001; 41-ФЗ (M-40); ФНС, 27.07.2026; emchd.ru, 10.2024; 243-ФЗ (M-41); 210-ФЗ (M-16); Указание Банка России № 7220-У; 315-ФЗ (M-04); CNews, 12.02.2026 — комплект МЧД и КЭП (M-19)");
    const lw = 7.7, h = BOTTOM - TOP;
    card(s, L, TOP, lw, h);
    const ev8 = [
      ["01.09.2024", "МЧД обязательна для представителей юрлиц", 0],
      ["01.01.2026", "12 признаков операций без согласия вместо 6 — Приказ № ОД-2506", 0],
      ["01.02.2026", "новые реквизиты МЧД: СНИЛС и ИНН представителя — Приказ № 1001", 0],
      ["01.03.2026", "реестр МЧД ищет по номеру и ИНН сторон; заработала ГИС «Антифрод» (41-ФЗ)", 0],
      ["27.07.2026", "в реестре ФНС — более 5 млн МЧД", 0],
      ["01.09.2026", "в силе закон об ИИ (243-ФЗ) и первые нормы антифрод-пакета (210-ФЗ)", 0],
      ["01.10.2026", "изменения 821-П о защите информации при переводах (№ 7220-У)", 0],
      ["01.09.2027", "единая форма МЧД становится основной (315-ФЗ)", 1],
    ];
    const lx = L + 1.62, step = (h - 0.5) / ev8.length;
    vline(s, lx + 0.07, TOP + 0.38, step * (ev8.length - 1), TINT.blueLine, 2);
    ev8.forEach(([d, t, fut], i) => {
      const y = TOP + 0.25 + i * step;
      T(s, d, L + 0.25, y, 1.25, 0.28, { size: 12, bold: true, color: fut ? HEX.dk2 : C.accent1, align: "right", valign: "middle" });
      s.addShape(pres.shapes.OVAL, { x: lx, y: y + 0.07, w: 0.14, h: 0.14, fill: { color: fut ? "FFFFFF" : HEX.accent1 }, line: { color: fut ? HEX.dk2 : HEX.accent1, width: 1.5 }, objectName: "Точка шкалы" });
      T(s, fut ? [{ text: t, options: { bold: true } }, { text: " · впереди", options: { color: HEX.accent1 } }] : t, lx + 0.35, y - 0.04, lw - (lx - L) - 0.55, 0.36, { size: 12.5, color: C.text1, valign: "middle" });
    });
    const rx = L + lw + GAP, rw = R - rx, gap = 0.2, ch = (h - 2 * gap) / 3;
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
    s.addNotes("[3:24–3:36] Почему сейчас. Доверенностей за два года стало втрое больше — более пяти миллионов, ВТБ сам продаёт комплект МЧД и КЭП. А нормы меняются каждые полгода — нужен движок, где они меняются данными, а не релизом.");
  }

  // ---------- 14. Мировая практика ----------
  {
    const s = slide("Контент", "Питч", "МИРОВАЯ ПРАКТИКА", "Мир уже выделил проверку полномочия представителя в отдельный шаг — и в отдельный продукт",
      "FATF, Рекомендация 10 (M-12); Регламент (ЕС) 2024/1624 и финальный проект RTS AMLA, 30.09.2026 (M-13, M-18); Signicat InstantAuthority — документация Signicat (M-37); Companies House, отчёт по ECCTA 2023 от 30.06.2025 (M-36); Еврокомиссия, 19.11.2025 (M-39); GLEIF, ISO 17442-3");
    const lw = 4.6, hy = TOP, gy = TOP + 0.42;
    T(s, "Требования к банкам", L, hy, lw, 0.32, { size: 13, bold: true, color: TINT.text2 });
    const reqs = [
      ["FATF · Рекомендация 10", "проверить, что лицо, действующее от имени клиента, уполномочено, и установить его личность"],
      ["ЕС · Регламент 2024/1624", "с 10.07.2027 — сведения о любом представителе клиента; проект RTS AMLA — проверка существования и объёма полномочия"],
      ["ЕС · Директива 2025/25", "выписка о компании: кто представляет компанию, по одному или совместно; цифровая доверенность ЕС — с 31.07.2028"],
    ];
    const rh = 1.22, rg = 0.1;
    reqs.forEach(([head, t], i) => {
      const y = gy + i * (rh + rg);
      card(s, L, y, lw, rh);
      tile(s, L + 0.2, y + 0.2, 0.42, "Globe");
      T(s, head, L + 0.74, y + 0.16, lw - 0.9, 0.3, { size: 12.5, bold: true, color: C.text2 });
      T(s, t, L + 0.74, y + 0.46, lw - 0.9, rh - 0.5, { size: 11, color: C.text1 });
    });
    const rx = L + lw + GAP, rw = R - rx;
    T(s, "Как это уже работает в мире", rx, hy, rw, 0.32, { size: 13, bold: true, color: TINT.text2 });
    const cases = [
      ["Building2", "Нидерланды · Signicat InstantAuthority", "запрос в Торговый реестр KvK + вход через iDIN → роль, мандат и его ограничения: проверка полномочия — отдельный продукт"],
      ["Landmark", "Великобритания · Companies House", "по закону ECCTA 2023 за год до 03.2025 отклонено 10 200 подозрительных заявлений; с 18.11.2025 — проверка личности директора до регистрации"],
      ["Briefcase", "ЕС · European Business Wallet", "предложение Еврокомиссии от 19.11.2025: признание полномочий представителей — обязательная предпосылка кошелька"],
      ["IdCard", "GLEIF · vLEI", "роль представителя компании как проверяемое удостоверение (ISO 17442-3) — тот же принцип, что у удостоверения «Мандата»"],
    ];
    const tg = 0.15, tw = (rw - tg) / 2, ch = (3 * rh + 2 * rg - tg) / 2;
    cases.forEach(([ic, head, t], i) => {
      const x = rx + (i % 2) * (tw + tg), y = gy + Math.floor(i / 2) * (ch + tg);
      card(s, x, y, tw, ch);
      tile(s, x + 0.2, y + 0.2, 0.44, ic);
      T(s, head, x + 0.76, y + 0.18, tw - 0.92, 0.5, { size: 12.5, bold: true, color: C.text2, valign: "middle" });
      T(s, t, x + 0.2, y + 0.76, tw - 0.38, ch - 0.82, { size: 11.5, color: C.text1 });
    });
    const by = gy + 3 * rh + 2 * rg + 0.15;
    band(s, L, by, W, BOTTOM - by, "«Мандат» делает то же для российской МЧД: полномочие на дату операции, норма и поле документа в каждом выводе, удостоверение со статусом", { icon: "Waypoints", size: 13, fill: TINT.blue, color: C.text2, tile: "FFFFFF", shadow: false, bold: true });
    s.addNotes("[3:36–3:50] Мир идёт туда же. FATF и регламент ЕС требуют проверить, что представитель уполномочен. В Нидерландах проверка полномочия — уже отдельный сервис, Британия с прошлого года проверяет личность каждого нового директора, а ЕС закладывает полномочия в бизнес-кошелёк. «Мандат» делает это для российской МЧД.");
  }

  // ---------- 15. Встройка ----------
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
      tile(s, x + 0.25, TOP + 0.22, 0.5, ic, hl ? { fill: TINT.navyTile, icon: "light" } : {});
      T(s, head, x + 0.9, TOP + 0.22, cw - 1.1, 0.5, { size: 15, bold: true, color: hl ? C.background1 : C.text2, valign: "middle" });
      bullets(s, items, x + 0.3, TOP + 0.9, cw - 0.55, ch - 1.0, { size: 12.5, color: hl ? C.background1 : C.text1, psa: 4 });
      if (i < 2) arrow(s, x + cw + 0.06, TOP + ch / 2, gap - 0.12, HEX.accent1, 2);
    });
    const y2 = TOP + ch + GAP, h2 = BOTTOM - y2, w2 = (W - GAP) / 2;
    const parts = [
      ["Check", HEX.accent3, "Работает сегодня", [`${RULES}, ${DEMOS}, CLI и HTTP API с ролями`, "разбор человеком: очередь P1–P3 и сроки", "мониторинг событий, статусы удостоверений, журнал", `${TESTS} проходят; SBOM и подписанное происхождение сборки`]],
      ["Plug", HEX.accent1, "Подключаем в пилоте", ["реестр МЧД, ЕГРЮЛ, ЕФРСБ — адаптеры к источникам банка через готовые порты", "проверка КЭП и ГОСТ-подпись — сертифицированные СКЗИ банка", "ключи — HSM по PKCS#11; вход — OIDC банка", "коды полномочий — выгрузка классификатора ЕСНСИ"]],
    ];
    parts.forEach(([ic, col, head, items], i) => {
      const x = L + i * (w2 + GAP);
      card(s, x, y2, w2, h2);
      dot(s, x + 0.25, y2 + 0.22, 0.42, col, ic);
      T(s, head, x + 0.82, y2 + 0.22, w2 - 1.0, 0.42, { size: 15, bold: true, color: C.text2, valign: "middle" });
      bullets(s, items, x + 0.3, y2 + 0.8, w2 - 0.55, h2 - 0.86, { size: 12, psa: 4 });
    });
    s.addNotes("[3:50–4:00] Встройка. «Мандат» работает в контуре банка без внешней сети. Ядро, правила, журнал и мониторинг готовы; реестр, ЕГРЮЛ, ЕФРСБ и проверку подписи через СКЗИ банка подключаем к пилоту через порты.");
  }

  // ---------- 16. Видение: сотрудник, клиент, ИИ-агент ----------
  {
    const s = slide("Контент", "Питч", "БАНК БУДУЩЕГО", "Дальше — один движок полномочий для сотрудника, клиента и ИИ-агента",
      "AP2 — Google, 16.09.2025 (M-38); European Business Wallet — предложение Еврокомиссии 19.11.2025 (M-39); привязка удостоверения к операции, сумме, получателю и nonce — src/mandat/audit/attestation.py, слайд A8");
    const cols = [
      ["UserCheck", "Сотрудник банка", "работает сегодня", HEX.accent3, ["вердикт на дату с нормой и полем документа", "досье спорного кейса с приоритетом и сроком", "журнал и удостоверение со статусом"]],
      ["Building2", "Клиент в «ВТБ Бизнес»", "после пилота", HEX.accent1, ["«Мои полномочия»: кто и до какого дня вправе действовать за компанию", "тревога при смене директора в ЕГРЮЛ — до первого платежа", "проверка подписанта контрагента перед сделкой"]],
      ["Bot", "ИИ-агент клиента", "2027–2028", HEX.dk2, ["агент платит по мандату: операция, сумма, получатель, срок", "банк проверяет мандат без доступа к базе и отзывает его по событию", "по образцу мандатов AP2: проверяемое удостоверение с пределами"]],
    ];
    const gap = 0.3, cw = (W - 2 * gap) / 3, ch = 3.55;
    cols.forEach(([ic, head, stage, col, items], i) => {
      const x = L + i * (cw + gap);
      const hl = i === 2;
      card(s, x, TOP, cw, ch, hl ? { fill: C.text2, line: null } : {});
      tile(s, x + 0.25, TOP + 0.24, 0.54, ic, hl ? { fill: TINT.navyTile, icon: "light" } : {});
      T(s, head, x + 0.95, TOP + 0.2, cw - 1.15, 0.36, { size: 15, bold: true, color: hl ? C.background1 : C.text2, valign: "middle" });
      const sw = stage.length * 0.078 + 0.36;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x + 0.95, y: TOP + 0.6, w: sw, h: 0.28, rectRadius: 0.14, fill: { color: hl ? TINT.navyTile : TINT.blue }, line: { type: "none" }, objectName: `Этап: ${stage}` });
      T(s, stage, x + 0.95, TOP + 0.6, sw, 0.28, { size: 10, bold: true, color: hl ? HEX.accent2 : col, align: "center", valign: "middle" });
      bullets(s, items, x + 0.3, TOP + 1.18, cw - 0.55, ch - 1.26, { size: 13.5, psa: 9, color: hl ? C.background1 : C.text1 });
      if (i < 2) arrow(s, x + cw + 0.04, TOP + ch / 2, gap - 0.08, HEX.accent1, 2);
    });
    band(s, L, TOP + ch + GAP, W, BOTTOM - (TOP + ch + GAP), "Ядро уже умеет главное для этого шага: удостоверение «Мандата» привязано к операции, сумме, получателю и одноразовому номеру запроса и отзывается по событию", { icon: "BadgeCheck", size: 14 });
    s.addNotes("[4:00–4:14] Куда это растёт. Сегодня «Мандат» помогает сотруднику. Следующий шаг — клиент в «ВТБ Бизнес» видит, кто вправе действовать за компанию, и получает тревогу при смене директора. Дальше — ИИ-агенты: агент платит по мандату с пределами, банк проверяет его без доступа к базе. Наше удостоверение это уже умеет.");
  }

  // ---------- 17. Пилот ----------
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
    const y2 = TOP + wh + GAP, h2 = 2.55, w2 = (W - GAP) / 2;
    const parts = [
      ["Target", "Критерии: продолжать или остановить", ["0 ложных «подтверждено» на всех кейсах — стоп-критерий", "доля «на разбор» не выше согласованного порога", "согласие с экспертами (каппа Коэна) не ниже межэкспертного", "все расхождения разобраны; проверка быстрее ручной"]],
      ["Handshake", "Что нужно от ВТБ", ["владелец процесса и куратор пилота", "регламент одной операции и типовые отказы", "50–100 обезличенных пакетов или тестовый контур", "юрист для правил; базовые цифры для расчёта эффекта"]],
    ];
    parts.forEach(([ic, head, items], i) => {
      const x = L + i * (w2 + GAP);
      card(s, x, y2, w2, h2);
      tile(s, x + 0.25, y2 + 0.22, 0.46, ic);
      T(s, head, x + 0.85, y2 + 0.22, w2 - 1.05, 0.46, { size: 15, bold: true, color: C.text2, valign: "middle" });
      bullets(s, items, x + 0.3, y2 + 0.85, w2 - 0.55, h2 - 0.95, { size: 13, psa: 5 });
    });
    const cy = y2 + h2 + GAP, chh = BOTTOM - cy;
    s.addImage({ data: G.band, x: L, y: cy, w: W, h: chh, altText: "", objectName: "Плашка запроса" });
    tile(s, L + 0.25, cy + (chh - 0.5) / 2, 0.5, "Rocket", { fill: TINT.navyTile, icon: "light" });
    T(s, "Наш запрос: теневой пилот через Акселератор ВТБ с метриками, согласованными до старта", L + 0.95, cy, W - 1.2, chh, { size: 16, bold: true, color: C.background1, valign: "middle" });
    s.addNotes("[4:14–4:36] Наш запрос конкретный: шесть недель теневого пилота на одной операции — праве подписи платёжных поручений по МЧД. «Мандат» работает рядом с сотрудниками и ни на что не влияет. Стоп-критерий — первое ложное «подтверждено». От банка — владелец процесса, регламент, обезличенные пакеты и юрист.");
  }

  // ---------- 18. Команда ----------
  {
    const s = slide("Контент", "Питч", "КОМАНДА", "Команда: архитектура правил и измерение качества", "github.com/KonkovDV/Mandat · собственный код под лицензией MIT");
    const people = [
      ["dmitrij.jpg", "Коньков Дмитрий", "Капитан · архитектура и правила", "Автор открытых проектов AeroBIM и SynAPS. Детерминированное ядро, граф полномочий, границы заявлений"],
      ["elena.jpg", "Конькова Елена", "Системный инженер · измерение и качество", "Методика оценки, корпус и разметка, воспроизводимость сборки, протокол пилота"],
    ];
    const pw = (W - GAP) / 2, ph = 2.75;
    people.forEach(([img, name, role, desc], i) => {
      const x = L + i * (pw + GAP);
      card(s, x, TOP, pw, ph);
      s.addShape(pres.shapes.OVAL, { x: x + 0.3, y: TOP + 0.38, w: 1.98, h: 1.98, fill: { color: TINT.blue }, line: { color: HEX.accent2, width: 2.5 }, objectName: "Рамка фото" });
      s.addImage({ path: path.join(ASSETS, img), x: x + 0.39, y: TOP + 0.47, w: 1.8, h: 1.8, rounding: true, altText: `Фото: ${name}` });
      T(s, name, x + 2.6, TOP + 0.45, pw - 2.85, 0.42, { size: 19, bold: true, color: C.text2 });
      T(s, role, x + 2.6, TOP + 0.9, pw - 2.85, 0.5, { size: 13, bold: true, color: C.accent1 });
      T(s, desc, x + 2.6, TOP + 1.42, pw - 2.85, 1.15, { size: 13, color: C.text1 });
    });
    const y2 = TOP + ph + GAP, h2 = BOTTOM - y2, w2 = (W - GAP) / 2;
    card(s, L, y2, w2, h2, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
    tile(s, L + 0.25, y2 + 0.25, 0.5, "Award", { fill: "FFFFFF" });
    T(s, "Что получает банк", L + 0.9, y2 + 0.25, w2 - 1.1, 0.5, { size: 15, bold: true, color: C.text2, valign: "middle" });
    T(s, "проверенный набор правил, протокол измерений, итоги пилота и эталонную реализацию под MIT для передачи команде банка", L + 0.3, y2 + 0.9, w2 - 0.6, h2 - 1.0, { size: 13 });
    const x2 = L + w2 + GAP;
    card(s, x2, y2, w2, h2, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
    tile(s, x2 + 0.25, y2 + 0.25, 0.5, "Search", { fill: "FFFFFF" });
    T(s, "Кого ищем в ВТБ", x2 + 0.9, y2 + 0.25, w2 - 1.1, 0.5, { size: 15, bold: true, color: C.text2, valign: "middle" });
    T(s, "владельца процесса добавления представителей юрлиц и юриста, который утвердит правила и окно «свежей» смены руководителя", x2 + 0.3, y2 + 0.9, w2 - 0.6, h2 - 1.0, { size: 13 });
    s.addNotes("[4:36–4:52] Команда: я отвечаю за архитектуру и правила, Елена — за методику измерения и качество. Юридическую проверку правил планируем с юристом банка на пилоте.");
  }

  // ---------- 19. Контакты ----------
  {
    const s = pres.addSlide({ masterName: "Финал", sectionTitle: "Питч" });
    s.addImage({ data: G.markDark, x: L, y: 0.6, w: 0.62, h: 0.56, altText: "Знак «Мандата»" });
    T(s, "Команда «Мандат»", L + 0.8, 0.6, 5.0, 0.56, { size: 16, bold: true, color: C.background1, valign: "middle" });
    s.addText(typo("Спасибо! Готовы к теневому пилоту в ВТБ"), { placeholder: "title" });
    const rows = [
      ["Phone", "Коньков Дмитрий, капитан", "+7 915 452-88-64 · konkov-dmitrij@yandex.ru"],
      ["Mail", "Конькова Елена, измерение и качество", "konkovaelena@yandex.ru"],
      ["Github", "Код и документация", "github.com/KonkovDV/Mandat · собственный код под MIT"],
    ];
    rows.forEach(([ic, who, t], i) => {
      const y = 3.0 + i * 0.72;
      tile(s, L, y + 0.06, 0.46, ic, { fill: TINT.navyTile, icon: "light" });
      T(s, who, L + 0.65, y, 6.5, 0.3, { size: 14, bold: true, color: C.background1, valign: "middle" });
      T(s, t, L + 0.65, y + 0.3, 6.5, 0.3, { size: 13, color: TINT.ice, valign: "middle" });
    });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: L, y: 5.35, w: 6.7, h: 0.95, rectRadius: 0.12, fill: { color: "FFFFFF", transparency: 90 }, line: { color: HEX.accent2, width: 1.5 }, objectName: "Плашка просьбы" });
    T(s, "Ищем в ВТБ владельца процесса добавления представителей юрлиц — чтобы согласовать протокол пилота до финала 11 декабря", L + 0.3, 5.35, 6.1, 0.95, { size: 14, bold: true, color: C.background1, valign: "middle" });
    const hw = 4.3;
    s.addImage({ data: G.hero, x: 8.2, y: 0.45, w: hw, h: hw * G.heroRatio, altText: "Схема: источники сходятся в один вердикт на дату операции" });
    const qx = 9.55, qy = 4.45, qw = 1.75;
    card(s, qx, qy, qw, 2.25, { fill: "FFFFFF", line: null, r: 0.14, name: "Карточка QR-кода" });
    s.addImage({ data: G.qr, x: qx + 0.2, y: qy + 0.2, w: qw - 0.4, h: qw - 0.4, altText: "QR-код: github.com/KonkovDV/Mandat" });
    T(s, "Код, демо и документы", qx + 0.1, qy + qw - 0.12, qw - 0.2, 0.42, { size: 10.5, bold: true, color: C.text2, align: "center", valign: "middle" });
    s.addNotes("[4:52–5:00] Спасибо! QR-код ведёт на код, демо и документы. Готовы к вопросам.");
  }
  if (pages() !== MAIN_SLIDES) throw new Error(`в питче ${pages()} слайдов, а ожидалось ${MAIN_SLIDES}`);

  // =====================================================================
  // ПРИЛОЖЕНИЕ
  // =====================================================================
  pres.addSection({ title: "Приложение" });
  {
    const s = pres.addSlide({ masterName: "Раздел", sectionTitle: "Приложение" });
    s.addText("Приложение: ответы на вопросы жюри", { placeholder: "title" });
    T(s, "Нажмите на раздел, чтобы перейти; на каждом слайде есть ссылка назад", L, 1.5, 11.0, 0.35, { size: 13, color: TINT.ice });
    const per = Math.ceil(APPX.length / 2), cw = (W - 0.5) / 2, rh = 0.5;
    APPX.forEach(([id, title], i) => {
      const col = Math.floor(i / per), row = i % per;
      const x = L + col * (cw + 0.5), y = 2.15 + row * rh;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: cw, h: rh - 0.08, rectRadius: 0.1, fill: { color: "FFFFFF", transparency: 92 }, line: { color: "FFFFFF", transparency: 75, width: 0.75 }, objectName: `Пункт оглавления ${id}` });
      T(s, id, x + 0.2, y, 0.6, rh - 0.08, { size: 13.5, bold: true, color: HEX.accent2, valign: "middle", name: `Номер ${id}` });
      T(s, title, x + 0.82, y, cw - 1.75, rh - 0.08, { size: 13.5, color: "FFFFFF", valign: "middle", name: `Пункт ${id}` });
      T(s, `с. ${appxPage(id)} →`, x + cw - 1.0, y, 0.85, rh - 0.08, { size: 11, color: TINT.ice, align: "right", valign: "middle" });
      link(s, x, y, cw, rh - 0.08, appxPage(id), `${id} ${title}`);
    });
  }

  // ---------- A1. Семь взглядов ----------
  {
    const s = slide("Контент", "Приложение", "A1 · ВРАЖДЕБНЫЙ РАЗБОР", "Семь взглядов на «Мандат»: главное возражение каждой роли и наш ответ",
      "docs/audit/MURDER_BOARD-2026-10-20.md (банкир, юрист, правовед, безопасник, ИБ, пользователь, Red Team); docs/audit/AUDIT-2026-10-27-DIFFERENTIAL.md; docs/JURY.md", { appx: "A1" });
    const roles = [
      ["Briefcase", "Банкир", "«Сколько экономит и не вырастет ли очередь?»", `Базовый сценарий: ${FTE[1]} ставок и ${LOSS[1]} млн ₽ ущерба в год, окупаемость — ${PAYBACK}. У очереди разбора приоритеты P1–P3 и сроки; порог доли «на разбор» — критерий пилота`],
      ["Scale", "Юрист банка", "«Кто отвечает за норму в правиле?»", "У каждого правила — норма, тест и статус. Юрист банка утверждает правила в первую неделю пилота; до утверждения строгий профиль не выдаёт по черновику ни «да», ни «нет»"],
      ["BookOpen", "Правовед", "«Полномочие — правоотношение на дату»", "Так и смоделировано: граф на дату операции (ст. 185–189 ГК РФ). Полномочие из обстановки (ст. 182) вне модели: без документа «да» нет"],
      ["Lock", "ИБ банка", "«Ed25519, а не ГОСТ; root перепишет журнал»", "Ключ — в HSM, Vault или KMS; ГОСТ — через СКЗИ банка. Без внешнего якоря журнала pilot и prod не стартуют"],
      ["ShieldAlert", "Антифрод", "«Подставной представитель с действующей МЧД»", "Проверяем полномочие, а не намерение. Признаки ОД-2506 — в антифроде банка; ему уходят подписанные события"],
      ["UserRound", "Сотрудник и клиент", "«Всё сходится — почему „на разбор“?»", "Карточка показывает сильнейшее возражение и следующий шаг; клиент получает список недостающего и может оспорить ответ"],
      ["Award", "Жюри", "«0 ложных „да“ — это точность 100%?»", `Это проверка логики на ${N.negatives} кейсах-ловушках: ни одной ошибки, верхняя граница ${N.fcUpper}. На потоке ВТБ ту же метрику даёт пилот — со стоп-критерием`],
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
    tile(s, x + 0.2, y + 0.2, 0.42, "ListChecks", { fill: TINT.navyTile, icon: "light" });
    T(s, "Полный разбор", x + 0.72, y + 0.2, cw - 0.9, 0.42, { size: 13.5, bold: true, color: C.background1, valign: "middle" });
    T(s, "Позиции, находки, статусы и тесты, которые их держат, — в docs/audit. Числа — только из реестра утверждений с командой воспроизведения", x + 0.2, y + 0.75, cw - 0.4, ch - 0.85, { size: 11.5, color: TINT.ice });
  }

  // ---------- A2. Критерии жюри ----------
  {
    const s = slide("Контент", "Приложение", "A2 · КРИТЕРИИ", "Критерии жюри — где в проекте ответ на каждый",
      "Критерии трека MVP по Положению «Академии Финтеха» (шкала 0–2; по открытым источникам) и тема трека ВТБ «Финтех без границ: банк будущего для бизнеса и клиентов»; docs/research/REQUIREMENTS_MATRIX.md", { appx: "A2" });
    table(s, ["Критерий", "Что показываем", "Где в колоде", "Чем проверить"], [
      ["Готовность MVP", `демо одной командой, ${DEMOS}, CLI, HTTP API, Docker, видео-демо`, "слайды 8, 9, 15", "./run-demo.sh; docker compose в CI"],
      ["Техническая реализация", `${RULES} с нормой и тестом, журнал Меркла, удостоверения со статусом, Cedar, сверка с внешними эталонами`, "слайды 5, 7, 10; A3, A8, A9, A14", `${TESTS}; evidence.json; AUDIT-2026-10-27-DIFFERENTIAL`],
      ["Потенциал пилота в ВТБ", "ВТБ сам продаёт комплект МЧД и КЭП; порты под реестр, ЕГРЮЛ, ЕФРСБ и СКЗИ; теневой режим без влияния на клиентов", "слайды 13, 15, 17", "docs/PILOT.md: теневой режим 6 недель"],
      ["Бизнес-модель", `лицензия в контуре банка и подписка на правила; окупаемость для ВТБ — ${PAYBACK}`, "слайд 12; A12", "финансовая модель пакета аналитики; C-15, C-16"],
      ["Тема трека: банк будущего", "один движок полномочий для сотрудника, клиента и ИИ-агента; мировая практика и реальные кейсы", "слайды 14, 16; A13", "M-35–M-39 реестра утверждений"],
      ["Презентация", "ответ сначала, демо в 4 шага, ответы на трудные вопросы", "слайды 2, 8; A15, A16", "docs/JURY.md; submission/deck/PROMPT.md"],
    ], L, TOP, W, [2.3, 4.3, 2.1, 3.433], { size: 12, headH: 0.46, rowH: 0.74 });
  }

  // ---------- A3. Прослеживаемость чисел ----------
  {
    const s = slide("Контент", "Приложение", "A3 · ПРОСЛЕЖИВАЕМОСТЬ", "Каждое число на слайдах — с командой воспроизведения или источником",
      "CLAIMS_REGISTRY.md — номера строк C-xx и M-xx; числа о продукте подставляются в колоду из evidence.json при сборке (make deck)", { appx: "A3" });
    table(s, ["Число на слайдах", "Что значит", "Как воспроизвести или где источник", "Строка"], [
      [`${N.falseConfirmed} из ${N.negatives}; ${N.fcUpper}`, "ложные «подтверждено» и верхняя граница 95%", "mandat eval --per-defect 6", "C-03"],
      [`${N.agree} / ${N.cases}; ${N.kinds} видов`, "совпадение с ожидаемым вердиктом", "то же; отложенный seed — synthetic_corpus_holdout", "C-03"],
      [`${N.coverage} / ${N.toHuman}`, "доля без человека / доля к человеку", "то же; в профиле strict — 0%", "C-05"],
      [`${FUZZ_TXT} мутаций`, "ни одного ложного «да» и ни одного падения", "python -m pytest tests/test_redteam_fuzz.py", "C-17"],
      [`${N.chains} цепочки`, "передоверие не расширяет полномочия; Cedar совпал", "pytest tests/test_properties.py tests/test_cedar.py", "C-06"],
      [N.medianMs, "медиана проверки кейса без сети", "python scripts/bench.py", "C-14"],
      [TESTS, "все автотесты проходят", "python -m pytest", "C-13"],
      [`${FTE[0]}–${FTE[2]} ставок; ${LOSS[0]}–${LOSS[2]} млн ₽`, "сценарии эффекта", "python scripts/effect_model.py --scenarios", "C-15, C-16"],
      [`${PAYBACK}; ${ROI}×`, "окупаемость и отдача для ВТБ, базовый сценарий", "python submission/analytics/build_model.py", "модель"],
      [`${AVG_Q2}; ${Math.round(CBR.q2.lossMln)} млн ₽`, "потери юрлиц во II кв. 2026", "обзор Банка России, таблица по юрлицам", "M-07, M-15"],
      ["более 5 млн МЧД; ×3", "реестр МЧД ФНС и рост за два года", "nalog.gov.ru, 27.07.2026; emchd.ru, 10.2024", "M-08, M-10"],
    ], L, TOP, W, [2.7, 3.6, 4.6, 1.233], { size: 11, headH: 0.4, rowH: 0.415 });
  }

  // ---------- A4. Путь сотрудника и клиента ----------
  {
    const s = slide("Контент", "Приложение", "A4 · ПОЛЬЗОВАТЕЛЬ", "Путь сотрудника и клиента: от ручной сводки трёх источников к одному ответу с доказательством",
      "Шаги «сегодня» — по описанию процесса в открытых источниках и docs/MVP.md; время на кейс измеряется хронометражем в пилоте (C-15). Пользовательские истории US-08 и US-09 — docs/MVP.md", { appx: "A4" });
    const lanes = [
      ["Сегодня, вручную", "FileSearch", "E8ECF3", TINT.text2, [
        "клиент приносит МЧД", "сотрудник проверяет статус в реестре", "запрашивает выписку ЕГРЮЛ", "читает устав, при сомнении — к юристу", "решение без записи оснований",
      ], "между обращениями смену директора никто не видит"],
      ["С «Мандатом»", "ShieldCheck", TINT.blue, HEX.dk2, [
        "клиент передаёт пакет", "граф полномочий на дату — за миллисекунды", "вердикт с нормой и полем документа", "спорное — досье с приоритетом и сроком", "решение в журнале, удостоверение со статусом",
      ], "после выдачи прав монитор следит за событиями ЕГРЮЛ и реестра"],
    ];
    const lh = (BOTTOM - TOP - GAP) / 2;
    lanes.forEach(([head, ic, fill, color, steps, tail], li) => {
      const y = TOP + li * (lh + GAP);
      card(s, L, y, W, lh, li ? { fill: TINT.blue, line: TINT.blueLine } : {});
      tile(s, L + 0.25, y + 0.25, 0.5, ic, li ? { fill: "FFFFFF" } : { fill: "EEF1F6" });
      T(s, head, L + 0.9, y + 0.25, 3.0, 0.5, { size: 15, bold: true, color: C.text2, valign: "middle" });
      const sx = L + 0.25, sw = (W - 0.5 - 4 * 0.3) / 5, sy = y + 0.92, sh = 0.95;
      steps.forEach((t, i) => {
        const x = sx + i * (sw + 0.3);
        card(s, x, sy, sw, sh, { fill: li ? "FFFFFF" : TINT.soft, line: li ? TINT.blueLine : TINT.line, shadow: false, r: 0.1 });
        T(s, t, x + 0.12, sy, sw - 0.24, sh, { size: 12, color, align: "center", valign: "middle", bold: i === 4 });
        if (i < 4) arrow(s, x + sw + 0.03, sy + sh / 2, 0.24, li ? HEX.accent1 : "9AA7BF", 1.5);
      });
      T(s, [{ text: li ? "Дальше: " : "Пробел: ", options: { bold: true } }, { text: tail }], L + 0.3, sy + sh + 0.1, W - 0.6, 0.32, { size: 12, color: li ? HEX.accent1 : ST.no.text });
    });
  }

  // ---------- A5. Рынок ----------
  {
    const s = slide("Контент", "Приложение", "A5 · РЫНОК", "Реестр, ЭДО и сервисы данных отвечают «действует ли МЧД» и «кто компания», «Мандат» — «можно ли эту операцию сегодня»",
      "«—» — нет в открытых описаниях продукта на 28.10.2026. Источники: vtb.ru/malyj-biznes/ukep/mchd; i.vkontur.ru/news/3157; developer.kontur.ru; описания Контур.Фокуса и СПАРК; docs/COMPETITORS.md; Signicat (M-37)", { appx: "A5" });
    table(s, ["Что сравниваем", "Сервис МЧД «ВТБ Бизнес»", "ЭДО: Контур.Диадок, 1С-ЭДО", "Данные: Контур.Фокус, СПАРК", "Реестр МЧД ФНС", "«Мандат»"], [
      ["Объект проверки", "выпуск исходящих МЧД", "даты МЧД и сертификат под документом", "досье и изменения компании", "действует ли МЧД", "операция банка на дату"],
      ["Полномочие на операцию", "—", "«Диадок не может» (Контур)", "—", "—", `${RULES} с нормой`],
      ["Передоверие", "признак передоверия", "—", "—", "сведения о цепочке", "сроки, сужение, каскад отмены"],
      ["ЕГРЮЛ на дату и устав", "—", "—", "история ЕГРЮЛ, без устава", "—", "в одной проверке"],
      ["Смена директора", "—", "—", "уведомление об изменении", "—", "сигнал или разбор; отзыв удостоверений"],
      ["Доказательство и аудит", "—", "—", "выписки", "статус в реестре", "XPath, ГРН, норма; журнал; удостоверение"],
    ], L, TOP + 0.1, W, [2.05, 1.95, 2.1, 2.05, 1.65, 2.333], { size: 11, highlight: 5, headH: 0.55, rowH: 0.52 });
    band(s, L, 5.95, W, 0.75, "В ЕС проверка полномочия — уже отдельный продукт (Signicat InstantAuthority, Нидерланды). Мы не конкурируем с выпуском МЧД и данными о компаниях — берём их на вход.", { icon: "Puzzle", size: 13, fill: TINT.blue, color: C.text2, tile: "FFFFFF", shadow: false });
  }

  // ---------- A6. Правила ----------
  {
    const s = slide("Контент", "Приложение", "A6 · ПРАВИЛА", `${RULES} со ссылкой на норму: что они ловят`,
      "Полная таблица «правило → норма → эффект → тест» — docs/RULES.md. Юрист банка утверждает правила подписанной записью; до утверждения строгий профиль не выносит по правилу ни «да», ни «нет»", { appx: "A6" });
    table(s, ["Группа", "Правила", "Что ловят"], [
      ["Формат", "R-FMT-001…005", "битый XML и XML-бомбы, чужой формат, реквизиты по Приказу № 1001, безотзывная доверенность, повтор документов и полей"],
      ["Стороны", "R-ID-001…005", "контрольные числа, чужая компания, чужой представитель, наименование не по ЕГРЮЛ, ИНН и СНИЛС разных людей"],
      ["Реестр и срок", "R-REG-001, R-TERM-001…002", "отмена и истечение, недоступный реестр, срок по закону — год со дня совершения"],
      ["Руководитель", "R-EIO-001…006, R-BNK-001", "подписант не директор на дату, смена директора (сигнал или разбор), недостоверность, ликвидация и банкротство"],
      ["Покрытие", "R-COV-001…002, R-JNT-001", "нет полномочия на операцию, код исключён из классификатора, совместные полномочия"],
      ["Передоверие", "R-SUB-001…010", "запрет, однократность, расширение, срок, пропущенное звено, каскад отмены, утрата полномочий, длинная цепочка, нотариальная форма"],
      ["Устав и подпись", "R-CHR-001…002, R-TXT-001, R-PAP-001, R-SIG-001…002", "порог одобрения, неразобранный устав, текстовые полномочия, бумажная доверенность, КЭП и подпись распоряжения руководителя"],
    ], L, TOP, W, [1.9, 3.75, 6.483], { size: 12, headH: 0.42, rowH: 0.62 });
  }

  // ---------- A7. Пять норм ----------
  {
    const s = slide("Контент", "Приложение", "A7 · ПРАВО", "Пять норм, на которых ошибается ручная сверка",
      "ГК РФ, ст. 186–188; 127-ФЗ, ст. 64, 94, 126; разъяснения ФНС о смене руководителя; описание схемы МЧД (ПрУтрПолн). Перед пилотом ссылки сверяет юрист банка — docs/REGULATORY.md", { appx: "A7" });
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
    band(s, L, TOP + ch + GAP, W, BOTTOM - (TOP + ch + GAP), "Ручная сверка «на сегодня» ошибается в обе стороны: отказывает по действующей МЧД и пропускает оборванную цепочку", { icon: "Scale", size: 14 });
  }

  // ---------- A8. Доверие без доступа к базе ----------
  {
    const s = slide("Контент", "Приложение", "A8 · ДОВЕРИЕ БЕЗ БАЗЫ", "Доверие без доступа к базе: подписанное удостоверение, статус и журнал",
      "Стандарты: W3C Verifiable Credentials 2.0 и Bitstring Status List v1.0 (Рекомендации W3C, 15.05.2025); RFC 6962 / RFC 9162. На стенде подпись Ed25519, к пилоту — ГОСТ через СКЗИ банка", { appx: "A8" });
    const cols = [
      ["BadgeCheck", "Удостоверение", ["вердикт, операция, хеш набора правил и входов", "номера МЧД цепочки", "без ФИО, только хеш ИНН представителя", "привязка к сумме, получателю и одноразовому nonce"]],
      ["Bell", "Статус", ["списки «отозвано» и «приостановлено»", "мониторинг меняет статус по событию", "проверяющий видит статус сам и отвергает подложенный старый список"]],
      ["Lock", "Журнал решений", ["хеш-цепочка и HMAC: правка записи видна", "дерево Меркла: доказательство одной записи", "аудитор не получает весь журнал", "внешний якорь сегментов в контуре банка"]],
    ];
    const cw = (W - 2 * GAP) / 3, ch = 3.4;
    cols.forEach(([ic, head, items], i) => {
      const x = L + i * (cw + GAP);
      card(s, x, TOP, cw, ch);
      tile(s, x + 0.25, TOP + 0.22, 0.52, ic);
      T(s, head, x + 0.92, TOP + 0.22, cw - 1.1, 0.52, { size: 16, bold: true, color: C.text2, valign: "middle" });
      bullets(s, items, x + 0.3, TOP + 0.95, cw - 0.55, ch - 1.05, { size: 13, psa: 6 });
    });
    const y2 = TOP + ch + GAP;
    card(s, L, y2, W, BOTTOM - y2, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
    T(s, "Сценарий", L + 0.3, y2 + 0.2, 2.0, 0.3, { size: 12, bold: true, color: C.accent1, cs: 0.5 });
    const flow = ["проверка", "удостоверение — в «ВТБ Бизнес»", "основную МЧД отменили", "мониторинг ставит «отозвано»", "интернет-банк видит статус сам"];
    const fy = y2 + 0.62, fh = BOTTOM - y2 - 0.85, fg = 0.3, fw = (W - 0.6 - 4 * fg) / 5;
    flow.forEach((t, i) => {
      const x = L + 0.3 + i * (fw + fg);
      card(s, x, fy, fw, fh, { fill: "FFFFFF", line: TINT.blueLine, shadow: false, r: 0.1 });
      T(s, t, x + 0.12, fy, fw - 0.24, fh, { size: 12.5, bold: i === 4, color: C.text2, align: "center", valign: "middle" });
      if (i < 4) arrow(s, x + fw + 0.03, fy + fh / 2, fg - 0.06, HEX.accent1, 1.5);
    });
  }

  // ---------- A9. Методика качества ----------
  {
    const s = slide("Контент", "Приложение", "A9 · ИЗМЕРЕНИЕ", "Как мы измеряем качество — и как пилот переносит метрики на данные ВТБ",
      "Источники: evidence.json, docs/EVALUATION.md, docs/audit/AUDIT-2026-10-27-DIFFERENTIAL.md; Brown, Cai, DasGupta (2001) — интервал Вилсона; Geifman, El-Yaniv (2017) — «риск — покрытие»", { appx: "A9" });
    table(s, ["Что", `Сейчас: корпус из ${N.cases} кейсов`, "В пилоте: данные ВТБ"], [
      ["Корпус", `${N.cases} кейса, ${N.kinds} видов: ${N.kindsBy.ok} зелёный, ${N.kindsBy.no} опровергающих, ${N.kindsBy.rv} на разбор, ${N.kindsBy.nd} без данных`, "50–100 обезличенных пакетов"],
      ["Ложные «подтверждено»", `${N.falseConfirmed} из ${N.negatives}, верхняя граница 95% — ${N.fcUpper}; по видам дефектов 0 из ${N.negativeTypes}, граница ${N.fcUpperByType}`, "стоп-критерий: 0 на всех кейсах пилота"],
      ["Полнота", `все опровергающие найдены, нижняя граница 95% — ${N.recallLow}`, `не меньше ${N.nRecall} дефектных случаев для ±5%`],
      ["Покрытие", `${N.coverage} автоматических вердиктов в профиле demo, риск среди них ≤ ${N.autoRiskUpper}; ${N.strictCoverage} в strict`, "порог согласуем с банком до старта"],
      ["Устойчивость", `${FUZZ_TXT} мутаций сценариев без ложного «да»; отложенный seed без расхождений`, "отдельная попытка построить ложное «да»"],
      ["Независимая проверка", `${N.chains} цепочки: «Мандат» и Cedar совпали; JCS, Меркл и подпись сверены с внешними эталонами`, "символьное доказательство свойства на Cedar SymCC"],
      ["Разметка", "ожидаемый вердикт задан генератором", "два независимых эксперта, каппа Коэна"],
    ], L, TOP, W, [2.5, 6.0, 3.633], { size: 12, headH: 0.42, rowH: 0.6 });
  }

  // ---------- A10. ИБ ----------
  {
    const s = slide("Контент", "Приложение", "A10 · ИНФОРМАЦИОННАЯ БЕЗОПАСНОСТЬ", "Всё в контуре банка: данные, модель и решения",
      "Проектные решения «Мандата»; оценку соответствия проводят ИБ и юристы банка в пилоте. Меры по 821-П, 851-П и ГОСТ Р 57580.1 сопоставлены в docs/REGULATORY.md; docs/DATA_PROTECTION.md, docs/THREAT_MODEL.md", { appx: "A10" });
    const tiles = [
      ["Server", "On-prem без сети", "ядро не делает внешних запросов; модель — только хосты из белого списка, по умолчанию выключена"],
      ["Fingerprint", "Персональные данные", "псевдонимизация ИНН, СНИЛС, паспорта, телефона, почты; в модель — только строки о полномочиях"],
      ["FileX", "Вредоносные файлы", "защита от XML-бомб и внешних сущностей, строгий JSON на всех внешних входах, лимиты размера и глубины"],
      ["KeyRound", "API и роли", "оператор, рецензент, аудитор; чужой кейс не виден; токены — только хеши, лимит частоты и тела"],
      ["Scale", "152-ФЗ и 115-ФЗ", "спроектировано с учётом 152-ФЗ; идентификацию по 115-ФЗ не подменяем — она за каналами банка"],
      ["Container", "Подпись и сборка", "КЭП — через СКЗИ банка, ключ — HSM, Vault или KMS; в CI закреплённые версии, pip-audit, CodeQL, SBOM"],
    ];
    const cw = (W - 2 * GAP) / 3, ch = (BOTTOM - TOP - GAP) / 2;
    tiles.forEach(([ic, head, t], i) => {
      const x = L + (i % 3) * (cw + GAP), y = TOP + Math.floor(i / 3) * (ch + GAP);
      card(s, x, y, cw, ch);
      tile(s, x + 0.25, y + 0.25, 0.52, ic);
      T(s, head, x + 0.92, y + 0.25, cw - 1.1, 0.52, { size: 15, bold: true, color: C.text2, valign: "middle" });
      T(s, t, x + 0.25, y + 0.95, cw - 0.5, ch - 1.05, { size: 13, color: C.text1 });
    });
  }

  // ---------- A11. Риски ----------
  {
    const s = slide("Контент", "Приложение", "A11 · РИСКИ", "Риски, которые банк увидит первыми, — и как мы их снимаем",
      "LIMITS.md — полный список границ; docs/AI_RISKS.md — матрица рисков ИИ; docs/audit — Red Team допуска операции", { appx: "A11" });
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

  // ---------- A12. Расчёт эффекта ----------
  {
    const s = slide("Контент", "Приложение", "A12 · ЭКОНОМИКА", "Расчёт эффекта: формулы, база Банка России и мировые ориентиры",
      "Источники: Банк России, I–II кв. 2026 (M-07, M-15); ВТБ, пресс-релиз к МСФО за 1П 2026 (M-09); Fenergo — отраслевые опросы 2022 и 2025 гг. (M-11); Companies House, 30.06.2025 (M-36); финансовая модель пакета аналитики", { appx: "A12" });
    const cw = (W - GAP) / 2, ch = 3.55;
    card(s, L, TOP, cw, ch);
    tile(s, L + 0.25, TOP + 0.22, 0.46, "Coins");
    T(s, "Формулы «Мандата»", L + 0.85, TOP + 0.22, cw - 1.1, 0.46, { size: 15, bold: true, color: C.text2, valign: "middle" });
    bullets(s, [
      "Ставки = клиенты × доля с событием представительства × минуты на кейс (с разбором) ÷ 60 ÷ 1 800 ч",
      `Ущерб = ${EFFECT.incidentsPerQuarter} × 4 × доля ВТБ × доля случаев с полномочиями × ${comma(EFFECT.lossPerIncidentMln, 2)} млн ₽`,
      `База — I полугодие 2026: ${CBR.q1.ops + CBR.q2.ops} операций против юрлиц на ${comma((CBR.q1.lossMln + CBR.q2.lossMln) / 1000, 1)} млрд ₽ (Банк России)`,
      "Доли и минуты калибруются в первые две недели пилота; расчёт — python scripts/effect_model.py --scenarios",
    ], L + 0.3, TOP + 0.9, cw - 0.55, ch - 1.0, { size: 12.5, psa: 6 });
    const x2 = L + cw + GAP;
    card(s, x2, TOP, cw, ch);
    tile(s, x2 + 0.25, TOP + 0.22, 0.46, "Globe");
    T(s, "Мировые ориентиры", x2 + 0.85, TOP + 0.22, cw - 1.1, 0.46, { size: 15, bold: true, color: C.text2, valign: "middle" });
    bullets(s, [
      "1 500–3 000 долл. США — столько больше половины корпоративных банков тратят на одну KYC-проверку клиента (опрос Fenergo, 2022)",
      "70% финансовых организаций теряли клиентов из-за медленного подключения (опрос Fenergo, 2025)",
      "Великобритания: за год регистратор оспорил ложные сведения по 100 400 компаниям и отклонил 10 200 подозрительных заявлений (Companies House, 2025)",
    ], x2 + 0.3, TOP + 0.9, cw - 0.55, ch - 1.0, { size: 12.5, psa: 6 });
    band(s, L, TOP + ch + GAP, W, BOTTOM - (TOP + ch + GAP), `Для ВТБ в базовом сценарии: окупаемость разовых затрат — ${PAYBACK}, чистая отдача — ${ROI} ₽ на рубль затрат за три года. Пилот заменяет параметры фактами банка`, { icon: "Timer", size: 14 });
  }

  // ---------- A13. Реальные кейсы ----------
  {
    const s = slide("Контент", "Приложение", "A13 · РЕАЛЬНЫЕ КЕЙСЫ", "Реальные кейсы: где ошибка в полномочиях стоит денег — и что делает «Мандат»",
      "M-15, M-17, M-35–M-38 реестра утверждений: Банк России; Forbes и АБН по данным РБК; ВС РФ; Companies House; документация Signicat; Google AP2", { appx: "A13" });
    table(s, ["Кейс", "Что произошло", "Урок для банка", "Что делает «Мандат»"], [
      ["«ЭОС», Россия, октябрь 2026", "09.10 в ЕГРЮЛ внесли неизвестного компании гендиректора; по версии компании — по поддельным документам; 19.10 прежние руководители восстановлены", "захват начинается в реестре, а заканчивается в банке", "свежая смена — «на разбор»; событие ЕГРЮЛ приостанавливает удостоверения"],
      ["ВС РФ, 2023, № 308-ЭС22-17199", "банк отказал в переводе 6,5 млн ₽ по поручению действующего руководителя; суд обязал провести операцию, ВС РФ оставил это в силе", "ошибка «нет» тоже стоит банку денег", "проверка на дату: законный подписант получает «да» с доказательством"],
      ["Банк России, II кв. 2026", `${CBR.q2.ops} операций без согласия у юрлиц на ${Math.round(CBR.q2.lossMln)} млн ₽; возмещено ${comma(CBR.q2.refundAccounts, 1)}% по счетам и ${comma(CBR.q2.refundSbp, 1)}% через СБП`, "потери бизнеса крупные и почти не возвращаются", "предотвращение до операции: неизвестное не становится «да»"],
      ["Companies House, Великобритания", "по закону ECCTA 2023 за год до 03.2025 отклонено 10 200 подозрительных заявлений; с 18.11.2025 — проверка личности директоров до регистрации", "реестр закрывает вход, но не операцию в банке", "банк проверяет полномочие на дату операции"],
      ["Signicat InstantAuthority, Нидерланды", "запрос в Торговый реестр KvK и вход через iDIN возвращают роль, мандат и его ограничения", "проверка полномочия — уже отдельный продукт", "то же для МЧД, ЕГРЮЛ и устава — с нормой и журналом"],
      ["Google AP2, 2025–2026", "платёж ИИ-агента опирается на подписанные мандаты с пределами пользователя", "агенту нужен проверяемый мандат", "удостоверение привязано к операции, сумме, получателю и nonce"],
    ], L, TOP, W, [2.45, 4.25, 2.55, 2.883], { size: 10.5, headH: 0.4, rowH: 0.74 });
  }

  // ---------- A14. Стандарты и SOTA 2026 ----------
  {
    const s = slide("Контент", "Приложение", "A14 · СТАНДАРТЫ И SOTA 2026", "Открытые стандарты и практики 2025–2026 в основе «Мандата»",
      "W3C, 15.05.2025; w3c/vc-di-eddsa; RFC 9162, RFC 3161; Sigstore; CNCF; standardwebhooks.com; GLEIF; OWASP Top 10 for LLM Applications 2025; SLSA, CycloneDX, OpenSSF Scorecard. docs/SOTA.md", { appx: "A14" });
    const std = [
      ["BadgeCheck", "W3C VC 2.0 и Bitstring Status List", "удостоверение проверки со статусом «действует / приостановлено / отозвано» (Рекомендации W3C, май 2025)"],
      ["Signature", "eddsa-jcs-2022", "каноническая запись, хеш и подпись совпадают с эталонными векторами W3C"],
      ["Layers", "RFC 9162 и внешний якорь", "журнал с доказательством одной записи; хеш сегмента — под метку RFC 3161 или в Sigstore Rekor"],
      ["GitCompare", "Cedar · CNCF", "вторая независимая реализация передоверия; символьная проверка Cedar SymCC — в плане"],
      ["Webhook", "Standard Webhooks", "подписанные события о полномочиях для антифрода банка"],
      ["IdCard", "GLEIF vLEI · ISO 17442-3", "роль представителя как проверяемое удостоверение — тот же принцип"],
      ["ShieldAlert", "OWASP LLM Top 10 2025", "риски советника-ИИ разобраны: шлюз цитат, данные в маркерах, без инструментов"],
      ["Container", "SLSA, SBOM, Scorecard", "подписанное происхождение сборки, CycloneDX SBOM, закреплённые версии"],
    ];
    const gap = 0.2, cw = (W - 3 * gap) / 4, ch = (BOTTOM - TOP - 0.95 - gap) / 2;
    std.forEach(([ic, head, t], i) => {
      const x = L + (i % 4) * (cw + gap), y = TOP + Math.floor(i / 4) * (ch + gap);
      card(s, x, y, cw, ch);
      tile(s, x + 0.2, y + 0.2, 0.44, ic);
      T(s, head, x + 0.76, y + 0.16, cw - 0.9, 0.52, { size: 12, bold: true, color: C.text2, valign: "middle" });
      T(s, t, x + 0.2, y + 0.78, cw - 0.38, ch - 0.84, { size: 11, color: C.text1 });
    });
    band(s, L, BOTTOM - 0.75, W, 0.75, "Всё на открытых стандартах: банк проверяет удостоверение, журнал и правила своими средствами и не зависит от поставщика", { icon: "ShieldCheck", size: 14 });
  }

  // ---------- A15–A16. Трудные вопросы ----------
  const hard = [
    [
      ["Чем вы лучше проверки статуса МЧД в реестре ФНС?", `Реестр отвечает «действует или отменена». Мы — покрывает ли МЧД эту операцию у этой компании на эту дату, с ЕГРЮЛ, уставом, передоверием и событиями. Статус реестра — одно из ${RULES}`],
      ["0 ложных «да» на синтетике — что это доказывает?", `Что правила ловят каждый из ${N.negativeTypes} видов дефектов: 0 из ${N.negatives}, верхняя граница ${N.fcUpper}. На потоке ВТБ ту же метрику даёт пилот — она же стоп-критерий`],
      ["Кто отвечает, если «подтверждено», а это мошенник?", "Решение остаётся за банком по его регламенту. «Мандат» даёт доказательство и журнал; в пилоте — теневой режим, права он не выдаёт"],
      ["Почему правила, а не машинное обучение?", "Полномочия — вопрос права, а не вероятности. Правила объяснимы и проверяемы; машинное обучение уместно для сигналов риска"],
      ["Зачем тогда ИИ?", "Извлечь положения устава и текстовые полномочия с дословной цитатой. Без модели продукт работает полностью"],
      [`Откуда ${LOSS[0]}–${LOSS[2]} млн ₽?`, "База — хищения у юрлиц по данным Банка России за I полугодие 2026; три явных параметра калибрует пилот"],
      ["Что работает сейчас, а что подключается в пилоте?", "Работают ядро, правила, журнал, удостоверения, мониторинг, API с ролями, SBOM. В пилоте — адаптеры к реестру МЧД, ЕГРЮЛ, ЕФРСБ и СКЗИ банка через готовые порты"],
      ["Почему в демо одно «подтверждено»?", "Демо — набор ловушек: 13 из 14 кейсов содержат дефект или сомнение. «Да» выдаётся только при полном доказательстве, включая устав"],
    ],
    [
      ["Почему смена директора не прекращает МЧД?", "Перечень оснований в п. 1 ст. 188 ГК РФ закрытый, ФНС это подтверждает. Новый директор может отменить МЧД"],
      ["Что при конфликте ЕГРЮЛ и МЧД?", "Корневую МЧД подписал не директор по ЕГРЮЛ на дату выдачи — «опровергнуто» (R-EIO-001). Сверить нельзя — «на разбор». Свежая смена директора — всегда разбор"],
      ["Как с ИБ и ПДн?", "On-prem, без сети, псевдонимизация, удостоверение без ФИО, роли в API. Оценку соответствия проводит банк"],
      ["Сколько стоит внедрение?", `Пилот — 6 недель на одной операции. Модель для ВТБ: внедрение ≈${comma(MODEL.inputs.impl_t1.values[1], 0)} млн ₽ и подписка ≈${comma(BANK.sub_vtb[1], 0)} млн ₽ в год — ≈${Math.round(100 * BANK.price_share[1])}% прямого эффекта банка; итоговую цену называем по замерам пилота`],
      ["Это требование только российское?", "Нет. FATF требует проверять, что представитель уполномочен; в ЕС с 10.07.2027 — Регламент 2024/1624 и стандарты AMLA; в Нидерландах это уже отдельный сервис"],
      ["Если запрос придёт от ИИ-агента?", "Агент действует от имени человека, а тот — от имени компании. «Мандат» проверяет всю цепочку и выдаёт удостоверение с пределами операции — как мандаты AP2"],
      ["Почему вам можно доверять код?", `${TESTS} проходят (v${N.version}, evidence.json), независимая проверка на Cedar, сверка с внешними эталонами, реестр утверждений`],
      ["Есть ли юрлицо для пилота?", "Для конкурса не нужно. К пилоту через Акселератор ВТБ — регистрация компании или партнёр-интегратор"],
    ],
  ];
  hard.forEach((rows, i) => {
    const id = i ? "A16" : "A15";
    const s = slide("Контент", "Приложение", `${id} · ТРУДНЫЕ ВОПРОСЫ`, `Трудные вопросы жюри (${i + 1}/2)`,
      "Ответы опираются на CLAIMS_REGISTRY.md и LIMITS.md: каждое число — с командой воспроизведения и границей", { appx: id });
    table(s, ["Вопрос", "Ответ"], rows, L, TOP, W, [4.0, 8.133], { size: 11, headH: 0.4, rowH: 0.56 });
  });

  // ---------- A17. Нормативная база ----------
  {
    const s = slide("Контент", "Приложение", "A17 · НОРМАТИВНАЯ БАЗА", "Нормативная база на октябрь 2026 и что она меняет в «Мандате»",
      "Ревизия 28.10.2026 по открытым источникам; перед пилотом каждую строку сверяет юрист банка. docs/REGULATORY.md, docs/SOURCES.md, реестр утверждений M-01–M-04, M-16, M-40, M-41", { appx: "A17" });
    table(s, ["Акт", "С какой даты", "Что меняет"], [
      ["63-ФЗ в ред. 476-ФЗ", "01.09.2024", "представитель юрлица подписывает своей КЭП вместе с МЧД"],
      ["ГК РФ, ст. 185–189", "действует", "срок по умолчанию — год; передоверие; закрытый перечень прекращения; каскад"],
      ["127-ФЗ; подп. 7 п. 1 ст. 188 ГК РФ", "действует", "конкурсное производство и внешнее управление прекращают МЧД"],
      ["115-ФЗ, ст. 7", "действует", "идентификация представителя и документ-основание: номер, дата, срок"],
      ["Приказ Банка России № ОД-2506", "01.01.2026", "12 признаков операций без согласия вместо 6; признак из ГИС — с 01.03.2026"],
      ["Приказ Минцифры № 1001", "01.02.2026", "СНИЛС и ИНН представителя, КПП, наименование полномочия рядом с кодом"],
      ["Реестр МЧД ФНС", "01.03.2026", "поиск МЧД по номеру, ИНН доверителя и ИНН представителя"],
      ["41-ФЗ, ГИС «Антифрод»", "01.03.2026", "обмен сведениями о мошенничестве банков, операторов и госорганов; события «Мандата» — в антифрод банка"],
      ["243-ФЗ о технологиях ИИ", "01.09.2026; ст. 5 — 01.03.2027", "национальные модели — по решению Правительства, для банков — с Банком России"],
      ["210-ФЗ, антифрод-пакет", "поэтапно с 01.09.2026", "обязанности банков по защите и возмещению; применимость к юрлицам определяет юрист банка"],
      ["821-П в ред. № 7220-У", "01.10.2026", "защита информации при переводах; меры «Мандата» сопоставлены в docs/REGULATORY.md"],
      ["Кодекс этики ИИ; 3-МР", "2025; 16.06.2026", "прозрачность, человек в контуре, подтверждение человеком при высоком риске"],
      ["315-ФЗ", "01.09.2027", "единая форма МЧД основная; формы Банка России — дополнительные"],
    ], L, TOP, W, [3.15, 2.5, 6.483], { size: 10.5, headH: 0.36, rowH: 0.345 });
  }

  // ---------- A18. Дорожная карта ----------
  {
    const s = slide("Контент", "Приложение", "A18 · ДОРОЖНАЯ КАРТА", "Дорожная карта: от онлайн-питча до решения о пилоте", "Сроки этапов — по сайту соревнования fintechacademy.ru (submission/README.md); задачи — docs/ROADMAP.md, docs/MVP.md, docs/HANDOFF_NEXT_QUOTA.md", { appx: "A18" });
    const cols = [
      ["до 01.11", "Спринт 1", ["готово: вертикальный срез — 14 демо-кейсов, отчёт, журнал, удостоверения", "пакет аналитики и видео-демо", "в работе: интервью владельцев процесса"], 0],
      ["10–15.11", "Онлайн-питч", ["питч 5 минут по сценарию показа", "живое демо: кейсы 01, 05, 10, 11 и мониторинг", "сверка критериев трека с организатором"], 0],
      ["16–29.11", "Спринт 2", ["профиль strict и правки по обратной связи", "обезличенные кейсы от эксперта", "коды из выгрузки ЕСНСИ", "прототип проверки КЭП через СКЗИ"], 0],
      ["03–08.12", "Спринт 3", ["план пилота, расчёт эффекта на данных интервью", "контракт интеграции с ДБО (OpenAPI)", "символьная проверка на Cedar SymCC"], 0],
      ["после 11.12", "Пилот", ["6 недель в теневом режиме", "одна операция", "решение: продолжать или остановить"], 1],
    ];
    const gap = 0.2, cw = (W - 4 * gap) / 5, ch = 3.8;
    cols.forEach(([d, head, items, hl], i) => {
      const x = L + i * (cw + gap);
      card(s, x, TOP, cw, ch, hl ? { fill: C.text2, line: null } : {});
      T(s, d, x + 0.22, TOP + 0.2, cw - 0.44, 0.3, { size: 12, bold: true, color: hl ? HEX.accent2 : C.accent1 });
      T(s, head, x + 0.22, TOP + 0.52, cw - 0.44, 0.42, { size: 17, bold: true, color: hl ? C.background1 : C.text2 });
      bullets(s, items, x + 0.24, TOP + 1.08, cw - 0.42, ch - 1.18, { size: 12, psa: 6, color: hl ? C.background1 : C.text1 });
    });
    band(s, L, TOP + ch + GAP, W, BOTTOM - (TOP + ch + GAP), "Финал 11.12.2026: живое демо и — если банк даст данные — результаты на обезличенных кейсах ВТБ", { icon: "Flag", size: 15, bold: true });
  }
  if (pages() !== INDEX_SLIDE + APPX.length) throw new Error(`слайдов ${pages()}, а ожидалось ${INDEX_SLIDE + APPX.length}`);

  await pres.writeFile({ fileName: OUT });
  console.log(`готово: ${OUT} — ${pages()} слайдов`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
