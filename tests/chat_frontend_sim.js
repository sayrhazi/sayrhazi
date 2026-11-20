/*
 * Simulation du front-end du dashboard chat (Node, sans dépendance).
 *
 * Vérifie des comportements qui ne sont pas testables en Python :
 *   1. deux rechargements concurrents de la barre latérale ne doivent pas
 *      afficher les discussions en double (création + événement
 *      `session.created`) ;
 *   2. après l'envoi d'un message, l'indicateur « l'agent est en cours de
 *      réponse » s'affiche, puis la discussion est rechargée quand la
 *      réponse de l'agent est terminée ;
 *   3. revert : chargement avec session.revert - les messages à partir du
 *      boundary sont masqués, la saisie est renvoyée au message, le bandeau
 *      s'affiche ; revert sur un message plus ancien ajoute des nœuds
 *      masqués ; l'envoi valide (les nœuds masqués sont supprimés, le
 *      bandeau s'efface) ; l'unrevert restaure les messages.
 *
 * Le mock `fetch` est stateful comme OpenCode : POST /revert pose
 * session.revert dans /info (messages conservés), POST prompt_async supprime
 * le boundary et l'au-delà (cleanup natif) et efface session.revert,
 * POST /unrevert remet session.revert à null.
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

function collectTree(root, pred, out) {
  out = out || [];
  const kids = (root && root.children) || [];
  for (const k of kids) {
    if (pred(k)) out.push(k);
    collectTree(k, pred, out);
  }
  return out;
}

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
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      toggle(c, v) { v ? this._s.add(c) : this._s.delete(c); },
      contains(c) { return this._s.has(c); },
      _reset(tokens) { this._s = new Set(tokens.filter(Boolean)); },
    },
    addEventListener() {},
    removeEventListener() {},
    remove() {
      if (!el.parentNode) return;
      const kids = el.parentNode.children || [];
      const i = kids.indexOf(el);
      if (i >= 0) kids.splice(i, 1);
      el.parentNode = null;
    },
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
    get nextElementSibling() {
      if (!el.parentNode) return null;
      const kids = el.parentNode.children || [];
      const i = kids.indexOf(el);
      return kids[i + 1] || null;
    },
    querySelector(sel) {
      if (sel === ".chat-session-delete") return makeEl("BUTTON");
      if (sel && sel.indexOf("[data-action=") === 0) return makeEl("BUTTON");
      if (sel && sel.indexOf("[data-act=") === 0) return makeEl("BUTTON");
      if (sel === ".sayrhazi-modal-close") return makeEl("BUTTON");
      return null;
    },
    querySelectorAll(sel) {
      // Sélecteur simple composé de classes : « .a.b.c » - tous les tokens
      // doivent être présents (comme dans le DOM réel).
      const last = String(sel || "").split(/\s+/).filter(Boolean).pop() || "";
      const tokens = last.split(".").filter(Boolean);
      if (tokens.length === 0) return [];
      const matches = (c) => c.classList && tokens.every((t) => c.classList.contains(t));
      return collectTree(el, matches);
    },
    setAttribute() {},
    getAttribute() { return null; },
    focus() {},
    contains(other) {
      const all = collectTree(el, () => true);
      return all.indexOf(other) >= 0;
    },
    getBoundingClientRect() { return { width: 100, height: 100, bottom: 100, top: 0 }; },
    set innerHTML(v) { el._html = String(v); el.children = []; },
    get innerHTML() { return el._html; },
    set textContent(v) { el._html = String(v); },
    get textContent() { return el._html; },
    set className(v) {
      el._cls = String(v == null ? "" : v);
      el.classList._reset(el._cls.split(/\s+/));
    },
    get className() { return el._cls || ""; },
  };
  return el;
}

function allText(el) {
  let out = String((el && el.textContent) || "") + " " + String((el && el._html) || "");
  ((el && el.children) || []).forEach((c) => { out += " " + allText(c); });
  return out;
}

const listEl = makeEl("div");
const convEl = makeEl("div");
const inputEl = makeEl("textarea");
const bannerEl = makeEl("div");
const bannerTextEl = makeEl("span");
const bannerFilesEl = makeEl("ul");
const agentWorkingEl = makeEl("div");
  const sendBtnEl = makeEl("button");
  const agentErrorBannerEl = makeEl("div");
  const agentErrorBannerIconEl = makeEl("span");
  const agentErrorBannerTextEl = makeEl("span");
  const agentErrorBannerInfoEl = makeEl("button");
  const compactionBadgeEl = makeEl("div");
  const compactionBadgeTextEl = makeEl("span");
  const highlightEl = makeEl("pre");
  const highlightCodeEl = makeEl("code");
  const autocompleteEl = makeEl("div");
  const warningBannerEl = makeEl("div");
  const warningBannerTextEl = makeEl("span");
  warningBannerEl.appendChild(warningBannerTextEl);
  const questionBannerEl = makeEl("div");
const questionTabsEl = makeEl("div");
const questionBodyEl = makeEl("div");
const questionDismissEl = makeEl("button");
const elements = {
  "chat-session-list": listEl,
  "chat-conversation": convEl,
  "chat-message-input": inputEl,
  "revert-banner": bannerEl,
  "revert-banner-text": bannerTextEl,
  "revert-banner-files": bannerFilesEl,
  "chat-agent-working": agentWorkingEl,
  "chat-send-btn": sendBtnEl,
  "agent-error-banner": agentErrorBannerEl,
  "agent-error-banner-icon": agentErrorBannerIconEl,
  "agent-error-banner-text": agentErrorBannerTextEl,
  "agent-error-banner-info": agentErrorBannerInfoEl,
  "compaction-badge": compactionBadgeEl,
  "compaction-badge-text": compactionBadgeTextEl,
  "chat-highlight": highlightEl,
  "chat-highlight-code": highlightCodeEl,
  "chat-autocomplete": autocompleteEl,
  "chat-warning-banner": warningBannerEl,
  "chat-warning-banner-text": warningBannerTextEl,
  "question-banner": questionBannerEl,
  "question-tabs": questionTabsEl,
  "question-body": questionBodyEl,
  "question-foot-dismiss": questionDismissEl,
  // Floating model tooltip : créé à la volée par ensureFloatTooltip()
  // (via document.body.appendChild), reconnue via id.
  // Not registered here (the mock document tree tracks them through createElement)
};

let fetchLog = [];
let messagesPayload = { ok: true, messages: [] };
let infoPayload = null; // null => {ok: true} (aucun revert)

function jsonResponse(payload) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(payload),
  });
}

// Mock stateful façon OpenCode : revert conserve les messages et pose
// session.revert ; prompt_async supprime boundary+l'au-delà et efface
// session.revert ; unrevert remet session.revert à null.
function opencodeInfo() {
  return infoPayload || { ok: true };
}
function opencodeApplyRevertCommit() {
  if (!infoPayload || !infoPayload.revert) return;
  const boundary = infoPayload.revert.messageID;
  const idx = (messagesPayload.messages || []).findIndex((m) => m && m.message_id === boundary);
  if (idx >= 0) messagesPayload = { ok: true, messages: messagesPayload.messages.slice(0, idx) };
  infoPayload = { ok: true };
}

const mockEventSources = [];
const ctx = {
  document: {
    addEventListener() {},
    getElementById: (id) => elements[id] || null,
    querySelector: () => null,
    querySelectorAll: (sel) => {
      // Sélecteur simple « .a.b.c… » : tous les tokens doivent être présents.
      const last = String(sel || "").split(/\s+/).filter(Boolean).pop() || "";
      const tokens = last.split(".").filter(Boolean);
      if (tokens.length === 0) return [];
      const matches = (c) => c.classList && tokens.every((t) => c.classList.contains(t));
      return collectTree(convEl, matches);
    },
    createElement: (t) => makeEl(t),
    createDocumentFragment: () => makeEl("#fragment"),
    body: makeEl("body"),
    title: "",
  },
  window: { addEventListener() {}, innerWidth: 1200, innerHeight: 800 },
  navigator: { clipboard: {}, userAgent: "node" },
  console, setTimeout, clearTimeout, setInterval, clearInterval,
  fetch: function (url, opts) {
    const u = String(url);
    const method = (opts && opts.method) || "GET";
    fetchLog.push({ url: u, method, body: opts && opts.body ? String(opts.body) : "" });
    if (u.indexOf("/info") >= 0) return jsonResponse(opencodeInfo());
    if (u.indexOf("/revert/preview") >= 0 && method === "POST") {
      // Comme le backend serveur : pose puis annule le revert côté OpenCode.
      let messageID = "msg_u2";
      try { messageID = (JSON.parse((opts && opts.body) || "{}").messageID) || messageID; } catch (e) {}
      infoPayload = { ok: true, session_id: "ses_a", revert: { messageID, snapshot: "snap1", diff: "" }, summary: null };
      const diff = infoPayload.revert.diff;
      infoPayload = { ok: true };
      return jsonResponse({ ok: true, session_id: "ses_a", diff, summary: null });
    }
    if (u.indexOf("/revert") >= 0 && method === "POST") {
      let messageID = "msg_u2";
      try { messageID = (JSON.parse((opts && opts.body) || "{}").messageID) || messageID; } catch (e) {}
      infoPayload = { ok: true, session_id: "ses_a", revert: { messageID, snapshot: "snap1", diff: "" }, summary: null };
      return jsonResponse({ ok: true, session_id: "ses_a", revert: infoPayload.revert, summary: null });
    }
    if (u.indexOf("/unrevert") >= 0 && method === "POST") {
      infoPayload = { ok: true };
      return jsonResponse({ ok: true, session_id: "ses_a", revert: null, summary: null });
    }
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
    if (u.indexOf("prompt_async") >= 0) { opencodeApplyRevertCommit(); return jsonResponse({ ok: true }); }
    if (u.indexOf("/api/chat/session") === 0) return jsonResponse({ ok: true, session_id: "ses_a" });
    return jsonResponse({ ok: true });
  },
  EventSource: function (url) {
    this.close = function () {};
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    const ns = { url, instance: this };
    mockEventSources.push(ns);
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
    mockEventSources,    mockEventSources: mockEventSources,
  localStorage: { getItem: () => null, setItem() {} },
  sessionStorage: (function () {
    const store = new Map();
    return {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => { store.set(k, String(v)); },
      clear: () => store.clear(),
      removeItem: (k) => store.delete(k),
    };
  })(),
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

const SIX_MESSAGES = [
  { role: "user", text: "bonjour", message_id: "msg_u1", seq: 1 },
  { role: "agent", text: "Salut", message_id: "msg_a1", seq: 2,
    items: [{ kind: "text", text: "Salut", part_id: "prt_a1" }] },
  { role: "user", text: "comment ça va", message_id: "msg_u2", seq: 3 },
  { role: "agent", text: "Ça va", message_id: "msg_a2", seq: 4,
    items: [
      { kind: "text", text: "Ça va", part_id: "prt_a2" },
      { kind: "tool", tool: "edit", file: "projet/a.txt", part_id: "prt_tool_a2",
        diff_content: "diff --git a/projet/a.txt b/projet/a.txt\n--- a/projet/a.txt\n+++ b/projet/a.txt\n@@ -1 +1 @@\n-old\n+new\n" },
    ] },
  { role: "user", text: "liste-moi les fichiers", message_id: "msg_u3", seq: 5 },
  { role: "agent", text: "Voici", message_id: "msg_a3", seq: 6,
    items: [
      { kind: "text", text: "Voici", part_id: "prt_a3" },
      { kind: "tool", tool: "write", file: "projet/nouveau.txt", part_id: "prt_tool_a3",
        diff_content: "diff --git a/projet/nouveau.txt b/projet/nouveau.txt\nnew file mode 100644\n--- /dev/null\n+++ b/projet/nouveau.txt\n@@ -0,0 +1,2 @@\n+ligne 1\n+ligne 2\n" },
    ] },
];

function resetChatState() {
  convEl.children = [];
  listEl.children = [];
  bannerEl.classList._s = new Set();
  bannerTextEl.textContent = "";
  bannerFilesEl.innerHTML = "";
  bannerFilesEl.hidden = true;
  inputEl.value = "";
  vm.runInContext('selectedChatAgent = "build"; currentSessionId = "ses_a"; remoteMessages = {}; pendingRevert = null; sessionIdleAt = {};', ctx);
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
  resetChatState();
  inputEl.value = "bonjour";
  const sending = ctx.sendChatMessage();
  sending.catch((e) => console.log("ERREUR sendChatMessage:", e && e.message));
  await new Promise((r) => setTimeout(r, 100));
  const hasIndicator = agentWorkingEl.hidden === false;
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
  check("indicateur masqué en fin de réponse", agentWorkingEl.hidden, true);
  check("la réponse est rendue en direct même sans attendre idle",
    convEl.children.length >= 2, true);
  const liveUserNodes = convEl.children.filter((c) => c.classList && c.classList.contains("user"));
  const liveUser = liveUserNodes[liveUserNodes.length - 1];
  check("bulle user live identifiee (id serveur) pour le bouton Revert",
    liveUser && liveUser.dataset && liveUser.dataset.messageId, "msg_u");

  // 4. Revert en cours au chargement : boundary masqué côté GUI
  resetChatState();
  messagesPayload = { ok: true, messages: SIX_MESSAGES.slice() };
  infoPayload = { ok: true, session_id: "ses_a",
    revert: {
      messageID: "msg_u3",
      snapshot: "snap1",
      diff: "diff --git a/projet/a.txt b/projet/a.txt\n" +
            "--- a/projet/a.txt\n" +
            "+++ b/projet/a.txt\n" +
            "@@ -1 +1 @@\n" +
            "-new\n" +
            "+old\n" +
            "diff --git a/projet/nouveau.txt b/projet/nouveau.txt\n" +
            "deleted file mode 100644\n" +
            "--- a/projet/nouveau.txt\n" +
            "+++ /dev/null\n" +
            "@@ -1,2 +0,0 @@\n" +
            "-ligne 1\n" +
            "-ligne 2\n",
    },
    summary: null };
  await ctx.loadSessionMessages("ses_a");
  check("revert au chargement : les messages à partir du boundary sont absents", convEl.children.length, 4);
  check("saisie renvoyée au message du boundary", inputEl.value, "liste-moi les fichiers");
  check("bandeau visible au chargement", bannerEl.classList.contains("visible"), true);
  check("bandeau : compteur de messages reverted", bannerTextEl.textContent, "1 message reverted");
  check("bloc fichiers affiché quand le revert touche des fichiers", bannerFilesEl.hidden, false);
  check("bloc fichiers : nom du fichier modifié", bannerFilesEl.innerHTML.indexOf("projet/a.txt") >= 0, true);
  check("bloc fichiers : nouveau fichier listé", bannerFilesEl.innerHTML.indexOf("projet/nouveau.txt") >= 0, true);
  check("bloc fichiers : suppressions comptées (revert)", bannerFilesEl.innerHTML.indexOf("-2") >= 0, true);
  check("bloc fichiers : format -N/+N", bannerFilesEl.innerHTML.indexOf("-1") >= 0 && bannerFilesEl.innerHTML.indexOf("+1") >= 0, true);

  // 4b. Revert local (sans marqueur serveur) : persistance via stash sessionStorage
  resetChatState();
  messagesPayload = { ok: true, messages: SIX_MESSAGES.slice() };
  infoPayload = null;
  await ctx.loadSessionMessages("ses_a");
  check("4b chargement initial sans revert", convEl.children.length, 6);
  const anchorStash = convEl.children[2]; // msg_u2
  const confirmP = ctx.confirmRevert(anchorStash, "comment ça va", "msg_u2");
  check("bloc fichiers : spinner affiche le temps de l'apercu serveur", bannerFilesEl.innerHTML.indexOf("revert-banner-file-pending") >= 0, true);
  check("bloc fichiers : aucune stat affichee avant l'apercu", bannerFilesEl.innerHTML.indexOf("revert-diff-") < 0, true);
  await confirmP;
  check("bloc fichiers : stats affichees une fois l'apercu recu", bannerFilesEl.innerHTML.indexOf("revert-diff-") >= 0, true);

  check("4b bandeau visible après revert local", bannerEl.classList.contains("visible"), true);
  await ctx.loadSessionMessages("ses_a");
  check("4b le stash restaure le masquage après rechargement", convEl.children.length, 2);
  check("4b le stash restaure le bandeau", bannerEl.classList.contains("visible"), true);
  check("4b saisie renvoyée au boundary via stash", inputEl.value, "comment ça va");

  // 5. Revert sur un message plus ancien (frontière reculée)
  resetChatState();
  vm.runInContext("sessionStorage.removeItem('sayrhazi.revertStash.ses_a'); sessionStorage.removeItem('sayrhazi.revertStash');", ctx);
  messagesPayload = { ok: true, messages: SIX_MESSAGES.slice() };
  infoPayload = null;
  await ctx.loadSessionMessages("ses_a");
  // Saisie renvoyée au boundary par le stash de 4b - il faut le réinitialiser
  // pour le scénario (session différente / stash vidé).
  const anchor = convEl.children[2]; // msg_u2
  await ctx.confirmRevert(anchor, "comment ça va", "msg_u2");
  check("bloc fichiers spontané sans réseau : projet/a.txt présent", bannerFilesEl.innerHTML.indexOf("projet/a.txt") >= 0, true);
  check("bloc fichiers spontané sans réseau : projet/nouveau.txt présent", bannerFilesEl.innerHTML.indexOf("projet/nouveau.txt") >= 0, true);
  check("revert plus ancien : u2 + a2 + u3 + a3 masqués",
    convEl.children.filter((c) => c.classList.contains("revert-hidden")).length, 4);
  check("saisie renvoyée au message du revert", inputEl.value, "comment ça va");
  check("bandeau : 2 user messages reverted", bannerTextEl.textContent, "2 messages reverted");

  // 6. Envoi validant : les nœuds masqués sont supprimés, le bandeau s'efface
  inputEl.value = "comment ça va";
  await ctx.sendChatMessage();
  await new Promise((r) => setTimeout(r, 100));
  const postsRevertAfterSend = fetchLog.filter((f) => f.method === "POST" && f.url.indexOf("/api/chat/session/ses_a/revert") >= 0 && f.url.indexOf("/revert/preview") < 0).length;
  check("POST /revert posé à l'envoi validant", postsRevertAfterSend >= 1, true);
  check("revert validé : plus aucun nœud masqué",
    convEl.children.some((c) => c.classList.contains("revert-hidden")), false);
  check("bandeau masqué après validation", bannerEl.classList.contains("visible"), false);
  check("prompt_async a bien supprimé le boundary côté backend mock",
    messagesPayload.messages.length, 2);

  // 7. Unrevert : les messages réapparaissent
  resetChatState();
  messagesPayload = { ok: true, messages: SIX_MESSAGES.slice() };
  infoPayload = { ok: true, session_id: "ses_a",
    revert: { messageID: "msg_u3", snapshot: "snap1", diff: "" }, summary: null };
  await ctx.loadSessionMessages("ses_a");
  check("re-chargement avec revert en cours", bannerEl.classList.contains("visible"), true);
  await ctx.confirmUnrevert();
  await new Promise((r) => setTimeout(r, 100));
  const postsUnrevert = fetchLog.filter((f) => f.method === "POST" && f.url.indexOf("/unrevert") >= 0).length;
  check("POST /unrevert appelé", postsUnrevert >= 1, true);
  check("messages restaurés après annulation", convEl.children.length, 6);
  check("bandeau masqué après annulation", bannerEl.classList.contains("visible"), false);
  check("saisie vidée après annulation", inputEl.value, "");
  check("bloc fichiers masqué après annulation", bannerFilesEl.hidden, true);

  // 8. Après annulation, un second revert affiche les fichiers immédiatement
  // (remoteMessages n'est plus vidé à la confirmation - régression corrigée)
  resetChatState();
  vm.runInContext("sessionStorage.removeItem('sayrhazi.revertStash.ses_a'); sessionStorage.removeItem('sayrhazi.revertStash');", ctx);
  messagesPayload = { ok: true, messages: SIX_MESSAGES.slice() };
  infoPayload = null;
  await ctx.loadSessionMessages("ses_a");
  await ctx.confirmRevert(convEl.children[4], "liste-moi les fichiers", "msg_u3");
  check("8a premier revert (u3) : fichier de l'agent détecté", bannerFilesEl.innerHTML.indexOf("projet/nouveau.txt") >= 0, true);
  await ctx.confirmUnrevert();
  check("8b annulation : messages restaurés", convEl.children.length, 6);
  await ctx.confirmRevert(convEl.children[2], "comment ça va", "msg_u2");
  check("8c second revert (u2) : fichiers visibles immédiatement",
    bannerFilesEl.innerHTML.indexOf("projet/a.txt") >= 0 && bannerFilesEl.innerHTML.indexOf("projet/nouveau.txt") >= 0, true);

  // 9. Composeur : la saisie appartient a chaque discussion (jamais partagee)
  // (nettoyage : la section 8c a laisse un revert en attente dans A)
  resetChatState();
  vm.runInContext("sessionStorage.removeItem('sayrhazi.revertStash.ses_a'); sessionStorage.removeItem('sayrhazi.revertStash');", ctx);
  messagesPayload = { ok: true, messages: SIX_MESSAGES.slice() };
  infoPayload = null;
  await ctx.loadSessionMessages("ses_a");
  inputEl.value = "brouillon de la discussion A";
  await ctx.loadSessionMessages("ses_b");
  check("9a changement de discussion : la saisie repart vide", inputEl.value, "");
  inputEl.value = "brouillon de la discussion B";
  await ctx.loadSessionMessages("ses_a");
  check("9b retour a la discussion A : la saisie repart vide a son tour", inputEl.value, "");
  inputEl.value = "brouillon conserve";
  await ctx.loadSessionMessages("ses_a");
  check("9c meme discussion rechargee : la saisie est preservee", inputEl.value, "brouillon conserve");

  // 10. Revert local en cours : aller-retour A -> B -> A (le revert survit, comme la TUI)
  resetChatState();
  vm.runInContext("sessionStorage.removeItem('sayrhazi.revertStash.ses_a');", ctx);
  messagesPayload = { ok: true, messages: SIX_MESSAGES.slice() };
  infoPayload = null;
  await ctx.loadSessionMessages("ses_a");
  await ctx.confirmRevert(convEl.children[2], "comment ça va", "msg_u2");
  check("10a bandeau visible dans la discussion A", bannerEl.classList.contains("visible"), true);
  await ctx.loadSessionMessages("ses_b");
  check("10b discussion B : pas de bandeau de revert de A", bannerEl.classList.contains("visible"), false);
  check("10b discussion B : saisie vide", inputEl.value, "");
  await ctx.loadSessionMessages("ses_a");
  check("10c retour a A : revert toujours present", bannerEl.classList.contains("visible"), true);
  check("10c retour a A : saisie renvoyee au message revert", inputEl.value, "comment ça va");

  // 11. Composer : fleche d'envoi grisee si la saisie est vide, verte sinon
  resetChatState();
  messagesPayload = { ok: true, messages: SIX_MESSAGES.slice() };
  infoPayload = null;
  await ctx.loadSessionMessages("ses_a");
  inputEl.value = "";
  sendBtnEl.disabled = true;
  sendBtnEl.classList.remove("stop");
  ctx.updateSendButtonAvailability();
  check("envoi grise quand la saisie est vide", sendBtnEl.disabled, true);
  inputEl.value = "bonjour";
  ctx.updateSendButtonAvailability();
  check("envoi actif quand la saisie est remplie", sendBtnEl.disabled, false);
  inputEl.value = "   ";
  ctx.updateSendButtonAvailability();
  check("espaces seuls comptent comme une saisie vide", sendBtnEl.disabled, true);

  // 12. Composer : le modèle choisi (et sa variante) sont transmis dans prompt_async
  resetChatState();
  messagesPayload = { ok: true, messages: [] };
  infoPayload = null;
  inputEl.value = "bonjour";
  ctx.selectComposerModel({ providerID: "google", modelID: "gemini-2.5-pro", name: "Gemini 2.5 Pro", providerName: "Google", free: false, variant: "high" });
  const stashedModel = vm.runInContext("JSON.parse(sessionStorage.getItem('sayrhazi.model') || 'null')", ctx);
  check("modele selectionne persiste dans le stash", stashedModel && stashedModel.modelID, "gemini-2.5-pro");
  check("variante selectionnee persistee dans le stash", stashedModel && stashedModel.variant, "high");
  await ctx.sendChatMessage();
  const modelPosts = fetchLog.filter((f) => f.method === "POST" && f.url.indexOf("prompt_async") >= 0);
  const modelBody = modelPosts.length ? JSON.parse(modelPosts[modelPosts.length - 1].body || "{}") : {};
  check("prompt_async contient le modele choisi", modelBody.model, { providerID: "google", modelID: "gemini-2.5-pro" });
  check("prompt_async contient la variante", modelBody.variant, "high");
  vm.runInContext("sessionStorage.removeItem('sayrhazi.model');", ctx);

  // 13. Question SSE : le bandeau s'affiche avec onglets + options
  resetChatState();
  messagesPayload = { ok: true, messages: [] };
  infoPayload = null;
  questionBannerEl.innerHTML = "";
  questionBannerEl.hidden = true;
  vm.runInContext(`
    if (typeof pendingQuestion !== 'undefined') pendingQuestion = null;
    pendingPermission = null;
    pendingRevert = null;
    if (typeof openQuestionBanner === 'function') {
      openQuestionBanner({
        id: 'que_test_42',
        sessionID: 'ses_a',
        questions: [{
          header: 'Forme',
          question: 'Quelle forme ?',
          options: [
            { label: 'Modal', description: 'fullscreen' },
            { label: 'Banner', description: 'like revert banner' },
          ],
          multiple: false,
          custom: true,
        }],
      });
    }
  `, ctx);
  check("question banner visible", questionBannerEl.hidden, false);
  check("question banner single-select : pas d'onglet Confirm", questionTabsEl.innerHTML.indexOf("Confirm") >= 0, false);
  check("question banner a rendu les options", questionBodyEl.innerHTML.indexOf("Modal") >= 0 && questionBodyEl.innerHTML.indexOf("Banner") >= 0, true);

  // 13b. Deux séries => l'onglet Confirm réapparaît (alignement OpenCode : plusieurs questions)
  resetChatState();
  questionBannerEl.innerHTML = "";
  questionBannerEl.hidden = true;
  vm.runInContext(`
    if (typeof pendingQuestion !== 'undefined') pendingQuestion = null;
    pendingPermission = null;
    pendingRevert = null;
    if (typeof openQuestionBanner === 'function') {
      openQuestionBanner({
        id: 'que_test_43',
        sessionID: 'ses_a',
        questions: [{
          header: 'Forme',
          question: 'Quelle forme ?',
          options: [ { label: 'Modal', description: 'fullscreen' } ],
          multiple: false,
          custom: true,
        }, {
          header: 'Cible',
          question: 'Quelle cible ?',
          options: [ { label: 'Agent', description: 'le tour actif' } ],
          multiple: false,
          custom: true,
        }],
      });
    }
  `, ctx);
  check("question banner multi-séries : onglet Confirm présent", questionTabsEl.innerHTML.indexOf("Confirm") >= 0, true);

  // 14. Permission : modal de confirmation / choix d'option
  resetChatState();
  messagesPayload = { ok: true, messages: [] };
  infoPayload = null;
  try {
    vm.runInContext(`
      pendingPermission = null;
      pendingQuestion = null;
      pendingRevert = null;
      if (typeof openPermissionModal === 'function') {
        openPermissionModal({
          id: 'per_test_99',
          sessionID: 'ses_a',
          permission: 'edit',
          patterns: ['src/a.ts'],
          metadata: { filepath: 'src/a.ts' },
          always: ['src/**'],
          tool: { messageID: 'msg_a1', callID: 'call_a' },
        });
      }
    `, ctx);
    // openPermissionModal regarde pendingPermission en globale non rattachée — on lit via vm
    const permState = vm.runInContext("typeof pendingPermission !== 'undefined' && pendingPermission ? 'set' : 'unset'", ctx);
    check("permission modal en attente", permState, "set");
  } catch (e) {
    console.error("permission sim err:", e);
    check("permission modal ouverte", false, true);
  }

  // 15. Bandeau d'erreurs upstream : retry (ambre, transitoire) puis erreur définitive (rouge)
  resetChatState();
  messagesPayload = { ok: true, messages: [] };
  infoPayload = null;
  try {
    vm.runInContext(`
      if (typeof showAgentRetryBanner === 'function') {
        currentSessionId = 'ses_error_sim';
        showAgentRetryBanner({ attempt: 2, message: 'Upstream request failed', next: Date.now() + 5000, statusCode: 429 });
      }
    `, ctx);
    check("bandeau retry affiche", agentErrorBannerEl.hidden, false);
    check("bandeau retry contient attempt",
      agentErrorBannerTextEl.textContent.indexOf("attempt #2") >= 0, true);
    check("bandeau retry = mode orange",
      agentErrorBannerEl.className.indexOf("--retry") >= 0, true);
    check("info masquee en retry", agentErrorBannerInfoEl.hidden, true);
  } catch (e) {
    console.error("retry banner err:", e);
    check("bandeau retry rendu", false, true);
  }

  try {
    vm.runInContext(`
      if (typeof showAgentFinalBanner === 'function') {
        showAgentFinalBanner({
          name: 'APIError',
          statusCode: 400,
          message: 'prefill_bad_request',
          responseBody: '{"code":"context_length_exceeded"}',
          isRetryable: false,
        });
      }
    `, ctx);
    check("bandeau final affiche", agentErrorBannerEl.hidden, false);
    check("bandeau final = mode rouge",
      agentErrorBannerEl.className.indexOf("--final") >= 0, true);
    check("bandeau final contient 400",
      agentErrorBannerTextEl.textContent.indexOf("400") >= 0, true);
    check("info visible en final", agentErrorBannerInfoEl.hidden, false);
  } catch (e) {
    console.error("final banner err:", e);
    check("bandeau final rendu", false, true);
  }

  try {
    vm.runInContext(`
      if (typeof hideAgentErrorBanner === 'function') hideAgentErrorBanner();
    `, ctx);
    check("bandeau masque a nouveau", agentErrorBannerEl.hidden, true);
  } catch (e) {
    console.error("hide banner err:", e);
    check("hideAgentErrorBanner ok", false, true);
  }

  // 16. Erreur définitive via session.error : bandeau rouge + bouton Envoyer réarmé
  resetChatState();
  messagesPayload = { ok: true, messages: [] };
  infoPayload = null;
  try {
    vm.runInContext(`
      currentSessionId = 'ses_error_sim';
      pendingRevert = null;
      disconnectSessionStream();
      if (typeof handleSessionStreamEvent === 'function') {
        handleSessionStreamEvent({
          kind: 'error',
          sessionID: 'ses_error_sim',
          name: 'APIError',
          statusCode: 400,
          message: 'prefill_bad_request',
          responseBody: '{"code":"context_length_exceeded"}',
          isRetryable: false,
        });
      }
    `, ctx);
    check("session.error -> bandeau rouge", agentErrorBannerEl.hidden, false);
    check("session.error -> mode final", agentErrorBannerEl.className.indexOf("--final") >= 0, true);
    check("session.error -> bouton d'envoi redevient Envoyer", sendBtnEl.textContent.trim(), "↑");
    check("session.error -> info visible", agentErrorBannerInfoEl.hidden, false);
  } catch (e) {
    console.error("session.error sim err:", e);
    check("session.error simulateur", false, true);
  }

  // 17. Erreur définitive via message.updated avec error (path live alternatif)
  resetChatState();
  messagesPayload = { ok: true, messages: [] };
  infoPayload = null;
  try {
    vm.runInContext(`
      currentSessionId = 'ses_msgerr_sim';
      remoteMessages = {};
      if (typeof handleSessionStreamEvent === 'function') {
        handleSessionStreamEvent({
          kind: 'message',
          messageID: 'msg_broken',
          role: 'agent',
          completed: 9999,
          error: {
            name: 'APIError',
            statusCode: 400,
            message: 'prefill_bad_request',
            responseBody: '{}',
            isRetryable: false,
          },
        });
      }
    `, ctx);
    check("message.updated avec error -> bandeau rouge", agentErrorBannerEl.hidden, false);
    check("message.updated -> mode final", agentErrorBannerEl.className.indexOf("--final") >= 0, true);
    // Une interruption volontaire ne doit pas lever le bandeau
    vm.runInContext(`
      if (typeof hideAgentErrorBanner === 'function') hideAgentErrorBanner();
      if (typeof handleSessionStreamEvent === 'function') {
        handleSessionStreamEvent({
          kind: 'message',
          messageID: 'msg_abort',
          role: 'agent',
          completed: 9999,
          error: {
            name: 'MessageAbortedError',
            statusCode: null,
            message: 'Aborted',
            responseBody: '',
            isRetryable: false,
          },
        });
      }
    `, ctx);
    check("message.updated MessageAbortedError -> pas de bandeau", agentErrorBannerEl.hidden, true);
  } catch (e) {
    console.error("message.updated err sim err:", e);
    check("message.updated simulateur", false, true);
  }

  // 18. Rechargement sur une session dont le dernier tour assistant porte info.error
  resetChatState();
  messagesPayload = { ok: true, messages: [
    { role: 'user', text: 'debug moi', message_id: 'msg_u_err', seq: 1 },
    { role: 'agent', text: 'Voici…', message_id: 'msg_a_err', seq: 2,
      items: [{ kind: 'text', text: 'Voici…', part_id: 'prt_a_err' }],
      error: {
        name: 'APIError',
        statusCode: 400,
        message: 'prefill_bad_request',
        responseBody: '{"code":"context_length_exceeded"}',
        isRetryable: false,
      } },
  ] };
  infoPayload = null;
  try {
    await ctx.loadSessionMessages('ses_error_sim');
    check("reload avec info.error -> bandeau rouge", agentErrorBannerEl.hidden, false);
    check("reload avec info.error -> mode final", agentErrorBannerEl.className.indexOf("--final") >= 0, true);
  } catch (e) {
    console.error("reload info.error sim err:", e);
    check("reload avec info.error simulateur", false, true);
  }

  // 19. Tour interne Compaction : message agent compaction affiche le badge,
  // puis le replay post-compaction (agent normal) le masque silencieusement.
  resetChatState();
  messagesPayload = { ok: true, messages: [] };
  infoPayload = null;
  try {
    vm.runInContext(`
      currentSessionId = 'ses_compact_sim';
      if (typeof handleSessionStreamEvent === 'function') {
        handleSessionStreamEvent({
          kind: 'message',
          messageID: 'msg_compaction',
          role: 'agent',
          agent: 'compaction',
          completed: null,
        });
      }
    `, ctx);
    check("compaction badge affiche", compactionBadgeEl.hidden, false);
    check("working strip masqué pendant Compaction", agentWorkingEl.hidden, true);
  } catch (e) {
    console.error("compaction sim err:", e);
    check("compaction simulateur", false, true);
  }
  try {
    vm.runInContext(`
      if (typeof handleSessionStreamEvent === 'function') {
        handleSessionStreamEvent({
          kind: 'message',
          messageID: 'msg_replay',
          role: 'agent',
          agent: 'bamse',
          completed: null,
        });
      }
    `, ctx);
    check("compaction badge masqué à la reprise", compactionBadgeEl.hidden, true);
  } catch (e) {
    console.error("compaction replay sim err:", e);
    check("compaction replay simulateur", false, true);
  }

  // 20. Mentions @fichier : parse attachements et coloration --accent
  resetChatState();
  messagesPayload = { ok: true, messages: [] };
  infoPayload = null;
  try {
    const matchData = vm.runInContext(`
      buildMentionAttachments('debug @src/foo.ts please');
      buildMentionAttachments('explique @docs/cli.md#20-50 en détails');
      buildMentionAttachments('@src/README.md#42');
      ({
        single: buildMentionAttachments('debug @src/foo.ts please'),
        range:  buildMentionAttachments('explique @docs/cli.md#20-50 en détails'),
        line:   buildMentionAttachments('@src/README.md#42'),
        empty:  buildMentionAttachments('salut'),
      });
    `, ctx);
    check("mention simple détectée", matchData.single.map((a) => a.path), ["src/foo.ts"]);
    check("plage de lignes détectée", matchData.range.map((a) => ({ path: a.path, endLine: a.endLine })), [{ path: "docs/cli.md", endLine: 50 }]);
    check("ligne unique détectée", matchData.line.map((a) => a.endLine), [42]);
    check("texte sans mention", matchData.empty, []);
  } catch (e) {
    console.error("mentions sim err:", e);
    check("mentions parse simulateur", false, true);
  }
  try {
    vm.runInContext(`
      if (typeof renderMentionHighlight === 'function') {
        currentSessionId = 'ses_mention';
        var input = document.getElementById('chat-message-input');
        input.value = 'blackbox @src/foo.ts done';
        renderMentionHighlight();
      }
    `, ctx);
    check("mention colorée dans --accent",
      highlightCodeEl.innerHTML.indexOf('mention-accent') >= 0, true);
    check("mention filename visible",
      highlightCodeEl.innerHTML.indexOf('src/foo.ts') >= 0, true);
  } catch (e) {
    console.error("highlight sim err:", e);
    check("highlight simulateur", false, true);
  }

  // 20b. Bandeau d'avertissement éphémère : attaché au-dessus du composer (emplacement des bandeaux)
  resetChatState();
  messagesPayload = { ok: true, messages: [] };
  infoPayload = null;
  warningBannerEl.hidden = true;
  elements["chat-warning-banner"] = warningBannerEl;
  try {
    vm.runInContext(`
      currentSessionId = 'ses_warn_sim';
      if (typeof showMentionWarning === 'function') showMentionWarning('Test : fichier non lisible.');
    `, ctx);
    check("warning banner affiche", warningBannerEl.hidden, false);
    check("warning banner contient le texte",
      warningBannerTextEl.textContent.indexOf('fichier non lisible') >= 0, true);
  } catch (e) {
    console.error("warning banner err:", e);
    check("warning banner rendu", false, true);
  }

  console.log(process.exitCode ? "ECHEC" : "OK : simulation front-end validee");
})();
