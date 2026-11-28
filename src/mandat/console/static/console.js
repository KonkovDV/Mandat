// Консоль стенда «Мандата». Без библиотек и без встроенных скриптов: CSP страницы разрешает только этот файл.
// Данные из ответов выводятся через textContent, разметку из ответа страница не вставляет.
"use strict";
(function () {
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const STATIC = "/console/static/";
  const NUM = new Intl.NumberFormat("ru-RU");
  const PCT = new Intl.NumberFormat("ru-RU", { style: "percent", maximumFractionDigits: 1 });

  // Вердикт → цвет и кадр Анны рядом с последним результатом.
  const VERDICT = {
    confirmed: { cls: "ok", face: "ok" },
    refuted: { cls: "no", face: "stop" },
    needs_review: { cls: "review", face: "think" },
    insufficient_data: { cls: "gap", face: "docs" },
  };
  const CHECK = {
    ok: ["ok", "выполнено"],
    fail: ["no", "не пройдено"],
    refuted: ["no", "не пройдено"],
    review: ["review", "на разбор"],
    not_verified: ["gap", "нет данных"],
    missing: ["gap", "нет данных"],
    waived: ["info", "подключает пилот"],
    not_applicable: ["gap", "не применяется"],
  };
  const EFFECT = {
    refutes: ["no", "опровергает"],
    needs_review: ["review", "на разбор"],
    not_verified: ["gap", "не проверено"],
    supports: ["ok", "подтверждает"],
    info: ["info", "справка"],
  };
  const REVIEW_STATE = {
    queued: ["review", "в очереди"],
    in_review: ["info", "у юриста"],
    docs_requested: ["gap", "ждём документы"],
    accepted: ["ok", "подтверждено юристом"],
    rejected: ["no", "отказ юриста"],
    closed: ["gap", "закрыт"],
    auto_decided: ["ok", "решено автоматически"],
  };
  const REASON = {
    open: "",
    accept: "Решение участников об одобрении сделки получено, ограничение устава выполнено",
    reject: "Полномочие на операцию документами не подтверждено",
    request_docs: "Нужно решение общего собрания участников об одобрении сделки",
    close: "",
    appeal: "Клиент оспаривает решение и прикладывает документы",
  };
  const METHOD = { heuristic: "разбор текста", llm_advisory: "модель", deterministic: "правило" };
  const ALERT = {
    possible_takeover: "признаки захвата компании",
    eio_changed: "смена руководителя",
    poa_revoked: "отмена МЧД",
  };
  const CHARTER_KIND = {
    approval_threshold: "порог одобрения сделки",
    joint_signature: "совместная подпись",
    large_deal_approval: "одобрение крупной сделки",
    unparsed_restriction: "ограничение без порога",
  };

  const state = { session: null, demo: [], operations: [], today: "", upload: { poas: [], egrul: "" }, channel: "rest", decided: [] };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  }
  function tag(kind, text) {
    return el("span", "tag " + kind, text);
  }
  function face(kind) {
    const img = el("img", "face");
    img.src = STATIC + "anna-" + kind + "-160.webp";
    img.alt = "";
    img.width = 46;
    img.height = 46;
    img.setAttribute("aria-hidden", "true");
    img.decoding = "async";
    return img;
  }
  // Кадр Анны — только у последнего результата вкладки: у прежних он снимается.
  function markLatest(panel, card, kind) {
    $$(".results .face", panel).forEach((img) => img.remove());
    card.prepend(face(kind));
  }
  function rub(value) {
    return value === null || value === undefined ? "—" : NUM.format(value) + " ₽";
  }
  function notice(text) {
    const box = $("#notice");
    box.textContent = text;
    box.hidden = false;
    clearTimeout(notice.timer);
    notice.timer = setTimeout(() => { box.hidden = true; }, 7000);
  }
  function busy(button, on, label) {
    if (!button) return;
    if (on) {
      button.dataset.label = button.textContent;
      button.textContent = label || "Выполняется…";
      button.disabled = true;
    } else {
      button.textContent = button.dataset.label || button.textContent;
      button.disabled = false;
    }
  }

  async function call(method, path, body, retried) {
    const options = { method, credentials: "same-origin", cache: "no-store", headers: {} };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(path, options);
    } catch (e) {
      return { ok: false, status: 0, data: { detail: "сервер стенда не отвечает" } };
    }
    let data;
    try {
      data = await response.json();
    } catch (e) {
      data = { detail: "ответ сервера не JSON" };
    }
    if (response.status === 401 && !retried && path !== "/console/api/session") {
      await openSession();
      return call(method, path, body, true);
    }
    return { ok: response.ok, status: response.status, data };
  }
  function problemText(reply) {
    const detail = reply.data && reply.data.detail;
    if (typeof detail === "string") return detail;
    return "запрос не выполнен (" + reply.status + ")";
  }

  // --- сессия и шапка -------------------------------------------------------------------------------------------
  async function openSession() {
    const reply = await call("POST", "/console/api/session", {}, true);
    if (!reply.ok) {
      notice("Сессия консоли не открыта: " + problemText(reply));
      return;
    }
    state.session = reply.data;
    const pill = $("#pill-session");
    pill.textContent = "сессия " + reply.data.session;
    pill.classList.add("on");
    const model = $("#pill-model");
    model.classList.toggle("on", reply.data.model.enabled);
    model.textContent = reply.data.model.enabled ? "модель: " + reply.data.model.model : "модель: не подключена";
    model.title = reply.data.model.note || "";
    const roles = reply.data.principals.map((p) => p.name).join(", ");
    $("#foot-principals").textContent =
      "Стенд на вымышленных данных, профиль demo. Учётные записи вашей сессии: " + roles +
      " — оператор, юрист и система ДБО, без прав администратора и удаления.";
    if (reply.data.commit) $("#foot-principals").textContent += " Сборка " + reply.data.commit.slice(0, 12) + ".";
    $("#consult-model").disabled = !reply.data.model.enabled;
    $("#consult-model").checked = reply.data.model.enabled;
    $("#consult-model-note").textContent = reply.data.model.enabled
      ? "Модель " + reply.data.model.model + " на " + reply.data.model.host + ": получает только обезличенные строки устава."
      : "Модель не подключена: консультант отвечает по фактам отчёта и разбору устава. Модель банка подключается на пилоте настройкой адреса.";
    updateCaseSelect(reply.data.cases);
  }
  function updateCaseSelect(cases) {
    if (!state.session) return;
    state.session.cases = cases;
    const select = clear($("#consult-case"));
    if (!cases.length) {
      select.append(el("option", "", "сначала проверьте кейс на вкладке «Проверка»"));
      select.disabled = true;
      return;
    }
    select.disabled = false;
    cases.forEach((c) => {
      const option = el("option", "", c.title + " · " + c.case_id);
      option.value = c.case_id;
      select.append(option);
    });
  }

  // --- вкладки --------------------------------------------------------------------------------------------------
  const TABS = ["check", "consult", "review", "dbo", "eval"];
  function selectTab(name, focus) {
    if (!TABS.includes(name)) name = "check";
    TABS.forEach((tab) => {
      const button = $("#tab-" + tab);
      const on = tab === name;
      button.setAttribute("aria-selected", on ? "true" : "false");
      button.tabIndex = on ? 0 : -1;
      $("#" + tab).hidden = !on;
      if (on && focus) button.focus();
    });
    if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
    if (name === "review") { loadQueue(); loadRegistry(); }
  }
  TABS.forEach((tab, index) => {
    const button = $("#tab-" + tab);
    button.addEventListener("click", () => selectTab(tab));
    button.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      const step = event.key === "ArrowRight" ? 1 : TABS.length - 1;
      selectTab(TABS[(index + step) % TABS.length], true);
    });
  });
  window.addEventListener("hashchange", () => selectTab(location.hash.slice(1)));
  $("#theme").addEventListener("click", () => {
    const root = document.documentElement;
    const dark = root.dataset.theme === "dark" ||
      (!root.dataset.theme && window.matchMedia("(prefers-color-scheme: dark)").matches);
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("mandat-theme", root.dataset.theme); } catch (e) { /* выбор не сохраняется */ }
  });

  // --- «Проверка» -----------------------------------------------------------------------------------------------
  function renderDemo() {
    const box = clear($("#demo-cases"));
    const fill = clear($("#fill-demo"));
    state.demo.forEach((c) => {
      const button = el("button", "case-btn");
      button.type = "button";
      const v = VERDICT[c.expected] || VERDICT.insufficient_data;
      button.append(el("span", "t", c.title), el("span", "n", c.name));
      const chips = el("span", "chips");
      chips.append(tag(v.cls, "ожидается: " + c.expected_label));
      button.append(chips);
      button.addEventListener("click", () => runCheck({ demo: c.name }, button));
      box.append(button);
      const option = el("option", "", c.name + " — " + c.title);
      option.value = c.name;
      fill.append(option);
    });
    const ops = clear($("#up-op"));
    state.operations.forEach((op) => {
      const option = el("option", "", op.name);
      option.value = op.code;
      ops.append(option);
    });
    $("#up-date").value = state.today;
  }
  async function runCheck(payload, button) {
    if (button) button.setAttribute("aria-busy", "true");
    const reply = await call("POST", "/console/api/check", payload);
    if (button) button.removeAttribute("aria-busy");
    if (!reply.ok) {
      notice("Проверка не выполнена: " + problemText(reply));
      return;
    }
    const target = $("#check-result");
    const previous = target.firstElementChild;
    if (previous && previous.dataset.summary) pushHistory(JSON.parse(previous.dataset.summary));
    clear(target);
    renderReport(target, reply.data, $("#check"));
    updateCaseSelect(reply.data.cases);
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    explainLater(target, reply.data.case_id);
  }
  // Вторая фаза: пояснение консультанта догружается после карточки вердикта.
  async function explainLater(target, caseId) {
    const card = el("article", "card");
    card.append(el("h2", "section-title", "Пояснение консультанта"), el("p", "pending", "готовим пояснение…"));
    const verdictCard = target.firstElementChild;
    verdictCard.after(card);
    const model = !!(state.session && state.session.model.enabled);
    const reply = await call("POST", "/console/api/consult", { case_id: caseId, use_model: model });
    clear(card).append(el("h2", "section-title", "Пояснение консультанта"));
    if (!reply.ok) {
      card.append(el("p", "note", "Пояснение не получено: " + problemText(reply) + ". Вердикт выше от него не зависит."));
      return;
    }
    const d = reply.data;
    const list = el("ul", "plain");
    d.explanation.forEach((s) => list.append(el("li", "", s.text)));
    card.append(list);
    let source = "Модель не подключена: пояснение собрано из фактов отчёта, каждая фраза ссылается на свой факт.";
    if (d.advice && d.advice.error) source = d.advice.error[0].toUpperCase() + d.advice.error.slice(1) + ".";
    else if (d.advice) source = "Справка модели по уставу: шлюз принял фактов " + d.advice.accepted.length + " из " + d.advice.proposed + "; " + d.advice_label + ".";
    card.append(el("p", "fine", source));
  }
  function pushHistory(item) {
    const box = $("#check-history");
    if (!box.firstChild) box.append(el("h2", "section-title", "Проверено в этой сессии"));
    const row = el("div", "item");
    const v = VERDICT[item.verdict] || VERDICT.insufficient_data;
    row.append(tag(v.cls, item.verdict_label), el("span", "", item.title), el("span", "id", item.case_id));
    box.firstChild.after(row);
  }
  function renderReport(target, d, panel) {
    const v = VERDICT[d.verdict] || VERDICT.insufficient_data;
    const card = el("article", "card verdict " + v.cls);
    card.dataset.summary = JSON.stringify({ verdict: d.verdict, verdict_label: d.verdict_label, title: d.title, case_id: d.case_id });
    const body = el("div", "body");
    body.append(el("p", "kicker", d.title || "Кейс"), el("p", "label-big", d.verdict_label));
    if (d.objection) body.append(el("p", "", d.objection));
    const meta = el("div", "meta");
    const op = d.operation || {};
    meta.append(
      el("span", "", (d.organization && d.organization.name) || ""),
      el("span", "", (op.name || op.code || "") + ", " + rub(op.amount_rub) + ", " + (op.date || "")),
      el("span", "", "кейс " + d.case_id),
    );
    if (d.elapsed_ms !== undefined) meta.append(el("span", "", "ответ API за " + NUM.format(d.elapsed_ms) + " мс"));
    body.append(meta);
    const checks = el("div", "chips");
    Object.values(d.checks || {}).forEach((c) => {
      const [cls, word] = CHECK[c.status] || ["gap", c.status];
      checks.append(tag(cls, c.label + ": " + word));
    });
    body.append(checks);
    const link = el("a", "btn small", "Карточка сотрудника (HTML)");
    link.href = d.report_url;
    link.target = "_blank";
    link.rel = "noopener";
    const actions = el("div", "actions");
    actions.append(link);
    body.append(actions);
    card.append(body);
    target.append(card);
    markLatest(panel, card, v.face);
    target.append(evidenceCard(d.evidence || []));
    if ((d.next_steps || []).length) {
      const steps = el("article", "card");
      steps.append(el("h2", "section-title", "Что сделать банку"));
      const list = el("ul", "plain");
      d.next_steps.forEach((s) => list.append(el("li", "", s.action + (s.unit ? " — " + s.unit : ""))));
      steps.append(list);
      target.append(steps);
    }
    const chain = el("article", "card");
    chain.append(el("h2", "section-title", "Цепочка полномочий"));
    const links = el("ul", "plain");
    (d.chain || []).forEach((line) => links.append(el("li", "", line)));
    if (!(d.chain || []).length) links.append(el("li", "", "доверенности нет: полномочие — у руководителя по ЕГРЮЛ"));
    chain.append(links);
    if ((d.stand || []).length) {
      const pilot = el("details");
      pilot.append(el("summary", "", "Что подключает пилот"));
      const list = el("ul", "plain");
      d.stand.forEach((line) => list.append(el("li", "", line)));
      pilot.append(list);
      chain.append(pilot);
    }
    target.append(chain);
  }
  function findingNode(f) {
    const node = el("div", "finding");
    const head = el("div", "head");
    const [cls, word] = EFFECT[f.effect] || ["gap", f.effect];
    head.append(tag(cls, word), el("span", "rule", f.rule_id + " · " + f.check));
    node.append(head, el("p", "msg", f.message));
    const dl = el("dl", "src");
    (f.sources || []).forEach((s) => {
      const dd = el("dd");
      if (s.quote) dd.append(el("span", "", "«" + s.quote + "»"));
      if (s.locator) dd.append(el("span", "loc", s.locator));
      dl.append(el("dt", "", s.source), dd);
    });
    if (f.norm_ref) dl.append(el("dt", "", "Норма"), el("dd", "", f.norm_ref));
    node.append(dl);
    return node;
  }
  function evidenceCard(rows) {
    const card = el("article", "card evidence");
    card.append(el("h2", "section-title", "Доказательства: поле МЧД, запись ЕГРЮЛ, фраза устава, норма"));
    const deciding = rows.filter((r) => r.effect !== "supports" && r.effect !== "info");
    const supporting = rows.filter((r) => r.effect === "supports");
    const first = deciding.concat(supporting).slice(0, Math.max(4, deciding.length));
    first.forEach((r) => card.append(findingNode(r)));
    const rest = rows.filter((r) => !first.includes(r));
    if (rest.length) {
      const more = el("details");
      more.append(el("summary", "", "Остальные находки: " + rest.length));
      rest.forEach((r) => more.append(findingNode(r)));
      card.append(more);
    }
    return card;
  }
  $("#mode-demo").addEventListener("click", () => switchMode(false));
  $("#mode-upload").addEventListener("click", () => switchMode(true));
  function switchMode(upload) {
    $("#mode-demo").setAttribute("aria-pressed", upload ? "false" : "true");
    $("#mode-upload").setAttribute("aria-pressed", upload ? "true" : "false");
    $("#demo-pane").hidden = upload;
    $("#upload-pane").hidden = !upload;
  }
  $("#up-poa").addEventListener("change", async (event) => {
    const files = Array.from(event.target.files || []).slice(0, 3);
    state.upload.poas = await Promise.all(files.map((f) => f.text()));
    $("#up-poa-note").textContent = files.length ? files.map((f) => f.name).join(", ") : "файл не выбран";
  });
  $("#up-egrul").addEventListener("change", async (event) => {
    const file = (event.target.files || [])[0];
    state.upload.egrul = file ? await file.text() : "";
    $("#up-egrul-note").textContent = file ? file.name : "файл не выбран";
  });
  $("#up-charter-file").addEventListener("change", async (event) => {
    const file = (event.target.files || [])[0];
    if (file) $("#up-charter").value = await file.text();
  });
  $("#fill-go").addEventListener("click", async () => {
    const name = $("#fill-demo").value;
    const reply = await call("GET", "/console/api/cases/" + encodeURIComponent(name));
    if (!reply.ok) {
      notice(problemText(reply));
      return;
    }
    const d = reply.data;
    state.upload.poas = d.poas;
    state.upload.egrul = d.egrul;
    $("#up-poa-note").textContent = "из демо-кейса " + name + ": МЧД " + d.poas.length;
    $("#up-egrul-note").textContent = "из демо-кейса " + name;
    $("#up-charter").value = d.charter || "";
    $("#up-op").value = d.operation.code;
    $("#up-amount").value = d.operation.amount_rub || "";
    $("#up-date").value = d.operation.date || state.today;
    $("#up-name").value = d.requester.full_name || "";
    $("#up-inn").value = d.requester.inn || "";
    $("#up-snils").value = d.requester.snils || "";
    $("#up-registry").value = d.registry_status;
  });
  $("#upload-pane").addEventListener("submit", async (event) => {
    event.preventDefault();
    const amount = parseInt($("#up-amount").value, 10);
    const payload = {
      upload: {
        poas: state.upload.poas,
        egrul: state.upload.egrul,
        charter: $("#up-charter").value,
        operation: { code: $("#up-op").value, date: $("#up-date").value, amount_rub: Number.isFinite(amount) ? amount : null },
        requester: { full_name: $("#up-name").value.trim(), inn: $("#up-inn").value.trim(), snils: $("#up-snils").value.trim() },
        registry_status: $("#up-registry").value,
      },
    };
    const button = $("#up-go");
    busy(button, true, "Проверяем…");
    await runCheck(payload);
    busy(button, false);
  });

  // --- «Консультант» --------------------------------------------------------------------------------------------
  $("#consult-go").addEventListener("click", async () => {
    const caseId = $("#consult-case").value;
    if (!caseId || $("#consult-case").disabled) {
      notice("Сначала проверьте кейс на вкладке «Проверка»");
      return;
    }
    const button = $("#consult-go");
    busy(button, true, "Консультант читает…");
    const reply = await call("POST", "/console/api/consult", { case_id: caseId, use_model: $("#consult-model").checked });
    busy(button, false);
    if (!reply.ok) {
      notice("Консультант не ответил: " + problemText(reply));
      return;
    }
    renderConsult(reply.data);
  });
  function renderConsult(d) {
    const target = clear($("#consult-result"));
    const head = el("article", "card");
    const chips = el("div", "chips");
    chips.append(tag("info", "вердикт правил: " + d.verdict_label), tag("gap", "кейс " + d.case_id));
    head.append(chips, el("p", "note", d.model.enabled
      ? "Модель " + d.model.model + " (" + d.model.host + ") подключена: её ответ — " + d.advice_label + "."
      : "Модель не подключена: ответ собран из фактов отчёта и разбора устава. На пилоте банк подключает свою модель адресом OpenAI-совместимого API."));
    target.append(head);

    const explain = el("article", "card");
    explain.append(el("h2", "section-title", d.question));
    const list = el("ul", "plain");
    d.explanation.forEach((s) => {
      const li = el("li", "", s.text + " ");
      s.fact_ids.forEach((id) => li.append(tag("info", id)));
      list.append(li);
    });
    explain.append(list);
    explain.append(el("p", "fine", "Каждая фраза ссылается на факт отчёта; фраз отброшено шлюзом: " + d.explanation_dropped + "."));
    const facts = el("details");
    facts.append(el("summary", "", "Факты отчёта, на которые ссылается пояснение: " + d.facts.length));
    const factList = el("ul", "plain");
    d.facts.forEach((f) => factList.append(el("li", "", f.id + ": " + f.text)));
    facts.append(factList);
    explain.append(facts);
    target.append(explain);

    const charter = el("article", "card");
    charter.append(el("h2", "section-title", "Устав: что нашёл разбор текста"));
    if (!d.charter.present) {
      charter.append(el("p", "note", "Устава в кейсе нет: ограничения полномочий руководителя проверяет юрист по запросу документов."));
    } else if (!d.charter.facts.length) {
      charter.append(el("p", "note", "Шаблоны ограничений в тексте не найдены. Отсутствие находок не равно отсутствию ограничений: кейс с уставом без отметки юриста не подтверждается автоматически."));
    } else {
      d.charter.facts.forEach((f) => charter.append(charterFact(f)));
    }
    target.append(charter);

    if (d.advice) target.append(adviceCard(d.advice));
  }
  function charterFact(f) {
    const node = el("div", "finding");
    const head = el("div", "head");
    head.append(tag("info", CHARTER_KIND[f.kind] || f.kind));
    if (f.value) head.append(el("span", "rule", rub(Number(f.value))));
    head.append(el("span", "rule", "уверенность " + f.confidence + " · " + (METHOD[f.method] || f.method)));
    node.append(head, el("p", "", "«" + f.quote + "»"), el("span", "fine", f.locator));
    return node;
  }
  function adviceCard(a) {
    const card = el("article", "card");
    const head = el("div", "chips");
    head.append(tag("review", a.label));
    card.append(el("h2", "section-title", "Справка модели по уставу"), head);
    if (a.sent) {
      const sent = el("details");
      sent.append(el("summary", "", "Что ушло в модель: только строки о полномочиях, ФИО и реквизиты заменены метками"));
      sent.append(el("pre", "", a.sent));
      card.append(sent);
    }
    if (a.error) {
      card.append(el("p", "note", a.error + ". Вердикт и пояснение выше от модели не зависят."));
      return card;
    }
    card.append(el("p", "", "Модель предложила фактов: " + a.proposed + ", шлюз принял: " + a.accepted.length + "."));
    a.accepted.forEach((f) => card.append(charterFact(f)));
    if (a.dropped.length) {
      const dropped = el("details");
      dropped.append(el("summary", "", "Отброшено шлюзом: " + a.dropped.length));
      const list = el("ul", "plain");
      a.dropped.forEach((why) => list.append(el("li", "", why)));
      dropped.append(list);
      card.append(dropped);
    }
    const verdict = el("div", "chips");
    verdict.append(tag("info", "вердикт без справки: " + a.verdict_before), tag("info", "со справкой: " + a.verdict_after));
    verdict.append(tag(a.verdict_unchanged ? "ok" : "no", a.verdict_unchanged ? "вердикт не изменился" : "вердикт изменился"));
    card.append(verdict, el("p", "fine", "Ответ модели за " + a.seconds + " с."));
    return card;
  }

  $("#gate-go").addEventListener("click", async () => {
    const button = $("#gate-go");
    busy(button, true, "Проверяем шлюз…");
    const reply = await call("GET", "/console/api/gate");
    busy(button, false);
    if (!reply.ok) {
      notice("Шлюз не проверен: " + problemText(reply));
      return;
    }
    const d = reply.data;
    const box = clear($("#gate-result"));
    const ok = d.rejected === d.planted && d.honest_passed === d.honest;
    box.append(el("p", "label-big " + (ok ? "ok" : "no"), d.label));
    const wrap = el("div", "scroll");
    const table = el("table");
    const head = el("tr");
    ["Шлюз", "Ответ модели", "Ловушка или честный", "Итог"].forEach((h) => head.append(el("th", "", h)));
    table.append(head);
    d.rows.forEach((r) => {
      const row = el("tr");
      const verdict = el("td");
      verdict.append(tag(r.passed ? "ok" : "no", r.passed ? "прошёл" : "отклонён"), el("div", "fine", r.reason));
      row.append(el("td", "", r.gate), el("td", "", r.output), el("td", "", (r.honest ? "честный: " : "ловушка: ") + r.trap), verdict);
      table.append(row);
    });
    wrap.append(table);
    box.append(wrap, el("p", "fine", "Кейс " + d.case + ", вердикт правил " + d.verdict_label + ". Ответы подставлены, решения считает код шлюза."));
  });

  // --- «Юрист» --------------------------------------------------------------------------------------------------
  $("#review-refresh").addEventListener("click", () => loadQueue());
  async function loadQueue() {
    if (!state.session) return;
    const reply = await call("GET", "/console/api/review");
    const target = clear($("#review-queue"));
    if (!reply.ok) {
      target.append(el("p", "note", "Очередь не загружена: " + problemText(reply)));
      return;
    }
    $("#review-who").textContent = "Юрист сессии: " + reply.data.reviewer;
    if (!reply.data.items.length) {
      target.append(el("p", "note", "Очередь пуста. Проверьте на вкладке «Проверка» кейс с вердиктом «на разбор» — например, сумму выше порога устава: он придёт сюда со сроком SLA."));
      return;
    }
    reply.data.items.forEach((item) => target.append(queueCard(item)));
  }
  function slaLine(item) {
    const line = el("div", "sla");
    line.append(el("span", "", "приоритет " + (item.effective_priority || item.priority || "—")));
    if (item.sla_hours) line.append(el("span", "", "SLA " + item.sla_hours + " ч"));
    if (item.due_at) line.append(el("span", "", "срок " + new Date(item.due_at).toLocaleString("ru-RU")));
    if (item.overdue) line.append(el("span", "overdue", "просрочен, приоритет поднят"));
    return line;
  }
  function queueCard(item) {
    const card = el("article", "card queue-item");
    const head = el("div", "chips");
    const [cls, word] = REVIEW_STATE[item.state] || ["gap", item.state];
    head.append(tag(cls, word), el("span", "fine", "кейс " + item.case_id + " · версия " + item.version));
    card.append(el("p", "msg", item.title), head, slaLine(item));
    const reason = el("input");
    reason.type = "text";
    reason.maxLength = 300;
    reason.setAttribute("aria-label", "Основание решения");
    const actions = el("div", "actions");
    const add = (action, label, primary) => {
      const button = el("button", "btn small" + (primary ? " primary" : ""), label);
      button.type = "button";
      button.addEventListener("click", () => act(item, action, reason.value || REASON[action], button));
      button.addEventListener("focus", () => { if (!reason.value) reason.placeholder = REASON[action] || "основание"; });
      actions.append(button);
    };
    if (item.state === "queued") add("open", "Взять в работу", true);
    if (item.state === "docs_requested") add("open", "Документы получены — взять снова", true);
    if (item.state === "in_review") {
      reason.placeholder = REASON.accept;
      add("accept", "Подтвердить полномочие", true);
      add("reject", "Отказать");
      add("request_docs", "Вернуть за документами");
      card.append(reason);
    }
    card.append(actions);
    return card;
  }
  async function act(item, action, reason, button) {
    busy(button, true, "…");
    const reply = await call("POST", "/console/api/review/" + encodeURIComponent(item.case_id) + "/" + action, {
      expected_version: item.version,
      reason: reason,
    });
    busy(button, false);
    if (!reply.ok) {
      notice("Действие не принято: " + problemText(reply));
      return;
    }
    const view = { ...reply.data, title: item.title };
    if (["accepted", "rejected", "closed"].includes(view.state)) {
      state.decided = [view].concat(state.decided.filter((d) => d.case_id !== view.case_id));
      renderDecided();
    }
    loadQueue();
  }
  function renderDecided() {
    const box = clear($("#review-done"));
    if (!state.decided.length) return;
    box.append(el("h2", "section-title", "Решено в этой сессии"));
    state.decided.forEach((d) => {
      const card = el("article", "card queue-item");
      const [cls, word] = REVIEW_STATE[d.state] || ["gap", d.state];
      const head = el("div", "chips");
      head.append(tag(cls, word), el("span", "fine", "кейс " + d.case_id + " · версия " + d.version));
      card.append(el("p", "msg", d.title), head);
      const list = el("ul", "plain");
      (d.history || []).forEach((h) => list.append(el("li", "", h.action + " — " + h.actor + " (" + h.role + ")" + (h.reason ? ": " + h.reason : ""))));
      card.append(list);
      const actions = el("div", "actions");
      if (d.state === "accepted" || d.state === "rejected") {
        const close = el("button", "btn small", "Закрыть кейс");
        close.type = "button";
        close.addEventListener("click", () => act(d, "close", "", close));
        actions.append(close);
      }
      const appeal = el("button", "btn small", "Жалоба клиента: вернуть в очередь");
      appeal.type = "button";
      appeal.addEventListener("click", () => act(d, "appeal", REASON.appeal, appeal));
      actions.append(appeal);
      card.append(actions);
      box.append(card);
    });
  }

  // --- «Юрист»: реестр заключений по уставу ---------------------------------------------------------------------
  const CONCLUSION_STATE = {
    draft: "review", approved: "ok", revoked: "no", recheck: "gap", expired: "gap",
  };
  async function loadRegistry() {
    if (!state.session) return;
    const reply = await call("GET", "/console/api/conclusions");
    const target = clear($("#registry-list"));
    if (!reply.ok) {
      target.append(el("p", "note", "Реестр не загружен: " + problemText(reply)));
      return;
    }
    const c = reply.data.client;
    $("#registry-client").textContent = c.name + ", ОГРН " + c.ogrn + ", устав sha256 " + c.charter_sha256.slice(0, 12) +
      "…: заключение пишет оператор, утверждает юрист сессии " + reply.data.lawyer + ", строгий профиль применяет его, пока устав тот же.";
    if (!reply.data.items.length) target.append(el("p", "note", "Заключений по этому уставу ещё нет: запишите черновик."));
    reply.data.items.forEach((item) => target.append(conclusionCard(item)));
  }
  function conclusionCard(item) {
    const card = el("article", "card queue-item");
    const head = el("div", "chips");
    head.append(tag(CONCLUSION_STATE[item.status] || "gap", item.status_label), tag("info", item.outcome_label));
    head.append(el("span", "fine", "№ " + item.id + " · версия " + item.version + (item.mine ? " · ваша сессия" : "")));
    card.append(head, el("p", "msg", item.basis || "основание не записано"));
    const who = "автор " + item.author + (item.approver ? ", утвердил " + item.approver : "") + ", действует до " + item.valid_until;
    card.append(el("p", "fine", who + "; применено " + item.applications.applied + ", закрыло устав в " + item.applications.cleared));
    const actions = el("div", "actions");
    const add = (action, label, reason) => {
      const button = el("button", "btn small", label);
      button.type = "button";
      button.addEventListener("click", async () => {
        busy(button, true, "…");
        const reply = await call("POST", "/console/api/conclusions/" + encodeURIComponent(item.id) + "/" + action, { expected_version: item.version, reason });
        busy(button, false);
        if (!reply.ok) notice("Действие не принято: " + problemText(reply));
        loadRegistry();
      });
      actions.append(button);
    };
    if (item.status === "draft") add("approve", "Утвердить (юрист)", "сверено с уставом");
    if (["draft", "approved", "recheck"].includes(item.status)) add("revoke", "Отозвать (юрист)", "устав изменён или вывод неверен");
    card.append(actions);
    return card;
  }
  $("#registry-draft").addEventListener("click", async () => {
    const button = $("#registry-draft");
    busy(button, true, "Записываем…");
    const reply = await call("POST", "/console/api/conclusions", { reading: $("#registry-reading").value, basis: $("#registry-basis").value });
    busy(button, false);
    if (!reply.ok) notice("Черновик не записан: " + problemText(reply));
    loadRegistry();
  });
  $("#registry-check").addEventListener("click", async () => {
    const button = $("#registry-check");
    busy(button, true, "Проверяем…");
    const reply = await call("POST", "/console/api/conclusions/check", {});
    busy(button, false);
    if (!reply.ok) {
      notice("Проверка не выполнена: " + problemText(reply));
      return;
    }
    const d = reply.data;
    const target = clear($("#registry-result"));
    const v = VERDICT[d.verdict] || VERDICT.insufficient_data;
    const card = el("article", "card verdict " + v.cls);
    const body = el("div", "body");
    body.append(el("p", "kicker", d.title), el("p", "label-big", d.verdict_label));
    const applied = (d.conclusions.applied || []).map((a) => "№ " + a.id).join(", ");
    body.append(el("p", "", applied ? "Устав закрыт заключением " + applied + "." : "Заключение не применено: устав идёт к юристу, как без реестра."));
    d.charter.forEach((f) => body.append(findingNode({ ...f, sources: [] })));
    body.append(el("p", "fine", "Строгий профиль на стенде не доверяет реестру, ЕГРЮЛ и подписи из пакета кейса, поэтому итог за человеком; на пилоте эти проверки закрывают адаптеры банка."));
    card.append(body);
    target.append(card);
    updateCaseSelect(d.cases);
    loadRegistry();
  });

  // --- «ДБО» ----------------------------------------------------------------------------------------------------
  $("#ch-rest").addEventListener("click", () => channel("rest"));
  $("#ch-authzen").addEventListener("click", () => channel("authzen"));
  function channel(name) {
    state.channel = name;
    $("#ch-rest").setAttribute("aria-pressed", name === "rest" ? "true" : "false");
    $("#ch-authzen").setAttribute("aria-pressed", name === "authzen" ? "true" : "false");
  }
  $("#dbo-issue").addEventListener("click", async () => {
    const button = $("#dbo-issue");
    busy(button, true, "Проверяем и выпускаем…");
    const reply = await call("POST", "/console/api/dbo/issue", {});
    busy(button, false);
    if (!reply.ok) {
      notice("Удостоверение не выпущено: " + problemText(reply));
      return;
    }
    const d = reply.data;
    updateCaseSelect(d.cases);
    clear($("#dbo-log"));
    const target = clear($("#dbo-credential"));
    const card = el("article", "card verdict ok");
    const body = el("div", "body");
    body.append(el("p", "kicker", "Удостоверение выпущено"), el("p", "label-big", d.verdict_label));
    body.append(el("p", "", (d.organization && d.organization.name) + ": " + d.operation.name + " на " + rub(d.amount_rub) + ", " + d.operation.date));
    const kv = el("dl", "kv");
    const s = d.credential.subject;
    [
      ["Получатель (audience)", s.audience],
      ["Одноразовый nonce", s.nonce],
      ["Сумма", rub(s.amountRub)],
      ["День операции", s.operationDate],
      ["Представитель", "HMAC-токен " + s.representativeToken],
      ["Действует до", d.credential.validUntil],
      ["Подпись", d.credential.proof.cryptosuite],
      ["Статус", "списки " + d.credential.status.join(", ")],
    ].forEach(([k, v]) => kv.append(el("dt", "", k), el("dd", "", v)));
    body.append(kv);
    card.append(body);
    target.append(card);
    markLatest($("#dbo"), card, "ok");
    $$("#dbo-actions button").forEach((b) => { b.disabled = false; });
    $("#dbo-actions [data-scenario=pay]").textContent = "Платёж " + rub(d.amount_rub);
    $("#dbo-actions [data-scenario=over]").textContent = "Платёж " + rub(d.amount_rub * 4) + " по тому же удостоверению";
  });
  const SCENARIO = {
    pay: "Платёж на проверенную сумму",
    replay: "Повтор того же запроса",
    over: "Сумма в 4 раза больше проверенной",
    takeover: "Смена руководителя, новая МЧД и заявка на смену реквизитов, затем платёж",
  };
  $$("#dbo-actions button").forEach((button) => {
    button.addEventListener("click", async () => {
      const scenario = button.dataset.scenario;
      busy(button, true, "ДБО спрашивает допуск…");
      const reply = await call("POST", "/console/api/dbo/authorize", { scenario, channel: state.channel });
      busy(button, false);
      if (!reply.ok) {
        notice("Допуск не запрошен: " + problemText(reply));
        return;
      }
      const d = reply.data;
      const card = el("article", "card verdict " + (d.allowed ? "ok" : "no"));
      const body = el("div", "body");
      body.append(el("p", "kicker", SCENARIO[d.scenario] + " · " + rub(d.amount_rub)));
      body.append(el("p", "label-big", d.allowed ? "ДОПУЩЕНО" : "ОСТАНОВЛЕНО"));
      if (d.answer && d.answer.already_admitted) body.append(el("p", "", "Тот же запрос уже допущен в " + d.answer.admitted_at + ": второго допуска нет, ДБО проводит операцию один раз."));
      if (d.alerts.length) {
        const kinds = d.alerts.map((a) => (ALERT[a] ? ALERT[a] + " (" + a + ")" : a)).join(", ");
        body.append(el("p", "", "Мониторинг: " + kinds + " — удостоверение приостановлено до списания денег."));
      }
      if (d.problems.length) {
        const list = el("ul", "plain");
        d.problems.forEach((p) => list.append(el("li", "", p)));
        body.append(list);
      }
      const meta = el("div", "meta");
      meta.append(el("span", "", "POST " + d.endpoint), el("span", "", "учётная запись dbo сессии"));
      body.append(meta);
      const raw = el("details");
      raw.append(el("summary", "", "Ответ API"), el("pre", "", JSON.stringify(d.answer, null, 2)));
      body.append(raw);
      card.append(body);
      const log = $("#dbo-log");
      log.prepend(card);
      markLatest($("#dbo"), card, d.allowed ? "ok" : "stop");
      if (scenario === "takeover") $$("#dbo-actions button").forEach((b) => { b.disabled = b.dataset.scenario !== "pay"; });
    });
  });

  // --- «Прогон» -------------------------------------------------------------------------------------------------
  let evalSource = "demo";
  ["demo", "corpus", "pasted"].forEach((name) => {
    $("#ev-" + name).addEventListener("click", () => {
      evalSource = name;
      ["demo", "corpus", "pasted"].forEach((other) => {
        $("#ev-" + other).setAttribute("aria-pressed", other === name ? "true" : "false");
        $("#ev-" + other + "-pane").hidden = other !== name;
      });
    });
  });
  function renderEvalChoice() {
    const box = clear($("#ev-demo-pane"));
    state.demo.forEach((c) => {
      const label = el("label");
      const box2 = el("input");
      box2.type = "checkbox";
      box2.value = c.name;
      box2.checked = true;
      label.append(box2, el("span", "", c.name + " · ожидается " + c.expected_label));
      box.append(label);
    });
  }
  $("#eval-go").addEventListener("click", async () => {
    const payload = { source: evalSource };
    if (evalSource === "demo") payload.names = $$("#ev-demo-pane input:checked").map((i) => i.value);
    if (evalSource === "demo" && !payload.names.length) {
      notice("Отметьте хотя бы один демо-кейс");
      return;
    }
    if (evalSource === "pasted") {
      try {
        payload.cases = JSON.parse($("#ev-json").value);
      } catch (e) {
        notice("Свои кейсы: нужен массив манифестов JSON");
        return;
      }
    }
    const button = $("#eval-go");
    busy(button, true, "Прогон идёт…");
    const reply = await call("POST", "/console/api/eval", payload);
    busy(button, false);
    if (!reply.ok) {
      notice("Прогон не запущен: " + problemText(reply));
      return;
    }
    renderEval(reply.data);
  });
  function renderEval(d) {
    const target = clear($("#eval-result"));
    const tiles = el("div", "tiles");
    const tile = (value, label, hero) => {
      const node = el("div", "tile" + (hero ? " hero" : ""));
      node.append(el("strong", "", NUM.format(value)), el("span", "fine", label));
      tiles.append(node);
    };
    tile(d.false_confirmed, "ложных ПОДТВЕРЖДЕНО", true);
    tile(d.correct, "совпало с ожиданием из " + NUM.format(d.cases));
    tile(d.to_review, "отдано человеку: на разбор и недостаточно данных");
    tile(d.refuted, "опровергнуто");
    target.append(tiles);
    const card = el("article", "card");
    card.append(el("h2", "section-title", d.false_confirmed_label));
    const wrap = el("div", "scroll");
    const table = el("table");
    const head = el("tr");
    ["Кейс", "Ожидалось", "Вердикт движка", ""].forEach((h) => head.append(el("th", "", h)));
    table.append(head);
    d.rows.forEach((r) => {
      const row = el("tr");
      const mark = el("td");
      mark.append(tag(r.expected === r.predicted ? "ok" : "review", r.expected === r.predicted ? "совпало" : "расхождение"));
      row.append(el("td", "", r.case), el("td", "", r.expected_label), el("td", "", r.predicted_label), mark);
      table.append(row);
    });
    wrap.append(table);
    card.append(wrap, el("p", "fine", (d.cached ? "Результат последнего прогона этих кейсов, " : "Прогон занял ") + d.seconds + " с. " + d.boundary + "."));
    target.append(card);
  }

  // --- старт ----------------------------------------------------------------------------------------------------
  async function start() {
    selectTab(location.hash.slice(1) || "check");
    await openSession();
    if (location.hash === "#review") { loadQueue(); loadRegistry(); }
    const reply = await call("GET", "/console/api/cases");
    if (reply.ok) {
      state.demo = reply.data.demo;
      state.operations = reply.data.operations;
      state.today = reply.data.today;
      renderDemo();
      renderEvalChoice();
    }
  }
  start();
})();
