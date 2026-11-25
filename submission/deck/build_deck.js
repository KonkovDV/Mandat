// Презентация «Мандата» как код: все числа о продукте берутся из evidence.json.
//
//   node build_deck.js ../../evidence.json ../slides/Mandat_VTB.pptx
//   python finish_deck.py ../slides/Mandat_VTB.pptx      # цвета темы, язык, alt-текст, метаданные
//
// Оформление — палитра ВТБ, заголовки Manrope, текст Golos Text, акцент — курсив Cormorant Garamond (все OFL);
// фирменный VTB Group UI лицензирован только для Группы ВТБ. Графика рисуется в graphics.js. Числа рынка и эффекта — из открытых источников
// и допущений команды; каждое записано в CLAIMS_REGISTRY.md с источником или командой воспроизведения.
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");
const sharp = require("sharp");
const { buildGraphics, photoPanel, roundedPhoto, squarePhoto, icon3d } = require("./graphics");

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
const COVER = `${comma(ev.tests.coverage_percent, 1)}%`;
// Матрица готовности к пилоту по чек-листу воркшопа 20.11 (docs/PILOT_CHECKLIST.md): статус пункта ставит прогон его
// тестов при сборке evidence.json, пункты без тестов — за банком. Упавший тест останавливает сборку колоды.
const READY = (() => {
  const checks = Object.values(ev.pilot_readiness.checks);
  const ok = checks.filter((c) => c.status === "проверено" && c.tests > 0 && c.failed === 0).length;
  const bank = checks.filter((c) => c.tests === 0).length;
  if (ok + bank !== checks.length) throw new Error("evidence.json: в матрице готовности есть пункт с упавшим тестом");
  return { ok, all: checks.length, bank, bankWord: plural(bank, "пункт", "пункта", "пунктов") };
})();
const FUZZ = (() => {
  const text = fs.readFileSync(path.join(ROOT, "tests", "test_redteam_fuzz.py"), "utf8");
  const m = text.match(/assert len\(fuzz_rows\) == (\d+)/);
  if (!m) throw new Error("в tests/test_redteam_fuzz.py не найдено закреплённое число мутаций");
  return Number(m[1]);
})();
// Четырёхзначные числа пишутся слитно (8814), от пяти знаков — с неразрывным пробелом между разрядами.
const ruNum = (n) => (Math.abs(n) < 10000 ? String(n) : n.toLocaleString("ru-RU").replace(/\s/g, " "));
const FUZZ_TXT = ruNum(FUZZ);

// ---------- Эффект: параметры — src/mandat/data/effect_scenarios.json, их же читает scripts/effect_model.py --scenarios ----------
const EFFECT_RAW = JSON.parse(fs.readFileSync(path.join(ROOT, "src", "mandat", "data", "effect_scenarios.json"), "utf8"));
const EFFECT = { ...EFFECT_RAW, lossPerIncidentMln: EFFECT_RAW.lossPerQuarterMln / EFFECT_RAW.incidentsPerQuarter };
const FTE = [0, 1, 2].map((i) => Math.round((EFFECT.clients * EFFECT.eventShare[i] * EFFECT.minutesSaved[i]) / 60 / EFFECT.hoursPerFte));
const LOSS = [0, 1, 2].map((i) => Math.round(EFFECT.incidentsPerQuarter * 4 * EFFECT.vtbShare[i] * EFFECT.authorityShare[i] * EFFECT.lossPerIncidentMln));
const shares = (arr) => arr.map((x) => Math.round(100 * x)).join(" / ");

// ---------- Банк России: операции без согласия у юрлиц за I–III кварталы 2026 (M-07, M-15, M-52) ----------
// Квартал — сумма двух строк таблицы обзора по юрлицам: счета и СБП. III кв.: 868 операций на 679,7 млн ₽ по счетам
// и 63 операции на 15,4 млн ₽ через СБП (cbr.ru, 19.11.2026; данные предварительные).
const CBR = {
  q1: { ops: 818, lossMln: 573, shareAll: 7.8 },
  q2: { ops: 780, accounts: 707, sbp: 73, lossMln: 831.4, refundAccounts: 19.5, refundSbp: 0.5, allOps: 458655, allLossMln: 7350.7, shareAll: 11.3 },
  q3: { ops: 931, accounts: 868, sbp: 63, lossMln: 695.1, refundAccounts: 11.2, refundSbp: 0, allOps: 477401, allLossMln: 7250.3, shareAll: 9.6 },
};
CBR.q3.avgMln = CBR.q3.lossMln / CBR.q3.ops;
CBR.q3.allAvgK = (CBR.q3.allLossMln * 1000) / CBR.q3.allOps;
CBR.q3.ratio = Math.round((CBR.q3.avgMln * 1000) / CBR.q3.allAvgK);
CBR.q3.change = Math.round((100 * (CBR.q3.lossMln - CBR.q2.lossMln)) / CBR.q2.lossMln);
CBR.m9 = { ops: CBR.q1.ops + CBR.q2.ops + CBR.q3.ops, lossMln: CBR.q1.lossMln + CBR.q2.lossMln + CBR.q3.lossMln };
if (CBR.m9.ops / 3 !== EFFECT.incidentsPerQuarter || Math.abs(CBR.m9.lossMln / 3 - EFFECT.lossPerQuarterMln) > 0.05) {
  throw new Error("effect_scenarios.json расходится с данными Банка России за I–III кварталы 2026");
}
const AVG_Q3 = `≈${comma(CBR.q3.avgMln, 2)} млн ₽`;

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
// Юнит-экономика базового сценария — то же деление значений модели, что scripts/effect_model.py --unit (C-21).
const UNIT = (() => {
  const i = 1;
  const manual = (BANK.minutes[i] * BANK.cost_fte[i] * 1e6) / (BANK.hours_fte[i] * 60);
  const mandat = (BANK.annual_cost[i] * 1e6) / (BANK.events[i] * 1000);
  return { manual: Math.round(manual / 10) * 10, mandat: Math.round(mandat), ratio: comma(manual / mandat, 1) };
})();

// ---------- Демо-кейс 14 для оси времени: даты берутся из файлов кейса ----------
const CASE14 = path.join(ROOT, "examples", "cases", "14_old_director_charter_cleared");
const C14 = (() => {
  const egrul = JSON.parse(fs.readFileSync(path.join(CASE14, "egrul.json"), "utf8"));
  const kase = JSON.parse(fs.readFileSync(path.join(CASE14, "case.json"), "utf8"));
  const poa = fs.readFileSync(path.join(CASE14, "poa-root.xml"), "utf8");
  const attr = (name) => (poa.match(new RegExp(`${name}="([^"]+)"`)) || [])[1];
  const [prev, next] = egrul.executives;
  return {
    prev, next, issued: attr("ДатаВыдДовер"), until: attr("СрокДейст"), op: kase.operation, poa: Object.keys(kase.registry)[0],
    who: kase.requester.full_name, org: kase.organization.name,
  };
})();

// ---------- Сценарий mandat showcase для слайда «Захват до платежа»: шаги — из отчёта самой команды ----------
// make deck запускает `mandat showcase --out var/showcase` перед сборкой.
const SHOW = (() => {
  const file = path.join(ROOT, "var", "showcase", "showcase.json");
  if (!fs.existsSync(file)) throw new Error("нет var/showcase/showcase.json: запустите mandat showcase --out var/showcase");
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  const got = data.steps.map((x) => x.outcome).join(" → ");
  const want = "ПОДТВЕРЖДЕНО → ВЫПУЩЕНО → ДОПУЩЕНО → ОТКАЗ → ОТКАЗ → ПРИЗНАКИ ЗАХВАТА → ОТКАЗ";
  if (got !== want || !data.journal.intact || !data.refusal_inclusion_proof_verified) {
    throw new Error(`mandat showcase дал «${got}», журнал цел: ${data.journal.intact}; слайд рассчитан на «${want}»`);
  }
  return data;
})();

// ---------- Ответы ядра на каждый день для кейса 14 (слайд 6): make deck пишет var/scrub.json ----------
const SCRUB = (() => {
  const file = path.join(ROOT, "var", "scrub.json");
  if (!fs.existsSync(file)) throw new Error("нет var/scrub.json: запустите python scripts/build_site.py --scrub var/scrub.json");
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  const kinds = new Set(data.segments.map((x) => x.verdict));
  if (kinds.size !== 4) throw new Error(`в ответах по датам ${kinds.size} вида вердикта, а слайд рассчитан на четыре`);
  return data;
})();

// ---------- Масштаб в базовом сценарии модели (слайд «После пилота») ----------
const SCALE = (() => {
  const base = MODEL.calc[1];
  return {
    banksFirst: base.series.banks[0], banksLast: base.const.banks_2031, yearFirst: MODEL.years[0], yearLast: MODEL.years[MODEL.years.length - 1],
    arr: Math.round(base.const.arr_2031), teamFirst: base.series.hc[0], teamLast: base.const.hc_2031,
    funding: Math.round(base.const.funding), ebitdaYear: base.const.year_ebitda,
  };
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

// ---------- Русская типографика: неразрывные пробелы ----------
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
const MAIN_SLIDES = 24;
// Номера слайдов питча, на которые ссылаются слайды 2, 3 и приложение A2. Сборка сверяет их с подзаголовками.
const P = { sprint: 3, task: 4, journey: 5, cost: 6, takeover: 7, now: 8, insight: 9, answer: 10, proof: 11, product: 12, site: 13, showcase: 14, quality: 15, embed: 16, ai: 17, data: 18, effect: 19, pilot: 20, scale: 21, vision: 22 };
const P_KICKER = {
  sprint: "СПРИНТ 2 · ЧТО СДЕЛАНО", task: "ЗАДАЧА ЗАКАЗЧИКА", journey: "ПУТЬ СОТРУДНИКА", cost: "ЦЕНА ОШИБКИ", takeover: "СЦЕНАРИЙ ЗАХВАТА", now: "ПОЧЕМУ СЕЙЧАС",
  insight: "КЛЮЧЕВАЯ ИДЕЯ", answer: "РЕШЕНИЕ", proof: "ДОКАЗАТЕЛЬНОСТЬ", product: "ПРОДУКТ СЕГОДНЯ", site: "ДЕМО В БРАУЗЕРЕ", showcase: "СПРИНТ 2 · ДОПУСК ПЛАТЕЖА",
  quality: "КАЧЕСТВО", embed: "В КОНТУРЕ БАНКА", ai: "ИИ ПОД КОНТРОЛЕМ", data: "ЗАЩИТА ДАННЫХ · ФОКУС СПРИНТА 2", effect: "ЭКОНОМИКА ДЛЯ ВТБ",
  pilot: "ПРЕДЛОЖЕНИЕ ВТБ", scale: "ПОСЛЕ ПИЛОТА", vision: "БАНК БУДУЩЕГО",
};
const KICKER_AT = {};
const APPX = [
  ["A1", "Семь взглядов на «Мандат»"],
  ["A2", "Критерии спринта 2"],
  ["A3", "Прослеживаемость чисел"],
  ["A4", "Условия трека"],
  ["A5", "Рынок и конкуренты"],
  [`A6`, `${RULES} с нормой`],
  ["A7", "Пять норм ручной сверки"],
  ["A8", "Доверие без доступа к базе"],
  ["A9", "Методика измерения качества"],
  ["A10", "Информационная безопасность"],
  ["A11", "Риски и как их снимаем"],
  ["A12", "Расчёт эффекта"],
  ["A13", "Реальные кейсы"],
  ["A14", "Открытые стандарты и практики 2026"],
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
  const G = await buildGraphics({ terminal: TERMINAL, qrSvg: path.join(ASSETS, "qr-github.svg"), qrSiteSvg: path.join(ASSETS, "qr-site.svg") });
  // Амбассадор и 3D-значки сделаны в Figma (docs/VISUALS.md).
  const VIS = path.join(ASSETS, "visuals");
  const ANNA = {
    cover: await photoPanel(path.join(VIS, "anna-hero.jpg"), 8.5, { position: "top" }),
    final: await photoPanel(path.join(VIS, "anna-final.jpg"), 8.75, { position: "left" }),
  };
  const ICON3D = {};
  for (const name of ["showcase", "law", "verify", "pilot", "criteria", "search"]) ICON3D[name] = await icon3d(path.join(VIS, `icon-${name}.png`));
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
  pres.author = "Команда «Мандат»";
  pres.company = "Команда «Мандат»";
  pres.title = "Мандат — единое окно полномочий для бизнес-клиентов банка";
  pres.subject = "Академия Финтеха 2026, трек ВТБ «Финтех без границ»: онбординг и проверка полномочий";
  pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
  const C = pres.SchemeColor;
  const FONT = THEME.bodyFontFace;
  // Заголовки, крупные числа и обложка — Manrope (шрифт заголовков темы), акцент обложки — курсив Cormorant Garamond.
  const DISPLAY = THEME.headFontFace;
  const SERIF = "Cormorant Garamond SemiBold";

  const L = 0.6, W = 12.133, R = L + W, TOP = 1.72, BOTTOM = 6.72, GAP = 0.25;

  // ---------- Макеты ----------
  pres.defineSlideMaster({
    title: "Обложка",
    background: { data: G.bgDark },
    objects: [
      // Название продукта — логотип над заголовком; сам заголовок обложки — что это за продукт.
      { placeholder: { options: { name: "title", type: "title", x: L, y: 2.62, w: 6.6, h: 1.1, fontSize: 24, bold: true, color: C.background1, align: "left", valign: "top", margin: 0 }, text: "" } },
    ],
  });
  pres.defineSlideMaster({
    title: "Контент",
    background: { color: HEX.lt2 },
    margin: [0.5, 0.6, 0.6, 0.6],
    objects: [
      { image: { x: R - 1.15, y: 0.335, w: 1.15, h: 0.23, data: G.lockup, altText: "Логотип «Мандата»" } },
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
      fontFace: o.font ?? ((o.bold ?? false) && (o.size ?? 14) >= 22 ? DISPLAY : undefined), hyperlink: o.link,
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
    if (section === "Питч" && kicker) KICKER_AT[kicker] = pages();
    if (kicker) s.addText(typo(kicker), { placeholder: "kicker" });
    // Последнее слово заголовка не остаётся на строке одно: два последних слова связаны неразрывным пробелом.
    if (title) s.addText(typo(title).replace(/ (?=\S+$)/, NB), { placeholder: "title" });
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
    s.addImage({ data: ANNA.cover.data, x: ANNA.cover.x, y: 0, w: ANNA.cover.w, h: ANNA.cover.h, altText: "Анна, амбассадор «Мандата», в тёмно-синем костюме", objectName: "Фото амбассадора" });
    T(s, "Академия Финтеха 2026 · трек ВТБ «Финтех без границ» · задача «Онбординг и проверка полномочий»", L, 0.55, 7.0, 0.55, { size: 12, color: TINT.ice });
    s.addImage({ data: G.lockupDark, x: L, y: 1.6, w: 3.9, h: 0.78, altText: "Логотип «Мандата»", objectName: "Логотип" });
    s.addText(typoRuns([{ text: "Единое окно полномочий", options: { breakLine: true } }, { text: "для бизнес-клиентов банка", options: { fontFace: SERIF, italic: true, bold: false, fontSize: 30, color: "8FD0FF" } }]), { placeholder: "title" });
    T(s, "Отвечает, может ли человек провести операцию от имени компании в этот день, и показывает, на чём держится ответ: поле документа, запись реестра, статья закона", L, 3.8, 6.3, 1.0, { size: 15, color: TINT.ice, lsm: 1.1 });
    let px = L;
    for (const k of ["ok", "no", "rv", "nd"]) px += pill(s, px, 5.0, k, { size: 9.5, h: 0.34 }) + 0.12;
    T(s, "Коньков Дмитрий", L, 6.12, 4.0, 0.34, { size: 14, bold: true, color: C.background1 });
    T(s, "капитан команды «Мандат»", L, 6.46, 4.0, 0.3, { size: 12, color: TINT.ice });
    T(s, "Полуфинал · спринт 2 · ноябрь 2026", 4.3, 6.46, 3.9, 0.3, { size: 12, color: TINT.ice, align: "right" });
    s.addNotes("[0:00–0:08] Здравствуйте, мы команда «Мандат». Отвечаем банку, может ли человек провести операцию за компанию в этот день, и показываем почему.");
  }

  // ---------- 2. Коротко ----------
  {
    const s = slide("Контент", "Питч", "КОРОТКО", "Вместо ручной сверки трёх источников банк получает один ответ с доказательством",
      `Банк России, обзор операций без согласия клиентов за III кв. 2026; числа о продукте — прогон ${N.asOf}, как повторить — слайд A3; окупаемость — финансовая модель в пакете аналитики`);
    const cols = [
      ["ПРОБЛЕМА", "TriangleAlert", AVG_Q3, `теряет компания на одной операции без согласия. А право подписи банк сверяет вручную, по трём источникам`, null],
      ["РЕШЕНИЕ", "Scale", "4 ответа", "на вопрос, может ли этот человек провести эту операцию компании сегодня. К каждому приложены норма и поле документа", null],
      ["КАЧЕСТВО", "BadgeCheck", `${N.falseConfirmed} из ${N.negatives}`, `ложных «да» на проверочных кейсах ${N.negativeTypes} видов, где подтверждать нельзя. ${TESTS} проходят`, null],
      ["ЗАПРОС", "Rocket", "6 недель", `теневого пилота на праве подписи платежей по МЧД. Затраты ВТБ возвращаются за ${PAYBACK}`, 1],
    ];
    const more = [[`подробнее: слайды ${P.task}–${P.now}`, P.task], [`подробнее: слайды ${P.insight}–${P.showcase}`, P.insight], [`подробнее: слайд ${P.quality} и A3`, P.quality], [`подробнее: слайды ${P.effect}–${P.pilot}`, P.effect]];
    const cw = (W - 3 * GAP) / 4, ch = 3.85;
    // 3D-значки из Figma: песочные часы с колоколом, весы, щит с ключом, календарь с флажком.
    const art = ["showcase", "law", "verify", "pilot"];
    cols.forEach(([k, ic, v, t, hl], i) => {
      const x = L + i * (cw + GAP);
      card(s, x, TOP, cw, ch, hl ? { fill: C.text2, line: null } : {});
      T(s, k, x + 0.28, TOP + 0.28, cw - 1.3, 0.3, { size: 11, bold: true, color: hl ? HEX.accent2 : C.accent1, cs: 1.2 });
      s.addImage({ data: ICON3D[art[i]], x: x + cw - 1.12, y: TOP - 0.02, w: 1.0, h: 1.0, altText: `Значок: ${k.toLowerCase()}`, objectName: `3D-значок ${ic}` });
      T(s, v, x + 0.28, TOP + 0.95, cw - 0.36, 0.8, { size: i === 0 ? 26 : 30, bold: true, color: hl ? C.background1 : C.accent1, valign: "middle" });
      T(s, t, x + 0.28, TOP + 1.85, cw - 0.56, 1.45, { size: 13, color: hl ? TINT.ice : C.text1 });
      const [label, target] = more[i];
      s.addShape(pres.shapes.LINE, { x: x + 0.28, y: TOP + ch - 0.52, w: cw - 0.56, h: 0, line: { color: hl ? "2E4F9E" : TINT.line, width: 0.75 }, objectName: "Разделитель" });
      T(s, `${label} →`, x + 0.28, TOP + ch - 0.46, cw - 0.56, 0.32, { size: 11, bold: true, color: hl ? HEX.accent2 : C.accent1, valign: "middle" });
      link(s, x, TOP + ch - 0.5, cw, 0.42, target, label);
    });
    band(s, L, TOP + ch + GAP, W, BOTTOM - (TOP + ch + GAP), "Если реестр молчит или документа нет, ответ — «недостаточно данных». Неизвестное в «да» не превращается.", { icon: "ShieldCheck", size: 15, bold: true });
    s.addNotes(`[0:08–0:26] Коротко. Право подписи банк сверяет вручную по трём источникам, а одна ошибка стоит компании около 750 тысяч рублей. «Мандат» отвечает на один вопрос четырьмя вердиктами и к каждому прикладывает норму. На ${N.negatives} ловушках ноль ложных «да». Просим шесть недель теневого пилота.`);
  }

  // ---------- 3. Спринт 2: что сделано, по шести критериям ----------
  {
    const s = slide("Контент", "Питч", "СПРИНТ 2 · ЧТО СДЕЛАНО", "За спринт 2 довели MVP до платежа, посчитали финансовую модель и защитили данные",
      `Критерии и баллы — fintechacademy.ru/task-1; подробный ответ на каждый критерий — слайд A2; числа о продукте — прогон ${N.asOf}`);
    const items = [
      ["Lock", "Данные защищены", "Чек-лист воркшопа 20.11 выполнен в коде и закрыт тестами: удалённое не возвращается из резервной копии, отчёт об инциденте готов за 24 и 72 часа. Отчёты зашифрованы", "Данные и комплаенс · 18 баллов"],
      ["Plug", "MVP доведён до платежа", `Интернет-банк спрашивает «Мандат» перед каждым платежом, в том числе по открытому стандарту OpenID AuthZEN 1.0. При признаках захвата платёж получает отказ до списания — слайд ${P.showcase}`, "Техническая реализация · 22 балла"],
      ["Coins", "Цена одной проверки", `Вручную ≈${UNIT.manual} ₽, затраты банка на «Мандат» ≈${UNIT.mandat} ₽, в ${UNIT.ratio} раза меньше. Разовые затраты ВТБ окупаются за ${PAYBACK}`, "Оценка ценности · 22 балла"],
      ["BadgeCheck", "Прогресс за спринт", `Допуск платежа, подпись юриста под правилом, шифрование хранилища, сайт с демо. ${TESTS} на Linux и Windows, покрытие ${COVER}. Уязвимости искали сами и закрыли найденное`, "Зрелость решения · 16 баллов"],
      ["Landmark", "Проблема в цифрах", `${ruNum(CBR.m9.ops)} операций без согласия у юрлиц за девять месяцев 2026 года (Банк России). «ЭОС» и дело ВС РФ: ошибка дорога в обе стороны. Пять интервью с банками это подтвердили`, "Подтверждённость проблемы · 12 баллов"],
      ["ScrollText", "Готово к пилоту", `Матрица готовности: ${READY.ok} из ${READY.all} пунктов проверены тестами, ${READY.bank} — за банком с владельцем и условием. Карточка нормы на каждое правило, план теневого пилота`, "Реалистичность пилота · 10 баллов"],
    ];
    const cw = (W - 2 * GAP) / 3, ch = (BOTTOM - TOP - GAP) / 2;
    items.forEach(([ic, head, text, crit], i) => {
      const x = L + (i % 3) * (cw + GAP), y = TOP + Math.floor(i / 3) * (ch + GAP);
      card(s, x, y, cw, ch);
      tile(s, x + 0.22, y + 0.2, 0.46, ic);
      T(s, head, x + 0.82, y + 0.18, cw - 1.02, 0.5, { size: 14, bold: true, color: C.text2, valign: "middle" });
      T(s, text, x + 0.22, y + 0.8, cw - 0.44, ch - 1.32, { size: 11.5, color: C.text1 });
      s.addShape(pres.shapes.LINE, { x: x + 0.22, y: y + ch - 0.46, w: cw - 0.44, h: 0, line: { color: TINT.line, width: 0.75 }, objectName: "Разделитель" });
      T(s, `Критерий: ${crit}`, x + 0.22, y + ch - 0.42, cw - 0.44, 0.32, { size: 10.5, bold: true, color: C.accent1, valign: "middle" });
    });
    s.addNotes(`[0:26–0:40] Что сделано за спринт, по критериям оценки. Чек-лист воркшопа о данных выполнен в коде и закрыт тестами. Интернет-банк спрашивает допуск перед каждым платежом, в том числе по открытому стандарту. Проверка обходится банку примерно в ${UNIT.mandat} ${plural(UNIT.mandat, "рубль", "рубля", "рублей")} вместо ${UNIT.manual}.`);
  }

  // ---------- 4. Задача заказчика: слова трека и ответ «Мандата» ----------
  {
    const s = slide("Контент", "Питч", "ЗАДАЧА ЗАКАЗЧИКА", "Трек просит быстрый онбординг и проверку полномочий через единое окно. Эту задачу мы и решаем",
      "Цитаты — условия трека ВТБ «Финтех без границ», направление «Решения для бизнеса и корпоративных клиентов» (fintechacademy.ru/task-1)");
    const lw = 3.75, lh = 4.0;
    card(s, L, TOP, lw, lh, { fill: C.text2, line: null });
    T(s, "ИЗ УСЛОВИЙ ТРЕКА", L + 0.3, TOP + 0.26, lw - 0.6, 0.26, { size: 10.5, bold: true, color: HEX.accent2, cs: 1.2 });
    T(s, "«Быстрый онбординг и проверка полномочий — верификация доверенностей, сведения из государственных и коммерческих реестров через единое окно»", L + 0.3, TOP + 0.62, lw - 0.6, 1.85, { size: 13.5, bold: true, color: C.background1, lsm: 1.05 });
    s.addShape(pres.shapes.LINE, { x: L + 0.3, y: TOP + 2.58, w: lw - 0.6, h: 0, line: { color: "2E4F9E", width: 0.75 }, objectName: "Разделитель" });
    T(s, "«Важна не технология сама по себе, а понятная проблема, сильный пользовательский сценарий и возможность довести решение до пилота в банковской среде»", L + 0.3, TOP + 2.7, lw - 0.6, 1.2, { size: 11.5, color: TINT.ice, lsm: 1.05 });
    const rx = L + lw + GAP, rw = R - rx;
    const c1 = 1.85, c2 = 2.75, ax = 0.42, c3 = rw - 0.5 - c1 - c2 - ax;
    const rh = 0.84, rg = 0.1, ry0 = TOP + 0.34;
    [["Слова трека", rx + 0.25, c1], ["Что за ними стоит", rx + 0.25 + c1, c2], ["Что делает «Мандат»", rx + 0.25 + c1 + c2 + ax, c3]]
      .forEach(([t, x, w]) => T(s, t, x, TOP, w, 0.28, { size: 11, bold: true, color: TINT.text2, valign: "middle" }));
    const rows = [
      ["«Быстрый онбординг»", "Представитель ждёт, пока сотрудник вручную сведёт доверенность, выписку и устав", "Ответ по однозначному пакету приходит сразу. Спорное получает человек, с готовым досье и сроком"],
      ["«Верификация доверенностей»", "Статуса в реестре мало: важны подписант, срок, передоверие и покрытие самой операции", `${RULES}, у каждого норма закона и поле документа`],
      ["«Реестры через единое окно»", "Доверенность, реестр ФНС, ЕГРЮЛ, сведения о банкротстве и устав лежат в разных системах", "Одна проверка и одна карточка. Источники банка подключаются через готовые интерфейсы"],
      ["«Довести до пилота»", "Данные клиентов остаются в банке, решение остаётся за человеком", "Работает в контуре банка без внешней сети. Шесть недель теневого пилота на одной операции"],
    ];
    rows.forEach(([words, behind, answer], i) => {
      const y = ry0 + i * (rh + rg);
      card(s, rx, y, rw, rh);
      T(s, words, rx + 0.25, y, c1 - 0.15, rh, { size: 12.5, bold: true, color: C.accent1, valign: "middle" });
      T(s, behind, rx + 0.25 + c1, y, c2 - 0.2, rh, { size: 11, color: TINT.text2, valign: "middle" });
      arrow(s, rx + 0.25 + c1 + c2 - 0.06, y + rh / 2, ax - 0.12, HEX.accent1, 1.5);
      T(s, answer, rx + 0.25 + c1 + c2 + ax, y, c3, rh, { size: 11.5, color: C.text1, valign: "middle" });
    });
    band(s, L, TOP + lh + GAP, W, BOTTOM - (TOP + lh + GAP), "Клиент быстрее получает ответ и видит, чего не хватает. Сотрудник работает в одном окне. Банк хранит основание каждого решения", { icon: "Users", size: 14, bold: true });
    s.addNotes("[0:40–0:52] Трек просит быстрый онбординг и проверку полномочий через единое окно. Мы отвечаем на каждое слово: ответ сразу, проверка всей цепочки, все источники в одной карточке и готовность к пилоту в контуре банка.");
  }

  // ---------- 5. Путь сотрудника и клиента ----------
  {
    const s = slide("Контент", "Питч", "ПУТЬ СОТРУДНИКА", "Сегодня сотрудник сводит три источника вручную, с «Мандатом» получает один ответ с доказательством",
      "Шаги «сегодня» — по открытым описаниям процесса и пяти интервью с банками. Минуты на один пакет измерит хронометраж в первые недели пилота");
    const lanes = [
      ["Сегодня, вручную", "FileSearch", "E8ECF3", TINT.text2, [
        "клиент приносит доверенность (МЧД)", "сотрудник проверяет статус в реестре", "запрашивает выписку ЕГРЮЛ", "читает устав, при сомнении идёт к юристу", "решение без записи оснований",
      ], "между обращениями смену директора никто не видит"],
      ["С «Мандатом»", "ShieldCheck", TINT.blue, HEX.dk2, [
        "клиент подаёт те же документы", "«Мандат» сводит их на дату операции за доли секунды", "ответ с нормой закона и полем документа", "спорное уходит сотруднику с досье и сроком", "решение в журнале, удостоверение можно отозвать",
      ], "после выдачи прав «Мандат» следит за ЕГРЮЛ, реестром и сроками доверенностей и предупреждает банк"],
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
    s.addNotes("[0:52–1:04] Сегодня сотрудник сам открывает доверенность, реестр, выписку и устав и решает без записи оснований. С «Мандатом» он получает ответ с нормой и полем документа, а спорное уходит в очередь разбора.");
  }

  // ---------- 6. Цена ошибки ----------
  {
    const s = slide("Контент", "Питч", "ЦЕНА ОШИБКИ", `Одна операция без согласия стоит компании ${AVG_Q3}, в ${CBR.q3.ratio} раз больше средней по всем клиентам`,
      "Банк России, обзоры отчётности об инцидентах информационной безопасности при переводе денежных средств: I кв. (14.05.2026), II кв. (04.08.2026) и III кв. 2026 (19.11.2026, данные предварительные), таблицы по юрлицам");
    card(s, L, TOP, 4.75, 3.78);
    T(s, "III квартал 2026 · операции без согласия клиента", L + 0.3, TOP + 0.22, 4.2, 0.3, { size: 12, bold: true, color: TINT.text2 });
    T(s, AVG_Q3, L + 0.3, TOP + 0.55, 4.2, 0.85, { size: 46, bold: true, color: C.accent1, valign: "middle" });
    T(s, `средняя операция против юрлица: ${CBR.q3.ops} ${plural(CBR.q3.ops, "операция", "операции", "операций")} на ${Math.round(CBR.q3.lossMln)} млн ₽`, L + 0.3, TOP + 1.42, 4.2, 0.5, { size: 12, color: TINT.text2 });
    const bx = L + 0.3, bw = 4.15;
    T(s, `юрлица · ${AVG_Q3}`, bx, TOP + 2.02, bw, 0.26, { size: 11, bold: true, color: C.text2 });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: bx, y: TOP + 2.3, w: bw, h: 0.24, rectRadius: 0.12, fill: { color: HEX.accent1 }, line: { type: "none" }, objectName: "Столбик: юрлица" });
    T(s, `все клиенты · ≈${Math.round(CBR.q3.allAvgK)} тыс. ₽ — в ${CBR.q3.ratio} раз меньше`, bx, TOP + 2.66, bw, 0.26, { size: 11, bold: true, color: C.text2 });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: bx, y: TOP + 2.94, w: 0.1, h: 0.24, rectRadius: 0.05, fill: { color: TINT.blueLine }, line: { type: "none" }, objectName: "Столбик: все клиенты" });
    T(s, `${comma(CBR.q3.allOps / 1000, 1)} тыс. операций на ${comma(CBR.q3.allLossMln / 1000, 2)} млрд ₽`, bx, TOP + 3.3, bw, 0.28, { size: 11, color: TINT.muted });
    const bX = L + 4.75 + GAP, bW = 3.85;
    card(s, bX, TOP, bW, 3.78);
    T(s, "Похищено у юрлиц в 2026 году, млн ₽", bX + 0.3, TOP + 0.22, bW - 0.6, 0.3, { size: 12, bold: true, color: TINT.text2 });
    s.addChart(pres.charts.BAR, [{ name: "Похищено у юрлиц, млн ₽", labels: ["I кв.", "II кв.", "III кв."], values: [CBR.q1.lossMln, Math.round(CBR.q2.lossMln), Math.round(CBR.q3.lossMln)] }], {
      ...chartBase(), x: bX + 0.2, y: TOP + 0.5, w: bW - 0.4, h: 1.95, barDir: "col", barGapWidthPct: 55,
      chartColors: ["9DB8E8", "9DB8E8", HEX.accent1], dataLabelFontSize: 14, dataLabelFormatCode: "#,##0", catAxisLabelFontSize: 11,
      valAxisMaxVal: 1000, objectName: "Диаграмма: похищено у юрлиц по кварталам 2026 года",
    });
    T(s, "за девять месяцев", bX + 0.3, TOP + 2.5, bW - 0.6, 0.26, { size: 11, color: TINT.text2 });
    T(s, `≈${comma(CBR.m9.lossMln / 1000, 1)} млрд ₽`, bX + 0.3, TOP + 2.76, bW - 0.6, 0.46, { size: 22, bold: true, color: C.text2, valign: "middle" });
    T(s, `${ruNum(CBR.m9.ops)} операций; в III кв. ${String(CBR.q3.change).replace("-", "−")}% к II кв., но средняя операция всё ещё ${AVG_Q3}`, bX + 0.3, TOP + 3.26, bW - 0.6, 0.4, { size: 11, color: TINT.muted });
    const cX = bX + bW + GAP, cW = R - cX;
    card(s, cX, TOP, cW, 1.78);
    T(s, `${comma(CBR.q3.refundAccounts, 1)}% и ${comma(CBR.q3.refundSbp, 0)}%`, cX + 0.3, TOP + 0.2, cW - 0.6, 0.6, { size: 26, bold: true, color: HEX.accent5, valign: "middle" });
    T(s, "столько возмещено компаниям в III кв. по счетам и через СБП — почти 90% ущерба остаётся у бизнеса", cX + 0.3, TOP + 0.85, cW - 0.6, 0.8, { size: 12, color: TINT.text2 });
    card(s, cX, TOP + 2.0, cW, 1.78, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
    tile(s, cX + 0.3, TOP + 2.2, 0.46, "SearchCheck", { fill: "FFFFFF" });
    T(s, "Где здесь полномочия", cX + 0.88, TOP + 2.2, cW - 1.1, 0.46, { size: 13, bold: true, color: C.text2, valign: "middle" });
    T(s, `В модели от ${Math.round(100 * EFFECT.authorityShare[0])} до ${Math.round(100 * EFFECT.authorityShare[2])}% таких операций связаны с подменой представителя. Точную долю измерит пилот`, cX + 0.3, TOP + 2.76, cW - 0.6, 0.96, { size: 11.5, color: C.text1 });
    band(s, L, 5.72, W, 0.95, "Право подписи складывается из машиночитаемой доверенности (МЧД), выписки ЕГРЮЛ и устава и меняется со временем. Сегодня эти три документа сотрудник банка сверяет вручную.", { icon: "Layers", size: 15 });
    const lossQ3 = Math.round(CBR.q3.lossMln);
    s.addNotes(`[1:04–1:18] Теперь цена ошибки. В третьем квартале у компаний ${CBR.q3.ops} ${plural(CBR.q3.ops, "операция", "операции", "операций")} без согласия на ${lossQ3} ${plural(lossQ3, "миллион", "миллиона", "миллионов")} рублей. Это около 750 тысяч на операцию, в ${CBR.q3.ratio} раз больше средней. Возвращают компаниям около десятой части.`);
  }

  // ---------- 7. Сценарий захвата ----------
  {
    const s = slide("Контент", "Питч", "СЦЕНАРИЙ ЗАХВАТА", "Захвату хватает пяти рабочих дней, а сверка «на сегодня» не видит событий между обращениями",
      "129-ФЗ, ст. 8 (срок регистрации); ГК РФ, ст. 188; разъяснения ФНС; Forbes и АБН по данным РБК, 22.10.2026 — случай «ЭОС» по версии компании; ВС РФ, Определение СКЭС от 02.05.2023 № 308-ЭС22-17199");
    const steps = [
      ["День 0", "Заявление в ФНС о смене директора по поддельным документам"],
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
    T(s, "Смена директора не прекращает МЧД — п. 1 ст. 188 ГК РФ, позиция ФНС", tx + 0.95, y2 + 0.52, tw - 1.2, 0.44, { size: 12, bold: true, color: C.accent1 });
    dot(s, tx + 0.36, y2 + 1.14, 0.38, HEX.accent5, "X");
    T(s, "Отказ законному руководителю — нарушение банка: суд обязал провести платёж на 6,5 млн ₽, ВС РФ оставил это в силе (№ 308-ЭС22-17199)", tx + 0.95, y2 + 1.0, tw - 1.25, 0.66, { size: 12, valign: "middle" });
    dot(s, tx + 0.36, y2 + 1.8, 0.38, HEX.accent4, "TriangleAlert");
    T(s, "Пропустить свежую смену — открыть дверь захвату", tx + 0.95, y2 + 1.72, tw - 1.25, 0.54, { size: 12, valign: "middle" });
    band(s, L, 5.9, W, 0.8, "Нужен ответ на дату операции — с историей событий ЕГРЮЛ и реестра МЧД, а не снимок «на сегодня»", { icon: "History", size: 15, bold: true });
    s.addNotes("[1:18–1:32] Захват выглядит так. Заявление о смене директора, через пять рабочих дней он в реестре, дальше новая доверенность и платёж. В октябре у агентства «ЭОС» десять дней значился чужой директор. А откажете законному руководителю — суд обяжет провести платёж.");
  }

  // ---------- 8. Почему сейчас: Россия и мир ----------
  {
    const s = slide("Контент", "Питч", "ПОЧЕМУ СЕЙЧАС", "Доверенностей стало втрое больше, правила меняются, а в мире такая проверка уже отдельный продукт",
      "63-ФЗ в ред. 476-ФЗ; приказ Банка России № ОД-2506; приказ Минцифры № 1001; ФНС, 27.07.2026; emchd.ru, 10.2024; 315-ФЗ; ВТБ, пресс-релиз к МСФО за 1П 2026; FATF, Рекомендация 10; Регламент (ЕС) 2024/1624 и проект стандартов AMLA от 30.09.2026; Signicat — сервисы InstantAuthority и Data Verification");
    const hy = TOP, gy = TOP + 0.42, bandH = 0.72, hh = BOTTOM - bandH - 0.2 - gy;
    const lw = 5.5, mw = 3.0, mx = L + lw + GAP, wx = mx + mw + GAP, ww = R - wx;
    T(s, "Россия: правила меняются каждые полгода", L, hy, lw, 0.32, { size: 13, bold: true, color: TINT.text2 });
    T(s, "Поток растёт", mx, hy, mw, 0.32, { size: 13, bold: true, color: TINT.text2 });
    T(s, "Мир: уже отдельный продукт", wx, hy, ww, 0.32, { size: 13, bold: true, color: TINT.text2 });
    card(s, L, gy, lw, hh);
    const ev8 = [
      ["01.09.2024", "МЧД обязательна для представителей юрлиц", 0],
      ["01.01.2026", "12 признаков операций без согласия вместо 6", 0],
      ["01.02.2026", "новые реквизиты МЧД: СНИЛС и ИНН представителя", 0],
      ["01.03.2026", "реестр ФНС ищет МЧД по номеру и ИНН сторон", 0],
      ["27.07.2026", "в реестре ФНС больше 5 млн МЧД", 0],
      ["01.09.2027", "единая форма МЧД по 315-ФЗ", 1],
    ];
    const lx = L + 1.55, step = (hh - 0.4) / ev8.length, y0 = gy + 0.2 + (step - 0.28) / 2;
    vline(s, lx + 0.07, y0 + 0.14, step * (ev8.length - 2), TINT.blueLine, 2);
    vline(s, lx + 0.07, y0 + 0.14 + step * (ev8.length - 2), step, TINT.blueLine, 2, "dash");
    ev8.forEach(([d, t, fut], i) => {
      const y = y0 + i * step;
      T(s, d, L + 0.2, y, 1.22, 0.28, { size: 12, bold: true, color: fut ? HEX.dk2 : C.accent1, align: "right", valign: "middle" });
      s.addShape(pres.shapes.OVAL, { x: lx, y: y + 0.07, w: 0.14, h: 0.14, fill: { color: fut ? "FFFFFF" : HEX.accent1 }, line: { color: fut ? HEX.dk2 : HEX.accent1, width: 1.5 }, objectName: "Точка шкалы" });
      T(s, fut ? [{ text: t, options: { bold: true } }, { text: " · впереди", options: { color: HEX.accent1 } }] : t, lx + 0.34, y - 0.09, lw - (lx - L) - 0.54, 0.46, { size: 12, color: C.text1, valign: "middle" });
    });
    // Поток: ×3 за два года (два столбца в масштабе) и клиенты ВТБ.
    const sh2 = (hh - 0.2) / 2;
    card(s, mx, gy, mw, sh2);
    T(s, "×3", mx + 0.25, gy + 0.12, 1.2, 0.6, { size: 32, bold: true, color: C.accent1, valign: "middle" });
    T(s, "МЧД в реестре ФНС за два года", mx + 0.25, gy + 0.76, 1.25, sh2 - 0.86, { size: 11, color: TINT.text2 });
    const bars = [["10.2024", "≈1,6 млн", 1.6], ["07.2026", "более 5 млн", 5]];
    const base = gy + sh2 - 0.34, hmax = sh2 - 0.76, bwid = 0.52;
    bars.forEach(([d, lab, val], j) => {
      const bh = (hmax * val) / 5, bx = mx + 1.55 + j * 0.72;
      s.addShape(pres.shapes.RECTANGLE, { x: bx, y: base - bh, w: bwid, h: bh, fill: { color: j ? HEX.accent1 : TINT.blueLine }, line: { type: "none" }, objectName: `МЧД в реестре, ${d}` });
      T(s, lab, bx - 0.3, base - bh - 0.26, bwid + 0.6, 0.24, { size: 9.5, bold: true, color: HEX.dk2, align: "center", valign: "bottom" });
      T(s, d, bx - 0.2, base + 0.04, bwid + 0.4, 0.22, { size: 9.5, color: TINT.muted, align: "center" });
    });
    card(s, mx, gy + sh2 + 0.2, mw, sh2);
    T(s, "1,8 млн", mx + 0.25, gy + sh2 + 0.32, mw - 0.5, 0.6, { size: 32, bold: true, color: C.accent1, valign: "middle" });
    T(s, "компаний и предпринимателей у ВТБ. Подписанты у них меняются", mx + 0.25, gy + sh2 + 0.96, mw - 0.5, sh2 - 1.04, { size: 11, color: TINT.text2 });
    // Мир: требование к банкам и готовый продукт.
    const world = [
      ["Globe", "FATF · Рекомендация 10", "банк проверяет, что лицо, действующее от имени клиента, уполномочено"],
      ["Globe", "ЕС · Регламент 2024/1624", "с 10.07.2027 банк собирает сведения о любом представителе клиента"],
      ["Building2", "Нидерланды и Швеция", "Signicat отвечает по реестрам, кто и в каком сочетании подписывает за компанию"],
    ];
    const wg = 0.14, wh = (hh - 2 * wg) / 3;
    world.forEach(([ic, head, t], i) => {
      const y = gy + i * (wh + wg);
      card(s, wx, y, ww, wh);
      tile(s, wx + 0.18, y + 0.12, 0.36, ic);
      T(s, head, wx + 0.66, y + 0.12, ww - 0.8, 0.36, { size: 12, bold: true, color: C.text2, valign: "middle" });
      T(s, t, wx + 0.18, y + 0.53, ww - 0.36, wh - 0.56, { size: 10.5, color: C.text1 });
    });
    band(s, L, BOTTOM - bandH, W, bandH, "Поэтому правила «Мандата» лежат данными: новую норму добавляют строка и тест, ядро при этом не меняется", { icon: "ScrollText", size: 13.5, bold: true });
    s.addNotes("[1:32–1:44] Почему сейчас. Доверенностей за два года стало втрое больше, правила меняются каждые полгода, поэтому у нас они лежат данными. FATF и Евросоюз требуют ту же проверку, в Нидерландах и Швеции это отдельный сервис.");
  }

  // ---------- 9. Ось времени ----------
  {
    const s = slide("Контент", "Питч", "КЛЮЧЕВАЯ ИДЕЯ", "Одна и та же доверенность в разные дни даёт разные ответы, поэтому мы проверяем на дату операции",
      "Демо-кейс 14: компания, люди и документы вымышлены, статус реестра и заключение об уставе заданы в кейсе. Ответ на каждый день посчитал «Мандат». Окно «свежей» смены руководителя — 30 дней, его утверждает юрист банка");
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
    const labels = ["Директор по ЕГРЮЛ", "Доверенность (МЧД)", "Реестр МЧД ФНС", "Смена руководителя", "Устав"];
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
    bar(2, C14.issued, "2027-05-01", "E2F4EB", "статус: действует", ST.ok.text);
    const tISO = C14.op.date;
    const winFrom = new Date(Date.parse(tISO) - 30 * 86400000).toISOString().slice(0, 10);
    s.addShape(pres.shapes.RECTANGLE, { x: X(winFrom), y: laneY(3) + 0.04, w: X(tISO) - X(winFrom), h: laneH - 0.08, fill: { color: "FFF1D4" }, line: { color: HEX.accent4, width: 1, dashType: "dash" }, objectName: "Окно свежей смены" });
    T(s, "← окно «свежей» смены: 30 дней до операции", X(tISO) + 0.12, laneY(3), rx - X(tISO) - 0.15, laneH, { size: 10, bold: true, color: ST.rv.text, valign: "middle" });
    const ex = X(C14.next.valid_from);
    s.addShape(pres.shapes.DIAMOND, { x: ex - 0.14, y: laneY(3) + laneH / 2 - 0.14, w: 0.28, h: 0.28, fill: { color: HEX.accent4 }, line: { color: "FFFFFF", width: 1.5 }, objectName: "Событие: смена руководителя" });
    T(s, `${fmt(C14.next.valid_from)}: МЧД не прекращается (п. 1 ст. 188 ГК РФ)`, ex + 0.22, laneY(3), X(winFrom) - ex - 0.35, laneH, { size: 10, bold: true, color: ST.rv.text, valign: "middle" });
    bar(4, "2025-12-01", "2027-05-01", "EEF1F6", "ограничений для этой операции нет", TINT.text2);
    const tx = X(tISO);
    vline(s, tx, laneTop - 0.12, lanesBottom - laneTop + 0.2, HEX.dk2, 2, "dash");
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: tx - 1.5, y: lanesBottom + 0.12, w: 3.0, h: 0.34, rectRadius: 0.17, fill: { color: HEX.dk2 }, line: { type: "none" }, objectName: "Дата операции" });
    T(s, `операция ${fmt(tISO)} · ${C14.op.amount_rub.toLocaleString("ru-RU")} ₽`, tx - 1.5, lanesBottom + 0.12, 3.0, 0.34, { size: 10.5, bold: true, color: "FFFFFF", align: "center", valign: "middle" });
    // Полоса ответов ядра на каждый день: те же даты, что у шкалы на главной странице сайта.
    const y2 = TOP + ch + GAP, h2 = BOTTOM - y2;
    card(s, L, y2, W, h2, { shadow: false });
    T(s, "Ответ «Мандата» на каждый день", L + 0.25, y2, 2.15, h2, { size: 12, bold: true, color: C.text2, valign: "middle" });
    const KIND = { confirmed: "ok", refuted: "no", needs_review: "rv", insufficient_data: "nd" };
    for (const seg of SCRUB.segments) {
      const x0 = Math.max(lx, X(seg.from)), x1 = Math.min(rx, X(seg.to) + (rx - lx) / 456);
      if (x1 - x0 < 0.04) continue;
      const st = ST[KIND[seg.verdict]];
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x0 + 0.01, y: y2 + 0.16, w: x1 - x0 - 0.02, h: h2 - 0.32, rectRadius: 0.06, fill: { color: st.tint }, line: { color: st.base, width: 1 }, objectName: `Ответ: ${st.label}` });
      // На узком отрезке — короткое слово: ответ не читается одним цветом.
      const full = st.label.toLowerCase().replace("недостаточно данных", "нет данных");
      const short = { ok: "да", no: "нет", rv: "разбор", nd: "нет данных" }[KIND[seg.verdict]];
      const width = x1 - x0;
      if (width > 0.4) T(s, width > 0.95 ? full : short, x0 + 0.03, y2 + 0.16, width - 0.06, h2 - 0.32, { size: width > 0.95 ? 10.5 : 9, bold: true, color: st.text, align: "center", valign: "middle" });
    }
    s.addNotes("[1:44–1:58] Главная идея: полномочие живёт на дату. Это кейс 14. Та же доверенность до выдачи — «опровергнуто», после — «подтверждено», в месяц после смены директора — «на разбор». Выписка устарела — не хватает данных. Срок кончился — снова «нет». Всё это посчитал сам продукт.");
  }

  // ---------- 10. Решение ----------
  {
    const s = slide("Контент", "Питч", "РЕШЕНИЕ", "Один вопрос и четыре ответа. Неизвестное в «да» не превращается",
      "«Мандат» проверяет полномочия, а не личность: идентификацию по 115-ФЗ проводят существующие каналы банка (КЭП, ЕБС, офис)");
    const qh = 0.92;
    card(s, L, TOP, W, qh, { fill: C.text2, line: null });
    const q = (t, hl) => ({ text: t, options: hl ? { color: HEX.accent2, bold: true } : {} });
    s.addText(typoRuns([q("Может ли "), q("этот человек", 1), q(" провести "), q("эту операцию", 1), q(" от имени "), q("этой компании", 1), q(" "), q("сегодня", 1), q("?")]),
      { x: L, y: TOP + 0.08, w: W, h: 0.5, isTextBox: true, margin: 0, fontSize: 19.5, bold: true, color: C.background1, align: "center", valign: "middle", objectName: "Вопрос" });
    // Тот же вопрос на демо-кейсе 14: значения берутся из case.json
    const short = (n) => n.replace(/^(\S+) (\S)\S* (\S)\S*$/, "$1 $2. $3.");
    T(s, [
      q("демо-кейс 14: "), q(short(C14.who), 1), q(" · "), q(`платёж на ${C14.op.amount_rub.toLocaleString("ru-RU")} ₽`, 1), q(" · "),
      q(C14.org, 1), q(" · "), q(ruDate(C14.op.date), 1),
    ], L, TOP + 0.57, W, 0.26, { size: 11.5, color: TINT.ice, align: "center", valign: "middle", name: "Пример: демо-кейс 14" });
    const items = [
      ["ok", "Все обязательные проверки пройдены", "роль для представителя и подписанное удостоверение"],
      ["no", "Полномочия нет", "слабое звено цепочки, норму закона и что запросить у клиента"],
      ["rv", "Спорный случай решает человек", "досье для сотрудника, приоритет и срок разбора"],
      ["nd", "Источник не ответил или нет документа", "список того, чего не хватает, вместо «да»"],
    ];
    const cw = (W - GAP * 3) / 4, cy = TOP + qh + 0.3, ch = 2.58;
    // Один вопрос расходится на четыре ответа
    const forkY = TOP + qh + 0.14;
    vline(s, L + W / 2, TOP + qh, forkY - TOP - qh, TINT.blueLine, 1.5);
    s.addShape(pres.shapes.LINE, { x: L + cw / 2, y: forkY, w: 3 * (cw + GAP), h: 0, line: { color: TINT.blueLine, width: 1.5 }, objectName: "Разветвление" });
    items.forEach((_, i) => vline(s, L + i * (cw + GAP) + cw / 2, forkY, cy - forkY, TINT.blueLine, 1.5));
    items.forEach(([k, head, gets], i) => {
      const x = L + i * (cw + GAP);
      card(s, x, cy, cw, ch);
      dot(s, x + 0.25, cy + 0.2, 0.46, ST[k].base, ST[k].icon);
      pill(s, x + 0.25, cy + 0.76, k, { size: 9.5, h: 0.3 });
      T(s, head, x + 0.25, cy + 1.12, cw - 0.5, 0.52, { size: 14, bold: true, color: C.text2 });
      T(s, "Банк получает", x + 0.25, cy + 1.66, cw - 0.5, 0.22, { size: 10.5, bold: true, color: TINT.muted });
      T(s, gets, x + 0.25, cy + 1.88, cw - 0.5, 0.64, { size: 11.5, color: C.text1 });
    });
    const steps = [
      ["01", "Принять", "доверенность, выписку ЕГРЮЛ, устав"],
      ["02", "Проверить", `цепочку полномочий на дату: ${RULES} с нормой`],
      ["03", "Ответить", "вердикт, доказательства, следующий шаг"],
      ["04", "Следить", "смена директора, отмена и срок доверенности"],
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
    s.addNotes("[1:58–2:10] Ответов четыре. Подтверждено — с ролью и удостоверением. Опровергнуто — с нормой. На разбор — человеку, с досье и сроком. Не хватает данных — список того, чего нет. Неизвестное в «да» не превращается.");
  }

  // ---------- 11. Доказательность ----------
  {
    const s = slide("Контент", "Питч", "ДОКАЗАТЕЛЬНОСТЬ", "К каждому ответу приложено, на чём он держится",
      "Демо-кейсы 14, 05 и 11: данные вымышлены, заключение об уставе в кейсе 14 задано заранее. Отчёт называет источник каждого факта. Реестр доверенностей, ЕГРЮЛ и проверку электронной подписи банк подключает в пилоте");
    const cw = 7.45, ch = BOTTOM - TOP;
    card(s, L, TOP, cw, ch);
    T(s, "Демо-кейс 14 · карточка ответа", L + 0.3, TOP + 0.25, 4.8, 0.28, { size: 11, bold: true, color: TINT.muted });
    pill(s, L + cw - 0.3 - pillW("ПОДТВЕРЖДЕНО"), TOP + 0.22, "ok");
    T(s, "Платёжное поручение на 480 000 ₽ · 20.10.2026", L + 0.3, TOP + 0.62, cw - 0.6, 0.34, { size: 16, bold: true, color: C.text2 });
    T(s, "Представитель Лебедева И. П. по МЧД от директора Петрова П. П.", L + 0.3, TOP + 0.98, cw - 0.6, 0.3, { size: 12, color: TINT.text2 });
    const rows = [
      ["ok", "Check", "МЧД подписал директор по ЕГРЮЛ на дату выдачи", `ЕГРЮЛ · ГРН ${C14.prev.grn}`],
      ["ok", "Check", "МЧД действует в реестре, дата операции в сроке", "поле МЧД · СрокДейст"],
      ["ok", "Check", "Полномочие покрывает подписание платёжных поручений", "поле МЧД · МашПолн[1]"],
      ["rv", "Flag", "Директор сменился 01.05.2026, МЧД не отменена: это сигнал, отказа нет", "п. 1 ст. 188 ГК РФ"],
    ];
    rows.forEach(([k, ic, t, ref], i) => {
      const y = TOP + 1.48 + i * 0.7;
      s.addShape(pres.shapes.LINE, { x: L + 0.3, y: y - 0.08, w: cw - 0.6, h: 0, line: { color: TINT.line, width: 0.75 }, objectName: "Разделитель" });
      dot(s, L + 0.3, y + 0.08, 0.4, ST[k].base, ic);
      T(s, t, L + 0.88, y, 3.85, 0.56, { size: 13, color: C.text1, valign: "middle" });
      card(s, L + 4.82, y + 0.1, cw - 5.12, 0.38, { fill: TINT.blue, line: null, shadow: false, r: 0.08, name: "Ссылка на доказательство" });
      T(s, ref, L + 4.92, y + 0.1, cw - 5.3, 0.38, { size: 10, bold: true, color: C.accent1, valign: "middle" });
    });
    T(s, "Одинаковые документы всегда дают одинаковый ответ: в отчёте записан отпечаток правил и входных данных. Каждое решение пишется в журнал.", L + 0.3, TOP + 4.32, cw - 0.6, 0.55, { size: 11.5, color: TINT.muted });
    const rx = L + cw + 0.28, rw = R - rx;
    T(s, "Та же компания — другие факты", rx, TOP, rw, 0.32, { size: 14, bold: true, color: C.text2 });
    const ah = 2.2, a1 = TOP + 0.45, a2 = a1 + ah + 0.12, x0 = rx + 0.28, x1 = R - 0.28;
    // Свежая смена: шкала 45 дней до операции, окно 30 дней (ADR-002), смена за 19 дней до t
    card(s, rx, a1, rw, ah);
    T(s, "Директор сменился 19 дней назад", x0, a1 + 0.2, x1 - x0, 0.32, { size: 14, bold: true, color: C.text2, valign: "middle" });
    pill(s, x0, a1 + 0.6, "rv", { label: "НА РАЗБОР · СРОК 4 ЧАСА", size: 9.5 });
    const dpp = (x1 - x0) / 45, wx = x1 - 30 * dpp, dx = x1 - 19 * dpp, ty = a1 + 1.26;
    T(s, "окно «свежей» смены · 30 дней", wx, ty - 0.27, x1 - wx, 0.2, { size: 9, bold: true, color: ST.rv.text, valign: "middle" });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x0, y: ty, w: x1 - x0, h: 0.07, rectRadius: 0.035, fill: { color: ST.nd.tint }, line: { type: "none" }, objectName: "Шкала дней до операции" });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: wx, y: ty, w: x1 - wx, h: 0.07, rectRadius: 0.035, fill: { color: HEX.accent4 }, line: { type: "none" }, objectName: "Окно свежей смены" });
    s.addShape(pres.shapes.DIAMOND, { x: dx - 0.1, y: ty - 0.065, w: 0.2, h: 0.2, fill: { color: HEX.accent4 }, line: { color: "FFFFFF", width: 1.5 }, objectName: "Смена руководителя" });
    s.addShape(pres.shapes.OVAL, { x: x1 - 0.08, y: ty - 0.045, w: 0.16, h: 0.16, fill: { color: HEX.dk2 }, line: { type: "none" }, objectName: "Дата операции" });
    T(s, "смена — 19 дней назад", dx - 0.85, ty + 0.13, 1.7, 0.2, { size: 9, bold: true, color: ST.rv.text, align: "center", valign: "middle" });
    T(s, "операция", x1 - 0.72, ty + 0.13, 0.72, 0.2, { size: 9, bold: true, color: C.text2, align: "right", valign: "middle" });
    T(s, "Свежая смена — первый шаг возможного захвата: уточнить у нового директора, при признаках — в антифрод", x0, a1 + 1.62, x1 - x0, 0.54, { size: 11, color: C.text1 });
    // Отмена основной МЧД: передоверие теряет силу вместе с ней
    card(s, rx, a2, rw, ah);
    T(s, "Основную МЧД отменили, осталось передоверие", x0, a2 + 0.16, x1 - x0, 0.52, { size: 14, bold: true, color: C.text2, valign: "middle" });
    pill(s, x0, a2 + 0.74, "no", { size: 9.5 });
    const cy2 = a2 + 1.16, chipH = 0.32, aw = 1.36, bw = 2.0;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x0, y: cy2, w: aw, h: chipH, rectRadius: 0.08, fill: { color: ST.no.tint }, line: { color: "F6B9BC", width: 0.75 }, objectName: "Основная МЧД отменена" });
    dot(s, x0 + 0.06, cy2 + 0.05, 0.22, ST.no.base, "X");
    T(s, "МЧД отменена", x0 + 0.34, cy2, aw - 0.38, chipH, { size: 9.5, bold: true, color: ST.no.text, valign: "middle" });
    arrow(s, x0 + aw + 0.06, cy2 + chipH / 2, x1 - bw - x0 - aw - 0.12, HEX.accent5, 1.5);
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x1 - bw, y: cy2, w: bw, h: chipH, rectRadius: 0.08, fill: { color: "FFFFFF" }, line: { color: HEX.accent5, width: 1, dashType: "dash" }, objectName: "Передоверие без силы" });
    dot(s, x1 - bw + 0.06, cy2 + 0.05, 0.22, ST.no.base, "Ban");
    T(s, "передоверие без силы", x1 - bw + 0.34, cy2, bw - 0.38, chipH, { size: 9.5, bold: true, color: ST.no.text, valign: "middle" });
    T(s, "С прекращением доверенности теряет силу и передоверие — п. 3 ст. 188 ГК РФ", x0, a2 + 1.62, x1 - x0, 0.54, { size: 11, color: C.text1 });
    s.addNotes("[2:10–2:22] У каждой строки есть доказательство: запись ЕГРЮЛ, поле доверенности, статья закона. Та же компания со свежей сменой директора идёт на разбор за четыре часа. А если отменили основную доверенность, передоверие тоже теряет силу.");
  }

  // ---------- 12. Продукт работает ----------
  {
    const s = slide("Контент", "Питч", "ПРОДУКТ СЕГОДНЯ", `Продукт уже работает: ${DEMOS}, карточка сотрудника и журнал собираются одной командой`,
      `Скриншот и вывод терминала настоящие, сняты при сборке презентации; «…» — пропущенные строки. Запуск одной командой: ./run-demo.sh. Версия ${N.version}, прогон ${N.asOf}`);
    const shot = await sharp(path.join(ASSETS, "demo-card-14.jpg")).metadata();
    const fw = 6.0, barH = 0.34, imgW = fw - 0.12, imgH = imgW * (shot.height / shot.width), fh = barH + imgH + 0.1;
    card(s, L, TOP, fw, fh, { r: 0.1, name: "Окно браузера" });
    s.addShape(pres.shapes.RECTANGLE, { x: L + 0.02, y: TOP + 0.02, w: fw - 0.04, h: barH - 0.02, fill: { color: "EEF2F8" }, line: { type: "none" }, objectName: "Панель браузера" });
    ["FF6B6B", "FFC35A", "5FD39A"].forEach((c, i) => s.addShape(pres.shapes.OVAL, { x: L + 0.16 + i * 0.18, y: TOP + 0.11, w: 0.12, h: 0.12, fill: { color: c }, line: { type: "none" }, objectName: "Кнопка окна" }));
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: L + 0.8, y: TOP + 0.06, w: fw - 1.0, h: 0.22, rectRadius: 0.11, fill: { color: "FFFFFF" }, line: { type: "none" }, objectName: "Адресная строка" });
    T(s, "var/demo/14_old_director_charter_cleared.html", L + 0.92, TOP + 0.06, fw - 1.2, 0.22, { size: 9, color: TINT.text2, valign: "middle" });
    s.addImage({ path: path.join(ASSETS, "demo-card-14.jpg"), x: L + 0.06, y: TOP + barH + 0.04, w: imgW, h: imgH, altText: "Скриншот карточки вердикта демо-кейса 14: «ПОДТВЕРЖДЕНО», сильнейшее возражение — смена руководителя с нормой п. 1 ст. 188 ГК РФ, операция на 480 000 ₽ 20.10.2026, организация и сигнал мониторинга" });
    // Метки на скриншоте: доли ширины и высоты кадра capture_demo.js (вердикт, возражение, стороны, сигналы)
    const ix = L + 0.06, iy = TOP + barH + 0.04;
    [[0.31, 0.232], [0.966, 0.278], [0.962, 0.472], [0.162, 0.782]].forEach(([fx, fy], i) => {
      const mx = ix + fx * imgW - 0.15, my = iy + fy * imgH - 0.15;
      s.addShape(pres.shapes.OVAL, { x: mx, y: my, w: 0.3, h: 0.3, fill: { color: HEX.accent1 }, line: { color: "FFFFFF", width: 2 }, shadow: shadow(), objectName: `Метка ${i + 1}` });
      T(s, String(i + 1), mx, my, 0.3, 0.3, { size: 11, bold: true, color: C.background1, align: "center", valign: "middle" });
    });
    ["вердикт", "сильнейшее возражение с нормой", "операция, организация, заявитель", "сигнал мониторинга"].forEach((t, i) => {
      const lx = L + (i % 2) * (fw / 2), ly = TOP + fh + 0.1 + Math.floor(i / 2) * 0.27;
      s.addShape(pres.shapes.OVAL, { x: lx, y: ly + 0.02, w: 0.2, h: 0.2, fill: { color: HEX.accent1 }, line: { type: "none" }, objectName: "Метка легенды" });
      T(s, String(i + 1), lx, ly + 0.02, 0.2, 0.2, { size: 8.5, bold: true, color: C.background1, align: "center", valign: "middle" });
      T(s, t, lx + 0.28, ly, fw / 2 - 0.32, 0.24, { size: 10.5, color: TINT.text2, valign: "middle", name: "Легенда скриншота" });
    });
    const rx = L + fw + GAP, rw = R - rx;
    const th = rw * G.terminalRatio;
    s.addImage({ data: G.terminal, x: rx, y: TOP, w: rw, h: th, altText: "Вывод терминала: mandat demo — 14 кейсов совпали с ожиданием, журнал цел; удостоверение действует; после событий захвата — тревога possible_takeover и статус «приостановлено», код 3" });
    const ly = TOP + th + 0.12, lh = BOTTOM - ly;
    card(s, rx, ly, rw, lh);
    T(s, "Что показывает демо", rx + 0.25, ly + 0.07, rw - 0.5, 0.3, { size: 13, bold: true, color: C.text2 });
    const demo = [
      "нет заключения об уставе — «на разбор», «да» без него не будет",
      "давняя смена директора — сигнал, свежая — «на разбор»",
      "отмена МЧД отзывает удостоверение, захват приостанавливает",
      "аудитор получает доказательство одной записи журнала",
    ];
    s.addText(demo.map((t, i) => ({ text: typo(t), options: { bullet: { type: "number" }, breakLine: i < demo.length - 1 } })),
      { x: rx + 0.25, y: ly + 0.37, w: rw - 0.45, h: lh - 0.4, isTextBox: true, margin: 0, fontSize: 10.5, color: C.text1, paraSpaceAfter: 0, valign: "top", objectName: "Шаги демо" });
    s.addNotes(`[2:22–2:34] Это работает сегодня, одной командой. Слева настоящая карточка сотрудника, справа терминал: все ${N.demo} кейсов дают ожидаемый ответ, журнал цел. После событий захвата то же удостоверение уже приостановлено.`);
  }

  // ---------- 13. Демо в браузере: сайт на шести экранах ----------
  {
    const s = slide("Контент", "Питч", "ДЕМО В БРАУЗЕРЕ", "Демо открывается в браузере на телефоне и на большом экране, ставить ничего не нужно",
      "На экранах — настоящие страницы сайта: захват по шагам, главная, разбор кейса, проверка подписи, ответ по датам и план пилота. Компании и люди в демо вымышлены");
    // Та же картинка, что в полосе «Шесть экранов» на главной сайта (docs/VISUALS.md). Её прозрачный фон заливается
    // цветом фона слайда: JPEG в разы легче PNG со снимками экранов, а на слайде разницы не видно.
    const file = path.join(ROOT, "site", "img", "devices-2400.webp");
    const meta = await sharp(file).metadata();
    const bh = 0.62, by = BOTTOM - bh;
    const dw = 8.0, dh = (dw * meta.height) / meta.width;
    if (TOP + dh > by - 0.04) throw new Error(`«Шесть экранов»: картинка высотой ${dh.toFixed(2)} дюйма заходит на плашку`);
    const jpg = await sharp(file).flatten({ background: `#${HEX.lt2}` }).jpeg({ quality: 88, chromaSubsampling: "4:4:4" }).toBuffer();
    s.addImage({
      data: "image/jpeg;base64," + jpg.toString("base64"), x: L - 0.05, y: TOP, w: dw, h: dh, objectName: "Шесть экранов",
      altText: "Сайт «Мандата» на шести устройствах: захват компании по шагам на большом экране, главная на мониторе, разбор кейса в тёмной теме на ноутбуке, проверка подписи на планшете, ось дат операции на iPhone и план пилота на Android",
    });
    const rx = L + dw + 0.2, rw = R - rx;
    // QR-код сайта: жюри открывает демо с телефона, пока идёт питч.
    const qh = 1.9, qs = 1.5;
    card(s, rx, TOP, rw, qh, { name: "Карточка QR-кода" });
    s.addImage({ data: G.qrSite, x: rx + 0.2, y: TOP + 0.2, w: qs, h: qs, altText: "QR-код: konkovdv.github.io/Mandat" });
    const qx = rx + qs + 0.38, qw = R - 0.18 - qx;
    T(s, "Откройте с телефона", qx, TOP + 0.24, qw, 0.62, { size: 13, bold: true, color: C.text2 });
    T(s, "демо без установки, данные вымышлены", qx, TOP + 0.9, qw, 0.48, { size: 10.5, color: C.text1 });
    T(s, "konkovdv.github.io/Mandat", qx, TOP + 1.42, qw, 0.3, { size: 9.5, bold: true, color: C.accent1, valign: "middle" });
    const ty = TOP + qh + GAP, th = by - GAP - ty;
    card(s, rx, ty, rw, th);
    tile(s, rx + 0.22, ty + 0.2, 0.44, "MousePointerClick");
    T(s, "Что можно проверить самому", rx + 0.8, ty + 0.2, rw - 1.0, 0.44, { size: 14, bold: true, color: C.text2, valign: "middle" });
    bullets(s, [
      "поменять факты кейса",
      "пройти захват по шагам до платежа",
      "проверить подпись и журнал",
      "посчитать эффект на своих числах",
    ], rx + 0.25, ty + 0.8, rw - 0.45, th - 0.9, { size: 11.5, psa: 4 });
    band(s, L, by, W, bh, "На сайте работает то же ядро, что в банке: ответы на кейсы посчитал продукт, а числа совпадают со слайдами", { icon: "MonitorSmartphone", size: 13, bold: true });
    s.addNotes("[2:34–2:44] Всё это работает и в браузере, без установки: на телефоне, планшете и большом экране. Кейсы, захват, подпись и калькулятор можно потрогать по QR-коду.");
  }

  // ---------- 14. Захват до платежа: mandat showcase ----------
  {
    const s = slide("Контент", "Питч", "СПРИНТ 2 · ДОПУСК ПЛАТЕЖА", "Интернет-банк спрашивает «Мандат» перед каждым платежом, и захват останавливается до списания денег",
      "Шаги и причины отказов — вывод команды mandat showcase на стенде: данные вымышлены, смену директора и новую доверенность задаёт сценарий. Запрос допуска — POST /v1/operations/authorize; nonce — одноразовый номер запроса");
    const STEP = {
      "ПОДТВЕРЖДЕНО": ST.ok, "ДОПУЩЕНО": ST.ok, "ОТКАЗ": ST.no, "ПРИЗНАКИ ЗАХВАТА": ST.rv,
      "ВЫПУЩЕНО": { base: HEX.accent1, tint: TINT.blue, text: "0A3C8C" },
    };
    const lw = 8.0, h = BOTTOM - TOP;
    card(s, L, TOP, lw, h);
    const rowH = (h - 0.2) / SHOW.steps.length, cx = L + 0.42;
    // Событие захвата и отказ после него — главное на слайде: строки подсвечены, итог залит цветом
    const hot = (i) => i >= SHOW.steps.length - 2;
    SHOW.steps.forEach((step, i) => {
      if (hot(i)) s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: L + 0.12, y: TOP + 0.1 + i * rowH + 0.03, w: lw - 0.24, h: rowH - 0.06, rectRadius: 0.1, fill: { color: STEP[step.outcome].tint }, line: { type: "none" }, objectName: `Подсветка: ${step.outcome}` });
    });
    vline(s, cx, TOP + 0.1 + rowH / 2, rowH * (SHOW.steps.length - 1), TINT.blueLine, 2);
    // Причина шага — дословно из отчёта; если не помещается рядом с плашкой, берётся одна её часть с «…».
    const fit = (text, width) => {
      const max = Math.floor(width / 0.078);
      if (text.length <= max) return text;
      const parts = text.split("; ");
      if (parts.length > 1 && parts[parts.length - 1].length + 2 <= max) return `… ${parts[parts.length - 1]}`;
      return `${parts[0].slice(0, max - 2)} …`;
    };
    SHOW.steps.forEach((step, i) => {
      const y = TOP + 0.1 + i * rowH, st = STEP[step.outcome];
      s.addShape(pres.shapes.OVAL, { x: cx - 0.17, y: y + (rowH - 0.34) / 2, w: 0.34, h: 0.34, fill: { color: st.base }, line: { color: "FFFFFF", width: 1.5 }, objectName: "Шаг" });
      T(s, String(i + 1), cx - 0.17, y + (rowH - 0.34) / 2, 0.34, 0.34, { size: 11, bold: true, color: C.background1, align: "center", valign: "middle" });
      // Имя каталога кейса из отчёта команды на слайде читается как номер демо-кейса.
      const title = step.title.replace(/^Проверка кейса (\d+)_\w+$/, "Проверка полномочий: демо-кейс $1");
      T(s, title, L + 0.8, y + 0.05, lw - 1.0, 0.3, { size: 11.5, bold: true, color: C.text2, valign: "middle" });
      const pw = pillW(step.outcome, 9);
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: L + 0.8, y: y + 0.36, w: pw, h: 0.26, rectRadius: 0.13, fill: { color: hot(i) ? st.base : st.tint }, line: { type: "none" }, objectName: `Итог шага: ${step.outcome}` });
      T(s, step.outcome, L + 0.8, y + 0.36, pw, 0.26, { size: 9, bold: true, color: hot(i) ? "FFFFFF" : st.text, align: "center", valign: "middle" });
      const dx = L + 0.8 + pw + 0.15, dw = L + lw - 0.2 - dx;
      if (step.detail) T(s, fit(step.detail, dw), dx, y + 0.36, dw, 0.26, { size: 10.5, color: TINT.text2, valign: "middle" });
    });
    const rx = L + lw + GAP, rw = R - rx;
    const why = 2.2, bank = 1.62, gap = 0.2, bh = h - why - bank - 2 * gap;
    card(s, rx, TOP, rw, why);
    tile(s, rx + 0.22, TOP + 0.2, 0.44, "Ban");
    T(s, "Почему отказ", rx + 0.8, TOP + 0.2, rw - 1.0, 0.44, { size: 14, bold: true, color: C.text2, valign: "middle" });
    bullets(s, [
      "повтор: одноразовый номер запроса (nonce) уже израсходован",
      "сумма: не больше проверенной в удостоверении",
      "после признаков захвата удостоверения приостановлены: отказ до списания",
    ], rx + 0.25, TOP + 0.78, rw - 0.45, why - 0.86, { size: 11, psa: 4 });
    const by = TOP + why + gap;
    card(s, rx, by, rw, bank);
    tile(s, rx + 0.22, by + 0.2, 0.44, "Landmark");
    T(s, "Что получает банк", rx + 0.8, by + 0.2, rw - 1.0, 0.44, { size: 14, bold: true, color: C.text2, valign: "middle" });
    T(s, "ответ «допустить» или причины отказа. Нет ответа — нет допуска. Спрашивать может только система банка, решение пишется в журнал без ИНН представителя", rx + 0.25, by + 0.74, rw - 0.45, bank - 0.8, { size: 11.5, color: C.text1 });
    band(s, rx, by + bank + gap, rw, bh, "Всю цепочку показывает одна команда: mandat showcase", { icon: "ShieldCheck", size: 13, bold: true });
    s.addNotes("[2:44–3:02] Главное за спринт. Перед каждым платежом интернет-банк спрашивает «Мандат». Удостоверение выпущено на один платёж: компания, день, сумма, получатель, одноразовый номер. Первый платёж проходит. Повтор и вчетверо большая сумма получают отказ. Потом признаки захвата, и следующий платёж отклонён до списания денег.");
  }

  // ---------- 15. Качество ----------
  {
    const s = slide("Контент", "Питч", "КАЧЕСТВО", `Ноль ложных «подтверждено» на ${N.negatives} кейсах, где подтверждать нельзя`,
      `Корпус: ${N.cases} ${plural(N.cases, "сгенерированный кейс", "сгенерированных кейса", "сгенерированных кейсов")}, ${N.kinds} видов ситуаций по нормам ГК РФ и формату МЧД — проверка логики правил. Прогон ${N.asOf}, версия ${N.version}; команды для повтора каждого числа — слайд A3`);
    const sw = 5.35, rows = [
      [`${N.falseConfirmed} из ${N.negatives}`, `ложных «подтверждено»; верхняя граница 95% — ${N.fcUpper}`],
      [`${N.agree} / ${N.cases}`, `совпадение с ожидаемым вердиктом, ${N.kinds} видов ситуаций; на втором наборе с другими значениями — ${N.holdoutMismatches} расхождений`],
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
    band(s, L, 5.92, W, 0.8, `${auto} однозначных кейсов «Мандат» закрыл сам, без единой ошибки. ${human} спорных и неполных передал сотруднику с готовым досье. На потоке ВТБ те же метрики снимет пилот.`, { icon: "Route", size: 13 });
    s.addNotes(`[3:02–3:14] Недопустимая ошибка одна — ложное «да». На ${N.negatives} ловушках таких ноль, верхняя граница ${N.fcUpper.replace("%", " процента")}. ${FUZZ} мутаций тоже не дали ложного «да». Передоверие сверено со второй реализацией на Cedar.`);
  }

  // ---------- 16. Встройка ----------
  {
    const s = slide("Контент", "Питч", "В КОНТУРЕ БАНКА", "«Мандат» ставится в контур ВТБ без внешней сети, источники подключаются через готовые интерфейсы",
      `vtb.ru — «Комплект МЧД и КЭП» в «ВТБ Бизнес»; версия ${N.version}, прогон ${N.asOf}. Порт — готовый интерфейс, к которому банк подключает свой источник`);
    const cols = [
      ["Database", "Источники", ["МЧД в XML и бумажная доверенность", "ЕГРЮЛ на дату", "реестр МЧД ФНС: номер + ИНН доверителя и представителя", "ЕФРСБ и устав"], 0],
      ["Cpu", "Движок «Мандата»", ["граф полномочий на дату", `${RULES} с нормой и тестом`, "журнал с деревом Меркла", "без внешней сети", "допуск операции для интернет-банка по OpenID AuthZEN 1.0"], 1],
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
      ["Check", HEX.accent3, "Работает сегодня", [`${RULES}, ${DEMOS}, CLI и HTTP API с ролями`, "разбор человеком: очередь P1–P3 и сроки", "мониторинг событий и сроков МЧД, статусы удостоверений, журнал", `масштаб: несколько воркеров и машин с общим хранилищем Redis, готовность — /readyz; кейс — ${N.medianMs}`]],
      ["Plug", HEX.accent1, "Подключаем в пилоте", ["реестр МЧД и ЕФРСБ — через готовые порты, выписка ЕГРЮЛ — через порт по тому же образцу", "проверка КЭП и ГОСТ-подпись — сертифицированные СКЗИ банка", "ключи — HSM по PKCS#11; вход — OIDC банка", "коды полномочий — выгрузка классификатора ЕСНСИ"]],
    ];
    parts.forEach(([ic, col, head, items], i) => {
      const x = L + i * (w2 + GAP);
      card(s, x, y2, w2, h2);
      dot(s, x + 0.25, y2 + 0.22, 0.42, col, ic);
      T(s, head, x + 0.82, y2 + 0.22, w2 - 1.0, 0.42, { size: 15, bold: true, color: C.text2, valign: "middle" });
      bullets(s, items, x + 0.3, y2 + 0.8, w2 - 0.55, h2 - 0.86, { size: 12, psa: 4 });
    });
    s.addNotes("[3:14–3:24] Встаём в контур банка без внешней сети. Ядро, правила и журнал готовы, интернет-банк спрашивает допуск по открытому стандарту. Реестр, ЕГРЮЛ и подпись банк подключает через готовые интерфейсы.");
  }

  // ---------- 17. ИИ под контролем ----------
  {
    const s = slide("Контент", "Питч", "ИИ ПОД КОНТРОЛЕМ", "Модель только приносит цитату из устава, вердикт выносят правила, спорное разбирает человек",
      "Принципы — Кодекс этики в сфере ИИ на финансовом рынке (письмо Банка России № ИН-016-13/91 от 09.07.2025) и 243-ФЗ от 26.07.2026, ст. 5 и 13; риски модели разобраны по OWASP Top 10 for LLM Applications 2025");
    const flow = [
      ["FileText", "Документ", "устав, текстовое полномочие"],
      ["Cpu", "Модель в контуре банка", "предлагает факт с дословной цитатой"],
      ["Filter", "Шлюз цитат", "нет цитаты в документе — нет факта"],
      ["Gavel", `${RULES} с нормой`, "выносят вердикт"],
      ["UserCheck", "Человек", "разбирает спорное; клиент может оспорить"],
    ];
    const gap = 0.3, fw = (W - gap * 4) / 5, fh = 1.92;
    flow.forEach(([ic, head, t], i) => {
      const x = L + i * (fw + gap);
      const hl = i === 3, model = i === 1;
      // Модель только советует (пунктир), правила решают (заливка)
      card(s, x, TOP, fw, fh, hl ? { fill: C.text2, line: null } : model ? { fill: TINT.blue, line: HEX.accent1, lw: 1.25, dash: "dash", shadow: false } : {});
      tile(s, x + 0.2, TOP + 0.2, 0.48, ic, hl ? { fill: TINT.navyTile, icon: "light" } : model ? { fill: "FFFFFF" } : {});
      if (hl || model) {
        const role = model ? "советует" : "решают", rw = pillTextW(role, 9) + 0.3;
        s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x + fw - 0.16 - rw, y: TOP + 0.3, w: rw, h: 0.28, rectRadius: 0.14, fill: { color: model ? "FFFFFF" : HEX.accent2 }, line: { type: "none" }, objectName: `Роль: ${role}` });
        T(s, role, x + fw - 0.16 - rw, TOP + 0.3, rw, 0.28, { size: 9, bold: true, color: model ? HEX.accent1 : HEX.dk2, align: "center", valign: "middle" });
      }
      T(s, head, x + 0.2, TOP + 0.76, fw - 0.36, 0.5, { size: 13.5, bold: true, color: hl ? C.background1 : C.text2 });
      T(s, t, x + 0.2, TOP + 1.28, fw - 0.36, 0.68, { size: 12, color: hl ? TINT.ice : C.text1 });
      if (i < flow.length - 1) arrow(s, x + fw + 0.04, TOP + fh / 2, gap - 0.08, HEX.accent1, 1.75);
    });
    // Ветка шлюза: факт без дословной цитаты дальше не идёт
    const gx = L + 2 * (fw + gap), ry = TOP + fh + 0.16;
    vline(s, gx + fw / 2, TOP + fh, 0.16, HEX.accent5, 1.5, "dash");
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: gx, y: ry, w: fw, h: 0.34, rectRadius: 0.08, fill: { color: ST.no.tint }, line: { color: "F6B9BC", width: 0.75 }, objectName: "Факт без цитаты отброшен" });
    dot(s, gx + 0.08, ry + 0.06, 0.22, ST.no.base, "X");
    T(s, "факт отброшен", gx + 0.38, ry, fw - 0.44, 0.34, { size: 9.5, bold: true, color: ST.no.text, valign: "middle" });
    const by = ry + 0.34 + 0.18, bh = 0.5;
    card(s, L, by, W, bh, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
    T(s, [
      { text: "Четыре вопроса организаторов об ИИ и наши ответы. ", options: { bold: true, color: HEX.dk2 } },
      { text: "Решение остаётся за человеком, как требует Кодекс этики Банка России в сфере ИИ" },
    ], L + 0.3, by, W - 0.6, bh, { size: 12, valign: "middle" });
    const py = by + bh + 0.2, ph = BOTTOM - py, pw = (W - GAP * 3) / 4;
    const pr = [
      ["Eye", "Что улучшает", "читает устав вместо сотрудника: находит пункт об ограничении полномочий и приносит его дословно"],
      ["Users", "Какие данные", "только строки устава о полномочиях; ФИО, паспорт, ИНН и СНИЛС заменены метками до отправки"],
      ["Lock", "Как проверяем", "цитата должна дословно найтись в документе, иначе факт отброшен. Тест следит, что вердикт модель не меняет"],
      ["Landmark", "Где стоит", "в контуре банка, российская или своя; по умолчанию выключена, без неё продукт работает"],
    ];
    pr.forEach(([ic, head, t], i) => {
      const x = L + i * (pw + GAP);
      card(s, x, py, pw, ph);
      tile(s, x + 0.2, py + 0.16, 0.4, ic);
      T(s, head, x + 0.7, py + 0.16, pw - 0.85, 0.4, { size: 13.5, bold: true, color: C.text2, valign: "middle" });
      T(s, t, x + 0.2, py + 0.62, pw - 0.38, ph - 0.66, { size: 11.5, color: C.text1 });
    });
    s.addNotes("[3:24–3:34] ИИ здесь только помощник. Модель приносит цитату из устава, вердикт выносят правила, спорное решает человек. Модель работает в контуре банка и по умолчанию выключена.");
  }

  // ---------- 18. Защита данных: фокус спринта 2 ----------
  {
    const s = slide("Контент", "Питч", "ЗАЩИТА ДАННЫХ · ФОКУС СПРИНТА 2", "Данные клиента не выходят из банка, а каждое чтение отчёта записано в журнал",
      "Фокус спринта 2 по условиям трека — «показать защиту данных». Строка устава вымышлена, метки — настоящий вывод кода на ключе стенда. Оценку соответствия требованиям закона и ИБ проводит банк");
    // Контур банка: путь данных слева направо, под ним журнал. Рамка модели пунктирная: модель включает банк.
    const zh = 2.52;
    card(s, L, TOP, W, zh, { line: HEX.accent1, dash: "dash", lw: 1.25, name: "Контур банка" });
    T(s, "КОНТУР БАНКА · БЕЗ ВНЕШНЕЙ СЕТИ", L + 0.25, TOP + 0.12, 6, 0.26, { size: 10, bold: true, color: C.accent1, cs: 1.5, valign: "middle" });
    const flow = [
      ["FileX", "Вход", "строгий разбор JSON и XML: внешние сущности запрещены, лишнее поле — ошибка"],
      ["Server", "Ядро", `разбор и ${RULES} без сетевых запросов`],
      ["Fingerprint", "Модель по выбору", "только строки устава о полномочиях; ФИО и номера — метки"],
      ["Lock", "Хранилище", "AES-256-GCM, шифр привязан к кейсу и владельцу отчёта"],
      ["Landmark", "Допуск ДБО", "только система банка; запрос одноразовый, устаревший статус — отказ"],
    ];
    const pad = 0.25, colW = (W - 2 * pad) / flow.length, d = 0.56, ty = TOP + 0.44;
    flow.forEach(([ic, head, t], i) => {
      const cx = L + pad + i * colW + colW / 2;
      if (i === 2) {
        s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
          x: cx - d / 2 - 0.07, y: ty - 0.07, w: d + 0.14, h: d + 0.14, rectRadius: (d + 0.14) * 0.28,
          fill: { color: "FFFFFF" }, line: { color: HEX.accent1, width: 1, dashType: "dash" }, objectName: "Рамка: модель включает банк",
        });
      }
      tile(s, cx - d / 2, ty, d, ic);
      T(s, head, cx - colW / 2 + 0.08, ty + d + 0.1, colW - 0.16, 0.3, { size: 13, bold: true, color: C.text2, align: "center", valign: "middle" });
      T(s, t, cx - colW / 2 + 0.12, ty + d + 0.42, colW - 0.24, 0.55, { size: 10.5, color: C.text1, align: "center" });
      if (i < flow.length - 1) {
        const ax = cx + d / 2 + 0.14, aw = colW - d - 0.28;
        arrow(s, ax, ty + d / 2, aw, HEX.accent1, 1.75);
        if (i === 1) T(s, "только метки", ax, ty + d / 2 - 0.3, aw, 0.24, { size: 9.5, bold: true, color: C.accent1, align: "center", valign: "bottom" });
      }
    });
    // Журнал идёт под всем путём: вердикты, чтения и удаления отчёта.
    const jy = TOP + zh - 0.44;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: L + pad, y: jy, w: W - 2 * pad, h: 0.32, rectRadius: 0.16, fill: { color: TINT.blue }, line: { type: "none" }, objectName: "Журнал аудита" });
    s.addImage({ data: G.icon.ScrollText, x: L + pad + 0.14, y: jy + 0.05, w: 0.22, h: 0.22, objectName: "Значок" });
    T(s, [{ text: "Журнал аудита: ", options: { bold: true, color: HEX.dk2 } }, { text: "вердикты, чтение и удаление отчёта, основания решений — отпечатком; хеш-цепочка и HMAC показывают правку записей" }], L + pad + 0.46, jy, W - 2 * pad - 0.6, 0.32, { size: 10.5, valign: "middle" });

    // Что видит модель: настоящий вывод privacy/names.py и privacy/pii.py на ключе стенда.
    const y1 = TOP + zh + 0.2, h1 = 1.42, bw = 7.3;
    card(s, L, y1, bw, h1);
    T(s, "Что видит модель: вывод кода на строке устава", L + 0.25, y1 + 0.12, bw - 0.5, 0.3, { size: 12.5, bold: true, color: C.text2, valign: "middle" });
    const red = (text) => ({ text, options: { bold: true, color: ST.no.text } });
    const blue = (text) => ({ text, options: { bold: true, color: HEX.accent1 } });
    const green = (text) => ({ text, options: { bold: true, color: ST.ok.text } });
    const rowsR = [
      ["В УСТАВЕ", [{ text: "Директор " }, red("Петров Алексей Иванович"), { text: ", ИНН " }, red("500100732259"), { text: ", паспорт " }, red("45 06 123456"), { text: ", вправе заключать сделки " }, green("до 1 000 000 рублей")]],
      ["В МОДЕЛЬ", [{ text: "Директор " }, blue("[ДИРЕКТОР]"), { text: ", ИНН " }, blue("[ИНН:d4d89e6e604b4e26]"), { text: ", паспорт " }, blue("[ПАСПОРТ:49e56e8994ff1f52]"), { text: ", вправе заключать сделки " }, green("до 1 000 000 рублей")]],
    ];
    rowsR.forEach(([lab, runs], i) => {
      const y = y1 + 0.48 + i * 0.46;
      T(s, lab, L + 0.25, y + 0.02, 1.0, 0.22, { size: 9, bold: true, color: TINT.muted, cs: 1 });
      T(s, runs, L + 1.3, y, bw - 1.55, 0.42, { size: 10.5, color: C.text1 });
    });
    const ix = L + bw + GAP, iw = R - ix;
    card(s, ix, y1, iw, h1);
    const items = [
      ["KeyRound", "Ключи: ", "HSM по PKCS#11, Vault Transit или KMS; без ключей пилотный режим не запускается"],
      ["Scale", "152-ФЗ: ", "удалённое по ст. 21 не возвращается из копии; отчёт об инциденте — за 24 и 72 ч"],
      ["Container", "Сборка: ", "pip-audit, Trivy, gitleaks, CodeQL, фаззинг входа, SBOM и подписанный релиз"],
    ];
    items.forEach(([ic, lead, t], i) => {
      const y = y1 + 0.1 + i * 0.42;
      tile(s, ix + 0.2, y + 0.03, 0.34, ic);
      T(s, [{ text: lead, options: { bold: true, color: HEX.dk2 } }, { text: t }], ix + 0.66, y, iw - 0.84, 0.4, { size: 10.5, valign: "middle" });
    });
    const by = y1 + h1 + 0.2;
    band(s, L, by, W, BOTTOM - by, `Чек-лист воркшопа 20.11: ${READY.ok} из ${READY.all} пунктов проверены тестами. За банком — вход с MFA, SIEM, уровень защищённости и основание обработки, оценка соответствия 152-ФЗ и ГОСТ Р 57580.1, КЭП через СКЗИ`, { icon: "ShieldCheck", size: 13, bold: true });
    s.addNotes(`[3:34–3:50] Защита данных. Ядро не ходит во внешнюю сеть, в модель уходят метки вместо ФИО и номеров. Отчёты зашифрованы, каждое чтение записано. Удалённый кейс не возвращается из резервной копии. Чек-лист воркшопа о данных проверяют тесты, а ${READY.bank} ${READY.bankWord} из него — за банком.`);
  }

  // ---------- 19. Экономика: одна проверка, эффект банка, затраты и возврат ----------
  {
    const s = slide("Контент", "Питч", "ЭКОНОМИКА ДЛЯ ВТБ", `Проверка стоит банку ≈${UNIT.mandat} ₽ вместо ≈${UNIT.manual} ₽, разовые затраты возвращаются за ${PAYBACK}`,
      "ВТБ, пресс-релиз к МСФО за 1П 2026: ≈1,8 млн предприятий и предпринимателей; Банк России, I–III кв. 2026. Формулы — слайд A12; полная модель с NPV, IRR и чувствительностью — в пакете аналитики. НДС учтён в затратах банка");
    const i = 1; // базовый сценарий модели
    const cw = (W - 2 * GAP) / 3, ch = 2.42;
    // Одна проверка: два столбика в масштабе.
    card(s, L, TOP, cw, ch);
    tile(s, L + 0.25, TOP + 0.2, 0.44, "Coins");
    T(s, "Одна проверка полномочий", L + 0.82, TOP + 0.2, cw - 1.0, 0.44, { size: 14, bold: true, color: C.text2, valign: "middle" });
    const bx = L + 0.25, bw = cw - 0.5;
    T(s, `вручную · ≈${UNIT.manual} ₽`, bx, TOP + 0.82, bw, 0.24, { size: 11, bold: true, color: C.text2 });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: bx, y: TOP + 1.08, w: bw, h: 0.2, rectRadius: 0.1, fill: { color: "9DB8E8" }, line: { type: "none" }, objectName: "Столбик: проверка вручную" });
    T(s, `с «Мандатом» · ≈${UNIT.mandat} ₽`, bx, TOP + 1.38, bw, 0.24, { size: 11, bold: true, color: C.text2 });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: bx, y: TOP + 1.64, w: (bw * UNIT.mandat) / UNIT.manual, h: 0.2, rectRadius: 0.1, fill: { color: HEX.accent1 }, line: { type: "none" }, objectName: "Столбик: проверка с «Мандатом»" });
    T(s, `в ${UNIT.ratio} раза дешевле; в базовом сценарии — ${Math.round(BANK.events[i])} тыс. проверок в год`, bx, TOP + 1.94, bw, 0.4, { size: 11, color: TINT.text2 });
    // Эффект банка: из чего складывается.
    const x2 = L + cw + GAP;
    card(s, x2, TOP, cw, ch);
    tile(s, x2 + 0.25, TOP + 0.2, 0.44, "Target");
    T(s, "Эффект банка в год", x2 + 0.82, TOP + 0.2, cw - 1.0, 0.44, { size: 14, bold: true, color: C.text2, valign: "middle" });
    T(s, `≈${Math.round(BANK.effect_cash[i])} млн ₽`, x2 + 0.25, TOP + 0.72, cw - 0.5, 0.56, { size: 28, bold: true, color: C.accent1, valign: "middle" });
    T(s, [
      { text: `${Math.round(BANK.cash_saving[i])} млн ₽`, options: { bold: true, color: HEX.dk2 } },
      { text: ` — время сотрудников: высвобождается ${FTE[i]} ${plural(FTE[i], "ставка", "ставки", "ставок")}, половина этого времени становится деньгами`, options: { breakLine: true } },
      { text: `${Math.round(BANK.bank_loss[i])} млн ₽`, options: { bold: true, color: HEX.dk2 } },
      { text: ` — потери банка, которых не будет: ${Math.round(100 * BANK.bank_loss_share[i])}% от ${LOSS[i]} млн ₽ ущерба клиентов` },
    ], x2 + 0.25, TOP + 1.3, cw - 0.5, ch - 1.36, { size: 10.5, color: C.text1, psa: 3 });
    // Затраты и возврат.
    const x3 = x2 + cw + GAP;
    card(s, x3, TOP, cw, ch, { fill: C.text2, line: null });
    tile(s, x3 + 0.25, TOP + 0.2, 0.44, "Timer", { fill: TINT.navyTile, icon: "light" });
    T(s, "Затраты и возврат", x3 + 0.82, TOP + 0.2, cw - 1.0, 0.44, { size: 14, bold: true, color: C.background1, valign: "middle" });
    T(s, PAYBACK, x3 + 0.25, TOP + 0.72, cw - 0.5, 0.56, { size: 28, bold: true, color: C.background1, valign: "middle" });
    T(s, `возвращаются разовые затраты, ≈${Math.round(BANK.one_off[i])} млн ₽. Лицензия и сопровождение правил — ≈${Math.round(BANK.annual_cost[i])} млн ₽ в год. За три года — ${ROI} ₽ чистой отдачи на рубль затрат`, x3 + 0.25, TOP + 1.3, cw - 0.5, ch - 1.36, { size: 11, color: TINT.ice });
    // Три сценария модели и то, что уточнит пилот.
    const y2 = TOP + ch + GAP, h2 = BOTTOM - y2, tw = 7.9;
    const rub = (v) => `${comma(v, 1)} ₽`;
    table(s, ["Сценарий модели", ...EFFECT.names], [
      ["Проверок в год, тыс.", ...BANK.events.map((v) => String(Math.round(v)))],
      ["Высвобождается ставок", ...FTE.map(String)],
      ["Предотвращённый ущерб, млн ₽", ...LOSS.map(String)],
      ["Окупаемость разовых затрат", ...BANK.payback_cash.map(months)],
      ["Отдача на рубль за три года", ...BANK.roi_cash.map(rub)],
    ], L, y2, tw, [2.9, 1.7, 1.6, 1.7], { size: 11, highlight: 2, headH: 0.36, rowH: (h2 - 0.36) / 5, name: "Три сценария модели" });
    const px = L + tw + GAP, pw = R - px;
    card(s, px, y2, pw, h2, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
    tile(s, px + 0.25, y2 + 0.2, 0.44, "ClipboardCheck", { fill: "FFFFFF" });
    T(s, "Что уточнит пилот", px + 0.82, y2 + 0.2, pw - 1.0, 0.44, { size: 14, bold: true, color: C.text2, valign: "middle" });
    T(s, `Три параметра: доля клиентов с событием представительства (${shares(EFFECT.eventShare)}%), минуты сотрудника на пакет (${EFFECT.minutesSaved.join(" / ")}) и доля операций, связанных с полномочиями (${shares(EFFECT.authorityShare)}%). Хронометраж первых двух недель заменит их фактами банка`, px + 0.25, y2 + 0.72, pw - 0.5, h2 - 0.8, { size: 10.5, color: C.text1 });
    s.addNotes(`[3:50–4:06] Теперь деньги. Проверка вручную стоит банку около ${UNIT.manual} рублей, с «Мандатом» — около ${UNIT.mandat}. Эффект в базовом сценарии — ${Math.round(BANK.effect_cash[i])} миллионов рублей в год, разовые затраты возвращаются за ${PAYBACK}. Они возвращаются во всех трёх сценариях.`);
  }

  // ---------- 20. Пилот ----------
  {
    const s = slide("Контент", "Питч", "ПРЕДЛОЖЕНИЕ ВТБ", "Предлагаем шесть недель теневого пилота на одной операции: подпись платежей по доверенности",
      "Теневой режим: «Мандат» проверяет те же пакеты, что и сотрудники, и не влияет на клиентов и выдачу прав. Протокол пилота и матрица готовности — в репозитории и на сайте проекта");
    // План шести недель: три фазы, стоп-критерий действует всё время, в конце — решение G2.
    const gh = 2.28;
    card(s, L, TOP, W, gh, { name: "План пилота" });
    const lx = L + 0.25, lw = 2.35, gx = L + 2.8, gR = R - 2.4, wk = (gR - gx) / 6;
    const ry0 = TOP + 0.5, rh = 0.34, rg = 0.1, gridH = 4 * rh + 3 * rg + 0.12;
    for (let i = 0; i < 6; i++) T(s, `неделя ${i + 1}`, gx + i * wk, TOP + 0.13, wk, 0.26, { size: 10, bold: true, color: TINT.muted, align: "center", valign: "middle" });
    for (let i = 0; i <= 6; i++) vline(s, gx + i * wk, ry0 - 0.06, gridH, TINT.line, 0.75, i === 0 || i === 6 ? undefined : "dash");
    const phases = [
      ["Подготовка", 0, 1, `протокол, пороги приёмки, ${READY.bank} ${READY.bankWord} матрицы готовности за банком`, "after"],
      ["Теневая проверка", 1, 5, "параллельно с сотрудниками, журнал расхождений", "in"],
      ["Разметка и замер", 5, 6, "независимая разметка расхождений, замер времени и точности", "before"],
    ];
    phases.forEach(([name, a, b, t, where], i) => {
      const y = ry0 + i * (rh + rg);
      T(s, name, lx, y, lw, rh, { size: 12.5, bold: true, color: C.text2, valign: "middle" });
      const bx = gx + a * wk + 0.06, bw = (b - a) * wk - 0.12;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: bx, y: y + 0.02, w: bw, h: rh - 0.04, rectRadius: 0.1, fill: { color: i === 1 ? HEX.accent1 : HEX.dk2 }, line: { type: "none" }, objectName: `Фаза: ${name}` });
      if (where === "in") T(s, t, bx + 0.16, y, bw - 0.32, rh, { size: 11, color: C.background1, valign: "middle" });
      else if (where === "after") T(s, t, bx + bw + 0.14, y, gR - (bx + bw) - 0.24, rh, { size: 11, color: C.text1, valign: "middle" });
      else T(s, t, gx + 0.1, y, bx - gx - 0.24, rh, { size: 11, color: C.text1, align: "right", valign: "middle" });
    });
    // Стоп-критерий — на всех шести неделях.
    const sy = ry0 + 3 * (rh + rg);
    T(s, "Стоп-критерий", lx, sy, lw, rh, { size: 12.5, bold: true, color: ST.no.text, valign: "middle" });
    s.addShape(pres.shapes.LINE, { x: gx + 0.06, y: sy + rh / 2, w: gR - gx - 0.12, h: 0, line: { color: HEX.accent5, width: 2, dashType: "dash" }, objectName: "Стоп-критерий" });
    const stopT = "первое ложное «подтверждено» — стоп в любую неделю";
    const stw = pillTextW(stopT, 11) * 0.92 + 0.3;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: gx + (gR - gx - stw) / 2, y: sy + 0.03, w: stw, h: rh - 0.06, rectRadius: (rh - 0.06) / 2, fill: { color: ST.no.tint }, line: { type: "none" }, objectName: "Подпись стоп-критерия" });
    T(s, stopT, gx + (gR - gx - stw) / 2, sy, stw, rh, { size: 11, bold: true, color: ST.no.text, align: "center", valign: "middle" });
    // Решение G2 — в конце шестой недели, напротив разметки и замера.
    const dd = 0.5, dX = gR + 0.24, dCy = ry0 + 2 * (rh + rg) + rh / 2;
    arrow(s, gR - 0.02, dCy, 0.22, HEX.accent3, 1.5);
    s.addShape(pres.shapes.DIAMOND, { x: dX, y: dCy - dd / 2, w: dd, h: dd, fill: { color: HEX.accent3 }, line: { type: "none" }, objectName: "Решение G2" });
    T(s, "G2", dX, dCy - dd / 2, dd, dd, { size: 10.5, bold: true, color: C.background1, align: "center", valign: "middle" });
    T(s, [{ text: "Решение", options: { bold: true, color: HEX.dk2, breakLine: true } }, { text: "продолжать или остановить" }], dX + dd + 0.1, dCy - 0.62, R - (dX + dd + 0.1) - 0.16, 0.84, { size: 11, valign: "bottom" });
    const y2 = TOP + gh + 0.2, h2 = 1.74, w2 = (W - GAP) / 2;
    const parts = [
      ["Target", "Критерии: продолжать или остановить", ["0 ложных «подтверждено»: граница 95 % ниже порога банка", "доля «на разбор» не выше согласованного порога", "согласие с экспертами не ниже, чем у экспертов между собой", "все расхождения разобраны; проверка быстрее ручной"]],
      ["Handshake", "Что нужно от ВТБ", ["владелец процесса и куратор пилота", "регламент одной операции и типовые отказы", "50–100 обезличенных пакетов или тестовый контур", "юрист для правил; базовые цифры для расчёта эффекта"]],
    ];
    parts.forEach(([ic, head, items], i) => {
      const x = L + i * (w2 + GAP);
      card(s, x, y2, w2, h2);
      tile(s, x + 0.25, y2 + 0.16, 0.42, ic);
      T(s, head, x + 0.82, y2 + 0.16, w2 - 1.02, 0.42, { size: 14, bold: true, color: C.text2, valign: "middle" });
      bullets(s, items, x + 0.3, y2 + 0.7, w2 - 0.55, h2 - 0.78, { size: 12, psa: 3 });
    });
    const cy = y2 + h2 + 0.2, chh = BOTTOM - cy;
    s.addImage({ data: G.band, x: L, y: cy, w: W, h: chh, altText: "", objectName: "Плашка запроса" });
    tile(s, L + 0.22, cy + (chh - 0.44) / 2, 0.44, "Rocket", { fill: TINT.navyTile, icon: "light" });
    T(s, "Просим теневой пилот через Акселератор ВТБ. Метрики согласуем до старта", L + 0.86, cy, W - 1.1, chh, { size: 15, bold: true, color: C.background1, valign: "middle" });
    s.addNotes("[4:06–4:20] Предлагаем шесть недель теневого пилота на одной операции: праве подписи платежей по доверенности. «Мандат» работает рядом с сотрудниками и ни на что не влияет. Стоп-критерий один: первое ложное «да».");
  }

  // ---------- 21. Масштаб: после пилота (фокус спринта 3) ----------
  {
    const s = slide("Контент", "Питч", "ПОСЛЕ ПИЛОТА", "После пилота идут эксплуатация, новые операции и второй банк, у каждого шага свои критерии",
      `Этапы G0–G4 и критерии перехода — план внедрения в пакете аналитики. Банки, подписка и команда — базовый сценарий финансовой модели ${MODEL.version} от ${MODEL.as_of}`);
    const gates = [
      ["G0", "Финал, 11.12.2026", "у ВТБ есть владелец процесса, протокол пилота готов"],
      ["G1", "≈01.03.2027", "протокол подписан, 50–100 пакетов, юрист утвердил правила"],
      ["G2", "≈15.04.2027", "ноль ложных «да», эффект не ниже консервативного сценария"],
      ["G3", "≈01.11.2027", "анализ уязвимостей, ГОСТ-подпись через СКЗИ, адаптеры к источникам"],
      ["G4", "≈30.06.2028", "полгода эксплуатации без ложного «да», второй банк в переговорах"],
    ];
    const gw = (W - 4 * 0.2) / 5, gh = 2.05;
    s.addShape(pres.shapes.LINE, { x: L + 0.3, y: TOP + 0.33, w: W - 0.6, h: 0, line: { color: TINT.blueLine, width: 2 }, objectName: "Линия ворот" });
    gates.forEach(([id, when, what], i) => {
      const x = L + i * (gw + 0.2);
      const last = i === gates.length - 1;
      s.addShape(pres.shapes.OVAL, { x: x + 0.12, y: TOP + 0.08, w: 0.5, h: 0.5, fill: { color: last ? HEX.dk2 : HEX.accent1 }, line: { color: "FFFFFF", width: 2 }, objectName: `Ворота ${id}` });
      T(s, id, x + 0.12, TOP + 0.08, 0.5, 0.5, { size: 12, bold: true, color: C.background1, align: "center", valign: "middle" });
      card(s, x, TOP + 0.72, gw, gh - 0.72, last ? { fill: TINT.blue, line: TINT.blueLine } : {});
      T(s, when, x + 0.18, TOP + 0.84, gw - 0.36, 0.3, { size: 12, bold: true, color: C.accent1 });
      T(s, what, x + 0.18, TOP + 1.16, gw - 0.36, gh - 1.2, { size: 11.5, color: C.text1 });
    });
    const y2 = TOP + gh + GAP, h2 = BOTTOM - y2, cw = (W - 2 * GAP) / 3;
    const cols = [
      ["Gauge", "Нагрузка", [`проверка кейса — ${N.medianMs}`, "несколько процессов и машин делят хранилище Redis", "повтор запроса не пройдёт и на соседнем сервере", "готовность к трафику — /readyz"]],
      ["Workflow", "Операции", ["сначала одна: подпись платежей по МЧД", "с 2028 года — открытие счёта, смена пользователей ДБО, кредиты и гарантии", "новая операция добавляется строками каталога и тестами, ядро не меняется"]],
      ["Building2", "Банки и деньги", [`договор с ${SCALE.banksFirst} банком в ${SCALE.yearFirst} году и ${SCALE.banksLast} к ${SCALE.yearLast}`, `годовая подписка ≈${SCALE.arr} млн ₽ к ${SCALE.yearLast}`, `команда — с ${SCALE.teamFirst} до ${SCALE.teamLast} человек, операционная прибыль с ${SCALE.ebitdaYear} года`, `до неё нужно ≈${SCALE.funding} млн ₽`]],
    ];
    cols.forEach(([ic, head, items], i) => {
      const x = L + i * (cw + GAP);
      card(s, x, y2, cw, h2);
      tile(s, x + 0.22, y2 + 0.2, 0.46, ic);
      T(s, head, x + 0.82, y2 + 0.2, cw - 1.0, 0.46, { size: 14, bold: true, color: C.text2, valign: "middle" });
      bullets(s, items, x + 0.26, y2 + 0.8, cw - 0.48, h2 - 0.88, { size: 12, psa: 4 });
    });
    s.addNotes(`[4:20–4:32] Как растём после пилота. Дальше эксплуатация и новые операции: открытие счёта, смена пользователей, кредиты. По модели к ${SCALE.yearLast} году ${SCALE.banksLast} ${plural(SCALE.banksLast, "банк", "банка", "банков")} с договором. Каждый шаг открывают ворота с критериями.`);
  }

  // ---------- 22. Видение: сотрудник, клиент, ИИ-агент ----------
  {
    const s = slide("Контент", "Питч", "БАНК БУДУЩЕГО", "Дальше тот же движок отвечает сотруднику, клиенту и ИИ-агенту",
      "AP2 (Agent Payments Protocol) — Google, 16.09.2025; European Business Wallet — предложение Еврокомиссии, 19.11.2025. Как устроено удостоверение — слайд A8");
    const cols = [
      ["UserCheck", "Сотрудник банка", "работает сегодня", HEX.accent3, ["вердикт на дату с нормой и полем документа", "досье спорного кейса с приоритетом и сроком", "журнал и удостоверение со статусом"]],
      ["Building2", "Клиент в «ВТБ Бизнес»", "после пилота", HEX.accent1, ["«Мои полномочия»: кто и до какого дня действует за компанию", "тревога при смене директора в ЕГРЮЛ — до первого платежа", "проверка подписанта контрагента перед сделкой"]],
      ["Bot", "ИИ-агент клиента", "2027–2028", HEX.dk2, ["агент действует от имени человека, а тот — от имени компании", "банк проверяет цепочку и пределы: операция, сумма, получатель, срок", "отзыв по событию, как у мандатов AP2"]],
    ];
    // Слева три шага по-прежнему, справа — кадр: Анна у голограммы, где много источников сходятся в один ответ.
    const pxL = 9.4, pw = R - pxL;
    const VISION = await roundedPhoto(path.join(VIS, "anna-vision.jpg"), pw, BOTTOM - TOP, 0.12, HEX.lt2, { position: "top" });
    const gap = 0.25, lw = pxL - L - 0.3, cw = (lw - 2 * gap) / 3, ch = 3.6;
    cols.forEach(([ic, head, stage, col, items], i) => {
      const x = L + i * (cw + gap);
      const hl = i === 2;
      card(s, x, TOP, cw, ch, hl ? { fill: C.text2, line: null } : {});
      tile(s, x + 0.22, TOP + 0.22, 0.5, ic, hl ? { fill: TINT.navyTile, icon: "light" } : {});
      const sw = stage.length * 0.075 + 0.34;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x + 0.86, y: TOP + 0.33, w: sw, h: 0.28, rectRadius: 0.14, fill: { color: hl ? TINT.navyTile : TINT.blue }, line: { type: "none" }, objectName: `Этап: ${stage}` });
      T(s, stage, x + 0.86, TOP + 0.33, sw, 0.28, { size: 10, bold: true, color: hl ? HEX.accent2 : col, align: "center", valign: "middle" });
      T(s, head, x + 0.22, TOP + 0.86, cw - 0.4, 0.5, { size: 14, bold: true, color: hl ? C.background1 : C.text2 });
      bullets(s, items, x + 0.22, TOP + 1.45, cw - 0.36, ch - 1.5, { size: 12, psa: 6, color: hl ? C.background1 : C.text1 });
      if (i < 2) arrow(s, x + cw + 0.03, TOP + ch / 2, gap - 0.06, HEX.accent1, 2);
    });
    band(s, L, TOP + ch + GAP, lw, BOTTOM - (TOP + ch + GAP), "Основа для этого уже есть: удостоверение «Мандата» привязано к операции, сумме, получателю и одноразовому номеру запроса и отзывается по событию", { icon: "BadgeCheck", size: 13 });
    card(s, pxL, TOP, pw, BOTTOM - TOP, { line: null, r: 0.12 });
    s.addImage({ data: VISION, x: pxL, y: TOP, w: pw, h: BOTTOM - TOP, altText: "Анна у голограммы: много источников сходятся в один проверенный ответ", objectName: "Фото амбассадора" });
    s.addNotes("[4:32–4:42] Дальше один движок полномочий для троих. Сотрудник видит ответ сегодня. Клиент в «ВТБ Бизнес» увидит, кто вправе действовать за компанию. А ИИ-агент будет платить по мандату с пределами.");
  }

  // ---------- 23. Команда ----------
  {
    const s = slide("Контент", "Питч", "КОМАНДА", "В команде двое: один отвечает за архитектуру и правила, другая за измерение качества", "github.com/KonkovDV/Mandat · собственный код под лицензией MIT");
    // Два круга: квадрат по лицу из исходного кадра (box — в точках файла), неоновая рамка исходника в круг не попадает.
    const people = [
      ["dmitrij.jpg", { x: 64, y: 50, side: 460 }, "Waypoints", "Коньков Дмитрий", "Капитан · продукт, архитектура и ИИ", "Постановка задач и приёмка. Детерминированное ядро, граф полномочий, модель угроз; автор AeroBIM и SynAPS"],
      ["elena.jpg", { x: 72, y: 56, side: 460 }, "Gauge", "Конькова Елена", "Системный инженер · аналитика и качество", "Методика оценки, корпус и разметка, проверка сборок и документов, протокол пилота"],
    ];
    const colW = 3.05, d = 2.3, ring = 0.1;
    for (const [i, [img, box, ic, name, role, desc]] of people.entries()) {
      const cx = L + colW / 2 + i * colW, cy = TOP + 0.42 + d / 2;
      s.addImage({ data: await squarePhoto(path.join(ASSETS, img), box), x: cx - d / 2, y: cy - d / 2, w: d, h: d, rounding: true, altText: `Фото: ${name}` });
      const bd = 0.56, bx = cx + d * 0.36 - bd / 2, by = cy + d * 0.36 - bd / 2;
      s.addShape(pres.shapes.OVAL, { x: bx - 0.05, y: by - 0.05, w: bd + 0.1, h: bd + 0.1, fill: { color: "FFFFFF" }, line: { type: "none" }, objectName: "Обводка значка" });
      dot(s, bx, by, bd, HEX.accent1, ic);
      const ty = cy + d / 2 + ring + 0.2;
      T(s, name, cx - colW / 2 + 0.05, ty, colW - 0.1, 0.4, { size: 18, bold: true, color: C.text2, align: "center" });
      T(s, role, cx - colW / 2 + 0.05, ty + 0.42, colW - 0.1, 0.48, { size: 12, bold: true, color: C.accent1, align: "center" });
      T(s, desc, cx - colW / 2 + 0.1, ty + 0.92, colW - 0.2, 0.9, { size: 11.5, color: C.text1, align: "center" });
    }
    // Справа — что банк получает и кого мы ищем; 3D-значки из того же набора, что на слайде 2.
    const x2 = L + 2 * colW + 0.35, w2 = R - x2, h2 = (BOTTOM - TOP - GAP) / 2;
    [
      [TOP, "criteria", "Кто закрывает роли", "продукт, разработку и ИИ ведёт Дмитрий; аналитику и качество — Елена; безопасность делим: модель угроз у Дмитрия, проверка сборок у Елены"],
      [TOP + h2 + GAP, "search", "Кого ищем в ВТБ", "владельца процесса добавления представителей юрлиц и юриста, который утвердит правила и окно «свежей» смены руководителя"],
    ].forEach(([y, art, head, text]) => {
      card(s, x2, y, w2, h2, { fill: TINT.blue, line: TINT.blueLine, shadow: false });
      s.addImage({ data: ICON3D[art], x: x2 + w2 - 1.32, y: y + 0.18, w: 1.12, h: 1.12, altText: `Значок: ${head.toLowerCase()}`, objectName: "3D-значок" });
      T(s, head, x2 + 0.32, y + 0.3, w2 - 1.8, 0.45, { size: 17, bold: true, color: C.text2, valign: "middle" });
      T(s, text, x2 + 0.32, y + 0.88, w2 - 1.8, h2 - 1.05, { size: 14, color: C.text1 });
    });
    s.addNotes("[4:42–4:52] Нас двое. Я отвечаю за архитектуру и правила, Елена — за методику измерения и качество. Юриста банка ждём на пилот.");
  }

  // ---------- 24. Контакты ----------
  {
    const s = pres.addSlide({ masterName: "Финал", sectionTitle: "Питч" });
    s.addImage({ data: ANNA.final.data, x: ANNA.final.x, y: 0, w: ANNA.final.w, h: ANNA.final.h, altText: "Анна, амбассадор «Мандата», с планшетом", objectName: "Фото амбассадора" });
    s.addImage({ data: G.lockupDark, x: L, y: 0.66, w: 2.4, h: 0.48, altText: "Логотип «Мандата»", objectName: "Логотип" });
    s.addText(typo("Спасибо! Готовы к теневому пилоту в ВТБ"), { placeholder: "title" });
    const rows = [
      ["Phone", "Коньков Дмитрий, капитан", "+7 915 452-88-64 · konkov-dmitrij@yandex.ru"],
      ["Mail", "Конькова Елена, измерение и качество", "konkovaelena@yandex.ru"],
      ["Github", "Код, документы и сайт", "github.com/KonkovDV/Mandat · konkovdv.github.io/Mandat"],
    ];
    rows.forEach(([ic, who, t], i) => {
      const y = 3.0 + i * 0.72;
      tile(s, L, y + 0.06, 0.46, ic, { fill: TINT.navyTile, icon: "light" });
      T(s, who, L + 0.65, y, 5.75, 0.3, { size: 14, bold: true, color: C.background1, valign: "middle" });
      T(s, t, L + 0.65, y + 0.3, 5.75, 0.3, { size: 13, color: TINT.ice, valign: "middle" });
    });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: L, y: 5.35, w: 6.25, h: 0.95, rectRadius: 0.12, fill: { color: "FFFFFF", transparency: 90 }, line: { color: HEX.accent2, width: 1.5 }, objectName: "Плашка просьбы" });
    T(s, "Ищем в ВТБ владельца процесса добавления представителей юрлиц — чтобы согласовать протокол пилота до финала 11 декабря", L + 0.3, 5.35, 5.65, 0.95, { size: 14, bold: true, color: C.background1, valign: "middle" });
    // Два QR-кода столбиком между текстом и фото: сайт с демо в браузере, код и документы на GitHub.
    const qw = 1.5, qx = 7.1;
    [[G.qrSite, "Сайт: демо в браузере", "QR-код: konkovdv.github.io/Mandat"], [G.qr, "Код и документы", "QR-код: github.com/KonkovDV/Mandat"]].forEach(([img, label, alt], i) => {
      const qy = 2.95 + i * 2.02;
      card(s, qx, qy, qw, 1.9, { fill: "FFFFFF", line: null, r: 0.14, name: "Карточка QR-кода" });
      s.addImage({ data: img, x: qx + 0.15, y: qy + 0.12, w: qw - 0.3, h: qw - 0.3, altText: alt });
      T(s, label, qx + 0.06, qy + 1.36, qw - 0.12, 0.46, { size: 9.5, bold: true, color: C.text2, align: "center", valign: "middle" });
    });
    s.addNotes("[4:52–5:00] Спасибо. Один QR-код ведёт на сайт с демо, второй на код. Готовы к вопросам.");
  }
  if (pages() !== MAIN_SLIDES) throw new Error(`в питче ${pages()} слайдов, а ожидалось ${MAIN_SLIDES}`);
  for (const [key, kicker] of Object.entries(P_KICKER)) {
    if (KICKER_AT[kicker] !== P[key]) throw new Error(`слайд «${kicker}»: номер ${KICKER_AT[kicker]}, а ссылки ведут на ${P[key]}`);
  }

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
      // Матовое стекло: фон из Figma ярок справа, без плотной подложки строки там не читаются.
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: cw, h: rh - 0.08, rectRadius: 0.1, fill: { color: "071D52", transparency: 18 }, line: { color: "FFFFFF", transparency: 72, width: 0.75 }, objectName: `Пункт оглавления ${id}` });
      T(s, id, x + 0.2, y, 0.6, rh - 0.08, { size: 13.5, bold: true, color: HEX.accent2, valign: "middle", name: `Номер ${id}` });
      T(s, title, x + 0.82, y, cw - 1.75, rh - 0.08, { size: 13.5, color: "FFFFFF", valign: "middle", name: `Пункт ${id}` });
      T(s, `с. ${appxPage(id)} →`, x + cw - 1.0, y, 0.85, rh - 0.08, { size: 11, color: TINT.ice, align: "right", valign: "middle" });
      link(s, x, y, cw, rh - 0.08, appxPage(id), `${id} ${title}`);
    });
  }

  // ---------- A1. Семь взглядов ----------
  {
    const s = slide("Контент", "Приложение", "A1 · ВОЗРАЖЕНИЯ", "Семь ролей в банке, главное возражение каждой и наш ответ",
      "Вопросы жюри и ответы — docs/JURY.md; числа — CLAIMS_REGISTRY.md с командой воспроизведения", { appx: "A1" });
    const roles = [
      ["Briefcase", "Банкир", "«Сколько экономит и не вырастет ли очередь?»", `Базовый сценарий: ${FTE[1]} ставок и ${LOSS[1]} млн ₽ ущерба в год, окупаемость — ${PAYBACK}. У очереди разбора приоритеты P1–P3 и сроки; порог доли «на разбор» — критерий пилота`],
      ["Scale", "Юрист банка", "«Кто отвечает за норму в правиле?»", "«Утверждено» — только по подписанной записи юриста банка (C-20). До неё строгий профиль не выдаёт по черновику ни «да», ни «нет»"],
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
    T(s, "Где подробнее", x + 0.72, y + 0.2, cw - 0.9, 0.42, { size: 13.5, bold: true, color: C.background1, valign: "middle" });
    T(s, "Трудные вопросы и ответы — в docs/JURY.md, границы продукта — в LIMITS.md. Числа — только из реестра утверждений с командой воспроизведения", x + 0.2, y + 0.75, cw - 0.4, ch - 0.85, { size: 11.5, color: TINT.ice });
  }

  // ---------- A2. Критерии спринта 2 ----------
  {
    const s = slide("Контент", "Приложение", "A2 · КРИТЕРИИ", "Где в проекте ответ на каждый критерий спринта 2",
      "Критерии и фокус спринта 2 («довести MVP, посчитать финансовую модель, показать защиту данных») — fintechacademy.ru/task-1. Подробные ответы — submission/README.md; C-xx и M-xx — строки реестра утверждений CLAIMS_REGISTRY.md", { appx: "A2" });
    table(s, ["Критерий", "Баллы", "Что показываем", "Где в колоде", "Чем проверить"], [
      ["Данные, безопасность и комплаенс", "0–18", "ядро без внешней сети; ПДн — метками до модели; отчёты зашифрованы, основания в журнале — отпечатком; чек-лист воркшопа 20.11 в коде: копии, инцидент 24 и 72 ч, ревизия доступа; подпись юриста; SBOM", `слайды ${P.sprint}, ${P.ai}, ${P.data}; A8, A10`, "C-09, C-12, C-20, C-24, C-25; docs/PILOT_CHECKLIST.md"],
      ["Техническая реализация, автоматизация и масштабирование", "0–22", `${RULES} и четыре вердикта; CLI и HTTP API с ролями; допуск для ДБО, в том числе по OpenID AuthZEN 1.0; МЧД нескольких подписантов; воркеры и машины с общим Redis; ${TESTS}`, `слайды ${P.product}, ${P.showcase}, ${P.quality}, ${P.embed}; A3, A9`, "evidence.json; mandat showcase; C-13, C-14, C-18, C-22, C-23, C-26"],
      ["Оценка ценности: unit-экономика или эффект и ROI", "0–22", `≈${UNIT.mandat} ₽ на проверку против ≈${UNIT.manual} ₽ вручную; эффект банка и окупаемость для ВТБ — ${PAYBACK}; три сценария, NPV и IRR`, `слайды ${P.sprint}, ${P.effect}; A12`, "пакет аналитики; effect_model.py --unit; C-15, C-16, C-21"],
      ["Зрелость решения и прогресс по доработке продукта", "0–16", "за спринт 2: подпись юриста, карточки норм, допуск ДБО и OpenID AuthZEN, захват до платежа, шифрование хранилища, чек-лист воркшопа в коде, тесты на Windows", `слайд ${P.sprint}; A18`, "CHANGELOG.md; submission/FORM_SPRINT2.md"],
      ["Подтверждённость проблемы, ценности и рынка", "0–12", "данные Банка России за I–III кварталы 2026; случай «ЭОС» и дело ВС РФ; FATF и ЕС; рынок двумя способами; открытый код конкурентов", `слайды ${P.cost}, ${P.takeover}, ${P.now}; A5, A13`, "M-07, M-15, M-52, M-12, M-13; пакет аналитики; docs/COMPETITORS.md"],
      ["Реалистичность пилота и готовность к внедрению", "0–10", `теневой пилот 6 недель; ворота G0–G4; порты к реестрам и СКЗИ; пакет для юриста; матрица готовности: ${READY.ok} из ${READY.all} пунктов проверены тестами`, `слайды ${P.embed}, ${P.pilot}, ${P.scale}; A18`, "docs/PILOT.md; docs/PILOT_CHECKLIST.md; docs/legal/README.md"],
    ], L, TOP, W, [2.75, 0.7, 3.95, 1.75, 2.983], { size: 10.5, headH: 0.42, rowH: 0.74 });
  }

  // ---------- A3. Прослеживаемость чисел ----------
  {
    const s = slide("Контент", "Приложение", "A3 · ПРОСЛЕЖИВАЕМОСТЬ", "У каждого числа на слайдах есть команда воспроизведения или источник",
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
      [`${AVG_Q3}; ${Math.round(CBR.q3.lossMln)} млн ₽`, "потери юрлиц в III кв. 2026", "обзор Банка России, таблица по юрлицам", "M-52"],
      ["более 5 млн МЧД; ×3", "реестр МЧД ФНС и рост за два года", "nalog.gov.ru, 27.07.2026; emchd.ru, 10.2024", "M-08, M-10"],
    ], L, TOP, W, [2.7, 3.6, 4.6, 1.233], { size: 11, headH: 0.4, rowH: 0.415 });
  }

  // ---------- A4. Условия трека ----------
  {
    const s = slide("Контент", "Приложение", "A4 · УСЛОВИЯ ТРЕКА", "Что требуют условия трека и где это сделано",
      "Условия — fintechacademy.ru/task-1; ответы — README.md, docs/SPRINT2.md и LIMITS.md. Оценку соответствия 152-ФЗ и требованиям ИБ проводит банк", { appx: "A4" });
    table(s, ["Условие трека", "Что сделано", "Где посмотреть"], [
      ["Российский рынок и регулирование, 152-ФЗ и 115-ФЗ", "у каждого правила норма ГК РФ или приказа о МЧД; ФИО и номера заменяются метками до модели; идентификацию по 115-ФЗ ведёт банк", `слайд ${P.data}; A7, A17`],
      ["Работа в контуре банка без открытой сети", "ядро не делает внешних запросов (C-12); Docker и пилотный compose с проверкой готовности", `слайды ${P.embed} и ${P.data}`],
      ["Генеративный ИИ на российских или своих моделях", "модель включает банк, она стоит в его контуре; вердикт модель не меняет", `слайд ${P.ai}`],
      ["Готовность к интеграции с системами банка", "HTTP API с ролями; допуск операции для ДБО по OpenID AuthZEN 1.0 (C-23); порты к реестру МЧД, ЕФРСБ и СКЗИ", `слайды ${P.showcase} и ${P.embed}`],
      ["Open source и предпочтение российскому ПО", "код открыт под лицензией MIT, у ядра две открытые зависимости; облачных сервисов продукт не вызывает; криптография ГОСТ — через СКЗИ банка", `слайд ${P.embed}; A14`],
      ["Понимание масштабирования", `проверка кейса — ${N.medianMs}; несколько воркеров и машин работают с общим хранилищем Redis`, `слайд ${P.scale}`],
      ["Работающий MVP, подтверждённый данными", `демо одной командой и в браузере; ${N.cases} проверочных кейса; числа — в evidence.json`, `слайды ${P.product}–${P.quality}`],
      ["Финансовая модель, окупаемость, план развития", "три сценария, затраты и эффект банка, окупаемость; ворота от пилота до второго банка", `слайды ${P.effect} и ${P.scale}; A12`],
    ], L, TOP, W, [3.4, 6.2, 2.533], { size: 10.5, rowH: 0.52, headH: 0.34, name: "Условия трека" });
  }

  // ---------- A5. Рынок ----------
  {
    const s = slide("Контент", "Приложение", "A5 · РЫНОК", "Реестр, ЭДО и сервисы данных говорят, действует ли МЧД и кто компания. «Мандат» говорит, можно ли эту операцию сегодня",
      "«—» — нет в открытых описаниях продукта на 22.11.2026. Источники: vtb.ru/malyj-biznes/ukep/mchd; i.vkontur.ru/news/3157; developer.kontur.ru; описания Контур.Фокуса и СПАРК; astral.ru/corporate/api-mchd и m4d.mig24.ru/help/api (M-55); Signicat и UC (M-37, M-53); docs/COMPETITORS.md", { appx: "A5" });
    table(s, ["Что сравниваем", "Сервис МЧД «ВТБ Бизнес»", "ЭДО: Контур.Диадок, 1С-ЭДО", "Данные: Контур.Фокус, СПАРК", "Реестр ФНС и API МЧД: Астрал, МИГ24", "«Мандат»"], [
      ["Объект проверки", "выпуск исходящих МЧД", "даты МЧД и сертификат под документом", "досье и изменения компании", "действует ли МЧД и что в ней", "операция банка на дату"],
      ["Полномочие на операцию", "—", "«Диадок не может» (Контур); в БСП 1С — на приложении", "—", "список полномочий из МЧД", `${RULES} с нормой`],
      ["Передоверие", "признак передоверия", "—", "—", "сведения о цепочке", "сроки, сужение, каскад отмены"],
      ["ЕГРЮЛ на дату и устав", "—", "—", "история ЕГРЮЛ, без устава", "—", "в одной проверке"],
      ["Смена директора", "—", "—", "уведомление об изменении", "—", "сигнал или разбор; отзыв удостоверений"],
      ["Доказательство и аудит", "—", "—", "выписки", "статус, XML и PDF МЧД", "XPath, ГРН, норма; журнал; удостоверение"],
    ], L, TOP + 0.1, W, [1.85, 1.9, 2.05, 2.0, 2.0, 2.333], { size: 11, highlight: 5, headH: 0.55, rowH: 0.52 });
    band(s, L, 5.95, W, 0.75, "В Нидерландах и Швеции проверка полномочия — уже отдельный продукт (Signicat, UC). Мы не конкурируем с выпуском МЧД, API реестра и данными о компаниях — берём их на вход.", { icon: "Puzzle", size: 13, fill: TINT.blue, color: C.text2, tile: "FFFFFF", shadow: false });
  }

  // ---------- A6. Правила ----------
  {
    const s = slide("Контент", "Приложение", "A6 · ПРАВИЛА", `Что ловят ${RULES}, у каждого есть ссылка на норму`,
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
    const s = slide("Контент", "Приложение", "A8 · ДОВЕРИЕ БЕЗ БАЗЫ", "Банк проверяет удостоверение, статус и журнал без доступа к базе «Мандата»",
      "Стандарты: W3C Verifiable Credentials 2.0 и Bitstring Status List v1.0 (Рекомендации W3C, 15.05.2025); RFC 6962 / RFC 9162. На стенде подпись Ed25519, к пилоту — ГОСТ через СКЗИ банка", { appx: "A8" });
    const cols = [
      ["BadgeCheck", "Удостоверение", ["вердикт, операция, хеш набора правил и входов", "номера МЧД цепочки", "без ФИО: представитель — HMAC-токен ИНН", "привязка к сумме, получателю и одноразовому nonce"]],
      ["Bell", "Статус", ["списки «отозвано» и «приостановлено»", "мониторинг меняет статус по событию или сроку МЧД", "проверяющий видит статус сам и отвергает подложенный старый список"]],
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
    const s = slide("Контент", "Приложение", "A9 · ИЗМЕРЕНИЕ", "Как мы измеряем качество и как пилот перенесёт эти метрики на данные ВТБ",
      "Источники: evidence.json, docs/EVALUATION.md, tools/experiments/jcs_differential; Brown, Cai, DasGupta (2001) — интервал Вилсона; Geifman, El-Yaniv (2017) — «риск — покрытие»", { appx: "A9" });
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
    const s = slide("Контент", "Приложение", "A10 · ИНФОРМАЦИОННАЯ БЕЗОПАСНОСТЬ", "Данные, модель и решения остаются в контуре банка",
      "Проектные решения «Мандата»; оценку соответствия проводят ИБ и юристы банка в пилоте. Меры по 821-П, 851-П и ГОСТ Р 57580.1 сопоставлены в docs/REGULATORY.md; docs/DATA_PROTECTION.md, docs/THREAT_MODEL.md, docs/PILOT_CHECKLIST.md", { appx: "A10" });
    const tiles = [
      ["Server", "On-prem без сети", "ядро не делает внешних запросов; модель — только хосты из белого списка, по умолчанию выключена"],
      ["Fingerprint", "Персональные данные", "в модель — только строки о полномочиях, номера — метками; отчёты зашифрованы AES-256-GCM; чтение и удаление — в журнале, основания — отпечатком; удалённое не возвращается из копии"],
      ["FileX", "Вредоносные файлы", "защита от XML-бомб и внешних сущностей, строгий JSON на всех внешних входах, лимиты размера и глубины"],
      ["KeyRound", "API и роли", "оператор, рецензент, аудитор и учётная запись ДБО: допуск к платежу — только ей; чужой кейс не виден; токены — хеши; ревизия доступа по сроку и владельцу"],
      ["Scale", "152-ФЗ и 115-ФЗ", "спроектировано с учётом 152-ФЗ; отчёт об инциденте — 24 и 72 часа от выявления; идентификацию по 115-ФЗ не подменяем — она за каналами банка"],
      ["Container", "Подпись и сборка", "КЭП — через СКЗИ банка, ключ — HSM, Vault или KMS; правило утверждает только подпись юриста; pip-audit, Trivy и gitleaks — в CI и по расписанию, CodeQL, фаззинг входа, подписанный релиз"],
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
    const s = slide("Контент", "Приложение", "A11 · РИСКИ", "Риски, которые банк увидит первыми, и что мы с ними делаем",
      "LIMITS.md — полный список границ; docs/AI_RISKS.md — матрица рисков ИИ; docs/THREAT_MODEL.md — модель угроз", { appx: "A11" });
    table(s, ["Риск", "Как снимаем", "Чем контролируем"], [
      ["Ложное подтверждение", "неизвестное не становится «да», обязательные группы проверок", "0 ложных «подтверждено» — стоп-критерий"],
      ["Удостоверение на чужую дату или компанию", "сверка дня по Москве и ИНН организации; срок не длиннее 30 дней и сроков цепочки", "тесты привязки к запросу; Red Team допуска"],
      ["Ошибка в норме", "норма у каждого правила; «утверждено» — только по подписанной записи юриста (C-20)", "доля правил, утверждённых юристом"],
      ["Ошибка модели", "нет цитаты — нет факта; вердикт модель не меняет", "тест «модель не меняет вердикт»"],
      ["Формат МЧД изменится", "теги — в файле соответствия, формы ЦБ по 315-ФЗ — через адаптер", "пересборка примеров одной командой"],
      ["Нагрузка «на разбор»", "сигнал вместо разбора там, где закон позволяет", "доля «на разбор» с порогом банка"],
      ["Интеграция с ДБО", "на пилоте — теневой режим без изменений в ДБО; контракт допуска операции готов", "сценарий пилота без доработок ДБО"],
      ["Утечка отчётов с ПДн", "отчёты зашифрованы и привязаны к владельцу; каждое чтение в журнале; удаление по 152-ФЗ", "выгрузка журнала чтений"],
    ], L, TOP, W, [3.0, 5.4, 3.733], { size: 12, headH: 0.42, rowH: 0.55 });
  }

  // ---------- A12. Расчёт эффекта ----------
  {
    const s = slide("Контент", "Приложение", "A12 · ЭКОНОМИКА", "Как посчитан эффект: формулы, данные Банка России и мировые ориентиры",
      "Источники: Банк России, I–III кв. 2026 (M-07, M-15, M-52); ВТБ, пресс-релиз к МСФО за 1П 2026 (M-09); Fenergo — отраслевые опросы 2022 и 2025 гг. (M-11); Companies House, 30.06.2025 (M-36); финансовая модель пакета аналитики, C-21", { appx: "A12" });
    const cw = (W - GAP) / 2, ch = 3.55;
    card(s, L, TOP, cw, ch);
    tile(s, L + 0.25, TOP + 0.22, 0.46, "Coins");
    T(s, "Формулы «Мандата»", L + 0.85, TOP + 0.22, cw - 1.1, 0.46, { size: 15, bold: true, color: C.text2, valign: "middle" });
    bullets(s, [
      `Ставки = клиенты × доля с событием представительства × минуты на кейс (с разбором) ÷ 60 ÷ ${ruNum(EFFECT.hoursPerFte)} ч`,
      `Ущерб = ${EFFECT.incidentsPerQuarter} × 4 × доля ВТБ × доля случаев с полномочиями × ${comma(EFFECT.lossPerIncidentMln, 2)} млн ₽`,
      `База — I–III кварталы 2026: ${ruNum(CBR.m9.ops)} операций против юрлиц на ${comma(CBR.m9.lossMln / 1000, 1)} млрд ₽ (Банк России)`,
      `Юнит-экономика: вручную — ≈${UNIT.manual} ₽, затраты банка на «Мандат» — ≈${UNIT.mandat} ₽ на проверку (effect_model.py --unit)`,
      "Доли и минуты калибруются в первые две недели пилота; расчёт — python scripts/effect_model.py --scenarios",
    ], L + 0.3, TOP + 0.9, cw - 0.55, ch - 1.0, { size: 12, psa: 5 });
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
    const s = slide("Контент", "Приложение", "A13 · РЕАЛЬНЫЕ КЕЙСЫ", "Реальные случаи, где ошибка в полномочиях стоила денег, и что сделал бы «Мандат»",
      "M-15, M-17, M-35–M-38, M-52, M-53 реестра утверждений: Банк России; Forbes и АБН по данным РБК; ВС РФ; Companies House; документация Signicat; Google AP2", { appx: "A13" });
    table(s, ["Кейс", "Что произошло", "Урок для банка", "Что делает «Мандат»"], [
      ["«ЭОС», Россия, октябрь 2026", "09.10 в ЕГРЮЛ внесли неизвестного компании гендиректора; по версии компании — по поддельным документам; 19.10 прежние руководители восстановлены", "захват начинается в реестре, а заканчивается в банке", "свежая смена — «на разбор»; событие ЕГРЮЛ приостанавливает удостоверения"],
      ["ВС РФ, 2023, № 308-ЭС22-17199", "банк отказал в переводе 6,5 млн ₽ по поручению действующего руководителя; суд обязал провести операцию, ВС РФ оставил это в силе", "ошибка «нет» тоже стоит банку денег", "проверка на дату: законный подписант получает «да» с доказательством"],
      ["Банк России, III кв. 2026", `${CBR.q3.ops} ${plural(CBR.q3.ops, "операция", "операции", "операций")} без согласия у юрлиц на ${Math.round(CBR.q3.lossMln)} млн ₽; возмещено ${comma(CBR.q3.refundAccounts, 1)}% по счетам и ${comma(CBR.q3.refundSbp, 0)}% через СБП`, "потери бизнеса крупные и почти не возвращаются", "предотвращение до операции: неизвестное не становится «да»"],
      ["Companies House, Великобритания", "по закону ECCTA 2023 за год до 03.2025 отклонено 10 200 подозрительных заявлений; с 18.11.2025 — проверка личности директоров до регистрации", "реестр закрывает вход, но не операцию в банке", "банк проверяет полномочие на дату операции"],
      ["Signicat, Нидерланды и Швеция", "KvK и iDIN возвращают роль, мандат и его ограничения; по реестру Bolagsverket — кто может подписать и в каких сочетаниях", "проверка полномочия — уже отдельный продукт", "то же для МЧД, ЕГРЮЛ и устава — с нормой и журналом"],
      ["Google AP2, 2025–2026", "платёж ИИ-агента опирается на подписанные мандаты с пределами пользователя", "агенту нужен проверяемый мандат", "удостоверение привязано к операции, сумме, получателю и nonce"],
    ], L, TOP, W, [2.45, 4.25, 2.55, 2.883], { size: 10.5, headH: 0.4, rowH: 0.74 });
  }

  // ---------- A14. Стандарты и SOTA 2026 ----------
  {
    const s = slide("Контент", "Приложение", "A14 · СТАНДАРТЫ И ПРАКТИКИ 2026", "Открытые стандарты и практики 2025–2026 в основе «Мандата»",
      "W3C, 15.05.2025; w3c/vc-di-eddsa; RFC 9162, RFC 3161; Sigstore; CNCF; standardwebhooks.com; OpenID AuthZEN Authorization API 1.0, 12.01.2026 (C-23); OWASP Top 10 for LLM Applications 2025; SLSA, CycloneDX, OpenSSF Scorecard. docs/SOTA.md", { appx: "A14" });
    const std = [
      ["BadgeCheck", "W3C VC 2.0 и Bitstring Status List", "удостоверение проверки со статусом «действует / приостановлено / отозвано» (Рекомендации W3C, май 2025)"],
      ["Signature", "eddsa-jcs-2022", "каноническая запись, хеш и подпись совпадают с эталонными векторами W3C"],
      ["Layers", "RFC 9162 и внешний якорь", "журнал с доказательством одной записи; хеш сегмента — под метку RFC 3161 или в Sigstore Rekor"],
      ["GitCompare", "Cedar · CNCF", "вторая независимая реализация передоверия; символьная проверка Cedar SymCC — в плане"],
      ["Webhook", "Standard Webhooks", "подписанные события о полномочиях для антифрода банка"],
      ["Plug", "OpenID AuthZEN 1.0", "ДБО спрашивает допуск по финальной спецификации от 12.01.2026; отказ — ответ 200 с причинами"],
      ["ShieldAlert", "OWASP LLM Top 10 2025", "риски советника-ИИ разобраны: шлюз цитат, данные в маркерах, без инструментов"],
      ["Container", "SLSA, SBOM, Scorecard", "подпись Sigstore и происхождение SLSA у релиза, CycloneDX SBOM, закреплённые версии"],
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
      [`Откуда ${LOSS[0]}–${LOSS[2]} млн ₽?`, "База — хищения у юрлиц по данным Банка России за I–III кварталы 2026; три явных параметра калибрует пилот"],
      ["Что работает сейчас, а что подключается в пилоте?", "Работают ядро, правила, журнал, удостоверения, мониторинг, API с ролями и допуск операции для ДБО. В пилоте — адаптеры банка к готовым портам реестра МЧД, ЕФРСБ и СКЗИ, для ЕГРЮЛ — новый порт по тому же образцу"],
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
      ["Есть ли юрлицо для пилота?", "К пилоту регистрируем компанию или идём через партнёра-интегратора — как удобнее Акселератору ВТБ"],
      ["С НДС ли посчитана окупаемость?", "Да. НДС 22 % на внедрение и сопровождение банк не берёт к вычету (п. 5 ст. 170 НК РФ) — он в затратах. Лицензия из реестра ПО — без НДС (пп. 26 п. 2 ст. 149 НК РФ)"],
    ],
  ];
  hard.forEach((rows, i) => {
    const id = i ? "A16" : "A15";
    const s = slide("Контент", "Приложение", `${id} · ТРУДНЫЕ ВОПРОСЫ`, `Трудные вопросы жюри (${i + 1}/2)`,
      "Ответы опираются на CLAIMS_REGISTRY.md и LIMITS.md: каждое число — с командой воспроизведения и границей", { appx: id });
    // Девять вопросов на слайде помещаются при кегле 10,5 и строке 0,5″; восемь — как раньше.
    const dense = rows.length > 8;
    table(s, ["Вопрос", "Ответ"], rows, L, TOP, W, [4.0, 8.133], { size: dense ? 10.5 : 11, headH: 0.4, rowH: dense ? 0.5 : 0.56 });
  });

  // ---------- A17. Нормативная база ----------
  {
    const s = slide("Контент", "Приложение", "A17 · НОРМАТИВНАЯ БАЗА", "Какие нормы действуют в ноябре 2026 года и какие вступят",
      "Ревизия 17.11.2026: 315-ФЗ, 253-ФЗ, 243-ФЗ и приказы № 856, 857, 1001 — по publication.pravo.gov.ru; перед пилотом строки сверяет юрист банка. docs/REGULATORY.md, ADR-010; M-01–M-04, M-16, M-40, M-41, M-43–M-45 реестра утверждений", { appx: "A17" });
    table(s, ["Акт", "С какой даты", "Что меняет"], [
      ["ГК РФ, ст. 847", "действует", "банк исполняет распоряжение лица, чьи права удостоверены документами: полномочие проверяется до операции"],
      ["63-ФЗ в ред. 476-ФЗ", "01.09.2024", "представитель юрлица подписывает своей КЭП вместе с МЧД"],
      ["ГК РФ, ст. 185–189", "действует", "срок по умолчанию — год; передоверие; закрытый перечень прекращения; каскад"],
      ["127-ФЗ; подп. 7 п. 1 ст. 188 ГК РФ", "действует", "конкурсное производство и внешнее управление прекращают МЧД"],
      ["115-ФЗ, ст. 7", "действует", "идентификация представителя и документ-основание: номер, дата, срок"],
      ["Приказ Банка России № ОД-2506", "01.01.2026", "12 признаков операций без согласия вместо 6; признак из ГИС — с 01.03.2026"],
      ["Приказ Минцифры № 1001", "01.02.2026", "представитель — СНИЛС и ИНН, паспорт по желанию; полномочие — код и наименование"],
      ["Реестр МЧД ФНС", "01.03.2026", "поиск МЧД по номеру, ИНН доверителя и ИНН представителя"],
      ["41-ФЗ, ГИС «Антифрод»", "01.03.2026", "обмен сведениями о мошенничестве; события «Мандата» — в антифрод банка"],
      ["243-ФЗ о технологиях ИИ", "01.09.2026; ст. 5 — 01.03.2027", "национальные модели — по решению Правительства, для банков — с Банком России"],
      ["210-ФЗ, антифрод-пакет", "поэтапно с 01.09.2026", "защита и возмещение клиентам; для юрлиц — решение юриста банка"],
      ["821-П в ред. № 7220-У", "01.10.2026", "защита информации при переводах; меры — в docs/REGULATORY.md"],
      ["Кодекс этики ИИ; 3-МР", "2025; 16.06.2026", "прозрачность, человек в контуре, подтверждение человеком при высоком риске"],
      ["253-ФЗ, банкротство", "27.07.2027", "реструктуризация долгов юрлица: R-EIO-004 отправляет МЧД на сверку с планом"],
      ["315-ФЗ", "01.09.2027", "единая форма МЧД на ЕПГУ, доп. формы — Банк России, ФНП, ГИС; R-FMT-002 готов"],
      ["Приказы Минцифры № 856, 857", "до 01.03.2028", "утрачивают силу: правила на них — на разбор до пересверки пакета"],
    ], L, TOP, W, [3.0, 2.35, 6.783], { size: 10, headH: 0.34, rowH: 0.288 });
  }

  // ---------- A18. Дорожная карта ----------
  {
    const s = slide("Контент", "Приложение", "A18 · ДОРОЖНАЯ КАРТА", "Что делаем от полуфинала до решения о пилоте", "Сроки этапов — fintechacademy.ru/task-1 и письмо организаторов (submission/README.md); задачи — docs/ROADMAP.md", { appx: "A18" });
    const cols = [
      ["до 01.11", "Спринт 1", ["вертикальный срез: 14 демо-кейсов, отчёт, журнал, удостоверения", "пакет аналитики и видео-демо", "пять интервью с банками"], 0],
      ["10–15.11", "Онлайн-питч", ["питч 5 минут и живое демо", "итог: полуфинал"], 0],
      ["16–29.11", "Спринт 2", ["подпись юриста под правилом", "допуск ДБО и захват до платежа", "шифрование хранилища, журнал чтений", "юнит-экономика", "сайт с демо в браузере"], 0],
      ["03–08.12", "Спринт 3", ["протокол пилота для подписи", "план масштабирования на цифрах интервью", "колода к финалу и прогон питча"], 0],
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
    band(s, L, TOP + ch + GAP, W, BOTTOM - (TOP + ch + GAP), "Финал 11.12.2026: живое демо, а если банк даст данные, то и результаты на обезличенных кейсах ВТБ", { icon: "Flag", size: 15, bold: true });
  }
  if (pages() !== INDEX_SLIDE + APPX.length) throw new Error(`слайдов ${pages()}, а ожидалось ${INDEX_SLIDE + APPX.length}`);

  await pres.writeFile({ fileName: OUT });
  console.log(`готово: ${OUT} — слайдов: ${pages()}`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
