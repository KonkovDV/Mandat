// Видео-демо «Мандата» из настоящего вывода продукта: карточки var/demo/*.html и вывод команд из capture.py.
//
//   ./run-demo.sh                          # из корня репозитория: пакет и карточки var/demo
//   python submission/video/capture.py     # настоящий вывод команд и HTTP API — out/outputs.json
//   node submission/video/build_video.js   # кадры через Chromium (Playwright), MP4 через ffmpeg
//
// Сцены идут в порядке сценария показа docs/DEMO.md. Строки терминала берутся из out/outputs.json как есть;
// пропуски обозначены «…», длинные хеши укорочены с «…». Числа качества — из evidence.json, как в колоде.
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

let chromium;
try {
  ({ chromium } = require("playwright"));
} catch {
  console.error("нужен Playwright: npm i -g playwright && npx playwright install chromium");
  process.exit(1);
}

const HERE = __dirname;
const ROOT = path.join(HERE, "..", "..");
const OUT = path.join(HERE, "out");
const FRAMES = path.join(OUT, "frames");
const CARDS = path.join(OUT, "cards");
const VIDEO = path.join(OUT, "Mandat_demo.mp4");
const FPS = 30;

const run = JSON.parse(fs.readFileSync(path.join(OUT, "outputs.json"), "utf8"));
const ev = JSON.parse(fs.readFileSync(path.join(ROOT, "evidence.json"), "utf8"));

// ---------- Числа: evidence.json и тест фазза — как в колоде (build_deck.js) ----------
const comma = (x, d = 1) => x.toFixed(d).replace(".", ",");
const N = (() => {
  const sc = ev.synthetic_corpus;
  const expected = { confirmed: 0, refuted: 0, needs_review: 0, insufficient_data: 0 };
  for (const [pair, count] of Object.entries(sc.confusion)) expected[pair.split("->")[0]] += count;
  const fuzz = fs.readFileSync(path.join(ROOT, "tests", "test_redteam_fuzz.py"), "utf8").match(/assert len\(fuzz_rows\) == (\d+)/);
  if (!fuzz) throw new Error("в tests/test_redteam_fuzz.py не найдено закреплённое число мутаций");
  return {
    cases: sc.cases,
    kinds: sc.defect_types,
    agree: Math.round(sc.cases * sc.agreement),
    negatives: sc.cases - expected.confirmed,
    falseConfirmed: sc.false_confirmed,
    fcUpper: `${comma(100 * sc.false_confirmed_rate_upper95)}%`,
    chains: ev.subdelegation_property.chains,
    tests: ev.tests.passed,
    coverage: `${comma(ev.tests.coverage_percent)}%`,
    median: `≈${comma(ev.latency.median_ms)} мс`,
    fuzz: Number(fuzz[1]).toLocaleString("ru-RU").replace(/\s/g, " "),
  };
})();

// Банк России, операции без согласия у юрлиц (CLAIMS_REGISTRY.md, M-07 и M-15) — те же числа, что в колоде.
const CBR = { h1Ops: 818 + 780, h1LossBln: (573 + 831.4) / 1000, q2AvgMln: 831.4 / 780, q2Ratio: 67, q2RefundAccounts: 19.5 };

// ---------- Строки терминала из настоящего вывода ----------
const short = (s) => s.replace(/\b([0-9a-f]{16})[0-9a-f]{48}\b/g, "$1…");
const outLines = (key) => run[key].out.split("\n").map((t) => ({ type: "out", text: short(t) }));
// Выбирает строки по образцам в порядке появления; между несмежными строками ставит «…».
function pick(key, patterns) {
  const lines = run[key].out.split("\n");
  const out = [];
  let at = -1;
  for (const p of patterns) {
    const i = lines.findIndex((t, j) => j > at && t.includes(p));
    if (i < 0) throw new Error(`${key}: нет строки «${p}»`);
    if (at >= 0 && i > at + 1) out.push({ type: "dim", text: "  …" });
    out.push({ type: "out", text: short(lines[i].replace(/^ {2,6}/, "  ")) });
    at = i;
  }
  out.push({ type: "dim", text: "  …" });
  return out;
}
const code = (key) => ({ type: "dim", text: `# код выхода ${run[key].code}` });
// Длинная команда переносится обратной косой чертой, как в терминале: строка не уходит за край окна.
const cmdOf = (key) => (run[key].cmd.length > 118 ? run[key].cmd.replace(" --public-key-file", " \\\n    --public-key-file") : run[key].cmd);

// ---------- Сцены ----------
const cases = {
  "01_ok": { scroll: "Что сделать дальше" },
  "05_director_changed": { scroll: "Что сделать дальше" },
  "14_old_director_charter_cleared": { scroll: "Цепочка полномочий" },
  "11_parent_revoked": { scroll: "Что сделать дальше" },
  "08_registry_unavailable": { scroll: "Что сделать дальше" },
};

function scenes(cardInfo) {
  const card = (id, s) => ({ kind: "card", url: `var/demo/${id}.html`, img: `out/cards/${id}.png`, scrollTo: cardInfo[id].targets[cases[id].scroll], ...s });
  const api = run.api.report;
  const j = (k) => `<span class="k">"${k}"</span>`;
  const s = (v) => `<span class="s">${JSON.stringify(v).replace(/</g, "&lt;")}</span>`;
  const step = api.next_steps.find((x) => x.because.some((b) => b.rule_id === "R-EIO-002"));
  const objection = api.objection.replace(/^(R-EIO-002: После выдачи МЧД )\S+( руководитель сменился).*?(\(п\. 1 ст\. 188 ГК РФ\)); (смена свежая.*)$/, "$1…$2 … $3; $4");
  const apiJson = [
    "{",
    `  ${j("case_id")}: ${s(api.case_id)},`,
    `  ${j("verdict")}: ${s(api.verdict)},`,
    `  ${j("verdict_label")}: ${s(api.verdict_label)},`,
    `  ${j("objection")}: ${s(objection)},`,
    `  ${j("routing")}: { ${j("priority")}: ${s(api.routing.priority)}, ${j("sla_hours")}: ${api.routing.sla_hours}, … },`,
    `  ${j("next_steps")}: [ …, { ${j("action")}: ${s(step.action)}, … } ],`,
    `  ${j("reproducibility_hash")}: ${s(short(api.reproducibility_hash))}`,
    "  …",
    "}",
  ].join("\n");
  return [
    { kind: "hero", dark: true, hold: 5.5, h1: "Мандат",
      p: "Проверка полномочий представителя компании на дату операции — с доказательством до поля документа",
      meta: "Видео-демо · Академия Финтеха 2026 · трек ВТБ «Финтех без границ». Данные кейсов вымышлены; команды и их вывод — настоящие" },
    { kind: "tiles", hold: 9, kicker: "Задача банка", title: "Цена ошибки в полномочиях", cols: 2, top: 270,
      tiles: [
        { v: `≈${comma(CBR.q2AvgMln, 2)} млн ₽`, t: `средняя операция без согласия против юрлица во II кв. 2026 — в ${CBR.q2Ratio} раз крупнее средней`, s: "Банк России, обзор за II кв. 2026" },
        { v: String(CBR.h1Ops), t: `операций без согласия у юрлиц за I полугодие 2026 на ${comma(CBR.h1LossBln, 1)} млрд ₽`, s: "Банк России, I и II кв. 2026" },
        { v: `${comma(CBR.q2RefundAccounts, 1)}%`, cls: "red", t: "возмещено компаниям по счетам во II кв. — остальное теряет бизнес", s: "Банк России, обзор за II кв. 2026" },
        { v: "5 рабочих дней", t: "хватает, чтобы в ЕГРЮЛ появился новый директор; у «ЭОС» в октябре 2026 чужой гендиректор значился 10 дней", s: "129-ФЗ, ст. 8; Forbes и АБН по данным РБК — по версии компании" },
      ],
      caption: "Вопрос банка: <b>вправе ли этот человек провести эту операцию за эту компанию в этот день?</b>" },
    { kind: "term", step: "1 · демо-кейсы", hold: 4.5, termTitle: "mandat · стенд",
      steps: [{ cmd: cmdOf("demo"), out: outLines("demo").slice(0, 17) }],
      caption: `Одна команда: <b>14 демо-кейсов</b> — все четыре ответа, каждый совпал с ожидаемым; журнал решений цел` },
    card("01_ok", { step: "2 · карточка сотрудника", v: "rv", verdict: "НА РАЗБОР · P2 · 24 ч",
      notes: [
        { h: "Всё сходится — кроме устава", t: "МЧД, ЕГРЮЛ и представитель совпадают, но для подписи платежей нужен разобранный устав. Без него «да» не будет.", law: "R-CHR-002" },
        { h: "Досье для сотрудника", t: "Сильнейшее возражение, следующий шаг и подразделение — в одной карточке." },
      ],
      caption: "Неизвестное никогда не становится «да»: спорное уходит человеку <b>с готовым досье</b>" }),
    card("05_director_changed", { step: "3 · свежая смена директора", v: "rv", verdict: "НА РАЗБОР · P1 · 4 ч",
      notes: [
        { h: "Директор сменился за 19 дней", t: "Смена директора МЧД не прекращает, но так начинается захват компании.", law: "п. 1 ст. 188 ГК РФ · R-EIO-002" },
        { h: "Приоритет P1, срок 4 часа", t: "ИБ, сопровождение юрлиц и юристы получают задачу с основанием." },
      ],
      caption: "Свежая смена руководителя — <b>первый шаг возможного захвата</b>: разбор с приоритетом и сроком" }),
    card("14_old_director_charter_cleared", { step: "4 · проверка на дату", v: "ok", verdict: "ПОДТВЕРЖДЕНО",
      notes: [
        { h: "МЧД от прежнего директора", t: "Выдана 01.03.2026, директор сменился 01.05.2026. На 20.10.2026 МЧД в сроке, не отменена, подписана законным на дату выдачи руководителем.", law: "п. 1 ст. 188 ГК РФ" },
        { h: "«Да» с сигналом", t: "Роль рекомендована; смена руководителя видна как сигнал мониторинга." },
      ],
      caption: "Проверка на дату: законный представитель получает «да» <b>с доказательством</b> — без лишнего отказа" }),
    card("11_parent_revoked", { step: "5 · оборванная цепочка", v: "no", verdict: "ОПРОВЕРГНУТО · P1 · 4 ч",
      notes: [
        { h: "Основную МЧД отменили", t: "Передоверие ещё выглядит действующим, но теряет силу вместе с основной МЧД.", law: "п. 3 ст. 188 ГК РФ · R-REG-001, R-SUB-006" },
      ],
      caption: "Отказ — с нормой и звеном цепочки: клиент сразу знает, <b>какой документ нужен</b>" }),
    card("08_registry_unavailable", { step: "6 · источник молчит", v: "nd", verdict: "НЕДОСТАТОЧНО ДАННЫХ",
      notes: [
        { h: "Реестр МЧД не ответил", t: "Молчание источника — не «действует». Сотрудник получает список недостающего.", law: "R-REG-001" },
      ],
      caption: "Четыре ответа вместо «да или нет»: <b>недостаточно данных — это не «да»</b>" }),
    { kind: "term", step: "7 · мониторинг: отмена МЧД", hold: 3.5, termTitle: "mandat · удостоверения и мониторинг",
      steps: [
        { cmd: cmdOf("verify_before"), out: [...outLines("verify_before"), code("verify_before")] },
        { cmd: cmdOf("monitor_revoked"), out: pick("monitor_revoked", ['"kind": "poa_invalidated"', "отменена", "п. 3 ст. 188", '"attestation_action": "revocation"']) },
        { cmd: cmdOf("verify_revoked"), out: [...outLines("verify_revoked"), code("verify_revoked")] },
      ],
      caption: "Удостоверение проверяется без доступа к базе; <b>отмена основной МЧД отзывает его по всей цепочке</b>" },
    { kind: "term", step: "8 · мониторинг: захват", hold: 4, termTitle: "mandat · удостоверения и мониторинг",
      steps: [
        { cmd: cmdOf("monitor_takeover"), out: pick("monitor_takeover", ['"kind": "possible_takeover"', '"risk": "high"', "за 72 ч", '"attestation_action": "suspension"']) },
        { cmd: cmdOf("verify_after"), out: [...outLines("verify_after"), code("verify_after")] },
      ],
      caption: "Смена директора, новая МЧД и смена реквизитов за 72 часа — <b>удостоверения приостановлены до платежа</b>" },
    { kind: "term", step: "9 · журнал решений", hold: 4, termTitle: "mandat · журнал",
      steps: [
        { cmd: cmdOf("journal_verify"), out: outLines("journal_verify") },
        { cmd: cmdOf("journal_proof"), out: pick("journal_proof", ['"seq": 3', '"size": 14', '"case_id"', '"verdict"', '"leaf_hash"', '"root"', '"verified": true']) },
      ],
      caption: "Журнал решений: правку записи видно, <b>аудитор получает доказательство одной записи</b> без всего журнала" },
    { kind: "term", step: "10 · HTTP API", hold: 6, narrow: true, termTitle: "ДБО или АБС банка → «Мандат»", json: apiJson,
      steps: [
        { cmd: "curl -s -X POST http://127.0.0.1:8000/v1/cases \\", out: [
          { type: "out", text: '    -H "Authorization: Bearer $MANDAT_API_TOKEN" \\' },
          { type: "out", text: '    -H "Content-Type: application/json" \\' },
          { type: "out", text: "    --data-binary @05_director_changed.json" },
          { type: "dim", text: `# HTTP ${run.api.code} · ответ — справа` },
        ] },
      ],
      caption: "HTTP API для ДБО и АБС: тот же вердикт в JSON — <b>с нормой, приоритетом, сроком и хешем воспроизводимости</b>" },
    { kind: "tiles", hold: 8, kicker: "Качество", title: "Всё воспроизводится командой из репозитория", cols: 3, top: 300,
      tiles: [
        { v: `${N.falseConfirmed} из ${N.negatives}`, cls: "green", t: "ложных «подтверждено» на кейсах-ловушках", s: `верхняя граница 95% — ${N.fcUpper}` },
        { v: `${N.agree} / ${N.cases}`, t: `совпадение с ожидаемым вердиктом, ${N.kinds} видов ситуаций`, s: "mandat eval --per-defect 6" },
        { v: N.fuzz, t: "мутаций сценариев: ни одного ложного «да» и ни одного падения", s: "tests/test_redteam_fuzz.py" },
        { v: String(N.chains), t: "цепочки передоверия: решения совпали с независимой реализацией на Cedar", s: "tests/test_cedar.py" },
        { v: String(N.tests), t: `автотестов проходят; покрытие кода ${N.coverage}`, s: "python -m pytest" },
        { v: N.median, t: "медиана проверки кейса с разбором документов, без сети", s: "scripts/bench.py" },
      ],
      caption: "Однозначное решается само, спорное — человеком; <b>теневой пилот переносит эти метрики на данные ВТБ</b>" },
    { kind: "hero", dark: true, hold: 7, h1: "Мандат",
      p: "Предлагаем ВТБ шесть недель теневого пилота на праве подписи платёжных поручений по МЧД",
      meta: "github.com/KonkovDV/Mandat · команда «Мандата»: Коньков Дмитрий, Конькова Елена" },
  ];
}

// ---------- Кадры ----------
const STILL = (state, dur) => ({ state, dur });
function frames(scene) {
  const out = [];
  const fadeIn = (state) => [0.8, 0.55, 0.3, 0.12].forEach((f) => out.push(STILL({ ...state, fade: f }, 1 / FPS)));
  if (scene.kind === "term") {
    const lines = [];
    const base = { ...scene, lines: [] };
    fadeIn({ ...base, lines: [] });
    for (const st of scene.steps) {
      const by = Math.max(2, Math.ceil(st.cmd.length / 45));
      for (let i = by; i < st.cmd.length + by; i += by) out.push(STILL({ ...base, lines: [...lines, { type: "cmd", text: st.cmd.slice(0, i), cursor: true }] }, 1 / FPS));
      lines.push({ type: "cmd", text: st.cmd });
      out.push(STILL({ ...base, lines: [...lines] }, 0.35));
      for (const l of st.out) {
        lines.push(l);
        out.push(STILL({ ...base, lines: [...lines] }, 2 / FPS));
      }
      out.push(STILL({ ...base, lines: [...lines] }, 1.0));
    }
    out[out.length - 1].dur += scene.hold;
  } else if (scene.kind === "card") {
    fadeIn({ ...scene, offset: 0 });
    out.push(STILL({ ...scene, offset: 0 }, 3.2));
    const to = scene.scrollTo, n = 24;
    for (let i = 1; i <= n; i++) {
      const t = i / n, ease = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
      out.push(STILL({ ...scene, offset: Math.round(to * ease) }, 1 / FPS));
    }
    out[out.length - 1].dur += 3.0;
  } else {
    fadeIn(scene);
    out.push(STILL(scene, scene.hold));
  }
  return out;
}

(async () => {
  fs.rmSync(FRAMES, { recursive: true, force: true });
  fs.mkdirSync(FRAMES, { recursive: true });
  fs.mkdirSync(CARDS, { recursive: true });
  const browser = await chromium.launch();

  // Настоящие карточки сотрудника: снимок всей страницы и положение разделов для прокрутки. Окно уже кадра
  // (880 точек) и масштаб 1238/880: на экране видео текст карточки в 1,4 раза крупнее, чем в браузере.
  const cardInfo = {};
  const DSF = 1238 / 880;
  const cp = await browser.newPage({ viewport: { width: 880, height: 504 }, deviceScaleFactor: DSF });
  for (const id of Object.keys(cases)) {
    const file = path.join(ROOT, "var", "demo", `${id}.html`);
    if (!fs.existsSync(file)) throw new Error(`нет ${file}: сначала ./run-demo.sh`);
    await cp.goto(`file://${file}`);
    const tops = await cp.evaluate(() => Object.fromEntries([...document.querySelectorAll("div.card h2")].map((h) => [h.textContent.trim(), h.closest("div.card").getBoundingClientRect().top + window.scrollY - 10])));
    const targets = Object.fromEntries(Object.entries(tops).map(([k, v]) => [k, Math.round(v * DSF)]));
    await cp.screenshot({ path: path.join(CARDS, `${id}.png`), fullPage: true });
    cardInfo[id] = { targets };
    if (targets[cases[id].scroll] === undefined) throw new Error(`${id}: нет раздела «${cases[id].scroll}»`);
  }
  await cp.close();

  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await page.goto(`file://${path.join(HERE, "video.html")}`);
  await page.evaluate(() => document.fonts.ready);
  const list = ["ffconcat version 1.0"];
  let n = 0, total = 0, last = "";
  for (const scene of scenes(cardInfo)) {
    for (const f of frames(scene)) {
      const key = JSON.stringify(f.state);
      if (key === last && list.length > 1) {
        const i = list.length - 1;
        const prev = Number(list[i].split(" ")[1]);
        list[i] = `duration ${(prev + f.dur).toFixed(4)}`;
      } else {
        await page.evaluate((st) => window.render(st), f.state);
        const name = `${String(++n).padStart(5, "0")}.jpg`;
        await page.screenshot({ path: path.join(FRAMES, name), type: "jpeg", quality: 92 });
        list.push(`file 'frames/${name}'`, `duration ${f.dur.toFixed(4)}`);
        last = key;
      }
      total += f.dur;
    }
  }
  list.push(`file 'frames/${String(n).padStart(5, "0")}.jpg'`);
  fs.writeFileSync(path.join(OUT, "frames.ffconcat"), list.join("\n") + "\n");
  await browser.close();

  execFileSync("ffmpeg", [
    "-y", "-loglevel", "error",
    "-f", "concat", "-safe", "0", "-i", path.join(OUT, "frames.ffconcat"),
    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
    "-vf", `fps=${FPS},format=yuv420p,fade=t=out:st=${(total - 0.6).toFixed(2)}:d=0.6`,
    "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-profile:v", "high",
    "-c:a", "aac", "-b:a", "64k", "-shortest",
    "-movflags", "+faststart",
    "-metadata", "title=Мандат — видео-демо", "-metadata", "artist=Команда «Мандата»",
    VIDEO,
  ], { stdio: "inherit" });
  console.log(`готово: ${path.relative(ROOT, VIDEO)} — ${n} кадров, ${total.toFixed(1)} с`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
