/*
 * Simulation du front-end du dashboard chat (Node, sans dépendance).
 *
 * Vérifie deux comportements qui ne sont pas testables en Python :
 *   1. deux rechargements concurrents de la barre latérale ne doivent pas
 *      afficher les discussions en double (création + événement
 *      `session.created`) ;
 *   2. après l'envoi d'un message, l'indicateur « l'agent est en cours de
 *      réponse » s'affiche, puis la discussion est rechargée quand la
 *      réponse de l'agent est terminée.
 *
 * Usage : node tests/chat_frontend_sim.js <chemin/vers/sayrhazi-server.js>
 * Sortie : « OK : ... » si tout passe, « FAIL ... » sinon.
 */
const fs = require("fs");
const vm = require("vm");

const SRC_PATH = process.argv[2];
if (!SRC_PATH) {
  console.error("Usage : node chat_frontend_sim.js <sayrhazi-server.js>");
  process.exit(2);
}
const SRC = fs.readFileSync(SRC_PATH, "utf8");

function makeEl(tag) {
  const el = {
    tagName: (tag || "DIV").toUpperCase(),
    _html: "",
    children: [],
    dataset: {},
    style: {},
    textContent: "",
    value: "",
    parentNode: null,
    className: "",
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      toggle(c, v) { v ? this._s.add(c) : this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    addEventListener() {},
    removeEventListener() {},
    remove() {},
    insertBefore(c, ref) {
      if (c && c.tagName === "#FRAGMENT") {
        c.children.forEach((x) => { x.parentNode = el; });
        const idx = el.children.indexOf(ref);
        if (idx >= 0) el.children.splice(idx, 0, ...c.children);
        else el.children.push(...c.children);
        return c;
      }
      c.parentNode = el;
      const idx = ref ? el.children.indexOf(ref) : -1;
      if (idx >= 0) el.children.splice(idx, 0, c);
      else el.children.push(c);
      return c;
    },
    appendChild(c) {
      if (c && c.tagName === "#FRAGMENT") {
        c.children.forEach((x) => { x.parentNode = el; el.children.push(x); });
        return c;
      }
      c.parentNode = el;
      el.children.push(c);
      return c;
    },
    get firstElementChild() { return el.children[0] || null; },
    get firstChild() { return el.children[0] || null; },
    querySelector(sel) {
      if (sel === ".chat-session-delete") return makeEl("BUTTON");
      if (sel && sel.indexOf("[data-action=") === 0) return makeEl("BUTTON");
      return null;
    },
    querySelectorAll() { return []; },
    setAttribute() {},
    getAttribute() { return null; },
    focus() {},
    getBoundingClientRect() { return { width: 100, height: 100, bottom: 100, top: 0 }; },
    set innerHTML(v) { el._html = String(v); el.children = []; },
    get innerHTML() { return el._html; },
    set textContent(v) { el._html = String(v); },
    get textContent() { return el._html; },
  };
  return el;
}

const listEl = makeEl("div");
const convEl = makeEl("div");
const inputEl = makeEl("textarea");
const elements = {
  "chat-session-list": listEl,
  "chat-conversation": convEl,
  "chat-message-input": inputEl,
};

let fetchLog = [];
let messagesPayload = { ok: true, messages: [] };

function jsonResponse(payload) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(payload),
  });
}

const ctx = {
  document: {
    addEventListener() {},
    getElementById: (id) => elements[id] || null,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: (t) => makeEl(t),
    createDocumentFragment: () => makeEl("#fragment"),
    body: makeEl("body"),
    title: "",
  },
  window: { addEventListener() {}, innerWidth: 1200, innerHeight: 800 },
  navigator: { clipboard: {}, userAgent: "node" },
  console, setTimeout, clearTimeout, setInterval, clearInterval,
  fetch: function (url, opts) {
    fetchLog.push({ url: String(url), method: (opts && opts.method) || "GET" });
    const u = String(url);
    if (u.indexOf("/api/chat/history/") >= 0) {
      return jsonResponse({
        ok: true,
        messages: [
          { session: "ses_a", title: "Discussion A", time: "10:00" },
          { session: "ses_b", title: "Discussion B", time: "10:01" },
          { session: "ses_c", title: "Discussion C", time: "10:02" },
        ],
      });
    }
    if (u.indexOf("/messages") >= 0) return jsonResponse(messagesPayload);
    if (u.indexOf("prompt_async") >= 0) return jsonResponse({ ok: true });
    if (u.indexOf("/api/chat/session") === 0) return jsonResponse({ ok: true, session_id: "ses_a" });
    return jsonResponse({ ok: true });
  },
  EventSource: function (url) {
    this.close = function () {};
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    const steps = [
      { kind: "message", messageID: "msg_u", role: "user", completed: null },
      { kind: "message", messageID: "msg_a", role: "agent", completed: null },
      { kind: "part", messageID: "msg_a", partID: "prt_a1",
        item: { kind: "text", text: "Bonjour !", part_id: "prt_a1" } },
      { kind: "idle", sessionID: "ses_a" },
    ];
    setTimeout(() => {
      if (typeof this.onopen === "function") this.onopen();
      steps.forEach((step) => {
        if (typeof this.onmessage === "function") {
          this.onmessage({ data: JSON.stringify(step) });
        }
      });
    }, 200);
  },
  localStorage: { getItem: () => null, setItem() {} },
  sessionStorage: { getItem: () => null, setItem() {}, clear() {} },
  Intl, Date, Math, JSON, URLSearchParams, FormData, Blob, XMLHttpRequest: function () {},
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(SRC, ctx);

function check(label, got, expected) {
  const ok = JSON.stringify(got) === JSON.stringify(expected);
  console.log((ok ? "OK   " : "FAIL ") + label + " -> " + JSON.stringify(got) +
    (ok ? "" : " (attendu " + JSON.stringify(expected) + ")"));
  if (!ok) process.exitCode = 1;
}

(async function () {
  // 1. Deux rechargements concurrents (création + événement `session.created`)
  const p1 = ctx.fetchChatSessions("build");
  const p2 = ctx.fetchChatSessions("build");
  await Promise.all([p1, p2]);
  check("pas de doublon dans la barre latérale", listEl.children.length, 3);

  // 2. Indicateur « l'agent est en cours de réponse »
  // (les `let` du script sont des liaisons lexicales : on les modifie depuis
  //  le contexte, pas via une propriété globale)
  vm.runInContext("selectedChatAgent = 'build'; currentSessionId = 'ses_a';", ctx);
  inputEl.value = "bonjour";
  const sending = ctx.sendChatMessage();
  sending.catch((e) => console.log("ERREUR sendChatMessage:", e && e.message));
  await new Promise((r) => setTimeout(r, 100));
  const hasIndicator = convEl.children.some(
    (c) => String(c.className || "").indexOf("chat-agent-working") >= 0);
  check("indicateur « agent en cours » affiché", hasIndicator, true);

  // 3. Fin de tour : le flux SSE émet la réponse puis session.idle.
  messagesPayload = {
    ok: true,
    messages: [
      { role: "user", text: "bonjour", message_id: "msg_u", seq: 1 },
      { role: "agent", text: "Bonjour !", message_id: "msg_a",
        completed: 1794500000000, seq: 2,
        items: [{ kind: "text", text: "Bonjour !", part_id: "prt_a1" }] },
    ],
  };
  vm.runInContext("sessionIdleAt = { ses_a: Date.now() + 1000 };", ctx);
  await sending;
  await new Promise((r) => setTimeout(r, 600));
  // Après session.idle, le pied natif est posé par finaliseLiveConversation.
  await new Promise((r) => setTimeout(r, 1200));
  const rechargements = fetchLog.filter((f) => f.url.indexOf("/messages") >= 0).length;
  check("la discussion est rechargée après la réponse", rechargements >= 1, true);
  check("la réponse est rendue en direct même sans attendre idle",
    convEl.children.length >= 2, true);

  console.log(process.exitCode ? "ECHEC" : "OK : simulation front-end validee");
})();
