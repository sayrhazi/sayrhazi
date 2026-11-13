/*
 * sayrhazi-serveur — interface du dashboard Sayrhazi
 * Lit l'API /api/data et /report/:agent depuis le serveur unifié.
 * Une seule page : sidebar à gauche, contenu principal à droite.
 */

let currentData = null;
let intervalSeconds = 2;
let selectedAgent = null;
let selectedChatAgent = null;
let currentSessionId = null;
let rawToolTexts = [];
let btnDebugJsonEl = null;
let watcherRunning = false;
let currentView = null;
let isNavigating = false;
let lastPath = null;

// Charge diff.esm.js (implementation maison de diffLines) pour le split view
// des outils Edit/Patch. Le paquet npm "diff" n'est pas toujours installe,
// d'ou l'usage du module ES embarque dans static/.
// diff.esm.js expose {type, lines[]} : on convertit au format jsdiff attendu
// par buildSplitRows ({value, count, added, removed}).
(function loadDiffModule() {
  try {
    import("/diff.esm.js").then(function (m) {
      if (m && typeof m.diffLines === "function") {
        window.Diff = {
          diffLines: function (oldText, newText) {
            var ops = m.diffLines(oldText || "", newText || "") || [];
            return ops.map(function (op) {
              var lines = op.lines || [];
              return {
                value: lines.join("\n"),
                count: lines.length,
                added: op.type === "insert",
                removed: op.type === "delete",
              };
            });
          },
        };
      }
    }).catch(function () { /* module indisponible */ });
  } catch (e) { /* import dynamique non supporte */ }
})();

// SSE streaming (étape 4 du plan)
let eventSource = null;
let sseConnected = false;
let sseReconnectTimeout = null;
let sseSessionId = null;

// Cycle de vie des discussions (création / renommage natif / suppression).
// Flux SSE `/event` d'OpenCode : session.created, session.updated (titre
// généré par l'agent natif `title`), session.deleted.
let sessionEventSource = null;
let sessionEventRetry = null;

// Sessions affichées dans la barre latérale (indexées par identifiant).
let chatSessions = [];
let chatSessionMenu = null;
// Compteur de génération : deux rechargements concurrents (création + événement
// `session.created`) ne doivent pas se superposer dans la barre latérale.
let chatListGeneration = 0;
// Veille de la réponse de l'agent (indicateur « en cours de réponse »).
let agentReplyTimer = null;
let agentReplyWatch = null;
// Dernières données des messages de la session chargée (clés par message_id).
// Le flux SSE /api/chat/stream les met à jour en place ; le rechargement
// complet fait autorité.
let remoteMessages = {};
let sessionStreamSource = null;
let sessionStreamRetry = 0;
let sessionStreamRetryTimer = null;
// Dernier signal `session.idle` reçu par session (OpenCode signale la fin de
// tour par cet événement) : permet de ne pas recharger trop tôt, quand un
// message d'outil est déjà terminé mais que l'agent continue.
let sessionIdleAt = {};
const AGENT_REPLY_POLL_MS = 1500;
const AGENT_REPLY_TIMEOUT_MS = 15 * 60 * 1000;
const AGENT_REPLY_STABLE_MS = 6000;

// Noms d'affichage des agents (au lieu des noms de fichiers)
const AGENT_DISPLAY_NAME = {
  architect: "Lawibrahim",
  "build-front": "Ali",
  "build-back": "Bamse",
  reviewer: "Hadji",
  security: "Hifadhui",
  qa: "Zawadi",
  frontend: "Ali",
  backend: "Bamse"
};

document.addEventListener("DOMContentLoaded", async () => {
  // Vider complètement le sessionStorage pour éviter toute restauration d'ancien état
  sessionStorage.clear();

  const script = document.querySelector('script[src*="sayrhazi-server"]');
  if (script && script.dataset.interval) {
    intervalSeconds = parseInt(script.dataset.interval, 10) || 2;
  }

  initNavigation();

  // Gérer les boutons retour/avance du navigateur
  window.addEventListener("popstate", initRouter);

  await loadData();
  await fetchWatcherStatus();
  startRefresh();

  initChatInput();

  const btnReport = document.getElementById("btn-view-report");
  if (btnReport) btnReport.addEventListener("click", (e) => { e.preventDefault(); showAgent(btnReport.dataset.agent); });

  const btnDebugJson = document.getElementById("btn-debug-json");
  btnDebugJsonEl = btnDebugJson;
  const debugPanel = document.getElementById("debug-json-panel");
  if (btnDebugJson && debugPanel) {
    btnDebugJson.addEventListener("click", (e) => {
      e.preventDefault();
      if (debugPanel.style.display === "none") {
        var lines = [];
        rawToolTexts.forEach(function (t, idx) {
          lines.push("=== #" + (idx + 1) + " ===");
          lines.push("tool: " + t.tool);
          lines.push("file: " + t.file);
          lines.push("time: " + t.time);
          lines.push("--- texte brut ---");
          lines.push(t.text);
          lines.push("");
        });
        debugPanel.textContent = lines.join("\n") || "(aucun message tool capturé)";
        debugPanel.style.display = "block";
        btnDebugJson.textContent = "Masquer JSON";
      } else {
        debugPanel.style.display = "none";
        btnDebugJson.textContent = "Debug JSON";
      }
    });
  }

  const btnBack = document.getElementById("btn-back-to-chat");
  if (btnBack) btnBack.addEventListener("click", (e) => { e.preventDefault(); showView("chat"); if (selectedChatAgent) showChat(selectedChatAgent); });

  initRouter();
});

function initRouter() {
  const path = window.location.pathname;
  if (path === lastPath) {
    return;
  }
  lastPath = path;
  if (path === "/" || path === "/dashboard") {
    selectedAgent = null;
    selectedChatAgent = null;
    sessionStorage.removeItem('sayrhazi_selected_agent');
    sessionStorage.removeItem('sayrhazi_selected_chat_agent');
    showView("dashboard");
    highlightNavLink(document.querySelector('.nav-list a[data-view="dashboard"]'));
    // Retirer l'active sur les liens agents si présent
    document.querySelectorAll('.nav-list a[data-agent]').forEach(a => a.classList.remove('active'));
    const btnReport = document.getElementById("btn-view-report");
    if (btnReport) btnReport.style.display = "none";
    if (btnDebugJsonEl) btnDebugJsonEl.style.display = "none";
  } else if (path.startsWith("/agents/")) {
    const agentFile = path.split("/agents/")[1];
    selectedAgent = null;
    sessionStorage.removeItem('sayrhazi_selected_agent');
    showView("chat");
    showChat(agentFile);
  }
}

let lastChatCall = 0;

function navigateTo(url, viewName, agentFile) {
  if (isNavigating) {
    return;
  }
  isNavigating = true;
  setTimeout(() => { isNavigating = false; }, 1000);

  const now = Date.now();
  if (now - lastChatCall < 300) {
    return;
  }
  lastChatCall = now;

  lastPath = null; // Permettre à initRouter de s'exécuter sur le nouveau path
  history.pushState(null, "", url);
  selectedAgent = null;
  sessionStorage.removeItem('sayrhazi_selected_agent');

  if (viewName === "dashboard") {
    showView("dashboard");
    highlightNavLink(document.querySelector('.nav-list a[data-view="dashboard"]'));
    document.querySelectorAll('.nav-list a[data-agent]').forEach(a => a.classList.remove('active'));
  } else if (viewName === "chat") {
    showView("chat");
    if (agentFile) {
      sessionStorage.setItem('sayrhazi_selected_chat_agent', agentFile);
      showChat(agentFile);
    } else if (selectedChatAgent) {
      showChat(selectedChatAgent);
    } else {
      const chatLinkEl = document.querySelector('.nav-list a[data-view="chat"]');
      if (chatLinkEl) highlightNavLink(chatLinkEl);
      document.querySelectorAll('.nav-list a[data-agent]').forEach(a => a.classList.remove('active'));
      const btnReport = document.getElementById("btn-view-report");
      if (btnReport) btnReport.style.display = "none";
    if (btnDebugJsonEl) btnDebugJsonEl.style.display = "none";
    }
  }
}

function initNavigation() {
  document.querySelectorAll(".nav-list a").forEach(link => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      const view = link.dataset.view;
      const agent = link.dataset.agent;

      if (view === "dashboard") {
        navigateTo("/dashboard", "dashboard");
      } else if (agent) {
        navigateTo("/agents/" + agent, "chat", agent);
      }
    });
  });
}

function showView(viewName) {
  currentView = viewName;
  document.querySelectorAll(".view").forEach(el => {
    el.classList.remove("active");
    el.classList.add("hidden");
  });
  const view = document.getElementById("view-" + viewName);
  if (view) {
    view.classList.remove("hidden");
    view.classList.add("active");
  } else {
    console.warn("[app.js] vue introuvable:", viewName);
  }
}

function highlightNavLink(link) {
  document.querySelectorAll(".nav-list a").forEach(a => a.classList.remove("active"));
  link.classList.add("active");
}

async function loadData() {
  try {
    const response = await fetch("/api/data");
    if (!response.ok) throw new Error("Erreur HTTP " + response.status);
    const data = await response.json();

    // Comparer sans le champ 'now' pour éviter le rafraîchissement inutile
    const clone = (d) => { const c = JSON.parse(JSON.stringify(d)); delete c.now; return c; };
    const prev = currentData ? JSON.stringify(clone(currentData)) : null;
    const curr = JSON.stringify(clone(data));
    if (prev === curr) return;

    currentData = data;
    renderDashboard(currentData);
    await fetchWatcherStatus();
    await fetchWatcherLogs();
  } catch (err) {
    console.error("Erreur chargement données:", err);
    document.getElementById("dashboard-table-body").innerHTML =
      '<tr><td colspan="13">Erreur de chargement des données</td></tr>';
  }
}

function startRefresh() {
  setInterval(loadData, intervalSeconds * 1000);
}

function renderDashboard(data) {
  if (!data) return;

  // En-tête
  document.getElementById("page-title").textContent = "Sayrhazi — " + data.task_id;
  document.getElementById("status-badge").textContent = data.status;
  document.getElementById("watcher-status").textContent = data.watcher;

  // Cartes récapitulatives
  document.getElementById("card-scope").textContent = data.scope;
  document.getElementById("card-controls").textContent =
    Object.entries(data.controls).map(kv => kv[0] + "=" + kv[1]).join(" · ") || "—";
  document.getElementById("card-archived").textContent = data.archived.join(", ") || "—";

  // Carte Watcher : état + PID si en cours
  const watcher = data.watcher || "arrêté";
  const watcherStatusEl = document.getElementById("card-watcher-status");
  const watcherPidEl = document.getElementById("card-watcher-pid");
  const isRunning = watcher.toLowerCase().startsWith("en cours");
  if (watcherStatusEl) {
    watcherStatusEl.textContent = isRunning ? "en cours" : "arrêté";
    watcherStatusEl.className = "badge " + (isRunning ? "bg-success" : "bg-danger");
  }
  if (watcherPidEl) {
    const m = watcher.match(/PID\s+(\d+)/i);
    watcherPidEl.textContent = m ? ("PID " + m[1]) : "—";
  }

  // Tableau des agents
  const tbody = document.getElementById("dashboard-table-body");
  tbody.innerHTML = "";

  data.agents.forEach(a => {
    const tr = document.createElement("tr");
    tr.dataset.agentFile = a.agent_file;
    tr.innerHTML = `
      <td class="agent">${colorizeReport(a.agent_file)}</td>
      <td class="role">${colorizeReport(a.role)}</td>
      <td>${esc(a.sid)}</td>
      <td>${esc(a.origine)}</td>
      <td>${esc(a.tokens)}</td>
      <td>${esc(a.usage_pct)}</td>
      <td>${esc(a.cout_sess)}</td>
      <td>${esc(a.calls)}</td>
      <td>${esc(a.manual_calls)}</td>
      <td>${esc(a.cout_projet)}</td>
      <td>${colorizeReport(a.report)}</td>
      <td class="etat" style="color:${a.etat_color}">${colorizeReport(a.etat)}</td>
    `;
    tbody.appendChild(tr);
  });

  // Sidebar : liste des agents avec noms d'affichage
  const agentList = document.getElementById("agent-list");
  agentList.innerHTML = "";
  data.agents.forEach(a => {
    const li = document.createElement("li");
    li.innerHTML = '<a href="#" data-agent="' + esc(a.agent_file) + '">' +
      esc(getAgentDisplayName(a.agent_file)) + '</a>';
    li.querySelector("a").addEventListener("click", (e) => {
      e.preventDefault();
      selectedAgent = null;
      sessionStorage.removeItem('sayrhazi_selected_agent');
      selectedChatAgent = a.agent_file;
      sessionStorage.setItem('sayrhazi_selected_chat_agent', a.agent_file);
      navigateTo("/agents/" + a.agent_file, "chat", a.agent_file);
      highlightNavLink(e.target);
    });
    if (a.agent_file === selectedAgent) {
      li.querySelector("a").classList.add("active");
    }
    agentList.appendChild(li);
  });

  // Synchroniser l'état active du lien "Vue d'ensemble" selon l'agent sélectionné
  const dashboardLink = document.querySelector('.nav-list a[data-view="dashboard"]');
  if (dashboardLink) {
    if (currentView !== "dashboard") {
      dashboardLink.classList.remove('active');
    } else if (selectedAgent) {
      dashboardLink.classList.remove('active');
    } else {
      dashboardLink.classList.add('active');
    }
  }

  // Restaurer le lien agent actif dans la sidebar si on est sur le chat d'un agent
  if (currentView === "chat" && selectedChatAgent) {
    document.querySelectorAll('.nav-list a[data-agent]').forEach(a => {
      a.classList.toggle('active', a.dataset.agent === selectedChatAgent);
    });
  }

  // Journal du dernier jour travaillé
  fetch("/api/logs")
    .then(r => r.text())
    .then(text => {
      document.getElementById("logs-content").textContent = text;
    })
    .catch(err => {
      document.getElementById("logs-content").textContent = "Erreur de chargement du journal.";
    });
}

function showAgent(agentFile) {

  if (!currentData) {
    console.error("[app.js] currentData est null!");
    return;
  }

  const agent = currentData.agents.find(a => a.agent_file === agentFile);
  if (!agent) {
    console.error("[app.js] agent non trouvé:", agentFile);
    return;
  }

  // Afficher la vue agent
  document.querySelectorAll(".view").forEach(el => {
    el.classList.remove("active");
    el.classList.add("hidden");
  });
  const agentView = document.getElementById("view-agent");
  agentView.classList.remove("hidden");
  agentView.classList.add("active");

  // Cacher le bouton Voir rapport, garder le lien agent actif
  const btnReport = document.getElementById("btn-view-report");
  if (btnReport) btnReport.style.display = "none";
    if (btnDebugJsonEl) btnDebugJsonEl.style.display = "none";
  document.querySelectorAll('.nav-list a[data-agent]').forEach(a => {
    a.classList.toggle('active', a.dataset.agent === agentFile);
  });
  const dashboardLink = document.querySelector('.nav-list a[data-view="dashboard"]');
  if (dashboardLink) dashboardLink.classList.remove('active');
  const chatLink = document.querySelector('.nav-list a[data-view="chat"]');
  if (chatLink) chatLink.classList.remove('active');
  const el = (id) => document.getElementById(id);
  if (el("detail-agent-name")) el("detail-agent-name").textContent = agent.agent_file;
  if (el("detail-agent-role")) el("detail-agent-role").textContent = agent.role;
  if (el("detail-agent-file")) el("detail-agent-file").textContent = agent.report;
  if (el("detail-agent-etat")) {
    el("detail-agent-etat").textContent = agent.etat;
    el("detail-agent-etat").style.color = agent.etat_color;
  }
  if (el("detail-agent-session")) el("detail-agent-session").textContent = agent.sid;
  if (el("detail-agent-origine")) el("detail-agent-origine").textContent = agent.origine;
  if (el("detail-agent-report")) el("detail-agent-report").textContent = agent.report;

  // Charger le contenu du rapport
  fetch("/report/" + agent.report)
    .then(r => r.text())
    .then(text => {
      document.getElementById("report-content").innerHTML = colorizeReport(text);
    })
    .catch(err => {
      document.getElementById("report-content").textContent = "Erreur de chargement du rapport.";
    });
}

/* --- Chat avec les agents (deux panneaux : sessions | discussion) --- */

function showChat(agentFile) {

  // Guard : ne pas afficher le chat si on n'est pas sur la vue chat
  if (currentView !== "chat") {
    return;
  }

  // Éviter les appels redondants si déjà sur ce chat
  if (selectedChatAgent === agentFile && document.getElementById("view-chat").classList.contains("active")) {
    selectedChatAgent = agentFile;
    sessionStorage.setItem('sayrhazi_selected_chat_agent', agentFile);
  } else {
    selectedChatAgent = agentFile;
    sessionStorage.setItem('sayrhazi_selected_chat_agent', agentFile);
    currentSessionId = null;
  }

  // Mettre à jour le header
  const displayName = getAgentDisplayName(agentFile);
  const titleEl = document.getElementById("chat-agent-name");
  const statusEl = document.getElementById("chat-agent-status");
  if (titleEl) titleEl.textContent = displayName;
  if (statusEl) {
    statusEl.textContent = "en ligne";
    statusEl.className = "badge bg-success";
  }

  // Afficher le bouton Voir rapport
  const btnReport = document.getElementById("btn-view-report");
  if (btnReport) {
    btnReport.dataset.agent = agentFile;
    btnReport.style.display = "inline-block";
  }

  // Afficher le bouton Debug JSON
  const btnDebugJson = document.getElementById("btn-debug-json");
  if (btnDebugJson) btnDebugJson.style.display = "inline-block";

  // Afficher la vue chat
  showView("chat");

  // Mettre en surbrillance le lien agent dans la sidebar si présent
  document.querySelectorAll('.nav-list a[data-agent]').forEach(a => {
    a.classList.toggle('active', a.dataset.agent === agentFile);
  });
  // Retirer l'active sur "Vue d'ensemble" quand on est sur un agent
  const dashboardLink = document.querySelector('.nav-list a[data-view="dashboard"]');
  if (dashboardLink) dashboardLink.classList.remove('active');

  // Réinitialiser les deux panneaux
  currentSessionId = null;
  disconnectSSE();
  disconnectSessionStream();
  stopAgentReplyWatch();
  const conv = document.getElementById("chat-conversation");
  if (conv) {
    conv.innerHTML = '<div class="chat-empty">Sélectionnez une session à gauche pour commencer la discussion, ou créez une nouvelle session.</div>';
  }

  // Charger la liste des sessions dans le panneau gauche
  fetchChatSessions(agentFile);

  // Suivre le cycle de vie des discussions (titre natif, création, suppression)
  connectSessionEvents();
}

async function fetchChatSessions(agentFile) {
  const listEl = document.getElementById("chat-session-list");
  if (!listEl) return;
  const generation = ++chatListGeneration;
  listEl.innerHTML = '';

  try {
    const response = await fetch("/api/chat/history/" + encodeURIComponent(agentFile));
    if (!response.ok) throw new Error("Erreur HTTP " + response.status);
    const data = await response.json();
    // Un rechargement plus récent a pris le relais : on ne rend rien, sinon
    // les discussions apparaissent en double dans la barre latérale.
    if (generation !== chatListGeneration) return;
    const messages = data.messages || [];
    chatSessions = messages.slice();

    if (messages.length === 0) {
      listEl.innerHTML = '<div class="chat-empty">Aucune session encore. Cliquez sur « Nouvelle session » pour commencer une conversation.</div>';
      return;
    }

    const fragment = document.createDocumentFragment();
    messages.forEach(m => {
      const item = document.createElement("div");
      item.className = "chat-session-item" + (m.session === currentSessionId ? " active" : "") + (m.auto ? " chat-session-auto" : "");
      item.dataset.sessionId = m.session || "";
      const title = chatSessionTitle(m);
      const time = m.time ? " · " + m.time : "";
      item.innerHTML =
        '<div class="chat-session-title">' + esc(title) + '</div>' +
        '<div class="chat-session-meta">' + esc(time) + '</div>' +
        '<button class="chat-session-delete" type="button" title="Supprimer la discussion" aria-label="Supprimer la discussion">✕</button>';
      item.addEventListener("click", (e) => {
        if (e.target && e.target.classList.contains("chat-session-delete")) return;
        e.preventDefault();
        e.stopPropagation();
        loadSessionMessages(m.session, m.title);
      });
      // Menu contextuel : suppression de la discussion.
      item.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        e.stopPropagation();
        openChatSessionMenu(e.clientX, e.clientY, m.session, title);
      });
      const delBtn = item.querySelector(".chat-session-delete");
      if (delBtn) {
        delBtn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          confirmAndDeleteSession(m.session, title);
        });
      }
      fragment.appendChild(item);
    });
    // Remplacement atomique : la liste n'est jamais à moitié rendue.
    listEl.innerHTML = '';
    listEl.appendChild(fragment);
  } catch (err) {
    if (generation !== chatListGeneration) return;
    console.error("Erreur chargement sessions:", err);
    listEl.innerHTML = '<div class="chat-empty">Erreur de chargement des sessions.</div>';
  }
}

// Affiche l'indicateur « l'agent est en cours de réponse ».
function showAgentWorkingIndicator() {
  const conv = document.getElementById("chat-conversation");
  if (!conv) return null;
  const div = document.createElement("div");
  div.className = "chat-message agent chat-agent-working";
  div.innerHTML =
    '<div class="chat-agent-working-inner">' +
    '<span class="chat-spinner" aria-hidden="true"></span>' +
    '<span>L’agent est en cours de réponse…</span>' +
    '</div>';
  conv.appendChild(div);
  scrollChatToBottom();
  return div;
}

// Arrête la veille de la réponse en cours.
function stopAgentReplyWatch() {
  const etaitActive = agentReplyWatch !== null;
  if (agentReplyTimer !== null) {
    clearTimeout(agentReplyTimer);
    agentReplyTimer = null;
  }
  agentReplyWatch = null;
  disconnectSessionStream();
  // Si une attente était en cours (session changée, vue réinitialisée,
  // réponse détectée, erreur, ou timeout), le bouton d'envoi redevient
  // disponible.
  if (etaitActive) {
    setSendButtonState("send");
  }
}

// Arrête le flux SSE /api/chat/stream et réinitialise son compteur de
// réessaie.
function disconnectSessionStream() {
  if (sessionStreamRetryTimer !== null) {
    clearTimeout(sessionStreamRetryTimer);
    sessionStreamRetryTimer = null;
  }
  if (sessionStreamSource) {
    try { sessionStreamSource.close(); } catch (e) { /* ignore */ }
    sessionStreamSource = null;
  }
  sessionStreamRetry = 0;
}

// Réconciliation finale après session.idle : récupère les messages en
// silence et pose le pied natif (durée de tour) sur les blocs existants,
// sans reconstruire la conversation ni afficher de chargement.
async function finalizeLiveConversation(sessionId) {
  try {
    const response = await fetch("/api/chat/session/" + encodeURIComponent(sessionId) + "/messages");
    if (!response.ok) throw new Error("HTTP " + response.status);
    const data = await response.json();
    const messages = (data.messages || []).slice();
    messages.forEach(m => { if (m && m.message_id) remoteMessages[m.message_id] = m; });
    messages.forEach(m => {
      if (!m || m.role !== "agent" || !m.series_end) return;
      const block = document.querySelector('[data-message-id="' + cssEscape(m.message_id) + '"]');
      if (!block) return;
      const agentLabel = (selectedChatAgent ? getAgentDisplayName(selectedChatAgent) : "Agent");
      const model = m.model || "";
      const duration = m.duration || "";
      if (!model && !duration) return;
      const foot = '<div class="agent-block-foot">▣ ' + esc(agentLabel) + ' · ' +
        (model ? esc(model) + (duration ? ' · ' : '') : '') + esc(duration) + '</div>';
      const existing = block.querySelector('.agent-block-foot');
      if (existing) {
        existing.outerHTML = foot;
      } else {
        const wrapper = document.createElement("div");
        wrapper.innerHTML = foot;
        block.appendChild(wrapper.firstElementChild);
      }
    });
    return true;
  } catch (err) {
    console.error("Finalisation en direct impossible:", err);
    return false;
  }
}

// Calcule et remplace un item agent dans le DOM sans reconstruire tout le bloc.
const streamRenderTimers = {};
function scheduleItemRender(msg, item) {
  const key = (msg.message_id || "") + ":" + (item.part_id || "");
  if (streamRenderTimers[key]) clearTimeout(streamRenderTimers[key]);
  streamRenderTimers[key] = setTimeout(function () {
    delete streamRenderTimers[key];
    renderStreamItemInPlace(msg, item);
  }, 45);
}

function renderStreamItemInPlace(msg, item) {
  const container = document.getElementById("chat-conversation");
  if (!container) return;
  let block = container.querySelector('[data-message-id="' + cssEscape(msg.message_id) + '"]');
  if (!block) {
    block = document.createElement("div");
    block.className = "chat-message agent";
    block.dataset.messageId = msg.message_id;
    container.appendChild(block);
  }
  const itemHtml = renderAgentItemHtml(item, msg.role || "agent");
  const existing = block.querySelector('[data-part-id="' + cssEscape(item.part_id || "") + '"]');
  if (existing) {
    existing.outerHTML = itemHtml;
  } else {
    const wrapper = document.createElement("div");
    wrapper.innerHTML = itemHtml;
    const foot = block.querySelector('.agent-block-foot');
    while (wrapper.firstChild) {
      const node = wrapper.firstChild;
      if (foot) block.insertBefore(node, foot);
      else block.appendChild(node);
    }
  }
  const newEl = block.querySelector('[data-part-id="' + cssEscape(item.part_id || "") + '"]');
  if (newEl) applySyntaxHighlight(newEl);
  wireAgentBlock(block);
}

// Routeur des événements du flux SSE /api/chat/stream.
function handleSessionStreamEvent(ev) {
  if (!ev || !ev.kind) return;

  if (ev.kind === "part") {
    const msg = remoteMessages[ev.messageID] || (remoteMessages[ev.messageID] = {
      role: "agent", text: "", reasoning: "", tools: [],
      items: [], model: "", parent_id: "",
      message_id: ev.messageID, time: "",
      metrics_only: false, duration: "", series_end: false, completed: null, seq: 0,
    });
    if (msg.role === "user") return;
    const items = msg.items || (msg.items = []);
    let idx = -1;
    for (let i = 0; i < items.length; i++) {
      if (items[i] && items[i].part_id === ev.item.part_id) { idx = i; break; }
    }
    if (idx >= 0) items[idx] = ev.item; else items.push(ev.item);
    scheduleItemRender(msg, ev.item);
    if (isScrolledToBottom()) scrollChatToBottom();
    return;
  }

  if (ev.kind === "text_delta") {
    const msg = remoteMessages[ev.messageID];
    if (!msg || msg.role === "user") return;
    const items = msg.items || (msg.items = []);
    let item = null;
    for (let i = 0; i < items.length; i++) {
      if (items[i] && items[i].part_id === ev.partID) { item = items[i]; break; }
    }
    if (!item) {
      item = { kind: "text", text: "", part_id: ev.partID };
      items.push(item);
    }
    item.text = (item.text || "") + String(ev.delta || "");
    scheduleItemRender(msg, item);
    if (isScrolledToBottom()) scrollChatToBottom();
    return;
  }

  if (ev.kind === "message") {
    if (!remoteMessages[ev.messageID]) {
      remoteMessages[ev.messageID] = {
        role: ev.role || "agent", text: "", reasoning: "", tools: [],
        items: [], model: "", parent_id: "",
        message_id: ev.messageID, time: "",
        metrics_only: false, duration: "", series_end: false,
        completed: ev.completed || null, seq: 0,
      };
    } else {
      if (ev.role) remoteMessages[ev.messageID].role = ev.role;
      if (ev.completed) remoteMessages[ev.messageID].completed = ev.completed;
    }
    return;
  }

  if (ev.kind === "idle" || ev.kind === "done") {
    disconnectSessionStream();
    stopAgentReplyWatch();
    document.querySelectorAll(".chat-agent-working").forEach(el => el.remove());
    setSendButtonState("send");
    if (currentSessionId) {
      // Pas de rechargement global : juste la pose du pied natif
      // (durée de tour) sur les blocs déjà rendus.
      finalizeLiveConversation(currentSessionId);
    }
    return;
  }

  if (ev.kind === "error") {
    disconnectSessionStream();
    stopAgentReplyWatch();
    document.querySelectorAll(".chat-agent-working").forEach(el => el.remove());
    const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    appendChatMessage("system", "Erreur : " + (ev.message || "inconnue"), now);
    setSendButtonState("send");
  }
}

// Ouvre la connexion SSE /api/chat/stream pour la session, avec réessai.
function connectSessionStream(sessionId) {
  disconnectSessionStream();
  sessionStreamSource = new EventSource("/api/chat/stream?session_id=" + encodeURIComponent(sessionId));
  sessionStreamSource.onopen = function () { sessionStreamRetry = 0; };
  sessionStreamSource.onmessage = function (e) {
    let ev;
    try { ev = JSON.parse(e.data); } catch (err) { return; }
    handleSessionStreamEvent(ev);
  };
  sessionStreamSource.onerror = function () {
    disconnectSessionStream();
    sessionStreamRetry = (sessionStreamRetry || 0) + 1;
    if (sessionStreamRetry <= 3) {
      sessionStreamRetryTimer = setTimeout(function () { connectSessionStream(sessionId); }, 3000);
      return;
    }
    // Le flux est mort : on recharge la discussion et le bouton redevient actif.
    loadSessionMessages(sessionId);
    setSendButtonState("send");
  };
}

// Attend la fin du tour de l'agent (time.completed du dernier message
// assistant) puis recharge la discussion. L'indicateur « en cours de
// réponse » disparait quand la réponse est arrivée.
function waitForAgentReply(sessionId, indicator) {
  stopAgentReplyWatch();
  const startedAt = Date.now();
  let lastCount = -1;
  let lastChangeAt = Date.now();
  agentReplyWatch = { sessionId: sessionId, indicator: indicator };

  const tick = async () => {
    if (!agentReplyWatch || agentReplyWatch.sessionId !== sessionId) return;
    let done = false;
    try {
      const response = await fetch("/api/chat/session/" + encodeURIComponent(sessionId) + "/messages");
      if (response.ok) {
        const data = await response.json();
        const messages = data.messages || [];
        const assistants = messages.filter(m => m.role === "agent");
        if (assistants.length !== lastCount) {
          lastCount = assistants.length;
          lastChangeAt = Date.now();
        }
        const last = assistants[assistants.length - 1];
        // `completed` n'est présent que lorsque le message agent est terminé.
        const completed = !!(last && last.completed);
        // Fin de tour confirmée par l'événement natif `session.idle`, ou à
        // défaut après quelques secondes sans nouveau message.
        const idle = (sessionIdleAt[sessionId] || 0) >= startedAt;
        const stable = Date.now() - lastChangeAt > AGENT_REPLY_STABLE_MS;
        if (completed && (idle || stable)) done = true;
      }
    } catch (err) {
      // Erreur réseau : on retentera au prochain tour.
    }
    if (done) {
      stopAgentReplyWatch();
      await loadSessionMessages(sessionId);
      setSendButtonState("send");
      return;
    }
    if (Date.now() - startedAt > AGENT_REPLY_TIMEOUT_MS) {
      stopAgentReplyWatch();
      if (indicator && indicator.parentNode) {
        indicator.innerHTML = '<div class="chat-empty">Aucune réponse de l’agent. Vérifie la session dans la TUI OpenCode.</div>';
      }
      setSendButtonState("send");
      return;
    }
    agentReplyTimer = setTimeout(tick, AGENT_REPLY_POLL_MS);
  };
  agentReplyTimer = setTimeout(tick, AGENT_REPLY_POLL_MS);
}

// Title affiché d'une session : tant qu'OpenCode n'a pas généré de titre,
// la discussion porte le titre par défaut ("New session - <horodatage>").
function chatSessionTitle(session) {
  const title = (session && session.title) ? String(session.title) : "";
  if (!title || session.default_title) return "Nouvelle session";
  return title;
}

// Met à jour le titre d'une discussion sans recharger toute la liste.
function updateChatSessionTitle(sessionId, title) {
  if (!sessionId || !title) return;
  const item = document.querySelector('.chat-session-item[data-session-id="' + cssEscape(sessionId) + '"]');
  if (item) {
    const titleEl = item.querySelector(".chat-session-title");
    if (titleEl) titleEl.textContent = title;
  }
  chatSessions.forEach(s => {
    if (s.session === sessionId) {
      s.title = title;
      s.default_title = false;
    }
  });
}

// Retire une discussion de la barre latérale (et ferme la discussion ouverte).
function removeChatSession(sessionId) {
  const item = document.querySelector('.chat-session-item[data-session-id="' + cssEscape(sessionId) + '"]');
  if (item) item.remove();
  chatSessions = chatSessions.filter(s => s.session !== sessionId);
  if (currentSessionId === sessionId) {
    currentSessionId = null;
    disconnectSSE();
    const conv = document.getElementById("chat-conversation");
    if (conv) {
      conv.innerHTML = '<div class="chat-empty">Discussion supprimée. Créez une nouvelle session ou choisissez une autre discussion.</div>';
    }
  }
}

// Échappement minimal pour un sélecteur CSS d'identifiant.
function cssEscape(value) {
  return String(value).replace(/["\\]/g, "\\$&");
}

// Menu contextuel d'une discussion (clic droit).
function openChatSessionMenu(x, y, sessionId, title) {
  closeChatSessionMenu();
  const menu = document.createElement("div");
  menu.className = "chat-session-menu";
  menu.innerHTML =
    '<div class="chat-session-menu-title">' + esc(title || "Discussion") + '</div>' +
    '<button type="button" class="chat-session-menu-item danger" data-action="delete">Supprimer la discussion</button>';
  document.body.appendChild(menu);
  chatSessionMenu = menu;

  // Position : sous le curseur, recalée pour rester dans la fenêtre.
  const rect = menu.getBoundingClientRect();
  const left = Math.min(x, window.innerWidth - rect.width - 8);
  const top = Math.min(y, window.innerHeight - rect.height - 8);
  menu.style.left = Math.max(4, left) + "px";
  menu.style.top = Math.max(4, top) + "px";

  menu.querySelector('[data-action="delete"]').addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    closeChatSessionMenu();
    confirmAndDeleteSession(sessionId, title);
  });

  setTimeout(() => {
    document.addEventListener("click", closeChatSessionMenu, { once: true });
    document.addEventListener("keydown", onChatSessionMenuKey, { once: true });
  }, 0);
}

function onChatSessionMenuKey(e) {
  if (e.key === "Escape") closeChatSessionMenu();
}

function closeChatSessionMenu() {
  if (chatSessionMenu) {
    chatSessionMenu.remove();
    chatSessionMenu = null;
  }
}

// Suppression : confirmation puis endpoint natif DELETE /api/chat/session/:id.
// La session n'est retirée de l'interface qu'après confirmation du serveur.
async function confirmAndDeleteSession(sessionId, title) {
  if (!sessionId) return;
  const label = title || "cette discussion";
  if (!window.confirm("Supprimer définitivement la discussion « " + label + " » ?\n\nLes messages et les sessions enfants seront supprimés par OpenCode.")) {
    return;
  }
  try {
    const response = await fetch("/api/chat/session/" + encodeURIComponent(sessionId), {
      method: "DELETE",
      headers: { "Content-Type": "application/json; charset=utf-8" }
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      alert("Suppression impossible : " + (data.message || data.error || "erreur " + response.status));
      return;
    }
    removeChatSession(sessionId);
  } catch (err) {
    console.error("Erreur suppression session:", err);
    alert("Erreur de connexion au serveur lors de la suppression.");
  }
}

// Flux SSE des événements de session : renommage natif du titre, création
// et suppression d'une discussion (par Sayrhazi ou depuis la TUI OpenCode).
function connectSessionEvents() {
  if (sessionEventSource !== null) return;
  try {
    sessionEventSource = new EventSource("/api/chat/session/event");
    sessionEventSource.onmessage = function (e) {
      let data;
      try {
        data = JSON.parse(e.data);
      } catch (err) {
        return;
      }
      handleSessionEvent(data);
    };
    sessionEventSource.onerror = function () {
      disconnectSessionEvents();
      sessionEventRetry = setTimeout(function () {
        sessionEventRetry = null;
        connectSessionEvents();
      }, 3000);
    };
  } catch (err) {
    console.error("[SSE session] impossible de se connecter:", err);
  }
}

function disconnectSessionEvents() {
  if (sessionEventSource !== null) {
    try {
      sessionEventSource.close();
    } catch (e) {
      // ignore
    }
    sessionEventSource = null;
  }
  if (sessionEventRetry !== null) {
    clearTimeout(sessionEventRetry);
    sessionEventRetry = null;
  }
}

// Format des événements OpenCode 1.19 : {id, type, properties: {...}}.
function handleSessionEvent(data) {
  if (!data || !data.type) return;
  const props = data.properties || {};
  const info = props.info || {};
  const sessionId = props.sessionID || info.id || "";

  if (data.type === "session.updated") {
    // OpenCode renomme la discussion après le premier message (agent natif
    // `title`) : le nouveau titre arrive dans l'événement.
    if (sessionId && info.title && !isDefaultSessionTitle(info.title)) {
      updateChatSessionTitle(sessionId, info.title);
    }
    return;
  }

  if (data.type === "session.deleted") {
    if (sessionId) removeChatSession(sessionId);
    return;
  }

  if (data.type === "session.idle") {
    // OpenCode signale la fin de tour : l'agent a fini de répondre.
    if (sessionId) sessionIdleAt[sessionId] = Date.now();
    return;
  }

  if (data.type === "session.created") {
    // Une session peut être créée ailleurs (TUI OpenCode) : on recharge la
    // liste pour rester à jour sans dupliquer les discussions. Seules les
    // sessions du projet courant concernent cette barre latérale.
    if (selectedChatAgent && isCurrentProjectSession(info)) {
      fetchChatSessions(selectedChatAgent);
    }
  }
}

// Vrai si la session appartient au projet affiché (même répertoire).
function isCurrentProjectSession(info) {
  if (!info || !info.directory) return true;
  const root = String(window.__sayrhaziRoot || "").replace(/\\/g, "/");
  if (!root) return true;
  const dir = String(info.directory).replace(/\\/g, "/");
  return dir === root || dir.indexOf(root) === 0;
}

function isDefaultSessionTitle(title) {
  return /^(New session|Child session) - \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(String(title || "").trim());
}

async function loadSessionMessages(sessionId, sessionTitle) {
  // Changer de discussion annule l'attente de la réponse en cours.
  stopAgentReplyWatch();
  disconnectSessionStream();
  remoteMessages = {};
  currentSessionId = sessionId;
  // Rouvrir (ou ouvrir) une discussion : on relit en bas (dernier message).
  chatStickToBottom = true;
  const conv = document.getElementById("chat-conversation");
  if (!conv) return;
  conv.innerHTML = '<div class="chat-empty">Chargement…</div>';

  // Mettre à jour l'item actif dans la liste
  document.querySelectorAll('.chat-session-item').forEach(item => {
    item.classList.remove('active');
  });
  // L'item actif sera re-marquée après le rechargement de la liste si besoin

  try {
    const response = await fetch("/api/chat/session/" + encodeURIComponent(sessionId) + "/messages");
    if (!response.ok) throw new Error("Erreur HTTP " + response.status);
    const data = await response.json();
    const messages = (data.messages || []).slice();
    // Garantir l'ordre chronologique selon la sequence retournee par le backend
    messages.sort((a, b) => {
      const sa = Number(a.seq);
      const sb = Number(b.seq);
      if (Number.isFinite(sa) && Number.isFinite(sb)) return sa - sb;
      return 0;
    });
    conv.innerHTML = "";

    if (messages.length === 0) {
      conv.innerHTML = '<div class="chat-empty">Aucun message dans cette session.</div>';
    } else {
      // Garder les messages en mémoire : le flux SSE /api/chat/stream les
      // remplace en place sans recharger toute la discussion.
      remoteMessages = {};
      messages.forEach(m => { if (m && m.message_id) remoteMessages[m.message_id] = m; });
      messages.forEach(m => appendChatMessage(m.role, m.text, m.time, m.metrics_only, m.is_reasoning, m.message_id, m.is_tool, m.tool, m.file, m.old_content, m.new_content, m.diff_content, m.tool_content, m.duration, m.reasoning, m.tools, m.items, m.model, m.series_end));
    }
    scrollChatToBottom();
    // Le flux SSE /api/chat/stream est ouvert lors d'un envoi (sendChatMessage),
    // pas ici.
  } catch (err) {
    console.error("Erreur chargement messages session:", err);
    conv.innerHTML = '<div class="chat-empty">Erreur de chargement des messages de la session.</div>';
  }
}

async function startNewSession() {
  if (!selectedChatAgent) return;
  try {
    // Création native : c'est OpenCode qui crée la session (vrai sessionID)
    // et qui renomme la discussion après le premier message.
    const response = await fetch("/api/chat/session", {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify({ agent: selectedChatAgent })
    });
    if (!response.ok) throw new Error("Erreur HTTP " + response.status);
    const data = await response.json();
    if (data.ok && data.session_id) {
      currentSessionId = data.session_id;
      await fetchChatSessions(selectedChatAgent);
      const conv = document.getElementById("chat-conversation");
      if (conv) {
        conv.innerHTML = '<div class="chat-empty">Nouvelle session prête. Écrivez votre premier message.</div>';
      }
      const input = document.getElementById("chat-message-input");
      if (input) input.focus();
    } else {
      alert("Impossible de créer une session : " + (data.message || "inconnue"));
    }
  } catch (err) {
    console.error("Erreur création session:", err);
    alert("Erreur de connexion au serveur.");
  }
}

async function sendChatMessage() {
  const input = document.getElementById("chat-message-input");
  const text = input.value.trim();
  if (!text || !selectedChatAgent) return;

  // Si aucune session n'est ouverte, en créer une d'abord
  if (!currentSessionId) {
    await startNewSession();
    if (!currentSessionId) return;
  }

  // Désactiver l'input pendant l'attente
  input.disabled = true;
  const btn = document.getElementById("chat-send-btn");
  if (btn) btn.disabled = true;

  // Afficher le message utilisateur immédiatement
  appendChatMessage("user", text, new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
  input.value = "";
  scrollChatToBottom();

  try {
    const response = await fetch("/api/chat/session/" + encodeURIComponent(currentSessionId) + "/prompt_async", {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify({ message: text, agent: selectedChatAgent })
    });
    if (!response.ok) throw new Error("Erreur HTTP " + response.status);
    const data = await response.json();

    if (data.ok) {
      setSendButtonState("stop");
      const indicator = showAgentWorkingIndicator();
      // Fluid SSE : met à jour les parts dès leur réception pendant que
      // l'agent répond. Le rechargement complet se fait à l'arrivée de
      // l'événement session.idle (pied natif + alignement).
      connectSessionStream(currentSessionId);
    } else {
      setSendButtonState("send");
      appendChatMessage("system", "Erreur : " + (data.message || "inconnue"), new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    }
  } catch (err) {
    setSendButtonState("send");
    console.error("Erreur envoi chat:", err);
    appendChatMessage("system", "Erreur de connexion au serveur.", new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
  } finally {
    input.disabled = false;
    if (btn) btn.disabled = false;
    input.focus();
    scrollChatToBottom();

    // Réinitialiser la hauteur du textarea
    if (input.tagName === "TEXTAREA") {
      input.style.height = "";
      input.style.height = input.scrollHeight + "px";
    }
  }
}

function getToolColor(toolName, toolText) {
  if (!toolName) return "#94a3b8";
  var key = toolName.charAt(0).toUpperCase() + toolName.slice(1).toLowerCase();
  const colors = {
    Read: "#3B82F6",
    Edit: "#F97316",
    Write: "#22C55E",
    Grep: "#A855F7",
    Glob: "#EAB308",
    Bash: "#71717A",
    List: "#06B6D4",
    Patch: "#EA580C",
    LSP: "#38BDF8",
  };
  var color = colors[key] || "#94a3b8";
  // Outils Playwright (Playwright_browser_navigate, ...) : couleur dediee
  if (toolName.toLowerCase().indexOf("playwright") === 0) {
    color = "#8B5CF6";
  }
  // Si l'outil a échoué, forcer le rouge du badge (même comportement que la TUI OpenCode)
  if (toolText && isToolFailed(toolText)) {
    color = "#EF4444";
  }
  return color;
}

// Détecte si un outil a échoué à partir de son texte brut (repr Python d'OpenCode).
function isToolFailed(text) {
  if (!text) return false;
  var t = text.toLowerCase();
  if (t.includes("could not find oldstring")) return true;
  if (t.includes("oldstring not found")) return true;
  if (t.includes("edit failed")) return true;
  if (t.includes("patch failed")) return true;
  if (t.includes("write failed")) return true;
  if (t.includes("read failed")) return true;
  if (t.includes("grep failed")) return true;
  if (t.includes("bash failed")) return true;
  if (t.includes("lsp failed")) return true;
  if (t.includes("glob failed")) return true;
  if (t.includes("list failed")) return true;
  if (t.includes("'status': 'error'") || t.includes('"status": "error"')) return true;
  if (t.includes("'status': 'failed'") || t.includes('"status": "failed"')) return true;
  return false;
}

// Extrait le message d'erreur du champ 'error' du repr Python (OpenCode outil en échec).
function extractToolError(text) {
  if (!text) return "";
  // Cherche 'error': '...' (repr Python) ou "error": "..." (JSON)
  var m = text.match(/'error':\s*'((?:[^'\\]|\\.)*)'/);
  if (m) {
    return m[1].replace(/\\'/g, "'").replace(/\\\\/g, "\\");
  }
  m = text.match(/"error":\s*"((?:[^"\\]|\\.)*)"/);
  if (m) {
    return m[1].replace(/\\"/g, '"').replace(/\\\\/g, "\\");
  }
  return "";
}

// Wrappe un bloc d'outil dans un conteneur avec erreur déroulable (garde uniquement le badge + fichier).
function wrapToolError(div, rawText) {
  if (!isToolFailed(rawText || "")) return div;
  var errorText = extractToolError(rawText || "");
  if (!errorText) return div;

  // Récupère le HTML du header existant (badge + fichier) quelque soit la structure
  var headerEl = div.querySelector(".tool-header")
    || div.querySelector(".split-header")
    || div.querySelector(".tool-simple")
    || (/\btool-simple\b/.test(div.className || "") ? div : null)
    || div.children[0];
  if (!headerEl) return div;
  var headerHtml = headerEl.innerHTML;

  // Reconstruit le div : seulement badge + fichier dans un header cliquable, puis l'erreur
  div.innerHTML = "";

  var header = document.createElement("div");
  header.className = "tool-header tool-header-error";
  header.style.cursor = "pointer";
  header.title = "Afficher / masquer l'erreur";
  header.innerHTML = headerHtml;

  var content = document.createElement("div");
  content.className = "tool-error-content";
  content.style.display = "none";
  content.innerHTML = '<div style="white-space:pre-wrap; font-family:monospace; color:#ef4444;   margin-top:0.4rem;">' + esc(errorText) + '</div>';

  div.appendChild(header);
  div.appendChild(content);

  header.addEventListener("click", (e) => {
    e.stopPropagation();
    var isOpen = content.style.display === "block";
    content.style.display = isOpen ? "none" : "block";
  });

  return div;
}

function detectToolsInText(text) {
  if (!text) return [];
  var tools = [];
  var simplified = text.replace(/[ \t]+/g, " ");
  // Cherche les motifs → Read, � Edit, → Write, � Patch n'importe où dans le texte
  var regex = /([→�])\s*(Read|Edit|Write|Patch)\s+([^\r\n]*?)(?=\s*(?:[→�]\s*(?:Read|Edit|Write|Patch)|$))/gi;
  var match;
  while ((match = regex.exec(simplified)) !== null) {
    var arrow = match[1];
    var toolName = match[2].charAt(0).toUpperCase() + match[2].slice(1).toLowerCase();
    var filePath = match[3].trim();
    if (filePath) {
      tools.push({
        tool: toolName,
        file: filePath,
        isDiff: (toolName === "Edit" || toolName === "Write" || toolName === "Patch")
      });
    }
  }
  return tools;
}

function extractFilePathFromText(text) {
  if (!text) return "";
  // 1. Clé structurée : filePath / file_path / path / filename entre quotes
  var m = text.match(/(?:filePath|file_path|path|filename)['"]?\s*:\s*['"]([^'"]+)['"]/i);
  if (m && (/[\\/]/.test(m[1]) || /^\./.test(m[1]))) return m[1];
  // 2. Chemin absolu : disque Windows C:\..., racine Unix /..., ou ./ ...
  m = text.match(/([A-Za-z]:\\[^\s'"]+|\/[^\s'"]+|\.[\\/][^\s'"]+)/);
  if (m && /[\\/]/.test(m[1])) return m[1];
  return "";
}

function extractDiffFromText(text) {
  if (!text) return "";
  // Le texte brut contient un JSON Python repr avec 'diff': "..." ou 'patch': "...".
  // Extraire la valeur du premier champ trouvé.
  var m = text.match(/'diff':\s*"([\s\S]*?)",\s*'filediff'/);
  if (!m) m = text.match(/'diff':\s*"([\s\S]*?)",\s*'additions'/);
  if (!m) m = text.match(/'patch':\s*"([\s\S]*?)",\s*'additions'/);
  if (!m) m = text.match(/'patch':\s*"([\s\S]*?)",\s*'filediff'/);
  if (!m) return "";
  var diff = m[1];
  // Nettoyer les séquences d'échappement du repr Python
  diff = diff.replace(/\\n/g, "\n");
  diff = diff.replace(/\\'/g, "'");
  diff = diff.replace(/\\"/g, '"');
  diff = diff.replace(/\\\\/g, "\\");
  return diff;
}

// Extraire le 'content' d'un appel Write depuis un JSON Python repr.
function extractContentFromText(text) {
  if (!text) return "";
  // Gère les séquences échappées (\' \\ \" \n ...) dans le repr Python.
  // Le content peut être entre guillemets simples (souvent sans apostrophe)
  // ou guillemets doubles (souvent avec apostrophe, ex: d\'Ali).
  var m = text.match(/'content':\s*"((?:[^"\\]|\\.)*)",\s*'filePath'/);
  if (!m) m = text.match(/'content':\s*'((?:[^'\\]|\\.)*)',\s*'filePath'/);
  if (!m) m = text.match(/'content':\s*"((?:[^"\\]|\\.)*)",\s*'output'/);
  if (!m) m = text.match(/'content':\s*'((?:[^'\\]|\\.)*)',\s*'output'/);
  if (!m) return "";
  var content = m[1];
  content = content.replace(/\\n/g, "\n");
  content = content.replace(/\\'/g, "'");
  content = content.replace(/\\"/g, '"');
  content = content.replace(/\\\\/g, "\\");
  return content;
}

function renderDetectedTools(tools, rawText) {
  var html = "";
  for (var i = 0; i < tools.length; i++) {
    var t = tools[i];
    if (t.isDiff) {
      // Pour les détections inline (→ Write, � Edit, → Patch) dans un message agent,
      // le texte brut peut contenir plusieurs outils : on ne peut pas associer
      // fiablement le 'content' à chaque outil. On affiche juste badge + fichier.
      // Le contenu s'affichera via renderSplitDiff quand newContent est disponible.
      html += renderToolSimple(t.tool, t.file);
    } else {
      html += renderToolSimple(t.tool, t.file);
    }
  }
  return html;
}

function renderToolSimple(tool, file, toolContent, todos, params) {
  var color = getToolColor(tool);
  var fileHtml = file ? '<span class="tool-file" style="color:' + color + ';">' + esc(file) + '</span>' : '';
  // Parametres d'appel (read [offset=30, limit=40], grep [pattern=...])
  var paramsHtml = params ? '<span class="tool-params">[' + esc(params) + ']</span>' : '';

  // Badge lisible pour les outils composes (Playwright_browser_navigate, ...)
  var badgeLabel = tool;
  var subLabel = "";
  if (tool && tool.toLowerCase().indexOf("playwright_") === 0) {
    badgeLabel = "Playwright";
    subLabel = tool.slice("Playwright_".length).replace(/^browser_/, "").replace(/_/g, " ");
  }
  var subHtml = subLabel ? '<span class="tool-sub">' + esc(subLabel) + '</span>' : '';

  // Liste de todos : rendu dedie (marqueurs colores)
  if (todos && todos.length) {
    return '<div class="tool-simple tool-simple-stacked">' +
      '<span class="tool-badge" style="background:' + color + ';">Todo</span>' +
      '<div class="tool-content-wrap todo-list">' + renderTodoList(todos) + '</div>' +
      '</div>';
  }

  var contentHtml = toolContent
    ? '<div class="tool-content-wrap">' +
      '<pre class="tool-content">' + ansiToHtml(toolContent) + '</pre>' +
      '<button type="button" class="tool-content-toggle" style="display:none;">\u22EF Click to expand</button>' +
      '</div>'
    : '';
  var wrapClass = toolContent ? "tool-simple tool-simple-stacked" : "tool-simple";
  return '<div class="' + wrapClass + '">' +
    '<span class="tool-badge" style="background:' + color + ';">' + esc(badgeLabel) + '</span>' +
    subHtml +
    fileHtml +
    paramsHtml +
    contentHtml +
    '</div>';
}

// Rend une liste de todos : [ ] blanc (a faire), [✓] vert (fait), [~] en cours.
function renderTodoList(todos) {
  var html = "";
  todos.forEach(function (td) {
    var status = String(td.status || "pending").toLowerCase();
    var done = (status === "completed" || status === "done" || status === "cancelled");
    var progress = (status === "in_progress" || status === "running" || status === "active");
    var markCls = done ? "todo-mark todo-mark-done" : (progress ? "todo-mark todo-mark-progress" : "todo-mark");
    var textCls = done ? "todo-text todo-text-done" : (progress ? "todo-text todo-text-progress" : "todo-text");
    var mark = done ? "[✓]" : (progress ? "[~]" : "[ ]");
    html += '<div class="todo-item">' +
      '<span class="' + markCls + '">' + mark + '</span>' +
      '<span class="' + textCls + '">' + esc(td.content) + '</span>' +
      '</div>';
  });
  return html;
}

// Active le depliage/repli des blocs d'outil trop longs (Bash, Playwright, ...).
// Mesure apres insertion dans le DOM pour savoir si le contenu deborde.
function setupToolContentToggles(container) {
  var wraps = container.querySelectorAll(".tool-content-wrap");
  for (var i = 0; i < wraps.length; i++) {
    (function (wrap) {
      if (wrap.dataset.wired) return;
      wrap.dataset.wired = "1";
      var pre = wrap.querySelector(".tool-content");
      var btn = wrap.querySelector(".tool-content-toggle");
      if (!pre || !btn) return;
      var update = function () {
        if (wrap.classList.contains("expanded")) {
          btn.style.display = "block";
          btn.textContent = "Click to collapse";
          return;
        }
        var overflow = pre.scrollHeight > pre.clientHeight + 2;
        wrap.classList.toggle("is-overflowing", overflow);
        btn.style.display = overflow ? "block" : "none";
        btn.textContent = "\u22EF Click to expand";
      };
      update();
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        wrap.classList.toggle("expanded");
        update();
      });
    })(wraps[i]);
  }
}

// Detecte le langage de coloration a partir de l'extension du fichier.
// highlight.js sait reconnaitre plus de 190 langages : on s'appuie sur lui
// pour la detection (aliases tsx, jsx, mjs, astro...) quand il est disponible.
function detectLang(file) {
  var f = String(file || "").toLowerCase();
  var ext = (f.match(/\.([a-z0-9]+)$/) || [])[1] || "";
  if (typeof hljs !== "undefined" && ext && hljs.getLanguage) {
    // L'extension est un nom ou un alias connu de highlight.js (ts, tsx, py, sh...)
    if (hljs.getLanguage(ext)) return ext;
    // Certains langages n'ont pas d'alias : on passe par une table
    var aliases = {
      tsx: "typescript", jsx: "javascript", mjs: "javascript", cjs: "javascript",
      mts: "typescript", cts: "typescript", astro: "xml", vue: "xml", svelte: "xml",
      htm: "xml", html: "xml", scss: "scss", sass: "scss", less: "css",
      py: "python", rb: "ruby", rs: "rust", kt: "kotlin", ps1: "powershell",
      zsh: "bash", sh: "bash", yml: "yaml", md: "markdown",
    };
    if (aliases[ext] && hljs.getLanguage(aliases[ext])) return aliases[ext];
    return "";
  }
  // Repli sans highlight.js
  if (/\.(ts|tsx|mts|cts)$/.test(f)) return "typescript";
  if (/\.(js|jsx|mjs|cjs)$/.test(f)) return "javascript";
  if (/\.py$/.test(f)) return "python";
  if (/\.(css|scss|sass|less)$/.test(f)) return "css";
  if (/\.(html|htm|astro|vue|svelte|xml)$/.test(f)) return "html";
  if (/\.json$/.test(f)) return "json";
  if (/\.(sh|bash|zsh|ps1)$/.test(f)) return "bash";
  if (/\.(yml|yaml)$/.test(f)) return "yaml";
  if (/\.sql$/.test(f)) return "sql";
  if (/\.go$/.test(f)) return "go";
  if (/\.rs$/.test(f)) return "rust";
  if (/\.java$/.test(f)) return "java";
  if (/\.(c|h|cpp|hpp)$/.test(f)) return "cpp";
  if (/\.php$/.test(f)) return "php";
  if (/\.rb$/.test(f)) return "ruby";
  return "";
}

// Colorise une ligne de code avec highlight.js (moteur multi-langages).
function highlightCode(text, lang) {
  var src = String(text == null ? "" : text);
  if (!src || !lang) return esc(src);
  if (typeof hljs === "undefined" || !hljs.highlight) return esc(src);
  try {
    return hljs.highlight(src, { language: lang, ignoreIllegals: true }).value;
  } catch (e) {
    return esc(src);
  }
}

// Rend une ligne de code : markdown inline pour les .md, coloration syntaxique sinon.
function renderCodeLine(line, lang, isMd) {
  if (isMd) return colorizeMarkdownInline(line);
  if (lang) return highlightCode(line, lang);
  return esc(line);
}

// Cellule de diff : 3 zones delimitees (numero de ligne | signe +/- | code).
function diffCellHtml(cls, num, sign, content, lang, isMd) {
  return '<td class="' + cls + '"><div class="tool-diff-cell">' +
    '<span class="tool-diff-num">' + esc(num) + '</span>' +
    '<span class="tool-diff-sign">' + esc(sign) + '</span>' +
    '<span class="tool-diff-line-content">' + renderCodeLine(content, lang, isMd) + '</span>' +
    '</div></td>';
}

// Regroupe les lignes de diff en rangees alignees : une suppression et un ajout
// correspondants se retrouvent sur la meme rangee (meme niveau vertical).
function buildAlignedRows(rows) {
  var out = [];
  var i = 0;
  while (i < rows.length) {
    var r = rows[i];
    if (r.type === "equal") {
      out.push({ left: r, right: r });
      i++;
      continue;
    }
    var dels = [];
    var ins = [];
    while (i < rows.length && rows[i].type === "delete") { dels.push(rows[i]); i++; }
    while (i < rows.length && rows[i].type === "insert") { ins.push(rows[i]); i++; }
    var n = Math.max(dels.length, ins.length);
    for (var j = 0; j < n; j++) {
      out.push({ left: dels[j] || null, right: ins[j] || null });
    }
  }
  return out;
}

function renderToolDiff(tool, file, text, rawText) {
  var color = getToolColor(tool, rawText || text);
  var isNewFile = (tool === "Write" || tool === "Wrote");
  var isMdFile = isNewFile && /\.(md|markdown)$/i.test(file || "");
  var diffLang = detectLang(file);
  var diffIsMd = /\.(md|markdown)$/i.test(file || "");

  if (isNewFile) {
    var content = text || "";
    var lines = content.split(/\r?\n/);
    if (lines.length > 0 && lines[lines.length - 1] === "") {
      lines.pop();
    }
    var lang = detectLang(file);
    var html = "";
    lines.forEach(function (line, idx) {
      var num = idx + 1;
      html += '<tr>' +
        '<td class="left write-cell"><div class="tool-diff-cell">' +
        '<span class="tool-diff-num">' + esc(String(num)) + '</span>' +
        '<span class="tool-diff-sign"> </span>' +
        '<span class="tool-diff-line-content">' + renderCodeLine(line, lang, isMdFile) + '</span>' +
        '</div></td>' +
        '</tr>';
    });
    if (!html) {
      html = '<tr><td class="left empty"><div class="tool-diff-cell">' +
        '<span class="tool-diff-num"></span><span class="tool-diff-sign"> </span><span class="tool-diff-line-content">—</span>' +
        '</div></td></tr>';
    }
    return '<div class="tool-diff-panel">' +
      '<div class="tool-header">' +
      '<span class="tool-badge" style="background:' + color + ';">' + esc(tool) + '</span>' +
      '<span class="tool-file" style="color:' + color + ';">' + esc(file) + '</span>' +
      '</div>' +
      '<div class="tool-diff-content" style="font-family:monospace;">' +
      '<table class="tool-diff-table tool-diff-table--one-col">' + html + '</table>' +
      '</div>' +
      '</div>';
  }

  var result = parseUnifiedPatch(text || "");
  var rows = result.rows;

  var trHtml = "";
  var hasContent = false;

  var i = 0;
  while (i < rows.length) {
    var r = rows[i];

    if (r.type === "equal") {
      var oldNum = (r.oldNo !== null && r.oldNo > 0) ? String(r.oldNo) : "";
      var newNum = (r.newNo !== null && r.newNo > 0) ? String(r.newNo) : "";
      trHtml += '<tr>' +
        diffCellHtml("left unchanged", oldNum, " ", r.left, diffLang, diffIsMd) +
        diffCellHtml("right unchanged", newNum, " ", r.right, diffLang, diffIsMd) +
        '</tr>';
      hasContent = true;
      i++;
      continue;
    }

    // Recueillir le run de deletes puis d'inserts consécutifs
    var dels = [];
    var ins = [];
    while (i < rows.length && rows[i].type === "delete") {
      dels.push(rows[i]);
      i++;
    }
    while (i < rows.length && rows[i].type === "insert") {
      ins.push(rows[i]);
      i++;
    }

    var n = Math.max(dels.length, ins.length);
    for (var j = 0; j < n; j++) {
      var d = dels[j];
      var insLine = ins[j];
      var oldNum = (d && d.oldNo !== null && d.oldNo > 0) ? String(d.oldNo) : "";
      var newNum = (insLine && insLine.newNo !== null && insLine.newNo > 0) ? String(insLine.newNo) : "";

      if (d && insLine) {
        trHtml += '<tr>' +
          diffCellHtml("left removed", oldNum, "-", d.left, diffLang, diffIsMd) +
          diffCellHtml("right added", newNum, "+", insLine.right, diffLang, diffIsMd) +
          '</tr>';
      } else if (d) {
        trHtml += '<tr>' +
          diffCellHtml("left removed", oldNum, "-", d.left, diffLang, diffIsMd) +
          diffCellHtml("right empty", "", " ", "", diffLang, diffIsMd) +
          '</tr>';
      } else if (insLine) {
        trHtml += '<tr>' +
          diffCellHtml("left empty", "", " ", "", diffLang, diffIsMd) +
          diffCellHtml("right added", newNum, "+", insLine.right, diffLang, diffIsMd) +
          '</tr>';
      }
      hasContent = true;
    }
  }

  if (!hasContent) {
    trHtml = '<tr>' +
      diffCellHtml("left empty", "", " ", "—") +
      diffCellHtml("right empty", "", " ", "—") +
      '</tr>';
  }
  return '<div class="tool-diff-panel">' +
    '<div class="tool-header">' +
    '<span class="tool-badge" style="background:' + color + ';">' + esc(tool) + '</span>' +
    '<span class="tool-file" style="color:' + color + ';">' + esc(file) + '</span>' +
    '</div>' +
    '<div class="tool-diff-content" style="font-family:monospace;">' +
    '<table class="tool-diff-table">' + trHtml + '</table>' +
    '</div>' +
    '</div>';
}

// Diff avec jsdiff (même bibliothèque qu'OpenCode) — split view colonnes左��.
function splitLineHtml(num, content, type) {
  var n = num !== null ? '<span class="split-num">' + num + '</span>' : '<span class="split-num"> </span>';
  return '<div class="split-line split-' + type + '">' + n + '<span class="split-content">' + esc(content || "") + '</span></div>';
}

function buildSplitRows(oldText, newText) {
  var rows = [];
  var oldNo = 0;
  var newNo = 0;
  var del = 0;
  var add = 0;

  var changes = (typeof window !== "undefined" && window.Diff)
    ? window.Diff.diffLines(oldText || "", newText || "")
    : [];

  if (!changes.length) return { rows: [], del: 0, add: 0 };

  changes.forEach(function (part) {
    if (!part) return;
    // Format jsdiff : {value, added, removed} — tolerance aux autres formats
    var lines;
    if (typeof part.value === "string") {
      lines = part.value.split(/\r?\n/);
    } else if (Array.isArray(part.lines)) {
      lines = part.lines.slice();
    } else {
      return;
    }
    // Le dernier élément vide après un \n final n'est pas une vraie ligne
    if (lines.length > 0 && lines[lines.length - 1] === "") {
      lines.pop();
    }

    var isAdded = part.added === true || part.type === "insert";
    var isRemoved = part.removed === true || part.type === "delete";

    if (isAdded) {
      add += lines.length;
      lines.forEach(function (line) {
        newNo++;
        rows.push({ type: "insert", left: "", right: line, oldNo: null, newNo: newNo });
      });
    } else if (isRemoved) {
      del += lines.length;
      lines.forEach(function (line) {
        oldNo++;
        rows.push({ type: "delete", left: line, right: "", oldNo: oldNo, newNo: null });
      });
    } else {
      lines.forEach(function (line) {
        oldNo++;
        newNo++;
        rows.push({ type: "equal", left: line, right: line, oldNo: oldNo, newNo: newNo });
      });
    }
  });

  return { rows: rows, del: del, add: add };
}

function parseUnifiedPatch(diffStr) {
  var rows = [];
  var oldNo = 0;
  var newNo = 0;
  var del = 0;
  var add = 0;
  var inHunk = false;

  var lines = (diffStr || "").split(/\r?\n/);

  for (var k = 0; k < lines.length; k++) {
    var line = lines[k];

    // Détecter le début d'un hunk
    if (line.startsWith("@@")) {
      inHunk = true;
      var m = line.match(/@@\s*-(\d+)(?:,\d+)?\s*\+(\d+)(?:,\d+)?/);
      if (m) {
        oldNo = parseInt(m[1], 10) - 1;
        newNo = parseInt(m[2], 10) - 1;
      }
      continue;
    }

    // Ignorer tout ce qui est avant le premier hunk (Index:, ====, ---, +++)
    if (!inHunk) continue;

    // À l'intérieur d'un hunk
    if (line.startsWith(" ")) {
      oldNo++;
      newNo++;
      rows.push({ type: "equal", left: line.slice(1), right: line.slice(1), oldNo: oldNo, newNo: newNo });
    } else if (line.startsWith("-")) {
      oldNo++;
      del++;
      rows.push({ type: "delete", left: line.slice(1), right: "", oldNo: oldNo, newNo: null });
    } else if (line.startsWith("+")) {
      newNo++;
      add++;
      rows.push({ type: "insert", left: "", right: line.slice(1), oldNo: null, newNo: newNo });
    } else if (line === "" || line.startsWith("\\")) {
      // Ligne vide ou marque de fin de patch \ No newline at end of file
      if (line === "" && k >= lines.length - 1) continue; // artefact du split final
      rows.push({ type: "equal", left: "", right: "", oldNo: null, newNo: null });
    } else if (line.startsWith("---") || line.startsWith("+++") || line.startsWith("Index:") || /^=+$/.test(line)) {
      // En-têtes de fichier à ignorer même à l'intérieur d'un hunk
      continue;
    } else {
      // Ligne inconnue à l'intérieur d'un hunk : traiter comme contexte
      oldNo++;
      newNo++;
      rows.push({ type: "equal", left: line, right: line, oldNo: oldNo, newNo: newNo });
    }
  }

  return { rows: rows, del: del, add: add };
}

function renderMarkdownBlock(text) {
  if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
    return '<pre><code style="white-space:pre-wrap;">' + esc(text || "") + '</code></pre>';
  }
  var html = marked.parse(text || "", { breaks: true });
  html = DOMPurify.sanitize(html || "", {
    ADD_ATTR: ["class"],
    ADD_TAGS: ["pre", "code", "span"],
  });
  return html;
}

function renderSplitDiff(tool, file, oldContent, newContent, diffContent, toolText) {
  var color = getToolColor(tool, toolText);
  var isNewFile = (tool === "Write" || tool === "Wrote");
  var isMdFile = isNewFile && /\.(md|markdown)$/i.test(file || "");

  if (isNewFile) {
    var lines = (newContent || "").split(/\r?\n/);
    if (lines.length > 0 && lines[lines.length - 1] === "") {
      lines.pop();
    }
    var html = "";
    lines.forEach(function (line, idx) {
      var num = idx + 1;
      html += '<div class="split-line write-line" style="font-family:monospace;display:flex;">' +
        '<span class="split-num">' + esc(String(num)) + '</span>' +
        '<span class="split-sign" style="width:1.1rem;color:var(--muted);text-align:right;padding-right:0.5rem;flex-shrink:0;"> </span>' +
        '<span class="split-content" style="flex:1;min-width:0;white-space:pre-wrap;overflow-wrap:anywhere;">' + (isMdFile ? colorizeMarkdownInline(line) : esc(line)) + '</span>' +
        '</div>';
    });
    if (!html) {
      html = '<div class="split-line write-line" style="font-family:monospace;">—</div>';
    }
    return '<div class="tool-split-diff">' +
      '<div class="split-header">' +
      '<span class="tool-badge" style="background:' + color + ';">' + esc(tool) + '</span>' +
      '<span class="split-file" style="color:' + color + ';">' + esc(file || "") + '</span>' +
      '</div>' +
      '<div class="split-lines" style="font-family:monospace;">' + html + '</div>' +
      '</div>';
  }

  var result;

  if (diffContent) {
    // Patch unifie : contient la fenetre de contexte et les vrais numeros
    // de ligne (via les en-tetes @@). C'est le rendu de la TUI OpenCode.
    result = parseUnifiedPatch(diffContent);
    if (result.rows.length === 0 && oldContent !== undefined && newContent !== undefined) {
      result = buildSplitRows(oldContent || "", newContent || "");
    }
    if (result.rows.length === 0) {
      // Dernier recours : afficher le diff brut d'un côté
      result = {
        rows: [{ type: "equal", left: diffContent, right: "", oldNo: 1, newNo: 1 }],
        del: 0,
        add: 0
      };
    }
  } else if (oldContent !== undefined && newContent !== undefined) {
    result = buildSplitRows(oldContent || "", newContent || "");
  } else {
    result = { rows: [], del: 0, add: 0 };
  }

  // Rangees alignees : une suppression et un ajout correspondant sur la meme ligne
  var aligned = buildAlignedRows(result.rows);
  var splitLang = detectLang(file);
  var splitIsMd = /\.(md|markdown)$/i.test(file || "");
  var trHtml = "";
  aligned.forEach(function (row) {
    var left = row.left;
    var right = row.right;
    var leftCls, leftNum, leftSign, leftText;
    if (left) {
      if (left.type === "delete") {
        leftCls = "left removed"; leftSign = "-"; leftText = left.left;
      } else {
        leftCls = "left unchanged"; leftSign = " "; leftText = left.left;
      }
      leftNum = (left.oldNo !== null && left.oldNo !== undefined) ? left.oldNo : "";
    } else {
      leftCls = "left empty"; leftNum = ""; leftSign = " "; leftText = "";
    }
    var rightCls, rightNum, rightSign, rightText;
    if (right) {
      if (right.type === "insert") {
        rightCls = "right added"; rightSign = "+"; rightText = right.right;
      } else {
        rightCls = "right unchanged"; rightSign = " "; rightText = right.right;
      }
      rightNum = (right.newNo !== null && right.newNo !== undefined) ? right.newNo : "";
    } else {
      rightCls = "right empty"; rightNum = ""; rightSign = " "; rightText = "";
    }
    trHtml += '<tr>' +
      diffCellHtml(leftCls, leftNum, leftSign, leftText, splitLang, splitIsMd) +
      diffCellHtml(rightCls, rightNum, rightSign, rightText, splitLang, splitIsMd) +
      '</tr>';
  });

  return '<div class="tool-diff-panel">' +
    '<div class="tool-header">' +
    '<span class="tool-badge" style="background:' + color + ';">' + esc(tool) + '</span>' +
    '<span class="tool-file" style="color:' + color + ';">' + esc(file || "") + '</span>' +
    '<span class="split-stats"><span class="split-stat-del">-' + result.del + '</span> <span class="split-stat-add">+' + result.add + '</span></span>' +
    '</div>' +
    '<div class="tool-diff-content" style="font-family:monospace;">' +
    '<table class="tool-diff-table">' + trHtml + '</table>' +
    '</div>' +
    '</div>';
}

function renderToolPanel(tool, file, text, rawText, toolContent) {
  var isDiffTool = (tool === "Edit" || tool === "Write" || tool === "Patch");
  if (isDiffTool) {
    return renderToolDiff(tool, file, text, rawText);
  }
  return renderToolSimple(tool, file, toolContent);
}

// Convertit un chemin absolu en chemin relatif a la racine du projet.
function toRelativePath(file) {
  if (!file) return "";
  try {
    var rootPath = (typeof window !== "undefined" && window.__sayrhaziRoot)
      ? String(window.__sayrhaziRoot)
      : "";
    if (!rootPath) return file;
    // On conserve le separateur d'origine : \ sous Windows, / ailleurs
    var sep = String(file).indexOf("\\") >= 0 ? "\\" : "/";
    var rp = rootPath.replace(/[\\/]+/g, "/");
    var rf = String(file).replace(/[\\/]+/g, "/");
    if (rf.startsWith(rp + "/") || rf === rp) {
      var rel = rf.slice(rp.length).replace(/^\/+/, "");
      return sep === "\\" ? rel.replace(/\//g, "\\") : rel;
    }
    return file;
  } catch (e) {
    return file;
  }
}

// Rend un outil individuel (badge + fichier + contenu) dans un bloc agent.
function renderToolBlock(t) {
  var toolName = (t.tool || "Tool").charAt(0).toUpperCase() + (t.tool || "Tool").slice(1).toLowerCase();
  var file = toRelativePath(t.file || "");
  var oldContent = t.old_content || "";
  var newContent = t.new_content || "";
  var diffContent = t.diff_content || "";
  var toolContent = t.tool_content || "";
  var errorMsg = t.error || "";

  // Outil en erreur : badge rouge + message déroulable
  if (errorMsg) {
    var headerHtml = '<span class="tool-badge" style="background:#EF4444;">' + esc(toolName) + '</span>' +
      (file ? '<span class="tool-file" style="color:#EF4444;">' + esc(file) + '</span>' : '');
    return '<div class="tool-header tool-header-error" style="cursor:pointer;" title="Afficher / masquer l\'erreur">' +
      headerHtml +
      '</div>' +
      '<div class="tool-error-content" style="display:none;">' +
      '<div style="white-space:pre-wrap; font-family:monospace; color:#ef4444; padding:0.6rem; background:#1a1a1a; border:1px solid #333; border-radius:0.35rem; margin-top:0.4rem;">' + esc(errorMsg) + '</div>' +
      '</div>';
  }

  var isDiffTool = (toolName === "Edit" || toolName === "Write" || toolName === "Patch");
  if (isDiffTool) {
    // Write : rendu ligne par ligne (colonne unique), markdown colorise en inline si .md
    if (toolName === "Write") {
      if (newContent) {
        return renderToolDiff(toolName, file, newContent);
      }
      if (diffContent) {
        return renderToolDiff(toolName, file, diffContent);
      }
      return renderToolDiff(toolName, file, "");
    }
    // Edit / Patch : diff 2 colonnes Ancien | Nouveau
    if (newContent || oldContent || diffContent) {
      return renderSplitDiff(toolName, file, oldContent, newContent, diffContent, "");
    }
    return renderToolDiff(toolName, file, "");
  }
  return renderToolSimple(toolName, file, toolContent, t.todos || null, t.params || "");
}

// Seuil au-delà duquel un raisonnement devient repliable (comme le + Thought de la TUI).
var REASONING_FOLD_MIN_CHARS = 120;

// Branche sur un bloc agent tous les handlers interactifs (dépliage des
// raisonnements, dépliage erreur des outils) — utilisé au chargement et en
// remplacement in-place lors du streaming.
function wireAgentBlock(div) {
  div.querySelectorAll(".reasoning-header.reasoning-foldable").forEach(function (rHeader) {
    if (rHeader.dataset.wired) return;
    rHeader.dataset.wired = "1";
    var rContent = rHeader.nextElementSibling;
    if (!rContent || !rContent.classList.contains("reasoning-content")) return;
    var rToggle = rHeader.querySelector(".reasoning-toggle");
    rHeader.addEventListener("click", function (e) {
      e.stopPropagation();
      var isOpen = rContent.style.display === "block";
      rContent.style.display = isOpen ? "none" : "block";
      if (rToggle) rToggle.textContent = isOpen ? "\u25BC" : "\u25B2";
    });
  });
  div.querySelectorAll(".tool-header-error").forEach(function (header) {
    if (header.dataset.wired) return;
    header.dataset.wired = "1";
    var content = header.nextElementSibling;
    if (content && content.classList.contains("tool-error-content")) {
      header.addEventListener("click", function (e) {
        e.stopPropagation();
        var isOpen = content.style.display === "block";
        content.style.display = isOpen ? "none" : "block";
      });
    }
  });
  setupToolContentToggles(div);
  applySyntaxHighlight(div);
}

// Rend un bloc agent complet : elements ordonnes (reasoning / outils / texte).
function renderAgentBlock(role, items, model, duration, seriesEnd) {
  var html = "";
  var list = items && items.length ? items : [];
  if (!list.length) return html;

  list.forEach(function (item) {
    html += renderAgentItemHtml(item, role);
  });

  // Pied de bulle : modele + duree totale (seulement en fin de reponse agent).
  // Rendu natif OpenCode : "▣ {agent} · {model} · {duration}"
  var foot = [];
  if (seriesEnd) {
    var agentLabel = (selectedChatAgent ? getAgentDisplayName(selectedChatAgent) : "Agent");
    if (model) foot.push(esc(model));
    if (duration) foot.push(esc(duration));
    if (foot.length) {
      html += '<div class="agent-block-foot">▣ ' + esc(agentLabel) + ' · ' + foot.join(' · ') + '</div>';
    }
  }
  return html;
}

// Rend le HTML d'un item agent (reasoning | tool | text). Le wrapper externe
// est porteur de data-part-id pour permettre le remplacement en place.
function renderAgentItemHtml(item, role) {
  var partId = item.part_id ? ' data-part-id="' + esc(item.part_id) + '"' : '';
  if (item.kind === "reasoning") {
    var agentName = (selectedChatAgent ? getAgentDisplayName(selectedChatAgent) : "Agent");
    var foldable = item.native !== false
      && String(item.text || "").length >= REASONING_FOLD_MIN_CHARS;
    return '<div class="agent-item agent-item--reasoning"' + partId + '>' +
      '<div class="reasoning-header ' + (foldable ? 'reasoning-foldable' : 'reasoning-static') + '"' +
        (foldable ? ' style="cursor:pointer;"' : '') + '>' +
        (foldable ? '<span class="reasoning-toggle">\u25BC</span>' : '') +
        '<span class="reasoning-label" style="font-style:italic;">' + esc(agentName) + ' refl\u00e9chi' + (item.duration ? ' \u00b7 ' + item.duration : '') + '</span>' +
      '</div>' +
      '<div class="reasoning-content"' + (foldable ? ' style="display:none;"' : '') + '>' +
        '<div class="reasoning-body">' + renderChatMarkdown(item.text, role) + '</div>' +
      '</div>' +
    '</div>';
  }
  if (item.kind === "tool") {
    return '<div class="agent-item agent-item--tool"' + partId + '>' + renderToolBlock(item) + '</div>';
  }
  if (item.kind === "text") {
    return '<div class="agent-item agent-item--text"' + partId + '>' +
      '<div class="agent-block-text"><div class="chat-bubble">' +
        renderChatMarkdown(item.text, role) +
      '</div></div>' +
    '</div>';
  }
  return '';
}

function appendChatMessage(role, text, time, metricsOnly, isReasoning, messageId, isTool, tool, file, oldContent, newContent, diffContent, toolContent, duration, reasoning, tools, items, model, seriesEnd) {
  const container = document.getElementById("chat-conversation");
  if (!container) return;

  // Retirer le message "vide" s'il existe
  const empty = container.querySelector(".chat-empty");
  if (empty) empty.remove();

  const div = document.createElement("div");
  if (messageId) {
    div.dataset.messageId = messageId;
  }

  // Nouveau format : bloc agent avec elements ordonnes (reasoning / outils / texte)
  // Uniquement pour les messages agent : les messages user passent par le rendu classique.
  if (items && items.length && role === "agent") {
    div.className = "chat-message agent";
    div.innerHTML = renderAgentBlock(role, items, model, duration, seriesEnd);
    wireAgentBlock(div);
    container.appendChild(div);
    scrollChatToBottom();
    return;
  }

  if (isTool) {
    rawToolTexts.push({
      tool: tool || "",
      file: file || "",
      text: text || "",
      time: time || "",
    });
    if (rawToolTexts.length > 100) rawToolTexts.shift();
    var resolvedTool = (tool || "Tool").charAt(0).toUpperCase() + (tool || "Tool").slice(1).toLowerCase();
    var resolvedFile = file || "";
    if (!resolvedFile && role === "agent" && !isReasoning) {
      var detectedFromText = detectToolsInText(text || "");
      if (detectedFromText.length > 0) {
        div.className = "chat-message tool";
        div.innerHTML = renderDetectedTools(detectedFromText, text || "");
        wrapToolError(div, text || "");
        container.appendChild(div);
        setupToolContentToggles(div);
        applySyntaxHighlight(div);
        scrollChatToBottom();
        return;
      }
      // Fallback : extraire un chemin de fichier depuis le texte brut de l'outil.
      // OpenCode envoie parfois le JSON de l'outil avec filePath/path à l'intérieur,
      // ex: edit [{'status':..., 'input':{'filePath': 'C:\\...\plan.txt'}}].
      // On ne retient que les tokens qui ressemblent à un chemin.
      resolvedFile = extractFilePathFromText(text || "");
      if (resolvedFile) {
        resolvedTool = (tool || "Tool").charAt(0).toUpperCase() + (tool || "Tool").slice(1).toLowerCase();
        // Afficher un chemin relatif à la racine du projet quand possible :
        // .opencode\agent\ali.md au lieu du chemin absolu.
        resolvedFile = toRelativePath(resolvedFile);
      }
    }
    var isDiffTool = (resolvedTool === "Edit" || resolvedTool === "Write" || resolvedTool === "Patch");
    // Split view style VS Code pour Edit/Write/Patch quand old/new ou diff sont disponibles.
    // Un Write sans fichier reconnu mais avec du contenu s'affiche aussi (contenu brut/markdown).
    if (isDiffTool && (resolvedFile || (resolvedTool === "Write" && newContent)) && (oldContent || newContent || diffContent)) {
      div.className = "chat-message tool";
      div.innerHTML = renderSplitDiff(resolvedTool, resolvedFile || "(sans fichier)", oldContent || "", newContent || "", diffContent || "", text || "");
      wrapToolError(div, text || "");
      container.appendChild(div);
      setupToolContentToggles(div);
      applySyntaxHighlight(div);
      scrollChatToBottom();
      return;
    }
    // Pour Read avec fichier trouvé : badge + fichier seulement, pas le texte brut.
    var displayText;
    if (resolvedFile && !isDiffTool) {
      displayText = "";
    } else if (resolvedFile && isDiffTool) {
      displayText = extractDiffFromText(text || "");
      if (!displayText && resolvedTool === "Write") {
        displayText = extractContentFromText(text || "") || "";
      }
      // Write sans contenu extractible : afficher juste le badge + fichier
      if (!displayText && resolvedTool === "Write") {
        div.className = "chat-message tool";
        div.innerHTML = renderToolSimple(resolvedTool, resolvedFile, toolContent || "");
        wrapToolError(div, text || "");
        container.appendChild(div);
        setupToolContentToggles(div);
        applySyntaxHighlight(div);
        scrollChatToBottom();
        return;
      }
    } else {
      displayText = text || "";
    }
    div.className = "chat-message tool";
    div.innerHTML = renderToolPanel(resolvedTool, resolvedFile, displayText, text || "", toolContent || "");
    wrapToolError(div, text || "");
    container.appendChild(div);
    setupToolContentToggles(div);
    scrollChatToBottom();
    return;
  }

  // Détecter les outils inline (→ Read, � Edit, → Write, � Patch) dans les messages agent
  if (role === "agent" && !isReasoning) {
    var detectedTools = detectToolsInText(text || "");
    if (detectedTools.length > 0) {
      div.className = "chat-message tool";
      div.innerHTML = renderDetectedTools(detectedTools, text || "");
      wrapToolError(div, text || "");
      container.appendChild(div);
      setupToolContentToggles(div);
      scrollChatToBottom();
      return;
    }
  }

  if (isReasoning) {
    div.className = "chat-message reasoning";

    const header = document.createElement("div");
    header.className = "reasoning-header";
    header.style.cursor = "pointer";
    header.title = "Afficher / masquer le raisonnement";

    const toggle = document.createElement("span");
    toggle.className = "reasoning-toggle";
    toggle.textContent = "\u25BC"; // chevron down

    const label = document.createElement("span");
    label.className = "reasoning-label";
    label.textContent = (selectedChatAgent ? getAgentDisplayName(selectedChatAgent) : "Agent") + " refléchi" + (duration ? " · " + duration : "");
    label.style.fontStyle = "italic";

    header.appendChild(toggle);
    header.appendChild(label);
    div.appendChild(header);

    const content = document.createElement("div");
    content.className = "reasoning-content";
    content.style.display = "none";

    const body = document.createElement("div");
    body.className = "reasoning-body";
    body.innerHTML = renderChatMarkdown(text, role);

    content.appendChild(body);
    div.appendChild(content);

    header.addEventListener("click", (e) => {
      e.stopPropagation();
      const isOpen = content.style.display === "block";
      content.style.display = isOpen ? "none" : "block";
      toggle.textContent = isOpen ? "\u25BC" : "\u25B2"; // down / up
    });

    container.appendChild(div);
    scrollChatToBottom();
    return;
  }

  div.className = "chat-message " + role + (metricsOnly ? " metrics-only" : "");

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";

  const body = document.createElement("div");
  body.className = "chat-bubble-body";
  body.innerHTML = renderChatMarkdown(text, role);
  bubble.appendChild(body);

  applySyntaxHighlight(body);

  const timeEl = document.createElement("span");
  timeEl.className = "chat-time";

  if (role === "user") {
    // Bulle user : l'heure sort de la bulle et passe sous celle-ci (comme le
    // pied modele/duree de l'agent). Le chevron de depliage reste dans la bulle.
    const foldToggle = document.createElement("button");
    foldToggle.className = "chat-fold-toggle";
    foldToggle.type = "button";
    foldToggle.textContent = "\u25BC";
    foldToggle.title = "Dérouler / replier le message";
    foldToggle.style.display = "none";
    foldToggle.addEventListener("click", (e) => {
      e.stopPropagation();
      var expanded = body.classList.toggle("expanded");
      foldToggle.textContent = expanded ? "\u25B2" : "\u25BC";
    });
    bubble.appendChild(foldToggle);
    bubble.classList.add("chat-bubble-foldable");

    timeEl.className = "chat-time chat-time-outside chat-user-bar";
    timeEl.setAttribute("aria-label", "Actions du message");
    timeEl.innerHTML =
      '<button type="button" class="chat-icon-btn" data-action="renverser" title="Renverser (bientôt disponible)" aria-label="Renverser" disabled>↩</button>' +
      '<span class="chat-icon-separator" aria-hidden="true">•</span>' +
      '<button type="button" class="chat-icon-btn" data-action="copy" title="Copier le message" aria-label="Copier le message">copy</button>' +
      '<span class="chat-icon-separator" aria-hidden="true">•</span>' +
      '<span class="chat-time-text">' + esc(time + (duration ? " · " + duration : "")) + '</span>';
    timeEl.querySelector('[data-action="copy"]').addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      copyTextToClipboard(text || "", ev.currentTarget);
    });
    div.appendChild(bubble);
    div.appendChild(timeEl);
    container.appendChild(div);
    setupUserFold(div);
    scrollChatToBottom();
    return;
  }

  timeEl.textContent = time + (duration ? " · " + duration : "");
  bubble.appendChild(timeEl);

  div.appendChild(bubble);
  container.appendChild(div);
  scrollChatToBottom();
}

// Affiche le chevron de deploiement d'une bulle user uniquement si le
// contenu depasse la hauteur limite (mesure realisee apres insertion dans le DOM).
function setupUserFold(container) {
  var bodies = container.querySelectorAll(".chat-message.user .chat-bubble-body");
  for (var i = 0; i < bodies.length; i++) {
    (function (body) {
      var toggle = container.querySelector(".chat-fold-toggle");
      if (!toggle) return;
      toggle.style.display = (body.scrollHeight > body.clientHeight + 2) ? "block" : "none";
    })(bodies[i]);
  }
}

// Copie un texte dans le presse-papier. Utilise l'API clipboard quand elle est
// disponible, sinon bascule sur un textarea invisible + execCommand('copy').
function copyTextToClipboard(text, button) {
  const value = String(text || "");
  const feedback = function () {
    if (!button) return;
    const avant = button.textContent;
    button.textContent = "copié";
    button.classList.add("chat-icon-btn-ok");
    setTimeout(function () {
      button.textContent = avant;
      button.classList.remove("chat-icon-btn-ok");
    }, 1400);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(value).then(feedback, feedback);
    return;
  }
  const ta = document.createElement("textarea");
  ta.value = value;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "0";
  ta.style.left = "0";
  ta.style.opacity = "0";
  ta.style.pointerEvents = "none";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  ta.setSelectionRange(0, ta.value.length);
  try { document.execCommand("copy"); } catch (e) { /* ignore */ }
  document.body.removeChild(ta);
  feedback();
}

// Promesse de scroll : tant que l'utilisateur n'a pas scrollé vers le haut,
// on suit automatiquement le fond pendant le streaming.
let chatStickToBottom = true;
let chatScrollProgrammatic = false;
function chatAttachStickListener() {
  const container = document.getElementById("chat-conversation");
  if (!container || container.dataset.stickReady) return;
  container.dataset.stickReady = "1";
  container.addEventListener("scroll", function () {
    if (chatScrollProgrammatic) { chatScrollProgrammatic = false; return; }
    chatStickToBottom = isNearBottom(container);
  }, { passive: true });
  container.addEventListener("wheel", function (e) {
    if (e.deltaY < 0) chatStickToBottom = false;
  }, { passive: true });
  container.addEventListener("touchmove", function () { chatStickToBottom = false; }, { passive: true });
}
function isNearBottom(container) {
  return container.scrollHeight - container.scrollTop - container.clientHeight < 80;
}
function scrollChatToBottom() {
  chatAttachStickListener();
  const container = document.getElementById("chat-conversation");
  if (container && chatStickToBottom) {
    chatScrollProgrammatic = true;
    container.scrollTop = container.scrollHeight;
  }
}
function isScrolledToBottom() {
  const container = document.getElementById("chat-conversation");
  if (!container) return true;
  return isNearBottom(container);
}

/* --- SSE streaming (étape 4 du plan) --- */

function connectSSE(sessionId) {
  if (sseSessionId === sessionId && eventSource !== null && sseConnected) {
    return;
  }
  disconnectSSE();

  sseSessionId = sessionId;
  const url = "/api/chat/global/event?session_id=" + encodeURIComponent(sessionId);

  try {
    eventSource = new EventSource(url);

    eventSource.onopen = function () {
      sseConnected = true;
      console.log("[SSE] connecté pour session " + sessionId);
    };

    eventSource.onmessage = function (e) {
      try {
        const data = JSON.parse(e.data);
        handleSSEEvent(data);
      } catch (err) {
        console.error("[SSE] erreur parsing message:", err);
      }
    };

    eventSource.onerror = function (err) {
      console.error("[SSE] erreur:", err);
      disconnectSSE();
      scheduleReconnect(sessionId);
    };
  } catch (err) {
    console.error("[SSE] impossible de se connecter:", err);
    scheduleReconnect(sessionId);
  }
}

function disconnectSSE() {
  if (eventSource !== null) {
    try {
      eventSource.close();
    } catch (err) {
      // ignore
    }
    eventSource = null;
  }
  sseConnected = false;
  sseSessionId = null;
}

function scheduleReconnect(sessionId) {
  if (sseReconnectTimeout !== null) {
    clearTimeout(sseReconnectTimeout);
  }
  sseReconnectTimeout = setTimeout(function () {
    sseReconnectTimeout = null;
    connectSSE(sessionId);
  }, 3000);
}

function handleSSEEvent(data) {
  if (!data || !data.session_id) {
    return;
  }

  if (sseSessionId && data.session_id !== sseSessionId) {
    return;
  }

  const eventType = data.event || data.type || "";

  switch (eventType) {
    case "message.updated":
    case "message.part.updated":
      handleMessageUpdated(data);
      break;
    case "message.part.removed":
      handleMessagePartRemoved(data);
      break;
    case "session.status":
      handleSessionStatus(data);
      break;
    case "session.idle":
      handleSessionIdle(data);
      break;
    case "session.error":
      handleSessionError(data);
      break;
    case "permission.asked":
      handlePermissionAsked(data);
      break;
    case "permission.replied":
      handlePermissionReplied(data);
      break;
    default:
      // événements ignorés pour l'instant
      break;
  }
}

function handleMessageUpdated(data) {
  if (!data) {
    return;
  }

  var existing = null;

  if (data.message_id) {
    existing = document.querySelector('[data-message-id="' + data.message_id + '"]');
  }

  if (!existing) {
    // Fallback : cibler le dernier message agent affiché (placeholder SSE)
    var agents = document.querySelectorAll(".chat-message.agent");
    if (agents.length > 0) {
      existing = agents[agents.length - 1];
    }
  }

  if (!existing) {
    return;
  }

  var bubble = existing.querySelector(".chat-bubble");
  if (!bubble) {
    return;
  }

  var html = "";
  if (data.text) {
    html = renderChatMarkdown(data.text, "agent");
  } else if (data.parts && Array.isArray(data.parts)) {
    html = data.parts
      .map(function (p) { return p.text || ""; })
      .join("");
    html = renderChatMarkdown(html, "agent");
  }

  if (html) {
    bubble.innerHTML = html;
    applySyntaxHighlight(bubble);
  }
}

function handleMessagePartRemoved(data) {
  console.log("[SSE] message.part.removed:", data);
}

function handleSessionStatus(data) {
  console.log("[SSE] session.status:", data);
}

function handleSessionIdle(data) {
  console.log("[SSE] session.idle:", data);
}

function handleSessionError(data) {
  console.log("[SSE] session.error:", data);
}

function handlePermissionAsked(data) {
  console.log("[SSE] permission.asked:", data);
}

function handlePermissionReplied(data) {
  console.log("[SSE] permission.replied:", data);
}

// Etat explicite du bouton d'envoi : "send" (prêt) ou "stop" (attente d'une
// réponse agent). Avant, un simple interrupteur togglé deux fois par clic sur
// le bouton, une fois par la touche Entree : le bouton restait bloqué sur
// "stop" pour les envois au clavier.
function setSendButtonState(mode) {
  const btn = document.getElementById("chat-send-btn");
  if (!btn) return;
  if (mode === "stop") {
    btn.classList.add("stop");
    btn.textContent = "\u25A0";
    btn.title = "Réponse de l'agent en cours...";
    btn.setAttribute("aria-label", "Réponse de l'agent en cours");
  } else {
    btn.classList.remove("stop");
    btn.textContent = "\u2191";
    btn.title = "Envoyer";
    btn.setAttribute("aria-label", "Envoyer");
  }
}

function toggleAttachPanel() {
  const btn = document.getElementById("chat-attach-btn");
  const panel = document.getElementById("chat-attach-panel");
  if (!btn || !panel) return;
  const show = panel.style.display !== "block";
  panel.style.display = show ? "block" : "none";
}

function handleAttachAction(action) {
  const input = document.getElementById("chat-message-input");
  const panel = document.getElementById("chat-attach-panel");
  if (panel) panel.style.display = "none";
  if (action === "file") {
    if (input) {
      input.placeholder = "Sélectionnez un fichier à joindre…";
      input.focus();
    }
  } else if (action === "files") {
    if (input) {
      input.placeholder = "Sélectionnez plusieurs fichiers…";
      input.focus();
    }
  } else if (action === "directory") {
    if (input) {
      input.placeholder = "Sélectionnez un répertoire…";
      input.focus();
    }
  }
}

function closeAttachPanelOnClickOutside(e) {
  const btn = document.getElementById("chat-attach-btn");
  const panel = document.getElementById("chat-attach-panel");
  if (!btn || !panel) return;
  if (!btn.contains(e.target) && !panel.contains(e.target)) {
    panel.style.display = "none";
  }
}

/* Écouteurs du formulaire de chat */
function initChatInput() {
  const input = document.getElementById("chat-message-input");
  const btn = document.getElementById("chat-send-btn");
  const btnNew = document.getElementById("btn-new-session");
  if (input) {
    input.addEventListener("input", function () {
      if (input.tagName === "TEXTAREA") {
        input.style.height = "";
        input.style.height = input.scrollHeight + "px";
      }
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendChatMessage();
      }
    });
  }
  if (btn) {
    btn.addEventListener("click", (e) => {
      // Tant que l'agent répond, le bouton est en mode « stop » (désactivé) :
      // ignorer le clic pour ne pas envoyer un second message par inadvertance.
      if (btn.classList.contains("stop")) return;
      e.preventDefault();
      sendChatMessage();
    });
  }
  if (btnNew) {
    btnNew.addEventListener("click", (e) => {
      e.preventDefault();
      startNewSession();
    });
  }

  const attachBtn = document.getElementById("chat-attach-btn");
  const panel = document.getElementById("chat-attach-panel");
  if (attachBtn) {
    attachBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      toggleAttachPanel();
    });
  }
  if (panel) {
    panel.querySelectorAll(".chat-attach-item").forEach(function (item) {
      item.addEventListener("click", (e) => {
        e.stopPropagation();
        handleAttachAction(item.dataset.action);
      });
    });
  }
  document.addEventListener("click", closeAttachPanelOnClickOutside);
}

function getAgentDisplayName(agentFile) {
  const name = AGENT_DISPLAY_NAME[agentFile] || agentFile;
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function ansiToHtml(text) {
  if (!text) return "";
  // Certains terminaux remplacent le caractere ESC par le symbole U+241B
  var src = String(text).replace(/␛/g, "\x1b");
  const parts = src.split(/(\x1b\[[0-9;]*m)/);
  let html = '<span class="ansi">';
  const colorMap = {
    "1": "ansi-bold", "31": "ansi-red", "32": "ansi-green",
    "33": "ansi-yellow", "34": "ansi-blue", "35": "ansi-magenta",
    "36": "ansi-cyan", "37": "ansi-white",
    // Couleurs brillantantes (90-97) et resets (39/49)
    "90": "ansi-bright-black", "91": "ansi-bright-red", "92": "ansi-bright-green",
    "93": "ansi-bright-yellow", "94": "ansi-bright-blue", "95": "ansi-bright-magenta",
    "96": "ansi-bright-cyan", "97": "ansi-bright-white",
  };
  for (const part of parts) {
    if (/^\x1b\[[0-9;]*m$/.test(part)) {
      if (part === "\x1b[0m" || part === "\x1b[39m" || part === "\x1b[49m") {
        html += '</span><span class="ansi">';
      } else {
        const codes = part.slice(2, -1).split(";").filter(Boolean);
        const classes = codes.map(c => colorMap[c] || "").filter(Boolean).join(" ");
        html += `</span><span class="ansi ${classes}">`;
      }
    } else {
      html += esc(part);
    }
  }
  html += '</span>';
  return html;
}

function colorizeReport(text) {
  if (!text) return "";
  let html = esc(text);
  const rules = [
    [/\b(VALIDÉ|VALIDE)\b/gi, "kw-valide"],
    [/\b(EN COURS)\b/gi, "kw-cours"],
    [/\b(ATTENTE|EN ATTENTE)\b/gi, "kw-attente"],
    [/\b(ERREUR|ECHEC|FAILED)\b/gi, "kw-erreur"],
    [/\b(WARNING)\b/gi, "kw-warning"],
    [/\b(OK)\b/gi, "kw-ok"],
    [/\b(TODO|FIXME)\b/gi, "kw-todo"],
  ];
  for (const [pattern, className] of rules) {
    html = html.replace(pattern, `<span class="${className}">$1</span>`);
  }
  return html;
}

window.esc = esc;

// Rendu Markdown sécurisé pour les messages (marked + DOMPurify + highlight.js)
function renderChatMarkdown(text, role) {
  if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
    return esc(text || "");
  }
  let cleaned = cleanOpenCodeFileBlock(text || "");
  // Le parsing Markdown consomme les backslashes d'echappement (\. devient .) :
  // on les double pour afficher le texte brut, comme dans la TUI
  // (ex: "PS C:\Users\...\Sarhi\.opencode>").
  cleaned = cleaned.replace(/\\/g, "\\\\");
  let markedText = colorizeKeywords(cleaned);
  let html = marked.parse(markedText || "", { breaks: true });
  html = DOMPurify.sanitize(html || "", {
    ADD_ATTR: ["class"],
    ADD_TAGS: ["pre", "code", "span"],
  });
  return html;
}

// Nettoie le balisage OpenCode injecte dans les parts de fichier lues:
// <path>...</path>, <type>...</type>, <content>...</content> et numerotation de ligne
function cleanOpenCodeFileBlock(text) {
  if (!text) return text;
  let result = text;
  // Supprimer les balises d'encadrement OpenCode
  result = result.replace(/<path>.*?<\/path>/gis, "");
  result = result.replace(/<type>.*?<\/type>/gis, "");
  result = result.replace(/<content>/gis, "");
  result = result.replace(/<\/content>/gis, "");
  // Supprimer les numerotations de ligne en debut de ligne (ex: "1: ", "42: ")
  result = result.replace(/^\s*\d+:\s*/gm, "");
  // Nettoyer les lignes vides multiples creees par la suppression
  result = result.replace(/\n{3,}/g, "\n\n");
  return result.trim();
}

function colorizeKeywords(text) {
  const rules = [
    [/\b(VALIDÉ|VALIDE)\b/gi, "kw-valide"],
    [/\b(EN COURS)\b/gi, "kw-cours"],
    [/\b(ATTENTE|EN ATTENTE)\b/gi, "kw-attente"],
    [/\b(ERREUR|ECHEC|FAILED)\b/gi, "kw-erreur"],
    [/\b(WARNING)\b/gi, "kw-warning"],
    [/\b(OK)\b/gi, "kw-ok"],
    [/\b(TODO|FIXME)\b/gi, "kw-todo"],
  ];
  let result = text;
  for (const [pattern, className] of rules) {
    result = result.replace(pattern, `<span class="${className}">$1</span>`);
  }
  return result;
}

// Colore les éléments markdown inline tout en gardant le texte brut ligne par ligne (pas de preview HTML).
function colorizeMarkdownInline(text) {
  if (!text) return "";
  var t = esc(text);
  // Titres en début de ligne : # ## etc. — couleur uniforme sur toute la ligne, h1 souligné
  t = t.replace(/^(#{1,6})\s(.*)$/gm, function (m, marks, rest) {
    var level = marks.length;
    var cls = "md-title";
    if (level === 1) cls += " md-title--h1";
    return '<span class="' + cls + '">' + esc(marks) + ' ' + esc(rest) + '</span>';
  });
  // Gras **texte** (avant italique)
  t = t.replace(/\*\*(.*?)\*\*/g, '<span class="md-bold">$1</span>');
  // Italique *texte*
  t = t.replace(/\*([^*]+?)\*/g, '<span class="md-italic">$1</span>');
  // Code inline `texte`
  t = t.replace(/`([^`]+)`/g, '<span class="md-code">$1</span>');
  // Listes en début de ligne - * 1. — seul le symbole est coloré
  t = t.replace(/^([\-*])\s(.*)$/gm, '<span class="md-bullet">$1</span> $2');
  t = t.replace(/^(\d+\.)\s(.*)$/gm, '<span class="md-bullet">$1</span> $2');
  // Liens [texte](url)
  t = t.replace(/\[([^\]]*)\]\(([^)]*)\)/g, '<span class="md-link">$1</span>');
  return t;
}

function applySyntaxHighlight(container) {
  if (typeof hljs === "undefined") return;
  try {
    container.querySelectorAll("pre code").forEach((block) => {
      hljs.highlightElement(block);
    });
  } catch (e) {
    // ignore
  }
}

window.renderChatMarkdown = renderChatMarkdown;

/* --- Contrôle du watcher (start/stop depuis l'interface web) --- */

async function fetchWatcherStatus() {
  try {
    const response = await fetch("/api/worker/status");
    if (!response.ok) throw new Error("Erreur HTTP " + response.status);
    const data = await response.json();
    watcherRunning = !!data.running;
    updateWatcherUI();
  } catch (err) {
    console.error("Erreur statut watcher:", err);
  }
}

async function fetchWatcherLogs() {
  try {
    const response = await fetch("/api/watcher-logs");
    if (!response.ok) throw new Error("Erreur HTTP " + response.status);
    const data = await response.json();
    const logs = data.logs || [];
    const el = document.getElementById("watcher-logs-content");
    if (el) {
      el.innerHTML = logs.length ? logs.map(l => ansiToHtml(l)).join("<br>") : "Aucune sortie console";
      el.scrollTop = el.scrollHeight;
    }
  } catch (err) {
    console.error("Erreur logs watcher:", err);
  }
}

function updateWatcherUI() {
  const btnStart = document.getElementById("btn-watcher-start");
  const btnStop = document.getElementById("btn-watcher-stop");
  const status = document.getElementById("watcher-control-status");
  if (btnStart) btnStart.disabled = watcherRunning;
  if (btnStop) btnStop.disabled = !watcherRunning;
  if (status) {
    status.textContent = watcherRunning ? "en cours" : "arrêté";
    status.className = "badge " + (watcherRunning ? "bg-success" : "bg-danger");
  }
}

async function startWatcher() {
  try {
    const response = await fetch("/api/worker/start", { method: "POST" });
    const data = await response.json();
    if (data.ok) {
      watcherRunning = true;
      updateWatcherUI();
    } else {
      alert(data.message || "Impossible de démarrer le watcher.");
    }
  } catch (err) {
    console.error("Erreur démarrage watcher:", err);
    alert("Erreur de connexion au serveur.");
  }
}

async function stopWatcher() {
  try {
    const response = await fetch("/api/worker/stop", { method: "POST" });
    const data = await response.json();
    if (data.ok) {
      watcherRunning = false;
      updateWatcherUI();
    } else {
      alert(data.message || "Impossible d'arrêter le watcher.");
    }
  } catch (err) {
    console.error("Erreur arrêt watcher:", err);
    alert("Erreur de connexion au serveur.");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const btnStart = document.getElementById("btn-watcher-start");
  const btnStop = document.getElementById("btn-watcher-stop");
  if (btnStart) btnStart.addEventListener("click", (e) => { e.preventDefault(); startWatcher(); });
  if (btnStop) btnStop.addEventListener("click", (e) => { e.preventDefault(); stopWatcher(); });
});