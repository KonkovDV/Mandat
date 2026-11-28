// Проверка удостоверения, списка статусов и записи журнала в браузере: WebCrypto, без библиотек и сети.
// Алгоритмы те же, что в src/mandat/audit: eddsa-jcs-2022 (attestation.py, jcs.py), Bitstring Status List
// со старшим битом первым (status_list.py), лист и узел дерева Меркла с префиксами 0x00 и 0x01 (merkle.py).
import { data, el, rub, ruDate } from "./common.js";

const page = data("page-data");
const enc = new TextEncoder();

// ---------- Каноническая запись ----------

/** RFC 8785 (JCS): ключи по кодовым единицам UTF-16, числа и строки как в ECMAScript. */
function jcs(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(jcs).join(",")}]`;
  return `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}:${jcs(value[k])}`).join(",")}}`;
}

/** Внутренняя запись журнала (mandat.data.canonical_json): ключи по кодовым точкам, без пробелов. */
function pyjson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(pyjson).join(",")}]`;
  const keys = Object.keys(value).sort((a, b) => {
    const x = [...a].map((c) => c.codePointAt(0)), y = [...b].map((c) => c.codePointAt(0));
    for (let i = 0; i < Math.min(x.length, y.length); i++) if (x[i] !== y[i]) return x[i] - y[i];
    return x.length - y.length;
  });
  return `{${keys.map((k) => `${JSON.stringify(k)}:${pyjson(value[k])}`).join(",")}}`;
}

// ---------- Байты ----------

const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
function base58(text) {
  let n = 0n;
  for (const ch of text) {
    const d = B58.indexOf(ch);
    if (d < 0) throw new Error("не base58btc");
    n = n * 58n + BigInt(d);
  }
  const out = [];
  while (n > 0n) { out.unshift(Number(n % 256n)); n /= 256n; }
  for (const ch of text) { if (ch === "1") out.unshift(0); else break; }
  return new Uint8Array(out);
}
function base64url(text) {
  const b64 = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}
const hex = (bytes) => [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
const unhex = (text) => Uint8Array.from(text.match(/../g), (h) => parseInt(h, 16));
const sha256 = async (bytes) => new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
const concat = (...parts) => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
};

/** Открытый ключ из multibase: «z» + base58btc(0xed 0x01 + 32 байта), как в did:key. */
function publicKey(multibase) {
  const raw = base58(multibase.slice(1));
  if (multibase[0] !== "z" || raw[0] !== 0xed || raw[1] !== 0x01 || raw.length !== 34) throw new Error("ключ не Ed25519 в multibase");
  return raw.slice(2);
}

async function verifyProof(credential, keyBytes) {
  const { proof, ...document } = credential;
  const { proofValue, ...config } = proof;
  const docText = jcs(document), configText = jcs(config);
  const docHash = await sha256(enc.encode(docText));
  const configHash = await sha256(enc.encode(configText));
  const key = await crypto.subtle.importKey("raw", keyBytes, { name: "Ed25519" }, false, ["verify"]);
  const signature = base58(proofValue.slice(1));
  const ok = await crypto.subtle.verify({ name: "Ed25519" }, key, signature, concat(configHash, docHash));
  return { ok, docBytes: enc.encode(docText).length, docHash: hex(docHash), configHash: hex(configHash) };
}

async function bitsOf(encoded) {
  if (!encoded.startsWith("u")) throw new Error("список не в multibase base64url");
  const stream = new Blob([base64url(encoded.slice(1))]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}
const bit = (bits, index) => (bits[Math.floor(index / 8)] >> (7 - (index % 8))) & 1;

// ---------- Разметка шагов ----------

function step(list, ok, title, detail) {
  list.append(el("li", { class: ok === null ? "" : ok ? "ok" : "no" }, el("span", { class: "mark", "aria-hidden": "true", text: ok === null ? "…" : ok ? "✓" : "✕" }), el("span", {}, title, detail ? el("small", { text: detail }) : null)));
}
function result(node, ok, text) {
  node.className = `result ${ok ? "ok" : "no"}`;
  node.textContent = text;
}

// ---------- 1. Подпись ----------

const original = page.attestation;
let working = structuredClone(original);
const tampered = new Set();
const fieldsNode = document.getElementById("att-fields");

const FIELDS = [
  ["Организация, ИНН", (c) => c.credentialSubject.organizationInn, "org"],
  ["Операция", (c) => ({ PAYMENT_SIGN: "подписание платежа" })[c.credentialSubject.operation] || c.credentialSubject.operation],
  ["День операции", (c) => ruDate(c.credentialSubject.operationDate), "date"],
  ["Проверенная сумма", (c) => rub(c.credentialSubject.amountRub), "amount"],
  ["Вердикт", (c) => ({ confirmed: "подтверждено" })[c.credentialSubject.verdict] || c.credentialSubject.verdict],
  ["Действует до", (c) => c.validUntil.slice(0, 16).replace("T", " ")],
  ["Представитель", (c) => `HMAC-токен ${c.credentialSubject.representativeToken.slice(0, 16)}…`],
  ["Бит в списках статусов", (c) => c.credentialStatus[0].statusListIndex],
  ["Ключ издателя", () => `${page.public_key.slice(0, 18)}…`],
];

function renderFields() {
  fieldsNode.replaceChildren(
    ...FIELDS.flatMap(([name, get, field]) => [
      el("dt", { text: name }),
      el("dd", { class: field && tampered.has(field) ? "changed" : null, text: get(working) }),
    ]),
  );
}

function applyTamper() {
  working = structuredClone(original);
  const subject = working.credentialSubject;
  if (tampered.has("amount")) subject.amountRub = 1920000;
  if (tampered.has("date")) subject.operationDate = "2026-10-21";
  if (tampered.has("org")) subject.organizationInn = subject.organizationInn.slice(0, -1) + ((Number(subject.organizationInn.at(-1)) + 1) % 10);
  renderFields();
}

async function checkSignature() {
  const list = document.getElementById("sig-steps");
  const out = document.getElementById("sig-result");
  list.replaceChildren();
  const keyBytes = publicKey(page.public_key);
  step(list, true, "Ключ из multibase", `Ed25519, 32 байта: ${hex(keyBytes).slice(0, 24)}…`);
  const r = await verifyProof(working, keyBytes);
  step(list, true, `Каноническая запись документа без подписи, RFC 8785: ${r.docBytes} байт`, null);
  step(list, true, "SHA-256 документа", r.docHash);
  step(list, true, "SHA-256 настроек подписи (proof без proofValue)", r.configHash);
  step(list, r.ok, r.ok ? "Подпись Ed25519 сходится" : "Подпись Ed25519 не сходится", "crypto.subtle.verify над двумя хешами подряд");
  seal(r.ok);
  result(out, r.ok, r.ok
    ? "Подпись верна: удостоверение выпустил держатель ключа, после подписи оно не менялось."
    : `Подпись не сходится: поменялось ${[...tampered].map((f) => ({ amount: "сумма", date: "день операции", org: "организация" })[f]).join(", ")}. Банк такое удостоверение не примет.`);
  await checkStatus(r.ok);
}

/** Печать на карточке: «подпись верна» или «не сходится»; при каждой проверке ставится заново. */
function seal(ok) {
  const node = document.getElementById("seal");
  node.hidden = false;
  node.className = `seal ${ok ? "ok" : "no"}`;
  node.querySelector(".seal-mark").textContent = ok ? "✓" : "✕";
  node.querySelector(".seal-text").textContent = ok ? "подпись верна" : "не сходится";
  requestAnimationFrame(() => node.classList.add("in"));
}

// ---------- 2. Статус ----------

let moment = "before";
let signatureOk = false;

async function checkStatus(sigOk = signatureOk) {
  signatureOk = sigOk;
  const list = document.getElementById("status-steps");
  const out = document.getElementById("status-result");
  list.replaceChildren();
  const keyBytes = publicKey(page.public_key);
  const index = Number(original.credentialStatus[0].statusListIndex);
  const states = {};
  for (const purpose of ["revocation", "suspension"]) {
    const credential = page.lists[moment][purpose];
    const sig = await verifyProof(credential, keyBytes);
    const bits = await bitsOf(credential.credentialSubject.encodedList);
    states[purpose] = bit(bits, index);
    const word = purpose === "revocation" ? "отзыва" : "приостановки";
    step(list, sig.ok, `Список ${word}, версия ${credential.credentialSubject.sequence}: подпись ${sig.ok ? "верна" : "не сходится"}`, `${bits.length * 8} бит после распаковки GZIP; бит ${index} = ${states[purpose]}`);
    if (!sig.ok) return result(out, false, "Список статусов не подписан издателем: проверка останавливается.");
  }
  if (!signatureOk) return result(out, false, "Статус прочитан, но подпись удостоверения не сходится: удостоверение отклоняется раньше статуса.");
  if (states.revocation) return result(out, false, "Отозвано: основную доверенность отменили, удостоверение больше не действует.");
  if (states.suspension) return result(out, false, "Приостановлено: признаки захвата компании. Следующий платёж по этому удостоверению получит отказ.");
  result(out, true, "Действует: ни отзыва, ни приостановки.");
}

// ---------- 3. Журнал ----------

let flipped = false;

async function checkJournal() {
  const list = document.getElementById("journal-steps");
  const out = document.getElementById("journal-result");
  list.replaceChildren();
  const j = page.journal;
  const record = structuredClone(j.record);
  if (flipped) record.details = { ...record.details, verdict: "refuted" };
  const text = pyjson(record);
  const leaf = await sha256(concat(new Uint8Array([0]), enc.encode(text)));
  step(list, hex(leaf) === j.leaf, `Лист: SHA-256(0x00 ‖ запись), запись № ${j.seq}, вердикт «${record.details.verdict}»`, hex(leaf));
  let fn = j.seq - 1, sn = j.size - 1, r = leaf;
  let broken = false;
  for (const [i, h] of j.proof.entries()) {
    if (sn === 0) { broken = true; break; }
    const sibling = unhex(h);
    if (fn % 2 === 1 || fn === sn) {
      r = await sha256(concat(new Uint8Array([1]), sibling, r));
      if (fn % 2 === 0) while (fn % 2 === 0 && fn !== 0) { fn >>= 1; sn >>= 1; }
    } else {
      r = await sha256(concat(new Uint8Array([1]), r, sibling));
    }
    fn >>= 1; sn >>= 1;
    step(list, null, `Узел ${i + 1} из ${j.proof.length}: SHA-256(0x01 ‖ левый ‖ правый)`, hex(r));
  }
  const ok = !broken && sn === 0 && hex(r) === j.root;
  step(list, ok, ok ? "Корень совпал с опубликованным" : "Корень не совпал", j.root);
  result(out, ok, ok
    ? `Запись № ${j.seq} есть в журнале из ${j.size} записей. Для этого хватило ${j.proof.length} хешей.`
    : "Запись поменяли: её хеш дал другой корень. Правку видно без доступа к остальному журналу.");
}

// ---------- Запуск ----------

async function supported() {
  if (!globalThis.crypto?.subtle || typeof DecompressionStream === "undefined") return false;
  try {
    await crypto.subtle.importKey("raw", new Uint8Array(32), { name: "Ed25519" }, false, ["verify"]);
    return true;
  } catch {
    return false;
  }
}

if (page) {
  renderFields();
  supported().then((ok) => {
    if (!ok) {
      result(document.getElementById("sig-result"), false, "Проверке подписи в браузере нужен Ed25519 в WebCrypto: подойдут Chrome 137+, Firefox 129+ или Safari 17+. Без браузера подпись проверяет командная строка: mandat attest-verify.");
      return;
    }
    for (const id of ["tamper", "moment", "journal-tamper"]) document.getElementById(id).hidden = false;
    for (const id of ["check-sig", "check-journal"]) document.getElementById(id).hidden = false;
    document.getElementById("check-sig").addEventListener("click", () => checkSignature());
    document.getElementById("check-journal").addEventListener("click", () => checkJournal());
    document.getElementById("tamper").addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      const field = button.dataset.field;
      if (tampered.has(field)) tampered.delete(field); else tampered.add(field);
      button.setAttribute("aria-pressed", String(tampered.has(field)));
      applyTamper();
      checkSignature();
    });
    document.getElementById("moment").addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      moment = button.dataset.moment;
      for (const b of document.querySelectorAll("#moment button")) b.setAttribute("aria-pressed", String(b === button));
      checkStatus();
    });
    document.getElementById("flip-verdict").addEventListener("click", (event) => {
      flipped = !flipped;
      event.currentTarget.setAttribute("aria-pressed", String(flipped));
      checkJournal();
    });
    checkSignature();
    checkJournal();
  });
}
