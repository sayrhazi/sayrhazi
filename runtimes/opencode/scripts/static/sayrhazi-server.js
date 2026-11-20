/*
 * sayrhazi-serveur - interface du dashboard Sayrhazi
 * Lit l'API /api/data et /report/:agent depuis le serveur unifié.
 * Une seule page : sidebar à gauche, contenu principal à droite.
 */

let currentData = null;
let intervalSeconds = 2;
let selectedAgent = null;
let selectedChatAgent = null;
let currentSessionId = null;
let pendingRevert = null;
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
// Streaming actif (agent génère des tokens/parts). Le bouton d'arrêt n'est
// activable qu'après l'arrivée du premier delta d'agent.
let sessionStreaming = {};

function isStopAvailable(sessionId) {
  return !!sessionStreaming[sessionId || currentSessionId];
}

function setStopClickable(sessionId, on) {
  const btn = document.getElementById("chat-send-btn");
  if (!btn) return;
  if (btn.classList.contains("stop")) {
    btn.disabled = !on;
  }
  sessionStreaming[sessionId || currentSessionId] = !!on;
}

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
  // (les stashs de revert sont conservés : ils servent à re-remplir la saisie
  // au rechargement tant que le revert n'est ni annulé ni validé).
  const stashBackup = {};
  if (typeof sessionStorage.length === "number" && typeof sessionStorage.key === "function") {
    for (let i = 0; i < sessionStorage.length; i++) {
      const k = sessionStorage.key(i);
      if (k && k.indexOf("sayrhazi.revertStash") === 0) {
        const v = sessionStorage.getItem(k);
        if (v !== null) stashBackup[k] = v;
      }
    }
  } else {
    const v = sessionStorage.getItem("sayrhazi.revertStash");
    if (v !== null) stashBackup["sayrhazi.revertStash"] = v;
  }
  sessionStorage.clear();
  Object.keys(stashBackup).forEach(k => { try { sessionStorage.setItem(k, stashBackup[k]); } catch (e) {} });

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
  // Les mentions @fichier déjà présentes dans la saisie (ex. après un revert)
  // sont reflétées même avant la première frappe.
  renderMentionHighlight();

  // Titre du chat cliquable : menu déroulant Renommer / Supprimer.
  const titleBtn = document.getElementById("chat-agent-title");
  if (titleBtn) titleBtn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    openChatTitleMenu(titleBtn);
  });

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
  const hasTask = data.task_id && String(data.task_id).trim() !== "-";
  document.getElementById("page-title").textContent = hasTask ? "Sayrhazi - " + data.task_id : "Sayrhazi";
  document.getElementById("status-badge").textContent = data.status;
  document.getElementById("watcher-status").textContent = data.watcher;

  // Cartes récapitulatives
  document.getElementById("card-scope").textContent = data.scope;
  document.getElementById("card-controls").textContent =
    Object.entries(data.controls).map(kv => kv[0] + "=" + kv[1]).join(" · ") || "-";
  document.getElementById("card-archived").textContent = data.archived.join(", ") || "-";

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
    watcherPidEl.textContent = m ? ("PID " + m[1]) : "-";
  }

  // Tableau des agents
  const tbody = document.getElementById("dashboard-table-body");
  tbody.innerHTML = "";

  data.agents.forEach(a => {
    const tr = document.createElement("tr");
    tr.dataset.agentFile = a.agent_file;
    tr.style.cursor = "pointer";
    tr.title = "Voir le rapport de l'agent";
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
    tr.addEventListener("click", () => {
      selectedAgent = a.agent_file;
      sessionStorage.setItem('sayrhazi_selected_agent', a.agent_file);
      showAgent(a.agent_file);
    });
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
  const statusEl = document.getElementById("chat-agent-status");
  updateChatHeaderTitle();
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

  // Charger la liste des sessions dans le panneau gauche, puis ouvrir
  // automatiquement la discussion la plus récente (haut de la liste) - la
  // conversation affiche « Chargement de la session... » en attendant.
  fetchChatSessions(agentFile).then(function () {
    if (selectedChatAgent !== agentFile) return;
    if (!chatSessions.length) return;
    const container = document.getElementById("chat-conversation");
    if (container) {
      container.innerHTML = '<div class="chat-empty">Chargement de la session...</div>';
    }
    loadSessionMessages(chatSessions[0].session, chatSessions[0].title);
  });

  // Suivre le cycle de vie des discussions (titre natif, création, suppression)
  connectSessionEvents();
  refreshComposerModel();
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
        '<div class="chat-session-meta">' + esc(time) + '</div>';
      item.addEventListener("click", (e) => {
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

// Affiche l'indicateur « l'agent est en cours de réponse » : bandeau
// persistant au-dessus de la saisie, visible tant que l'agent travaille.
function showAgentWorkingIndicator(sessionId) {
  const strip = document.getElementById("chat-agent-working");
  if (!strip) return null;
  strip.hidden = false;
  if (sessionId) strip.dataset.session = sessionId;
  return strip;
}

// Masque l'indicateur et oublie la discussion associée (fin de réponse ou
// erreur) - voir loadSessionMessages pour l'affichage conditionnel.
function hideAgentWorkingIndicator() {
  const strip = document.getElementById("chat-agent-working");
  if (!strip) return;
  strip.hidden = true;
  delete strip.dataset.session;
}

// ---------------------------------------------------------------------------
// Bandeau d'erreurs upstream : retry (ambre, agent toujours actif) et erreur
// définitive (rouge, tour terminé). Deux états distincts symétriques positionnés
// au-dessus du working strip, sous la conversation.
// ---------------------------------------------------------------------------
let agentErrorState = null; // { mode: 'retry'|'final', message, statusCode, attempt, next, name, responseBody }
let agentErrorCountdownTimer = null;

function formatAgentErrorCountdown() {
  if (!agentErrorState || agentErrorState.mode !== "retry") return "";
  var remain = Math.max(0, Math.round((Number(agentErrorState.next || 0) - Date.now()) / 1000));
  return remain > 0 ? ("retrying in " + remain + "s") : "retrying…";
}

function renderAgentErrorBanner() {
  var banner = document.getElementById("agent-error-banner");
  if (!banner) return;
  var icon = document.getElementById("agent-error-banner-icon");
  var textEl = document.getElementById("agent-error-banner-text");
  var info = document.getElementById("agent-error-banner-info");
  if (!agentErrorState) {
    banner.hidden = true;
    banner.className = "agent-error-banner";
    return;
  }
  banner.hidden = false;
  banner.className = "agent-error-banner agent-error-banner--" + agentErrorState.mode;
  if (icon) icon.textContent = agentErrorState.mode === "retry" ? "△" : "✕";  if (textEl) {
    if (agentErrorState.mode === "retry") {
      textEl.textContent = "Upstream request failed" +
        (agentErrorState.statusCode ? " [" + agentErrorState.statusCode + "]" : "") +
        " · " + formatAgentErrorCountdown() +
        " · attempt #" + (agentErrorState.attempt || 0);
    } else {
      var short = agentErrorState.statusCode ? " [" + agentErrorState.statusCode + "]" : "";
      textEl.textContent = "Upstream request failed" + short +
        (agentErrorState.message ? " · " + agentErrorState.message : "");
    }
  }
  if (info) info.hidden = agentErrorState.mode !== "final";
}

function cancelAgentErrorTimers() {
  if (agentErrorCountdownTimer) { clearInterval(agentErrorCountdownTimer); agentErrorCountdownTimer = null; }
}

function showAgentRetryBanner(ev) {
  cancelAgentErrorTimers();
  agentErrorState = {
    mode: "retry",
    attempt: Number(ev.attempt || 0),
    message: String(ev.message || ""),
    next: Number(ev.next || 0) || Date.now() + 5000,
    statusCode: ev.statusCode || null,
  };
  renderAgentErrorBanner();
  agentErrorCountdownTimer = setInterval(function () {
    if (!agentErrorState || agentErrorState.mode !== "retry") return;
    renderAgentErrorBanner();
    if (Number(agentErrorState.next || 0) <= Date.now() && agentErrorCountdownTimer) {
      clearInterval(agentErrorCountdownTimer);
      agentErrorCountdownTimer = null;
    }
  }, 1000);
}

function showAgentFinalBanner(err) {
  cancelAgentErrorTimers();
  err = err || {};
  var name = String(err.name || "");
  var msg = String(err.message || err.error || "");
  // Interruption volontaire : silencieuse, aucun bandeau (le pied « Interrompu » de la bulle suffit).
  if (/MessageAbortedError/i.test(name) || /abort/i.test(msg)) {
    hideAgentErrorBanner();
    return;
  }
  var data = err || {};
  var body = "";
  if (typeof data.responseBody === "string" && data.responseBody) {
    body = data.responseBody;
    if (body.length > 8000) body = body.slice(0, 8000) + "\n… (tronqué)";
  }
  agentErrorState = {
    mode: "final",
    name: String(data.name || "UnknownError"),
    statusCode: data.statusCode || null,
    message: String(data.message || data.error || ""),
    responseBody: body,
    isRetryable: !!data.isRetryable,
  };
  hideAgentWorkingIndicator();
  setSendButtonState("send");
  renderAgentErrorBanner();
}

function hideAgentErrorBanner() {
  cancelAgentErrorTimers();
  agentErrorState = null;
  renderAgentErrorBanner();
}

// Reprise silencieuse : dès le premier token réel, on masque le bandeau.
function maybeShowResumedBanner() {
  if (!agentErrorState || agentErrorState.mode !== "retry") return;
  hideAgentErrorBanner();
}

// ---------------------------------------------------------------------------
// Erreur définitive : bouton « i » de la bannière → modale dédiée (jamais un
// toast). Bouton unique « Fermer ».
// ---------------------------------------------------------------------------
function openUpstreamErrorModal() {
  if (!agentErrorState || agentErrorState.mode !== "final") return;
  var name = agentErrorState.name || "UnknownError";
  var status = agentErrorState.statusCode ? String(agentErrorState.statusCode) : "—";
  var raw = agentErrorState.responseBody;
  var msg = agentErrorState.message || "Erreur du fournisseur de modèle.";
  var preBlock = raw
    ? '<pre class="sayrhazi-modal-note" style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:34vh;overflow:auto;">' + esc(raw) + '</pre>'
    : '<p class="sayrhazi-modal-note">Réponse brute indisponible.</p>';
  sayrhaziModal({
    icon: "!",
    title: "△ Upstream request failed",
    bodyHTML:
      '<p><strong>' + esc(name) + '</strong> · HTTP ' + esc(status) + '</p>' +
      '<p>' + esc(msg) + '</p>' +
      preBlock,
    cancelLabel: "Fermer",
    hideConfirm: true,
  });
}

// ---------------------------------------------------------------------------
// Tour interne de Compaction OpenCode : l'agent `compaction` tourne
// silencieusement (rôle=assistant, agent/mode "compaction"). Même élément
// que le working strip mais en plus discret ; disparaît dès la reprise.
// ---------------------------------------------------------------------------
let compactionMsgs = new Set();

function showCompactionBadge() {
  var badge = document.getElementById("compaction-badge");
  if (!badge) return;
  badge.hidden = false;
  hideAgentWorkingIndicator(); // le working strip disparait pendant la compaction
}

function hideCompactionBadge() {
  var badge = document.getElementById("compaction-badge");
  if (!badge) return;
  badge.hidden = true;
}

function isCompactionMessage(id) {
  return compactionMsgs.has(id);
}

function trackCompactionMessage(id) {
  compactionMsgs.add(id);
}

// ---------------------------------------------------------------------------
// Mentions @fichier.ext#start-end : coloration accent dans le composer,
// autocomplétion avec /api/chat/find-file, mentions envoyées comme pièces
// jointes natives OpenCode (parts `file`).
// ---------------------------------------------------------------------------
const MENTION_RE = /@([A-Za-z0-9_.\/-]+)(?:#[0-9]+(?:-[0-9]+)?)?/g;
const ACTIVE_MENTION_RE = /@([A-Za-z0-9_.\/-]*)$/;

function renderMentionHighlight() {
  var input = document.getElementById("chat-message-input");
  var pre = document.getElementById("chat-highlight");
  var code = document.getElementById("chat-highlight-code");
  if (!input || !pre || !code) return;
  var value = input.value || "";
  // Recopie le texte avec les mentions mises en évidence. Les offsets du
  // textarea naturel sont conservés : la couche est : exactement au-dessus.
  var html = "";
  var last = 0;
  var match;
  MENTION_RE.lastIndex = 0;
  while ((match = MENTION_RE.exec(value)) !== null) {
    html += esc(value.slice(last, match.index));
    html += '<span class="mention-accent">' + esc(match[0]) + '</span>';
    last = match.index + match[0].length;
  }
  html += esc(value.slice(last));
  // White-space conservé pour un wrap identique au textarea
  html = html.replace(/\n$/, "\n");
  code.innerHTML = html;
  pre.scrollTop = input.scrollTop;
  pre.scrollLeft = input.scrollLeft;
  updateMentionUnreadable();
}

var mentionUnreadableTimer = null;
var mentionUnreadablePaths = [];

function updateMentionUnreadable() {
  var input = document.getElementById("chat-message-input");
  if (!input) return;
  var value = input.value || "";
  var paths = [];
  var match;
  MENTION_RE.lastIndex = 0;
  while ((match = MENTION_RE.exec(value)) !== null) {
    var p = match[1];
    if (p && paths.indexOf(p) < 0) paths.push(p);
  }
  mentionUnreadablePaths = paths;
}

function showMentionWarning(message) {
  var banner = document.getElementById("chat-warning-banner");
  if (!banner) return;
  var text = document.getElementById("chat-warning-banner-text");
  if (text) text.textContent = message;
  banner.style.opacity = "1";
  banner.hidden = false;
  if (mentionUnreadableTimer) clearTimeout(mentionUnreadableTimer);
  mentionUnreadableTimer = setTimeout(function () {
    if (banner) banner.style.opacity = "0";
    setTimeout(function () { if (banner) banner.hidden = true; }, 180);
  }, 2500);
}

// ---------------------------------------------------------------------------
// Autocomplétion @fichier : liste d'items ancrée au-dessus du composer (similaire
// aux bandeaux). Navigation au clavier (Up/Down/Enter/Tab/Escape). Origine : OpenCode TUI.
// ---------------------------------------------------------------------------
let autocompleteState = null; // {start, end, items, labels, index, query}
let autocompleteDebounce = null;

function closeAutocomplete() {
  autocompleteState = null;
  var list = document.getElementById("chat-autocomplete");
  if (list) list.hidden = true;
}

function isAutocompleteOpen() {
  return autocompleteState !== null;
}

function getActiveMentionEnd(value, caretPos) {
  var active = ACTIVE_MENTION_RE.exec(value.slice(0, caretPos));
  if (!active) return null;
  return { start: caretPos - active[0].length, end: caretPos, query: active[1] || "" };
}

function fetchMentionMatches(query) {
  var activeQuery = (query || "").trim();
  if (!activeQuery) return Promise.resolve([]);
  return fetch("/api/chat/find-file?query=" + encodeURIComponent(activeQuery) + "&type=any&limit=12")
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) { return (data && data.matches) || []; })
    .catch(function () { return []; });
}

function renderAutocomplete() {
  var list = document.getElementById("chat-autocomplete");
  if (!list || !autocompleteState) return;
  var items = autocompleteState.items;
  if (!items.length) {
    list.innerHTML = '<div class="chat-autocomplete-empty">No matching items</div>';
    list.hidden = false;
    return;
  }
  var html = '<ul class="chat-autocomplete-list">';
  for (var i = 0; i < items.length; i++) {
    var it = items[i];
    var pathHtml = "";
    var needle = (autocompleteState.query || "").toLowerCase();
    var lower = it.path.toLowerCase();
    var pos = lower.indexOf(needle);
    if (pos >= 0) {
      pathHtml = esc(it.path.slice(0, pos)) +
        '<span class="mention-accent">' + esc(it.path.slice(pos, pos + needle.length)) + '</span>' +
        esc(it.path.slice(pos + needle.length));
    } else {
      pathHtml = esc(it.path);
    }
    html += '<li class="chat-autocomplete-item' +
      (autocompleteState.index === i ? " chat-autocomplete-item--active" : "") +
      '" data-index="' + i + '" role="option">'
      + (it.directory ? "📁 " : "") + pathHtml
      + '</li>';
  }
  html += "</ul>";
  list.innerHTML = html;
  list.hidden = false;
  var active = list.querySelector(".chat-autocomplete-item--active");
  if (active && active.scrollIntoView) {
    try { active.scrollIntoView({ block: "nearest" }); } catch (e) { /* ignore */ }
  }
  Array.prototype.forEach.call(list.querySelectorAll(".chat-autocomplete-item"), function (el) {
    el.addEventListener("mousedown", function (e) {
      e.preventDefault();
      var idx = parseInt(el.getAttribute("data-index"), 10);
      if (!isNaN(idx)) pickAutocompleteIndex(idx);
    });
  });
}

function openMentionPalette(start, end, query) {
  autocompleteState = { start: start, end: end, query: query, items: [], index: 0 };
  renderAutocomplete();
  if (autocompleteDebounce) clearTimeout(autocompleteDebounce);
  autocompleteDebounce = setTimeout(function () {
    if (!autocompleteState || autocompleteState.query !== query) return;
    fetchMentionMatches(query).then(function (matches) {
      if (!autocompleteState || autocompleteState.query !== query) return;
      autocompleteState.items = matches;
      autocompleteState.index = 0;
      renderAutocomplete();
    });
  }, 120);
}

function pickAutocompleteIndex(index) {
  if (!autocompleteState) return;
  var input = document.getElementById("chat-message-input");
  var item = autocompleteState.items[index];
  if (!input || !item) { closeAutocomplete(); return; }
  var before = input.value.slice(0, autocompleteState.start);
  var after = input.value.slice(autocompleteState.end);
  var inserted = "@" + item.path;
  input.value = before + inserted + after + " ";
  var pos = (before + inserted).length + 1;
  input.setSelectionRange(pos, pos);
  autocompleteState = null;
  var list = document.getElementById("chat-autocomplete");
  if (list) list.hidden = true;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.focus();
  renderMentionHighlight();
  updateSendButtonAvailability();
}

function moveAutocomplete(delta) {
  if (!autocompleteState) return;
  if (!autocompleteState.items.length) return;
  var n = autocompleteState.items.length;
  autocompleteState.index = ((autocompleteState.index + delta) % n + n) % n;
  renderAutocomplete();
}

function applyAutocompleteForInput() {
  var input = document.getElementById("chat-message-input");
  if (!input) return;
  var caret = input.selectionStart || 0;
  var active = getActiveMentionEnd(input.value, caret);
  if (!active) {
    closeAutocomplete();
    return;
  }
  var sameQuery = autocompleteState && autocompleteState.query === active.query && autocompleteState.end === active.end;
  if (sameQuery) return;
  openMentionPalette(active.start, active.end, active.query);
}

function handleAutocompleteKeydown(e) {
  if (!autocompleteState || !isAutocompleteOpen()) return false;
  if (e.key === "Escape") {
    e.preventDefault();
    closeAutocomplete();
    return true;
  }
  if (e.key === "ArrowDown") {
    e.preventDefault();
    moveAutocomplete(1);
    return true;
  }
  if (e.key === "ArrowUp") {
    e.preventDefault();
    moveAutocomplete(-1);
    return true;
  }
  if (e.key === "Enter" || e.key === "Tab") {
    e.preventDefault();
    if (autocompleteState.items.length) {
      pickAutocompleteIndex(autocompleteState.index);
    }
    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// Envoi avec mentions @fichier#1-19.
// ---------------------------------------------------------------------------
function buildMentionAttachments(text) {
  var attachments = [];
  var match;
  MENTION_RE.lastIndex = 0;
  while ((match = MENTION_RE.exec(text)) !== null) {
    var path = match[1];
    if (!path) continue;
    var lineEnd = -1;
    var rangePart = match[0].slice(path.length + 1);
    if (rangePart && rangePart.charAt(0) === "#") {
      var nums = rangePart.slice(1).split("-");
      if (nums.length === 1) {
        lineEnd = parseInt(nums[0], 10);
      } else if (nums.length === 2) {
        lineEnd = parseInt(nums[1], 10);
      }
    }
    var fullPath = path;
    attachments.push({ path: fullPath, startLine: 1, endLine: lineEnd > 0 ? lineEnd : undefined });
  }
  return attachments;
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
      const modelProvider = m.model_provider || "";
      const duration = m.duration || "";
      if (!model && !duration) return;
      var bits = [];
      if (model) bits.push(esc(model) + (modelProvider ? ' · ' + esc(modelProvider) : ''));
      if (duration) bits.push(esc(duration));
      if (!bits.length) return;
      const foot = '<div class="agent-block-foot">▣ ' + esc(agentLabel) + ' · ' + bits.join(' · ') + '</div>';
      const existing = block.querySelector('.agent-block-foot');
      if (existing) {
        existing.outerHTML = foot;
      } else {
        const wrapper = document.createElement("div");
        wrapper.innerHTML = foot;
        block.appendChild(wrapper.firstElementChild);
      }
    });
    scrollChatToBottom();
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

  // Une fois le contenu agent rendu dans le DOM, le bouton "stop" devient activable.
  if (sessionStreaming[currentSessionId]) {
    const btn = document.getElementById("chat-send-btn");
    if (btn && !btn.classList.contains("stop")) setSendButtonState("stop");
    if (btn) btn.disabled = false;
  }

  // Scroll après l'insertion/remplacement : `scrollChatToBottom` lit
  // scrollHeight, donc il voit le contenu ajouté à cette frame.
  scrollChatToBottom();
}

// Dernière bulle user sans id serveur dans la conversation affichée.
function lastUnidentifiedUserNode(scope) {
  const nodes = scope.querySelectorAll(".chat-message.user");
  for (let i = nodes.length - 1; i >= 0; i--) {
    if (!nodes[i].dataset.messageId) return nodes[i];
  }
  return null;
}

// Attribue les ids serveur aux bulles user mais sans recharger la discussion :
// les nœuds live (envoyés depuis le composer) n'ont pas d'id jusqu'au premier
// appel à la re-rendu global ; on matche alors les nœuds aux messages du
// serveur, du plus récent au plus ancien.
async function syncLiveUserMessageIds(sessionId) {
  if (!sessionId) return;
  try {
    const response = await fetch("/api/chat/session/" + encodeURIComponent(sessionId) + "/messages");
    if (!response.ok) return;
    const data = await response.json();
    const serverUsers = (data.messages || []).filter(m => m && m.role === "user" && m.message_id);
    const scope = document.getElementById("chat-conversation");
    if (!scope) return;
    const domUsers = Array.from(scope.querySelectorAll(".chat-message.user"));
    const missing = domUsers.filter(n => !n.dataset.messageId);
    if (missing.length === 0) return;
    const claimed = new Set(domUsers.map(n => n.dataset.messageId).filter(Boolean));
    const candidates = serverUsers.filter(m => !claimed.has(m.message_id));
    for (let i = 0; i < missing.length && candidates.length > 0; i++) {
      const m = candidates[candidates.length - 1 - i];
      missing[missing.length - 1 - i].dataset.messageId = String(m.message_id);
    }
  } catch (err) {
    // id indisponible : le bouton ↩ fonctionnera après le prochain rechargement
  }
}

// Dernière bulle user sans id dans la conversation affichée (fallback clic ↩).
async function ensureLiveUserMessageId(anchorEl) {
  const scope = document.getElementById("chat-conversation");
  if (!scope || !anchorEl) return;
  if (anchorEl.dataset.messageId) return;
  await syncLiveUserMessageIds(currentSessionId);
  return !!anchorEl.dataset.messageId;
}

// Routeur des événements du flux SSE /api/chat/stream.
function handleSessionStreamEvent(ev) {
  if (!ev || !ev.kind) return;

  if (ev.kind === "part" || ev.kind === "text_delta") {
    // En attente que le rendu du premier contenu agent affiche le bouton stop
    sessionStreaming[currentSessionId] = true;
    // Reprise après une boucle de retry : le bandeau disparaît dès la reprise.
    maybeShowResumedBanner();
  }

  if (ev.kind === "part") {
    if (isCompactionMessage(ev.messageID)) return;
    // RetryPart : inline rien ; elle alimente/actualise juste le bandeau ambre.
    if (ev.item && ev.item.kind === "retry") {
      if (agentErrorState && agentErrorState.mode === "retry") {
        if (ev.item.attempt) agentErrorState.attempt = ev.item.attempt;
        if (ev.item.error_status) agentErrorState.statusCode = ev.item.error_status;
        if (ev.item.error_message) agentErrorState.message = ev.item.error_message;
        renderAgentErrorBanner();
      }
      return;
    }
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
    scrollChatToBottom();
    return;
  }

  if (ev.kind === "text_delta") {
    if (isCompactionMessage(ev.messageID)) return;
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
    scrollChatToBottom();
    return;
  }

  if (ev.kind === "retry") {
    showAgentRetryBanner(ev);
    return;
  }

  if (ev.kind === "message") {
    // Tour interne OpenCode (agent "compaction") ou replay post-compaction :
    // les deux passent par le même superviseur sans produire de bulle visible.
    var evAgent = String(ev.agent || "").toLowerCase();
    if (ev.role === "agent" && evAgent === "compaction") {
      trackCompactionMessage(ev.messageID);
      hideAgentErrorBanner();
      showCompactionBadge();
      return;
    }
    if (isCompactionMessage(ev.messageID)) {
      if (ev.completed !== null) {
        // Tour interne terminé : on laisse le replay post-compaction continuer.
        // Le badge reste jusqu'au message assistant rejoué.
      }
      return;
    }
    if (ev.role === "agent") {
      // Premier assistant du replay post-compaction : fin de la compaction.
      hideCompactionBadge();
      if (!currentSessionId) {
        // La session n'a pas été ouverte, on recharge dès la reprise.
      }
    }
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
    // Message assistant finalisé avec une erreur amont → bandeau rouge définitif.
    if (ev.error && ev.role === "agent" && ev.completed !== null) {
      showAgentFinalBanner(ev.error);
      return;
    }
    // Message utilisateur créé par prompt_async : on note son id serveur sur
    // la bulle live pour que le bouton ↩ (Revert) soit utilisable sur la
    // bulle sans rechargement de la page.
    if (ev.role === "user" && ev.messageID) {
      const scope = document.getElementById("chat-conversation");
      if (scope) {
        const node = lastUnidentifiedUserNode(scope);
        if (node) node.dataset.messageId = String(ev.messageID);
      }
    }
    return;
  }

  if (ev.kind === "idle" || ev.kind === "done") {
    disconnectSessionStream();
    stopAgentReplyWatch();
    hideAgentWorkingIndicator();
    hideCompactionBadge();
    sessionStreaming[currentSessionId] = false;
    setSendButtonState("send");
    if (currentSessionId) {
      // Pas de rechargement global : juste la pose du pied natif
      // (durée de tour) sur les blocs déjà rendus.
      finalizeLiveConversation(currentSessionId);
      // Sécurité : s'assurer que toutes les bulles user live portent leur id serveur.
      syncLiveUserMessageIds(currentSessionId);
    }
    return;
  }

  if (ev.kind === "error") {
    var msgStr = String(ev.message || "");
    // Interruption volontaire de l'utilisateur : silencieuse, pas de system message.
    if (/MessageAbortedError|aborted/i.test(msgStr) || /MessageAbortedError/i.test(String(ev.name || ""))) {
      try { sessionStorage.setItem("sayrhazi.abortedAt." + currentSessionId, String(Date.now())); } catch (e) { /* ignore */ }
      disconnectSessionStream();
      stopAgentReplyWatch();
      hideAgentWorkingIndicator();
      hideAgentErrorBanner();
      hideCompactionBadge();
      setSendButtonState("send");
      if (currentSessionId) loadSessionMessages(currentSessionId);
      return;
    }
    disconnectSessionStream();
    stopAgentReplyWatch();
    hideCompactionBadge();
    showAgentFinalBanner(ev);
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
    // Le flux est mort : on recharge uniquement quand aucun revert n'est en
    // cours localement (une rechargement ferait clignoter l'interface) ; sinon
    // on se contente de rendre l'envoi disponible - l'état local est le bon.
    if (!pendingRevert) loadSessionMessages(sessionId);
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
  updateChatHeaderTitle();
}

// Titre courant du header « Chat - <agent> : <discussion> » ; "" si aucune discussion ouverte.
function currentSessionLabel() {
  if (!currentSessionId) return "";
  const s = chatSessions.find(x => x.session === currentSessionId);
  return s ? chatSessionTitle(s) : "";
}

function updateChatHeaderTitle() {
  const sessionEl = document.getElementById("chat-session-name");
  const label = currentSessionLabel();
  if (sessionEl) {
    sessionEl.textContent = label;
    sessionEl.title = label || "";
  }
  const titleBtn = document.getElementById("chat-agent-title");
  if (titleBtn) {
    titleBtn.disabled = !currentSessionId;
    titleBtn.classList.toggle("chat-agent-title--active", !!currentSessionId);
    if (!currentSessionId) titleBtn.setAttribute("aria-expanded", "false");
  }
}

// Menu déroulant du titre du chat (Renommer / Supprimer).
function openChatTitleMenu(anchorEl) {
  if (!currentSessionId) return;
  if (chatSessionMenu) {
    closeChatSessionMenu();
    return;
  }
  const title = currentSessionLabel();
  const menu = document.createElement("div");
  menu.className = "chat-session-menu";
  menu.innerHTML =
    '<div class="chat-session-menu-title">' + esc(title || "Discussion") + '</div>' +
    '<button type="button" class="chat-session-menu-item" data-action="rename">Renommer la discussion</button>' +
    '<button type="button" class="chat-session-menu-item danger" data-action="delete">Supprimer la discussion</button>';
  document.body.appendChild(menu);
  chatSessionMenu = menu;

  const rect = anchorEl.getBoundingClientRect();
  const mw = menu.offsetWidth || 230;
  const mh = menu.offsetHeight || 130;
  menu.style.position = "fixed";
  menu.style.left = Math.min(Math.max(4, rect.left), Math.max(4, window.innerWidth - mw - 8)) + "px";
  menu.style.top = Math.min(rect.bottom + 4, Math.max(4, window.innerHeight - mh - 8)) + "px";

  menu.querySelector('[data-action="rename"]').addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    closeChatSessionMenu();
    openRenameSessionModal(currentSessionId, currentSessionLabel());
  });
  menu.querySelector('[data-action="delete"]').addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    closeChatSessionMenu();
    confirmAndDeleteSession(currentSessionId, currentSessionLabel());
  });

  const titleBtn = anchorEl;
  titleBtn.setAttribute("aria-expanded", "true");
  setTimeout(() => {
    document.addEventListener("click", closeChatSessionMenu, { once: true });
    document.addEventListener("keydown", onChatSessionMenuKey, { once: true });
  }, 0);
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
  updateChatHeaderTitle();
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
  const titleBtn = document.getElementById("chat-agent-title");
  if (titleBtn) titleBtn.setAttribute("aria-expanded", "false");
}

// Suppression : confirmation via un modal Sayrhazi (jamais de boîte native)
// puis endpoint natif DELETE /api/chat/session/:id. La session n'est retirée
// de l'interface qu'après confirmation du serveur.
function confirmAndDeleteSession(sessionId, title) {
  if (!sessionId) return;
  const label = title || "cette discussion";
  sayrhaziModal({
    icon: "⚠",
    title: "Supprimer la discussion",
    bodyHTML:
      '<p>Supprimer définitivement la discussion <strong>« ' + esc(label) + ' »</strong> ?</p>' +
      '<p class="sayrhazi-modal-note">Les messages et les sessions enfants seront supprimés par OpenCode. Cette action est irréversible.</p>',
    cancelLabel: "Annuler",
    confirmLabel: "Supprimer",
    onConfirm: function () { deleteSessionConfirmed(sessionId, label); },
  });
}

async function deleteSessionConfirmed(sessionId, label) {
  try {
    const response = await fetch("/api/chat/session/" + encodeURIComponent(sessionId), {
      method: "DELETE",
      headers: { "Content-Type": "application/json; charset=utf-8" }
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      showChatErrorModal("Suppression impossible", (data && (data.message || data.error)) || "erreur " + response.status);
      return;
    }
    removeChatSession(sessionId);
  } catch (err) {
    console.error("Erreur suppression session:", err);
    showChatErrorModal("Suppression impossible", "Erreur de connexion au serveur lors de la suppression.");
  }
}

// Modal d'erreur sobre (un seul bouton « Fermer ») - jamais alert().
function showChatErrorModal(title, message) {
  sayrhaziModal({
    icon: "!",
    title: title,
    bodyHTML: '<p>' + esc(message) + '</p>',
    cancelLabel: "Fermer",
    hideConfirm: true,
  });
}

// Renommage : modal avec saisie pré-remplie du nom courant, Annuler / Confirmer.
function openRenameSessionModal(sessionId, currentTitle) {
  if (!sessionId) return;
  var pendingTitle = currentTitle || "";
  sayrhaziModal({
    icon: "✎",
    title: "Renommer la discussion",
    bodyHTML:
      '<label class="sayrhazi-modal-rename-label" for="sayrhazi-rename-input">Nom de la discussion</label>' +
      '<input type="text" id="sayrhazi-rename-input" class="sayrhazi-modal-rename-input" maxlength="200" autocomplete="off" spellcheck="false" />' +
      '<p class="sayrhazi-modal-note sayrhazi-modal-note--error" id="sayrhazi-rename-error" hidden>Le nom ne peut pas être vide.</p>',
    cancelLabel: "Annuler",
    confirmLabel: "Confirmer",
    beforeConfirm: function () {
      var input = document.getElementById("sayrhazi-rename-input");
      var error = document.getElementById("sayrhazi-rename-error");
      var value = input ? input.value.trim() : "";
      if (!value) {
        if (error) error.hidden = false;
        if (input) input.focus();
        return false;
      }
      pendingTitle = value;
      return true;
    },
    onConfirm: function () { submitRenameSession(sessionId, pendingTitle); },
  });
  setTimeout(function () {
    var input = document.getElementById("sayrhazi-rename-input");
    if (!input) return;
    input.value = pendingTitle;
    input.focus();
    input.select();
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        var btn = document.querySelector("#sayrhazi-modal-backdrop [data-act=\"confirm\"]");
        if (btn) btn.click();
      }
    });
  }, 0);
}

async function submitRenameSession(sessionId, newTitle) {
  if (!newTitle) return;
  try {
    const response = await fetch("/api/chat/session/" + encodeURIComponent(sessionId) + "/title", {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify({ title: newTitle })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      showChatErrorModal("Renommage impossible", (data && (data.message || data.error)) || "erreur " + response.status);
      return;
    }
    updateChatSessionTitle(sessionId, newTitle);
  } catch (err) {
    console.error("Erreur renommage session:", err);
    showChatErrorModal("Renommage impossible", "Erreur de connexion au serveur lors du renommage.");
  }
}

// === Revert / Annuler le revert ===
// Sayrhazi orchestre la primitive native d'OpenCode (POST /revert, POST
// /unrevert, état `session.revert` lu via /info) : rien n'est supprimé avant
// un envoi validant (cleanup natif dans prompt_async) ; les messages
// reverted sont masqués côté GUI, jamais effacés côté lecture. Le bandeau
// persiste tant que le revert n'est ni annulé (unrevert) ni validé (envoi).
// Le stash sessionStorage garde le texte de la saisie éditée entre le revert
// et la validation, y compris après un rechargement de page.

var revertPreviewCache = null;    // { sessionId, messageId, diff, at }
var revertPreviewInflight = null; // { sessionId, messageId, promise }
// L'aperçu atomique (revert+unrevert) est LE moyen d'obtenir le diff d'un
// revert sans route v1 dédiée : il est donc lancé dès l'ouverture de la
// modal - l'utilisateur lit la confirmation pendant que OpenCode calcule,
// la confirmation affiche alors les fichiers instantanément. Le résultat
// n'est conservé que 10 s : le bandeau est un affichage, la vraie
// restauration des fichiers se fait sur le snapshot d'OpenCode au moment
// de l'envoi ; un disque modifié entre-temps change le diff réel, pas la
// décision « revert oui/non ».
function getRevertPreview(sid, messageId) {
  var now = Date.now();
  if (revertPreviewCache && revertPreviewCache.sessionId === sid && revertPreviewCache.messageId === messageId && (now - revertPreviewCache.at) < 10000) {
    return Promise.resolve(revertPreviewCache);
  }
  if (revertPreviewInflight && revertPreviewInflight.sessionId === sid && revertPreviewInflight.messageId === messageId) {
    return revertPreviewInflight.promise;
  }
  var promise = fetch("/api/chat/session/" + encodeURIComponent(sid) + "/revert/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({ messageID: messageId })
  }).then(function (resp) {
    return resp.json().catch(function () { return {}; }).then(function (data) {
      if (!resp.ok || !data || !data.ok) {
        var err = new Error("aperçu non disponible : " + resp.status);
        err.status = resp.status;
        throw err;
      }
      var entry = { sessionId: sid, messageId: messageId, diff: String(data.diff || ""), at: Date.now() };
      revertPreviewCache = entry;
      return entry;
    });
  }).finally(function () {
    if (revertPreviewInflight && revertPreviewInflight.promise === promise) revertPreviewInflight = null;
  });
  revertPreviewInflight = { sessionId: sid, messageId: messageId, promise: promise };
  return promise;
}


function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// Normalise un chemin de fichier : sépareurs homogènes, préfixe du dossier
// racine Sayrhazi retiré (insensible à la casse) - les diffs outils portent
// souvent un chemin absolu là où le git diff produit du relatif.
function normalizeDiffPath(name) {
  var s = String(name || "").trim().replace(/\\/g, "/");
  if (!s) return s;
  if (s.indexOf("./") === 0) s = s.slice(2);
  var root = String(window.__sayrhaziRoot || "").replace(/\\/g, "/");
  if (root) {
    if (root.slice(-1) !== "/") root += "/";
    if (s.toLowerCase().indexOf(root.toLowerCase()) === 0) s = s.slice(root.length);
  }
  if (s.charAt(0) === "/") s = s.slice(1);
  return s;
}

// Parse le patch unifié `session.revert.diff` : [{filename, additions, deletions}].
// Le diff produit par OpenCode est standard (diff --git + hunks).
function parseRevertDiff(diff) {
  var files = [];
  if (!diff) return files;
  var lines = String(diff).split(/\r?\n/);
  var current = null;
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    var m = line.match(/^diff --git a\/(.+) b\/(.+)$/);
    if (m) {
      current = { filename: normalizeDiffPath(m[2] || m[1]), additions: 0, deletions: 0 };
      files.push(current);
      continue;
    }
    if (!current) continue;
    if (line.substr(0, 4) === "+++ ") {
      // Fichier supprimé : +++ /dev/null - le nom vient de la ligne diff --git.
      var pm = line.match(/^\+\+\+ b\/(.+)$/);
      if (pm) current.filename = normalizeDiffPath(pm[1]);
      continue;
    }
    if (line.substr(0, 4) === "--- ") continue;
    if (line.charAt(0) === "+") current.additions += 1;
    else if (line.charAt(0) === "-") current.deletions += 1;
  }
  return files;
}

// Remplit ou vide le bloc « fichiers concernés » du bandeau (masqué si aucun fichier).
function renderRevertBannerFiles(filesEl) {
  if (!filesEl) return;
  var pr = pendingRevert;
  if (pr && pr.filesLoading) {
    filesEl.innerHTML = '<li class="revert-banner-file revert-banner-file-pending"><span class="chat-spinner" aria-hidden="true"></span>Calcul du diff…</li>';
    filesEl.hidden = false;
    return;
  }
  var files = pr && Array.isArray(pr.diffFiles) ? pr.diffFiles : [];
  if (files.length === 0) {
    filesEl.innerHTML = "";
    filesEl.hidden = true;
    return;
  }
  filesEl.innerHTML = files.map(function (f) {
    const stats =
      (f.additions > 0 ? '<span class="revert-diff-add">+' + f.additions + "</span>" : "") +
      (f.deletions > 0 ? '<span class="revert-diff-del">-' + f.deletions + "</span>" : "");
    var name = String(f.filename || "");
    return '<li class="revert-banner-file"><span class="revert-banner-file-name" title="' + esc(name) + '">' +
      esc(name) + '</span><span class="revert-banner-file-stats">' + stats + "</span></li>";
  }).join("");
  filesEl.hidden = false;
}

// Stash du revert : une entrée PAR discussion (sayrhazi.revertStash.<id>) -
// un revert en attente dans la discussion A survit à un aller-retour vers
// la discussion B, comme côté TUI OpenCode.
function readRevertStash(sessionId) {
  try {
    if (!sessionId) return JSON.parse(sessionStorage.getItem("sayrhazi.revertStash") || "null");
    return JSON.parse(sessionStorage.getItem("sayrhazi.revertStash." + sessionId) || "null");
  } catch (err) {
    return null;
  }
}

function writeRevertStash(stash) {
  try {
    if (stash && stash.sessionId) sessionStorage.setItem("sayrhazi.revertStash." + stash.sessionId, JSON.stringify(stash));
  } catch (err) {
    // stockage plein : la restauration de la saisie ne survivra pas au reload
  }
}

function clearRevertStash(sessionId) {
  try {
    sessionStorage.removeItem("sayrhazi.revertStash" + (sessionId ? "." + sessionId : ""));
  } catch (err) {
    // ignore
  }
}

function truncateText(value, max) {
  var s = String(value || "").replace(/\s+/g, " ").trim();
  var limit = max || 240;
  if (s.length <= limit) return s;
  return s.slice(0, limit).replace(/\s+\S*$/, "") + "…";
}

function frenchCount(n, singular, plural) {
  return n === 1 ? "1 " + singular : n + " " + (plural || singular + "s");
}

// Compte les user messages masqués à partir du boundary inclus (même
// définition que la TUI OpenCode).
function countRevertHiddenUsers() {
  return document.querySelectorAll("#chat-conversation .chat-message.user.revert-hidden").length;
}

// Les nœuds masqués portent toujours la classe .revert-hidden (rendu à la
// volée ou après un clic ↩) : on les retire tous, quel que soit leur id.
function clearRevertHiddenNodes() {
  var removed = 0;
  document.querySelectorAll("#chat-conversation .revert-hidden").forEach(function (el) {
    el.remove();
    removed += 1;
  });
  return removed;
}

// Modal maison Sayrhazi : jamais de window.confirm/alert pour cette action.
// Renvoie vers onConfirm(confirmLabel) si l'utilisateur confirme, sinon rien.
function sayrhaziModal(opts) {
  var backdrop = document.createElement("div");
  backdrop.className = "sayrhazi-modal-backdrop";
  backdrop.id = "sayrhazi-modal-backdrop";
  var defaultFooterHTML =
    '<button type="button" class="sayrhazi-modal-btn sayrhazi-modal-btn-ghost" data-act="cancel">' + esc(opts.cancelLabel || "Annuler") + '</button>' +
    (opts.hideConfirm ? '' : '<button type="button" class="sayrhazi-modal-btn sayrhazi-modal-btn-accent" data-act="confirm">' + esc(opts.confirmLabel || "Confirmer") + '</button>');
  var footerHTML = typeof opts.footerHTML === "string" && opts.footerHTML.length ? opts.footerHTML : defaultFooterHTML;
  backdrop.innerHTML =
    '<div class="sayrhazi-modal" role="dialog" aria-modal="true" aria-labelledby="sayrhazi-modal-title">' +
      '<div class="sayrhazi-modal-header">' +
        '<span class="sayrhazi-modal-icon" aria-hidden="true">' + esc(opts.icon || "↩") + '</span>' +
        '<h3 class="sayrhazi-modal-title" id="sayrhazi-modal-title">' + esc(opts.title || "") + '</h3>' +
        '<button type="button" class="sayrhazi-modal-close" aria-label="Fermer">&times;</button>' +
      '</div>' +
      '<div class="sayrhazi-modal-body">' + opts.bodyHTML + '</div>' +
      '<div class="sayrhazi-modal-footer' + (opts.hideConfirm ? ' sayrhazi-modal-footer--single' : '') + '">' +
        footerHTML +
      '</div>' +
    '</div>';

  var previousFocus = document.activeElement;

  function close() {
    document.removeEventListener("keydown", onKey, true);
    backdrop.remove();
    if (previousFocus && previousFocus.focus) previousFocus.focus();
  }

  function onKey(ev) {
    if (ev.key === "Escape") {
      ev.preventDefault();
      ev.stopPropagation();
      close();
    }
  }

  backdrop.addEventListener("mousedown", function (ev) {
    if (ev.target === backdrop) close();
  });
  var closeBtn = backdrop.querySelector(".sayrhazi-modal-close");
  if (closeBtn) closeBtn.addEventListener("click", close);
  var cancelBtn = backdrop.querySelector('[data-act="cancel"]');
  if (cancelBtn) cancelBtn.addEventListener("click", function () {
    console.log("[modal] fermée via « " + (opts.cancelLabel || "Annuler") + " »");
    close();
  });
  if (!opts.hideConfirm) {
    var accentBtn = backdrop.querySelector('[data-act="confirm"]');
    if (accentBtn) accentBtn.addEventListener("click", function () {
      console.log("[modal] confirmée");
      if (typeof opts.beforeConfirm === "function" && opts.beforeConfirm() === false) return;
      close();
      if (typeof opts.onConfirm === "function") opts.onConfirm();
    });
  }

  document.body.appendChild(backdrop);
  document.addEventListener("keydown", onKey, true);
  var confirmBtn = backdrop.querySelector('[data-act="confirm"]') || backdrop.querySelector('[data-act="cancel"]');
  if (confirmBtn) confirmBtn.focus();
  return { close: close };
}

// Calcul local (synchrone) des fichiers concernés : union des diffs des
// outils edit/write/patch présents dans les messages masqués. Aucun aller-
// retour réseau ; affichage immédiat. Les diffs venus d'opérations bash non
// liées à un patch ne sont pas détectés - la suppression réelle avant ce
// clic se fait par snapshot à l'envoi, à cet instant précis.
function localDiffFiles(anchorEl) {
  var byFile = {};
  var node = anchorEl;
  while (node) {
    if (node.classList && node.classList.contains("chat-message")) {
      var id = node.dataset && node.dataset.messageId;
      if (id) {
        var m = remoteMessages[id];
        if (m && Array.isArray(m.items)) {
          mergeDiffFiles(byFile, m.items);
        }
      }
    }
    node = node.nextElementSibling;
  }
  return Object.keys(byFile).map(function (k) { return byFile[k]; });
}

// Union des diffs d’outils edit/write/patch sur une plage de messages
// (chemin JSON interne à /messages), utilisée au rechargement.
function diffFilesFromMessages(messages, boundaryIndex) {
  var byFile = {};
  for (var i = boundaryIndex; i < messages.length; i++) {
    var m = messages[i];
    if (m && Array.isArray(m.items)) mergeDiffFiles(byFile, m.items);
  }
  return Object.keys(byFile).map(function (k) { return byFile[k]; });
}

// Union des diffs outils présents dans les messages masqués.
// Le diff transporté par l'outil (edit/write/patch) est calculé dans le sens :
// fichiers avec les changements que l'agent a faits (création/modification).
// Le banner annonce ce que le revert fera : supprimer / restaurer - donc les
// ajouts de l'outil deviennent les suppressions du revert, et inversement.
function mergeDiffFiles(byFile, items) {
  items.forEach(function (item) {
    if (!item || item.kind !== "tool") return;
    var file = normalizeDiffPath(item.file || "");
    if (!file) return;
    if (typeof item.additions === "number" || typeof item.deletions === "number") {
      // Revert : l'ajout de l'outil devient la suppression du revert.
      upsertFile(byFile, file, item.deletions || 0, item.additions || 0);
    } else if (item.diff_content) {
      parseRevertDiff(item.diff_content).forEach(function (f) {
        // Revert : + ↔ - inversés pour chaque hunk.
        upsertFile(byFile, f.filename, f.deletions || 0, f.additions || 0);
      });
    }
  });
}

function upsertFile(byFile, file, add, del) {
  var b = byFile[file];
  if (!b) b = byFile[file] = { filename: file, additions: 0, deletions: 0 };
  b.additions += add;
  b.deletions += del;
}

// Modal A - « Restaurer ce message » : confirmation avant revert.
// Compte les messages postérieurs visibles entre le boundary et la fin.
function openRevertModal(anchorEl, text, messageId) {
  var posterior = 0;
  var posteriorUsers = 0;
  var node = anchorEl ? anchorEl.nextElementSibling : null;
  console.log("[revert] ouverture modal A", { messageId: messageId || "(sans id)", sessionId: currentSessionId, text: truncateText(text, 60) });
  // Lance le calcul du diff pendant que l'utilisateur lit la modal : à la
  // confirmation, les fichiers s'affichent sans attendre un second aperçu.
  if (messageId && currentSessionId) {
    getRevertPreview(currentSessionId, messageId).catch(function (err) {
      console.error("[revert] aperçu en tâche de fond impossible", err);
    });
  }
  while (node) {
    if (node.classList && node.classList.contains("chat-message")) {
      posterior += 1;
      if (node.classList.contains("user")) posteriorUsers += 1;
    }
    node = node.nextElementSibling;
  }
  console.log("[revert] messages postérieurs :", posterior, "dont", posteriorUsers, "user");
  var quote = truncateText(text, 240);
  var posteriorLi = posterior > 0
    ? '<li>' + esc(frenchCount(posterior, "message")) + ' postérieur' + (posterior > 1 ? "s" : "") +
      ' (' + esc(frenchCount(posteriorUsers, "message", "messages")) + ' de vous) seront <strong>masqués</strong> dans la conversation ;</li>'
    : '<li><strong>aucun</strong> message postérieur - le message sera seulement renvoyé dans la saisie ;</li>';
  var body =
    '<div class="sayrhazi-modal-quote">« ' + esc(quote || "(message vide)") + ' »</div>' +
    '<p>Sayrhazi reviendra à ce message :</p>' +
    '<ul class="sayrhazi-modal-list">' +
      posteriorLi +
      '<li>les fichiers seront <strong>restaurés à l’état antérieur</strong> (snapshot OpenCode) ;</li>' +
      '<li>rien n’est <strong>supprimé</strong> tant que vous n’envoyez pas un nouveau message - vous pourrez <strong>annuler ce revert</strong> depuis le bandeau.</li>' +
    '</ul>' +
    '<p class="sayrhazi-modal-note">Attention : les fichiers modifiés par les autres agents entre-temps seront eux aussi restaurés.</p>';

  sayrhaziModal({
    icon: "↩",
    title: "Restaurer ce message",
    bodyHTML: body,
    cancelLabel: "Annuler",
    confirmLabel: "Restaurer ce message",
    onConfirm: function () { confirmRevert(anchorEl, text, messageId); }
  });
}

// Clic « Restaurer ce message » : le bandeau s'affiche immédiatement avec un
// spinner dans le bloc fichiers ; les diffs outils locaux ne sont conservés
// qu'en secours. L'aperçu serveur (revert+unrevert atomique, sans effet de
// bord) renvoie le diff réel d'OpenCode, qui remplit le bloc dès qu'il répond.
async function confirmRevert(anchorEl, text, messageId) {
  var sid = currentSessionId;
  console.log("[revert] confirmRevert appelé", { sessionId: sid || "(aucune)", messageId: messageId || "(sans id)" });
  if (!sid || !messageId) {
    console.log("[revert] arrêt - session ou message introuvable");
    appendChatMessage("system", "Revert impossible : session ou message introuvable.", nowTime());
    return;
  }
  var filesLocales = localDiffFiles(anchorEl);
  applyRevertVisual(anchorEl, text, messageId, filesLocales, true);

  // Recalcul complet depuis le serveur (aperçu atomique, aucun effet) :
  // couvre les modifications faites par bash/autres outils sans __diff___.
  // L'aperçu a le plus souvent déjà été lancé à l'ouverture de la modal :
  // il ne reste alors qu'à l'attendre.
  try {
    var preview = await getRevertPreview(sid, messageId);
    var diff = String(preview.diff || "");
    if (diff && pendingRevert && pendingRevert.messageId === messageId) {
      var previewFiles = parseRevertDiff(diff);
      var merged = {};
      previewFiles.forEach(function (f) { merged[f.filename] = f; });
      (Array.isArray(pendingRevert.diffFiles) ? pendingRevert.diffFiles : []).forEach(function (f) {
        if (!merged[f.filename]) merged[f.filename] = f;
      });
      pendingRevert.diff = diff;
      pendingRevert.diffFiles = Object.keys(merged).map(function (k) { return merged[k]; });
      writeRevertStash({ sessionId: sid, messageId: messageId, text: pendingRevert.text || "", suppressedIds: pendingRevert.suppressedIds, revertedUsers: pendingRevert.revertedUsers, diff: diff });
    }
    finishRevertFilesLoading();
  } catch (err) {
    console.error("[revert] aperçu impossible", err);
    finishRevertFilesLoading();
  }
}

// Fin du chargement du bloc fichiers : le diff réel d'OpenCode (ou l'estimé
// local en secours) remplace le spinner.
function finishRevertFilesLoading() {
  if (!pendingRevert) return;
  pendingRevert.filesLoading = false;
  updateRevertBanner();
}


// Modal B - « Annuler le revert » : restauration via l'unrevert natif.
function openUnrevertModal() {
  var users = pendingRevert && typeof pendingRevert.revertedUsers === "number"
    ? pendingRevert.revertedUsers : countRevertHiddenUsers();
  var hidden = pendingRevert && Array.isArray(pendingRevert.suppressedIds)
    ? pendingRevert.suppressedIds.length
    : document.querySelectorAll("#chat-conversation .revert-hidden").length;
  var body =
    '<p>Les <strong>' + esc(frenchCount(users, "message", "messages")) + ' reverted</strong> (' +
      esc(frenchCount(hidden, "message")) + ' masqué' + (hidden > 1 ? "s" : "") + ' au total) seront restaurés dans la conversation, et les fichiers reprendront l’état qu’ils avaient <strong>avant le revert</strong>.</p>' +
    '<p class="sayrhazi-modal-note sayrhazi-modal-note--info">Aucune perte n’a eu lieu : les messages reverted n’ont jamais été effacés, ils étaient seulement masqués.</p>';

  sayrhaziModal({
    icon: "↩",
    title: "Annuler le revert",
    bodyHTML: body,
    cancelLabel: "Retour",
    confirmLabel: "Annuler le revert",
    onConfirm: function () { confirmUnrevert(); }
  });
}

// Unrevert natif : restaure l'état d'avant le revert et affiche les messages.
async function confirmUnrevert() {
  var sid = currentSessionId;
  if (!sid) return;
  try {
    var resp = await fetch("/api/chat/session/" + encodeURIComponent(sid) + "/unrevert", {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" }
    });
    var data = await resp.json().catch(function () { return {}; });
    if (!resp.ok || !data.ok) {
      appendChatMessage("system", "Annulation impossible : " + ((data && (data.message || data.error)) || "erreur " + resp.status), nowTime());
      return;
    }
  } catch (err) {
    console.error("Erreur unrevert:", err);
    appendChatMessage("system", "Erreur de connexion au serveur lors de l’annulation.", nowTime());
    return;
  }
  clearRevertVisual(false);
}

// Applique l'état visuel du revert : boundary + postérieurs masqués, la
// saisie est renvoyée au message (comme la TUI), le bandeau s'affiche.
// Appelé juste après la confirmation côté client ; la suite dépend de l'aperçu.
// filesLoading : le bandeau s'affiche tout de suite mais le bloc fichiers
// reste en spinner jusqu'à ce que l'aperçu serveur (diff réel d'OpenCode)
// ait répondu - les estimés locaux ne sont affichés qu'en secours.
function applyRevertVisual(anchorEl, text, messageId, diffFiles, filesLoading) {
  if (!anchorEl) { console.log("[revert] applyRevertVisual - ancre introuvable"); return; }
  var sessionId = currentSessionId;
  console.log("[revert] applyRevertVisual", { messageId, sessionId });
  // Frontière reculée : tenir compte de l'état déjà masqué pour fusionner
  // le compteur et la liste des ids (boundary plus ancien que le précédent).
  var previousIds = pendingRevert && Array.isArray(pendingRevert.suppressedIds) ? pendingRevert.suppressedIds : [];
  var previousUsers = pendingRevert && typeof pendingRevert.revertedUsers === "number" ? pendingRevert.revertedUsers : 0;
  var hiddenUsersBefore = countRevertHiddenUsers();
  var hiddenNotInDom = Math.max(0, previousUsers - hiddenUsersBefore);
  var suppressedIds = previousIds.slice();
  var node = anchorEl;
  while (node) {
    if (node.classList && node.classList.contains("chat-message")) {
      var id = node.dataset && node.dataset.messageId;
      if (!node.classList.contains("revert-hidden")) node.classList.add("revert-hidden");
      if (id && suppressedIds.indexOf(id) === -1) suppressedIds.push(id);
    }
    node = node.nextElementSibling;
  }
  var revertedUsers = countRevertHiddenUsers() + hiddenNotInDom;
  console.log("[revert] masqués", suppressedIds.length, "nœuds -", revertedUsers, "user message(s) reverted");
  console.log("[revert] état appliqué - bandeau affiché, saisie renvoyée au boundary");
  var files = Array.isArray(diffFiles) ? diffFiles.slice() : [];
  pendingRevert = {
    messageId: String(messageId || ""),
    sessionId: sessionId,
    text: String(text || ""),
    suppressedIds: suppressedIds,
    revertedUsers: revertedUsers,
    diff: "",
    diffFiles: files,
    filesLoading: !!filesLoading,
  };
  writeRevertStash({ sessionId: sessionId, messageId: pendingRevert.messageId, text: pendingRevert.text, suppressedIds: pendingRevert.suppressedIds, revertedUsers: pendingRevert.revertedUsers, diff: "" });
  // On ne supprime pas remoteMessages : le bouton ↩ sur un message plus
  // ancien doit pouvoir recompter les diffs des messages masqués (calcul local).
  var input = document.getElementById("chat-message-input");
  if (input) {
    input.value = pendingRevert.text;
    input.style.height = "";
    input.style.height = input.scrollHeight + "px";
    input.focus();
    renderMentionHighlight();
  }
  updateSendButtonAvailability();
  updateRevertBanner();
}

// Retire l'état visuel : commit = les nœuds masqués sont supprimés (la
// suppression effective côté OpenCode a lieu à l'envoi, cleanup natif) ;
// annulation = les nœuds masqués réapparaissent.
// Vide la saisie si elle contient toujours le brouillon renvoyé par le revert.
function clearComposerIfMatches(text) {
  if (!text) return;
  var input = document.getElementById("chat-message-input");
  if (!input) return;
  if (input.value === text) {
    input.value = "";
    input.style.height = "";
    input.style.height = input.scrollHeight + "px";
    updateSendButtonAvailability();
    renderMentionHighlight();
    closeAutocomplete();
  }
}

// Vidage complet de la saisie (bouton « Annuler le revert »).
function clearComposer() {
  var input = document.getElementById("chat-message-input");
  if (!input) return;
  input.value = "";
  input.style.height = "";
  input.style.height = input.scrollHeight + "px";
  renderMentionHighlight();
  closeAutocomplete();
  updateSendButtonAvailability();
}

function clearRevertVisual(commit) {
  clearRevertStash(currentSessionId);
  if (commit) {
    clearRevertHiddenNodes();
    if (pendingRevert && Array.isArray(pendingRevert.suppressedIds)) {
      pendingRevert.suppressedIds.forEach(function (id) { delete remoteMessages[id]; });
    }
  } else {
    var hidden = document.querySelectorAll("#chat-conversation .revert-hidden");
    if (pendingRevert && hidden.length === 0) {
      // Revert venu du rechargement (rien n'a jamais été rendu) : l'unrevert
      // côté serveur est fait, on recharge la discussion complète.
      var sid = currentSessionId;
      pendingRevert = null;
      updateRevertBanner();
      clearComposer();
      if (sid) loadSessionMessages(sid);
      return;
    }
    hidden.forEach(function (el) { el.classList.remove("revert-hidden"); });
    // Le message renvoyé de la saisie est replacé dans l'historique : on vide la saisie.
    clearComposer();
  }
  pendingRevert = null;
  updateRevertBanner();
}

function updateRevertBanner() {
  var banner = document.getElementById("revert-banner");
  var label = document.getElementById("revert-banner-text");
  var filesEl = document.getElementById("revert-banner-files");
  if (!banner || !label) return;
  if (!pendingRevert) {
    banner.classList.remove("visible");
    if (filesEl) { filesEl.innerHTML = ""; filesEl.hidden = true; }
    return;
  }
  var users = pendingRevert && typeof pendingRevert.revertedUsers === "number"
    ? pendingRevert.revertedUsers : 0;
  label.textContent = users <= 1 ? "1 message reverted" : users + " messages reverted";
  renderRevertBannerFiles(filesEl);
  banner.classList.add("visible");
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
    // Un revert peut être (dé)clenché hors de Sayrhazi (TUI OpenCode) : on
    // resynchronise l'état du bandeau en relisant /info ; en cas d'écart, on
    // recharge la discussion (messages filtrés par session.revert).
    // Ne jamais déclencher de rechargement global quand le serveur n'a pas
    // de revert : c'est l'aperçu Sayrhazi (revert+unrevert immédiats) qui
    // génère ces événements et le rechargement ferait clignoter l'écran.
    if (sessionId && sessionId === currentSessionId) {
      fetch("/api/chat/session/" + encodeURIComponent(sessionId) + "/info")
        .then(r => (r.ok ? r.json() : null))
        .catch(() => null)
        .then(d => {
          if (!d || sessionId !== currentSessionId) return;
          const has = !!(d.revert && d.revert.messageID);
          const local = !!pendingRevert;
          if (has && !local) loadSessionMessages(sessionId);
        });
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

  if (data.type === "permission.asked") {
    // La session courante doit accepter/refuser la permission de l'agent.
    if (sessionId && sessionId === currentSessionId) {
      openPermissionModal(props);
    }
    return;
  }

  if (data.type === "question.asked") {
    // L'agent attend une ou plusieurs réponses de l'utilisateur.
    if (sessionId && sessionId === currentSessionId) {
      openQuestionBanner(props);
    }
    return;
  }

  if (data.type === "permission.replied" || data.type === "question.replied" || data.type === "question.rejected") {
    const rid = props && (props.requestID || props.id);
    if (rid && pendingPermission && pendingPermission.request && pendingPermission.request.id === rid) {
      // La réponse est validée : la modal se ferme.
      hidePermissionModal();
    }
    if (rid && pendingQuestion && pendingQuestion.requestID === rid) {
      pendingQuestion = null;
      renderQuestionBanner();
    }
    return;
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

// === Permission / Question : demandes de l'agent (modal et bandeau)
// ===
var pendingPermission = null;
var pendingQuestion = null;

function permissionKind(req) {
  var meta = (req && req.metadata) || {};
  var m = function (k) { return typeof meta[k] === "string" ? meta[k] : ""; };
  var t = function (s, n) { n = n || 200; s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
  var p = req && req.permission;
  if (p === "edit") return { icon: "→", title: "Edit " + t(m("filepath") || "(fichier)") };
  if (p === "read") return { icon: "→", title: "Read " + t(m("filePath") || m("filepath")) };
  if (p === "list") return { icon: "→", title: "List " + t(m("path") || m("filepath")) };
  if (p === "glob") return { icon: "✱", title: "Glob " + t(m("pattern")) };
  if (p === "grep") return { icon: "✱", title: "Grep " + t(m("pattern")) };
  if (p === "bash") return { icon: "#", title: "Shell command" };
  if (p === "webfetch") return { icon: "%", title: "WebFetch " + t(m("url")) };
  if (p === "websearch") return { icon: "◈", title: "WebSearch " + t(m("query")) };
  if (p === "external_directory") return { icon: "←", title: "Accès répertoire externe" };
  if (p === "task") return { icon: "#", title: "Task " + t(m("subagent_type")) };
  if (p === "doom_loop") return { icon: "⟳", title: "Poursuivre après échecs répétés" };
  return { icon: "⚙", title: "Call tool " + (p || "?") };
}

function permissionChoiceBodyAndFooter(request) {
  var kind = permissionKind(request);
  var html = '<div class="perm-head"><span class="perm-icon">' + esc(kind.icon) + '</span>'
    + '<span class="perm-title">' + esc(kind.title) + '</span></div>';
  if (Array.isArray(request.patterns) && request.patterns.length) {
    html += '<div class="perm-section-title">Patterns</div>';
    html += '<ul class="perm-patterns">';
    request.patterns.forEach(function (p) { html += '<li>' + esc(p) + '</li>'; });
    html += '</ul>';
  }
  var footer =
    '<button type="button" class="sayrhazi-modal-btn sayrhazi-modal-btn-danger sayrhazi-modal-btn-wide" data-perm-action="reject">Rejeter</button>' +
    '<button type="button" class="sayrhazi-modal-btn sayrhazi-modal-btn-ghost" data-perm-action="always">Autoriser toujours</button>' +
    '<button type="button" class="sayrhazi-modal-btn sayrhazi-modal-btn-accent" data-perm-action="once">Autoriser une fois</button>';
  return { body: html, footer: footer };
}

function openPermissionModal(request) {
  if (!request || !request.id) return;
  if (pendingPermission && pendingPermission.request && pendingPermission.request.id === request.id) return;
  pendingPermission = null;
  var choice = permissionChoiceBodyAndFooter(request);
  pendingPermission = { request: request };
  sayrhaziModal({
    icon: "△",
    title: "Permission required",
    bodyHTML: choice.body,
    footerHTML: choice.footer,
  });
  wirePermissionModal();
}

function hidePermissionModal() {
  pendingPermission = null;
  var backdrop = document.getElementById("sayrhazi-modal-backdrop");
  if (backdrop) backdrop.remove();
}

function wirePermissionModal() {
  var backdrop = document.getElementById("sayrhazi-modal-backdrop");
  if (!backdrop) return;
  backdrop.addEventListener("click", function (e) {
    var t = e.target && e.target.closest ? e.target.closest("[data-perm-action]") : null;
    if (!t) return;
    var action = t.getAttribute("data-perm-action");
    if (action === "once") respondPermission("once", "");
    else if (action === "always") renderPermissionAlwaysStep();
    else if (action === "reject") renderPermissionRejectStep();
    else if (action === "back") {
      var choice = permissionChoiceBodyAndFooter(pendingPermission && pendingPermission.request);
      swapModalBodyAndFooter(choice.body, choice.footer);
    }
    else if (action === "confirm-always") respondPermission("always", "");
    else if (action === "confirm-reject") {
      var ta = document.getElementById("perm-reject-message");
      respondPermission("reject", ta ? ta.value.trim() : "");
    }
  });
}

function swapModalBodyAndFooter(bodyHTML, footerHTML) {
  var backdrop = document.getElementById("sayrhazi-modal-backdrop");
  if (!backdrop) return;
  var body = backdrop.querySelector(".sayrhazi-modal-body");
  var foot = backdrop.querySelector(".sayrhazi-modal-footer");
  if (body) body.innerHTML = bodyHTML;
  if (foot) foot.innerHTML = footerHTML;
}

function renderPermissionAlwaysStep() {
  if (!pendingPermission) return;
  var req = pendingPermission.request;
  var always = Array.isArray(req.always) ? req.always.filter(Boolean) : [];
  var html = '<div class="perm-sub-title"><span class="perm-icon">△</span><span>Autoriser toujours</span></div>'
    + '<p class="perm-sub-note">Sera autorisé jusqu’au redémarrage d’OpenCode :</p>'
    + '<ul class="perm-patterns perm-patterns--always">';
  if (always.length) {
    always.forEach(function (p) { html += '<li>' + esc(p) + '</li>'; });
  } else {
    html += '<li><i>(aucun pattern)</i></li>';
  }
  html += '</ul>';
  swapModalBodyAndFooter(html,
    '<button type="button" class="sayrhazi-modal-btn sayrhazi-modal-btn-ghost" data-perm-action="back">Annuler</button>'
    + '<button type="button" class="sayrhazi-modal-btn sayrhazi-modal-btn-accent" data-perm-action="confirm-always">Confirmer</button>');
}

function renderPermissionRejectStep() {
  if (!pendingPermission) return;
  var html = '<div class="perm-sub-title"><span class="perm-icon">✕</span><span>Rejeter l’action</span></div>'
    + '<p class="perm-sub-note">L’agent lira ce message pour se corriger :</p>'
    + '<textarea id="perm-reject-message" class="perm-reject-message" rows="4" placeholder="Pourquoi refuser ? (optionnel)" spellcheck="false" autofocus></textarea>';
  swapModalBodyAndFooter(html,
    '<button type="button" class="sayrhazi-modal-btn sayrhazi-modal-btn-ghost" data-perm-action="back">Annuler</button>'
    + '<button type="button" class="sayrhazi-modal-btn sayrhazi-modal-btn-danger" data-perm-action="confirm-reject">Rejeter</button>');
  var ta = document.getElementById("perm-reject-message");
  if (ta) ta.focus();
}

function respondPermission(reply, message) {
  if (!pendingPermission || !currentSessionId) return;
  var rid = pendingPermission.request && pendingPermission.request.id;
  if (!rid) return;
  fetch("/api/chat/session/" + encodeURIComponent(currentSessionId) + "/permission/" + encodeURIComponent(rid) + "/reply", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({ reply: reply, message: message || undefined })
  }).then(function () {
    hidePermissionModal();
    // Recharger l'info de la session (fire des throws si absent — silencieux)
    fetch("/api/chat/session/" + encodeURIComponent(currentSessionId) + "/info").catch(function () {});
  }).catch(function (err) { console.error("[permission] erreur", err); hidePermissionModal(); });
}


// Rafraîchit silencieusement les demandes en attente (permission/question) pour
// la discussion courante, afin qu'après un reload les modals/bandeaux reviennent.
async function refreshPendingQuiet() {
  if (!currentSessionId) return;
  try {
    var resp = await fetch("/api/chat/session/" + encodeURIComponent(currentSessionId) + "/pending");
    var data = await resp.json().catch(function () { return {}; });
    if (!data || !data.ok) return;
    var perms = Array.isArray(data.permission) ? data.permission : [];
    var ques = Array.isArray(data.question) ? data.question : [];
    if (perms.length && !pendingPermission) openPermissionModal(perms[0]);
    if (ques.length && !pendingQuestion) openQuestionBanner(ques[0]);
  } catch (err) {
    console.error("[pending] chargement silencieux impossible", err);
  }
}

async function loadSessionMessages(sessionId, sessionTitle) {
  // Changer de discussion annule l'attente de la réponse en cours.
  stopAgentReplyWatch();
  disconnectSessionStream();
  // La saisie appartient à la discussion : ouvrir une autre discussion vide
  // la zone de texte (un brouillon renvoyé par un revert ailleurs ne suit
  // jamais). Un simple rechargement de la même discussion conserve la saisie.
  // Le bandeau d'erreur appartient à la discussion ouverte : on l'efface
  // avant le rechargement ; le rendu final le réaffiche le cas échéant via
  // info.error du dernier message agent.
  hideAgentErrorBanner();
  const switching = sessionId !== currentSessionId;
  remoteMessages = {};
  currentSessionId = sessionId;
  pendingRevert = null;
  updateRevertBanner();
  // L'indicateur « agent travaille » appartient à la discussion en cours :
  // il suit l'utilisateur seulement quand il y travaille.
  const workingStrip = document.getElementById("chat-agent-working");
  if (workingStrip) {
    const workingSession = workingStrip.dataset ? workingStrip.dataset.session : null;
    workingStrip.hidden = !workingSession || workingSession !== currentSessionId;
  }
  if (switching) {
    const switchInput = document.getElementById("chat-message-input");
    if (switchInput) {
      switchInput.value = "";
      switchInput.style.height = "";
      switchInput.style.height = switchInput.scrollHeight + "px";
      renderMentionHighlight();
      closeAutocomplete();
    }
    updateSendButtonAvailability();
  }
  // Rouvrir (ou ouvrir) une discussion : on relit en bas (dernier message).
  chatStickToBottom = true;
  const conv = document.getElementById("chat-conversation");
  if (!conv) return;
  console.log("[reload] loadSessionMessages déclenché - stack:", new Error("trace").stack);
  try {
    const infoPromise = fetch("/api/chat/session/" + encodeURIComponent(sessionId) + "/info")
      .then(r => (r.ok ? r.json() : null))
      .catch(() => null);
    const response = await fetch("/api/chat/session/" + encodeURIComponent(sessionId) + "/messages");
    if (!response.ok) throw new Error("Erreur HTTP " + response.status);
    const data = await response.json();
    const infoData = await infoPromise;
    const messages = (data.messages || []).slice();
    // Garantir l'ordre chronologique selon la sequence retournee par le backend
    messages.sort((a, b) => {
      const sa = Number(a.seq);
      const sb = Number(b.seq);
      if (Number.isFinite(sa) && Number.isFinite(sb)) return sa - sb;
      return 0;
    });
    conv.innerHTML = "";

    // Revert en cours (session.revert != null) : seuls les messages
    // antérieurs au boundary sont affichés ; le boundary et les suivants
    // sont masqués côté GUI (ils restent dans SQLite jusqu'à l'envoi).
    const revertInfo = infoData && infoData.revert && infoData.revert.messageID ? infoData.revert : null;
    let boundaryIndex = -1;
    let stashPending = null;
    if (revertInfo) {
      for (let i = 0; i < messages.length; i++) {
        if (messages[i] && messages[i].message_id === revertInfo.messageID) { boundaryIndex = i; break; }
      }
    } else {
      // Le revert est posé côté OpenCode uniquement à l'envoi ; un revert
      // en cours local est restauré depuis le stash sessionStorage.
      const stash = readRevertStash(sessionId);
      if (stash && stash.sessionId === sessionId && stash.messageId) {
        for (let i = 0; i < messages.length; i++) {
          if (messages[i] && messages[i].message_id === stash.messageId) { boundaryIndex = i; stashPending = stash; break; }
        }
      }
    }
    const suppressed = [];
    let revertedUsers = 0;
    if (boundaryIndex >= 0) {
      for (let i = boundaryIndex; i < messages.length; i++) {
        const m = messages[i];
        if (m && m.message_id) suppressed.push(m.message_id);
        if (m && m.role === "user") revertedUsers += 1;
      }
    }
    const toRender = boundaryIndex >= 0 ? messages.slice(0, boundaryIndex) : messages;

    if (messages.length === 0) {
      conv.innerHTML = '<div class="chat-empty">Aucun message dans cette session.</div>';
    } else if (toRender.length === 0 && (revertInfo || stashPending)) {
      conv.innerHTML = '<div class="chat-empty">La conversation sera renvoyée à son point de départ - écrivez un nouveau message pour valider le revert.</div>';
    } else {
      // Garder les messages en mémoire : le flux SSE /api/chat/stream les
      // remplace en place sans recharger toute la discussion.
      remoteMessages = {};
      toRender.forEach(m => { if (m && m.message_id) remoteMessages[m.message_id] = m; });
      toRender.forEach(m => appendChatMessage(m.role, m.text, m.time, m.metrics_only, m.is_reasoning, m.message_id, m.is_tool, m.tool, m.file, m.old_content, m.new_content, m.diff_content, m.tool_content, m.duration, m.reasoning, m.tools, m.items, m.model, m.series_end, m.model_provider));
      // Redérouler les erreurs intermediaires (RetryPart) n'est pas utile
      // après rechargement ; seule l'erreur finale du tour (info.error) ré-affiche
      // le bandeau rouge.
      for (let i = toRender.length - 1; i >= 0; i--) {
        var mm = toRender[i];
        if (mm && mm.role === "agent") {
          if (mm.error) showAgentFinalBanner(mm.error);
          break;
        }
      }
    }
    if (boundaryIndex >= 0) {
      // La saisie est renvoyée au message du boundary (comme la TUI) ; si
      // l'utilisateur avait commencé à l'éditer avant un rechargement, le
      // stash sessionStorage prime.
      const boundaryMsg = messages[boundaryIndex];
      const stash = stashPending || readRevertStash(sessionId);
      const boundaryId = revertInfo ? revertInfo.messageID : stash.messageId;
      const draft = stash && stash.sessionId === sessionId && stash.messageId === boundaryId && typeof stash.text === "string"
        ? stash.text
        : (boundaryMsg && boundaryMsg.text) || "";
      const diff = revertInfo ? String(revertInfo.diff || "") : String(stash.diff || "");
      // Fichiers concernés : même sans texte diff complet, on les extrait des
      // outils edit/write/patch des messages chargés - rendu instantané.
      let diffFiles = parseRevertDiff(diff);
      if (!diffFiles.length) diffFiles = diffFilesFromMessages(messages, boundaryIndex);
      pendingRevert = {
        messageId: boundaryId,
        sessionId: sessionId,
        text: draft,
        suppressedIds: suppressed,
        revertedUsers: revertedUsers,
        diff: diff,
        diffFiles: diffFiles,
      };
      const input = document.getElementById("chat-message-input");
      if (input) {
        input.value = draft;
        input.style.height = "";
        input.style.height = input.scrollHeight + "px";
        renderMentionHighlight();
      }
      updateSendButtonAvailability();
    } else {
      pendingRevert = null;
      // Stash devenu obsolète (commit fait ailleurs depuis) : nettoyage -
      // uniquement celui de CETTE discussion ; une autre discussion en revert
      // en cours ne perd pas son stash.
      clearRevertStash(sessionId);
    }
    updateRevertBanner();
    updateChatHeaderTitle();
    refreshPendingQuiet();
    // Si l'agent répond encore (dernier message assistant sans `completed`), on
    // rétablit l'indicateur et le bouton ■. L'événement session.idle stoppera
    // tout automatiquement.
    const latestAgent = (function () {
      for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i] && messages[i].role === "agent") return messages[i];
      }
      return null;
    })();
    if (latestAgent && !latestAgent.completed) {
      setSendButtonState("stop");
      // Au rechargement : l'agent travaille encore - le bouton stop
      // accompagne depuis l'arrivée SSE.
      showAgentWorkingIndicator(sessionId);
      connectSessionStream(sessionId);
      // Le SSE ne renseigne pas sur le type de phase interne ; on laisse le
      // bouton grisé jusqu'à réception d'événements agent réels.
      var chatBtn = document.getElementById("chat-send-btn");
      if (chatBtn) chatBtn.disabled = true;
    }
    // Foot Interrompu sur le dernier bloc agent si session aborted
    injectInterruptionFooter(sessionId);
    document.querySelectorAll(".chat-session-item").forEach(el => {
      el.classList.toggle("active", el.dataset.sessionId === sessionId);
    });
    scrollChatToBottom();
    // Le flux SSE /api/chat/stream est ouvert lors d'un envoi (sendChatMessage),
    // pas ici (sauf agent encore en cours de réponse).
  } catch (err) {
    console.error("Erreur chargement messages session:", err);
    pendingRevert = null;
    updateRevertBanner();
    updateChatHeaderTitle();
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
      pendingRevert = null;
      composerAttachments = [];
      renderComposerAttachments();
      const newSessionInput = document.getElementById("chat-message-input");
      if (newSessionInput) { newSessionInput.value = ""; newSessionInput.style.height = ""; }
      updateSendButtonAvailability();
      updateRevertBanner();
      updateChatHeaderTitle();
      await fetchChatSessions(selectedChatAgent);
      updateChatHeaderTitle();
      const conv = document.getElementById("chat-conversation");
      if (conv) {
        conv.innerHTML = '<div class="chat-empty">Nouvelle session prête. Écrivez votre premier message.</div>';
      }
      const input = document.getElementById("chat-message-input");
      if (input) input.focus();
    } else {
      showChatErrorModal("Création impossible", "Impossible de créer une session : " + (data.message || "inconnue"));
    }
  } catch (err) {
    console.error("Erreur création session:", err);
    showChatErrorModal("Création impossible", "Erreur de connexion au serveur.");
  }
}

async function abortAgent() {
  if (!currentSessionId) return;
  // On coupe localement l'indicateur et le flux SSE tout de suite, sans
  // attendre l'événement session.error éventuel de l'SSE.
  disconnectSessionStream();
  stopAgentReplyWatch();
  hideAgentWorkingIndicator();
  hideAgentErrorBanner();
  sessionStreaming[currentSessionId] = false;
  setSendButtonState("send");
  try {
    const resp = await fetch("/api/chat/session/" + encodeURIComponent(currentSessionId) + "/abort", {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify({})
    });
    const data = await resp.json().catch(function () { return {}; });
    if (!resp.ok || !data.ok) {
      const msg = (data && (data.message || data.error)) || "erreur " + resp.status;
      console.warn("abort refusée:", msg);
      return;
    }
    // Foot ▣ {agent} · {model} · Interrompu affiché au prochain render
    try { sessionStorage.setItem("sayrhazi.abortedAt." + currentSessionId, String(Date.now())); } catch (e) { /* ignore */ }
    // On recharge pour finaliser la vue.
    loadSessionMessages(currentSessionId);
  } catch (err) {
    console.error("abort refusée:", err);
  }
}

// Ajoute « Interrompu » comme pied au dernier bloc agent s'il n'en a pas.
// Lit le marqueur sessionStorage posé par abortAgent().
function injectInterruptionFooter(sessionId) {
  var stamp = 0;
  try { stamp = parseInt(sessionStorage.getItem("sayrhazi.abortedAt." + sessionId) || "0", 10); } catch (e) { return; }
  if (!stamp) return;
  var agentLabel = (selectedChatAgent ? getAgentDisplayName(selectedChatAgent) : "Agent");
  var modelLabel = "";
  var cm = composerModel();
  if (cm) {
    modelLabel = cm.name + (cm.providerName ? " · " + cm.providerName : "");
  }
  var conv = document.getElementById("chat-conversation");
  if (!conv) return;
  var blocks = conv.querySelectorAll(".chat-message.agent");
  var last = blocks && blocks.length ? blocks[blocks.length - 1] : null;
  var text = "▣ " + agentLabel + " · " + (modelLabel ? modelLabel + " · " : "") + "Interrompu";
  if (last) {
    var foot = last.querySelector(".agent-block-foot");
    if (foot) {
      foot.innerHTML = text;
    } else {
      var wrapper = document.createElement("div");
      wrapper.innerHTML = '<div class="agent-block-foot">' + text + '</div>';
      last.appendChild(wrapper.firstElementChild);
    }
    return;
  }
  // Aucun bloc agent : on ajoute un bloc minimal avec le pied seul.
  var div = document.createElement("div");
  div.className = "chat-message agent";
  div.innerHTML = '<div class="agent-block-foot">' + text + '</div>';
  conv.appendChild(div);
  scrollChatToBottom();
}

async function sendChatMessage() {
  const input = document.getElementById("chat-message-input");
  const text = (input.value || "").trim();
  const hasAttachments = Array.isArray(composerAttachments) && composerAttachments.length > 0;
  if ((!text && !hasAttachments) || !selectedChatAgent) return;

  // Le bandeau d'erreur définitive disparaît dès qu'on renvoie un message.
  hideAgentErrorBanner();

  // Si aucune session n'est ouverte, en créer une d'abord
  if (!currentSessionId) {
    await startNewSession();
    if (!currentSessionId) return;
  }

  // Désactiver l'input pendant l'attente
  input.disabled = true;
  const btn = document.getElementById("chat-send-btn");
  if (btn) btn.disabled = true;

  // Afficher le message utilisateur immédiatement (les items joints via « + »
  // sont insérés tels quels dans la bulle, comme les mentions via « @ », pour
  // qu'ils persistent dans le chat et que l'agent puisse les lire).
  var joinedMentions = (Array.isArray(composerAttachments) ? composerAttachments : [])
    .filter(function (a) { return !isImageAttachment(a); })
    .map(function (a) { return "@" + (a.filename || "fichier"); });
  var liveText = text;
  if (joinedMentions.length) {
    liveText = (liveText ? liveText + " " : "") + joinedMentions.join(" ");
  }
  var liveItems = (Array.isArray(composerAttachments) ? composerAttachments : []).map(function (a) {
    return { kind: "tool", tool: "FILE", file: a.filename || "fichier", url: a.url, mime: a.mime, size: a.size, directory: !!a.directory };
  });
  appendChatMessage("user", liveText, new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), false, false, null, false, null, null, null, null, null, null, null, null, null, liveItems);
  input.value = "";
  input.style.height = "";
  updateSendButtonAvailability();
  renderMentionHighlight();
  closeAutocomplete();
  scrollChatToBottom();

  // Si un revert est en attente, il n'est posé côté OpenCode qu'à cet instant :
  // la restauration des fichiers et la suppression des messages reverted se
  // produisent dans le même envoi (cleanup natif au moment du prompt).
  if (pendingRevert && pendingRevert.messageId) {
    try {
      var rresp = await fetch("/api/chat/session/" + encodeURIComponent(currentSessionId) + "/revert", {
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify({ messageID: pendingRevert.messageId })
      });
      var rdata = await rresp.json().catch(function () { return {}; });
      if (!rresp.ok || !rdata.ok) {
        console.error("[revert] envoi du revert refusé par OpenCode", rresp.status, rdata);
        appendChatMessage("system", "Revert impossible : " + ((rdata && (rdata.message || rdata.error)) || "erreur " + rresp.status), nowTime());
        setSendButtonState("send");
        input.disabled = false;
        if (btn) btn.disabled = false;
        return;
      }
    } catch (err) {
      console.error("[revert] erreur lors de l'envoi du revert", err);
      appendChatMessage("system", "Erreur de connexion au serveur lors du revert.", nowTime());
      setSendButtonState("send");
      input.disabled = false;
      if (btn) btn.disabled = false;
      return;
    }
  }

  try {
    var modelForSend = composerModelForSend();
    var mentionAttachments = buildMentionAttachments(text);
    // Mentions d'un même chemin dupliqué dédupliquées avant l'envoi.
    var seenAttach = {};
    mentionAttachments = mentionAttachments.filter(function (a) {
      if (!a || !a.path) return false;
      if (seenAttach[a.path]) return false;
      seenAttach[a.path] = true;
      return true;
    });
    // Pièces jointes uploadées via le bouton « + » (Fichiers / Répertoires) —
    // mentionne simplement par basename pour l'audio/image/PDF.
    var uploadedAttachments = composerAttachments.map(function (a) {
      var mention = "@" + (a.filename || "fichier");
      return { url: a.url, filename: a.filename, mime: a.mime, mention: mention };
    });
    composerAttachments = [];
    renderComposerAttachments();
    var payload = { message: liveText, agent: selectedChatAgent };
    if (modelForSend) {
      payload.model = { providerID: modelForSend.providerID, modelID: modelForSend.modelID };
      if (modelForSend.variant) payload.variant = modelForSend.variant;
    }
    var allAttachments = mentionAttachments.concat(uploadedAttachments);
    if (allAttachments.length) payload.attachments = allAttachments;
    const response = await fetch("/api/chat/session/" + encodeURIComponent(currentSessionId) + "/prompt_async", {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify(payload)
    });
    const data = await response.json().catch(() => ({ ok: false, message: "Réponse invalide" }));

    if (response.ok && data.ok) {
      setSendButtonState("send");
      if (btn) btn.disabled = true;
      // Un envoi avec un revert en attente valide définitivement le revert
      // (cleanup côté OpenCode dans prompt_async) : la GUI supprime alors
      // les nœuds masqués au lieu de les réafficher.
      if (pendingRevert) clearRevertVisual(true);
      try { sessionStorage.removeItem("sayrhazi.abortedAt." + currentSessionId); } catch (e) { /* ignore */ }
      const indicator = showAgentWorkingIndicator(currentSessionId);
      // Fluid SSE : met à jour les parts dès leur réception pendant que
      // l'agent répond. Le rechargement complet se fait à l'arrivée de
      // l'événement session.idle (pied natif + alignement).
      connectSessionStream(currentSessionId);
    } else {
      setSendButtonState("send");
      var attachCode = data && data.code ? String(data.code) : "";
      if (attachCode.indexOf("ATTACH_") === 0) {
        showMentionWarning((data && data.message) || "pièce jointe refusée");
      } else {
        appendChatMessage("system", "Erreur : " + ((data && (data.message || data.error)) || "inconnue"), new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
      }
    }
  } catch (err) {
    setSendButtonState("send");
    console.error("Erreur envoi chat:", err);
    appendChatMessage("system", "Erreur de connexion au serveur.", new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
  } finally {
    input.disabled = false;
    updateSendButtonAvailability();
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
  content.innerHTML = '<div style="white-space:pre-wrap; font-family:Fira Code,ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; color:#ef4444;   margin-top:0.4rem;">' + esc(errorText) + '</div>';

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
  file = toRelativePath(file || "");
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
  file = toRelativePath(file || "");
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
        '<span class="tool-diff-num"></span><span class="tool-diff-sign"> </span><span class="tool-diff-line-content">-</span>' +
        '</div></td></tr>';
    }
    return '<div class="tool-diff-panel">' +
      '<div class="tool-header">' +
      '<span class="tool-badge" style="background:' + color + ';">' + esc(tool) + '</span>' +
      '<span class="tool-file" style="color:' + color + ';">' + esc(file) + '</span>' +
      '</div>' +
      '<div class="tool-diff-content" style="font-family:Fira Code,ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">' +
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
      diffCellHtml("left empty", "", " ", "-") +
      diffCellHtml("right empty", "", " ", "-") +
      '</tr>';
  }
  return '<div class="tool-diff-panel">' +
    '<div class="tool-header">' +
    '<span class="tool-badge" style="background:' + color + ';">' + esc(tool) + '</span>' +
    '<span class="tool-file" style="color:' + color + ';">' + esc(file) + '</span>' +
    '</div>' +
    '<div class="tool-diff-content" style="font-family:Fira Code,ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">' +
    '<table class="tool-diff-table">' + trHtml + '</table>' +
    '</div>' +
    '</div>';
}

// Diff avec jsdiff (même bibliothèque qu'OpenCode) - split view colonnes左��.
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
    // Format jsdiff : {value, added, removed} - tolerance aux autres formats
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
  file = toRelativePath(file || "");
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
      html += '<div class="split-line write-line" style="font-family:Fira Code,ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;display:flex;">' +
        '<span class="split-num">' + esc(String(num)) + '</span>' +
        '<span class="split-sign" style="width:1.1rem;color:var(--muted);text-align:right;padding-right:0.5rem;flex-shrink:0;"> </span>' +
        '<span class="split-content" style="flex:1;min-width:0;white-space:pre-wrap;overflow-wrap:anywhere;">' + (isMdFile ? colorizeMarkdownInline(line) : esc(line)) + '</span>' +
        '</div>';
    });
    if (!html) {
      html = '<div class="split-line write-line" style="font-family:Fira Code,ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">-</div>';
    }
    return '<div class="tool-split-diff">' +
      '<div class="split-header">' +
      '<span class="tool-badge" style="background:' + color + ';">' + esc(tool) + '</span>' +
      '<span class="split-file" style="color:' + color + ';">' + esc(file || "") + '</span>' +
      '</div>' +
      '<div class="split-lines" style="font-family:Fira Code,ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">' + html + '</div>' +
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
    '<div class="tool-diff-content" style="font-family:Fira Code,ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;">' +
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
    // Chemins toujours affichés avec des '/' homogènes (comme la TUI), même sous Windows.
    var rf = String(file).trim().replace(/\\/g, "/");
    if (rf.indexOf("./") === 0) rf = rf.slice(2);
    var rootPath = (typeof window !== "undefined" && window.__sayrhaziRoot)
      ? String(window.__sayrhaziRoot).replace(/[\\/]+/g, "/").replace(/\/+$/, "")
      : "";
    if (!rootPath) return rf;
    if (rf.toLowerCase() === rootPath.toLowerCase()) return ".";
    if (rf.toLowerCase().indexOf(rootPath.toLowerCase() + "/") === 0) {
      return rf.slice(rootPath.length + 1);
    }
    return rf;
  } catch (e) {
    return String(file);
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
      '<div style="white-space:pre-wrap; font-family:Fira Code,ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; color:#ef4444; padding:0.6rem; background:#1a1a1a; border:1px solid #333; border-radius:0.35rem; margin-top:0.4rem;">' + esc(errorMsg) + '</div>' +
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
// raisonnements, dépliage erreur des outils) - utilisé au chargement et en
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
function renderAgentBlock(role, items, model, duration, seriesEnd, modelProvider) {
  var html = "";
  var list = items && items.length ? items : [];
  if (!list.length) return html;

  list.forEach(function (item) {
    html += renderAgentItemHtml(item, role);
  });

  // Pied de bulle : modele + fournisseur + duree totale (seulement en fin de reponse agent).
  // Rendu natif OpenCode : "▣ {agent} · {model} · {duration}"
  var foot = [];
  if (seriesEnd) {
    var agentLabel = (selectedChatAgent ? getAgentDisplayName(selectedChatAgent) : "Agent");
    if (model) foot.push(esc(model) + (modelProvider ? ' · ' + esc(modelProvider) : ''));
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

function appendChatMessage(role, text, time, metricsOnly, isReasoning, messageId, isTool, tool, file, oldContent, newContent, diffContent, toolContent, duration, reasoning, tools, items, model, seriesEnd, modelProvider) {
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
    div.innerHTML = renderAgentBlock(role, items, model, duration, seriesEnd, modelProvider);
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
  if (messageId) div.dataset.messageId = messageId;

  // Images jointes via « + » : miniature au-dessus de la bulle utilisateur.
  if (role === "user" && Array.isArray(items)) {
    var imageItems = items.filter(function (it) {
      return it && isImageAttachment(it);
    });
    if (imageItems.length) {
      var imgWrap = document.createElement("div");
      imgWrap.className = "chat-user-images";
      imgWrap.innerHTML = imageItems.map(function (a) {
        return '<img class="chat-user-image" src="' + esc(a.url || "") + '" alt="' + esc(a.file || a.filename || "image") + '" />';
      }).join("");
      div.appendChild(imgWrap);
    }
  }

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";

  const body = document.createElement("div");
  body.className = "chat-bubble-body";
  body.innerHTML = renderChatMarkdown(text, role);
  if (role === "user") {
    body.innerHTML = colorizeUserMentions(body.innerHTML);
  }
  bubble.appendChild(body);

  applySyntaxHighlight(body);

  const timeEl = document.createElement("span");
  timeEl.className = "chat-time";

  if (role === "user") {
    // Bulle user : l'heure sort de la bulle et passe sous celle-ci (comme le
    // pied modele/duree de l'agent). Le chevron de depliage reste dans la bulle.
    // Le bouton ↩ ouvre le revert (modal A) ; tant qu'aucun envoi ne suit, la
    // suppression definitive n'a pas lieu (API OpenCode : cleanup à l'envoi).
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
    // Bulles des items joints via « + » : même forme que dans le composer,
    // placées en bas de la bulle utilisateur.
    if (Array.isArray(items)) {
      var userAttachItems = items.filter(function (it) {
        return it && (it.kind === "tool" &&
          (String(it.tool || "").toUpperCase() === "FILE" || it.url || it.mime)) &&
          !isImageAttachment(it);
      });
      if (userAttachItems.length) {
        var attachWrap = document.createElement("div");
        attachWrap.className = "chat-user-attachments";
        attachWrap.innerHTML = userAttachItems.map(function (a) {
          var folderIcon = a.directory ? "📁 " : "";
          var label = folderIcon + esc(a.file || a.filename || "fichier") +
            (a.size ? ' <span class="chat-attach-chip-size">' + esc(formatFileSize(a.size)) + "</span>" : "");
          return '<div class="chat-attach-chip"><span class="chat-attach-chip-name">' + label + '</span></div>';
        }).join("");
        bubble.appendChild(attachWrap);
      }
    }
    bubble.appendChild(foldToggle);
    bubble.classList.add("chat-bubble-foldable");

    timeEl.className = "chat-time chat-time-outside chat-user-bar";
    timeEl.setAttribute("aria-label", "Actions du message");
    timeEl.innerHTML =
      '<button type="button" class="chat-icon-btn" data-action="revert" title="Revert - revenir à ce message" aria-label="Revert">↩</button>' +
      '<span class="chat-icon-separator" aria-hidden="true">•</span>' +
      '<button type="button" class="chat-icon-btn" data-action="copy" title="Copier le message" aria-label="Copier le message">copy</button>' +
      '<span class="chat-icon-separator" aria-hidden="true">•</span>' +
      '<span class="chat-time-text">' + esc(time + (duration ? " · " + duration : "")) + '</span>';
    timeEl.querySelector('[data-action="revert"]').addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      let id = messageId || "";
      if (!id) {
        console.log("[revert] id manquant sur la bulle live, résolution serveur…");
        await ensureLiveUserMessageId(div);
        id = div.dataset.messageId || "";
      }
      console.log("[revert] clic ↩ message", id || "(sans id)", "dans la session", currentSessionId);
      if (!id) {
        appendChatMessage("system", "Revert impossible : impossible de résoudre l'identifiant du message. Rechargez la page.", nowTime());
        return;
      }
      openRevertModal(div, text || "", id);
    });
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
function chatAttachStickListener() {
  const container = document.getElementById("chat-conversation");
  if (!container || container.dataset.stickReady) return;
  container.dataset.stickReady = "1";
  // Un seul indicateur : la position réelle. Tout scroll de l'utilisateur
  // (y compris le nôtre, toujours au fond) réaligne la sentinelle - pas de
  // drapeau intermédiaire qui raterait un événement.
  container.addEventListener("scroll", function () {
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
    container.scrollTop = container.scrollHeight;
  }
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
let stopArmed = false;
let stopArmedTimer = null;
const STOP_ARM_TIMEOUT_MS = 5000;

function disarmStop() {
  stopArmed = false;
  if (stopArmedTimer) { clearTimeout(stopArmedTimer); stopArmedTimer = null; }
  const btn = document.getElementById("chat-send-btn");
  if (!btn) return;
  if (btn.classList.contains("stop-armed")) {
    btn.classList.remove("stop-armed");
    btn.title = "Réponse de l'agent en cours...";
    btn.setAttribute("aria-label", "Réponse de l'agent en cours");
  }
}

function armStop() {
  const btn = document.getElementById("chat-send-btn");
  if (!btn) return;
  stopArmed = true;
  btn.classList.add("stop-armed");
  btn.title = "Cliquer encore pour interrompre";
  btn.setAttribute("aria-label", "Cliquer encore pour interrompre l'agent");
  if (stopArmedTimer) clearTimeout(stopArmedTimer);
  stopArmedTimer = setTimeout(function () {
    disarmStop();
  }, STOP_ARM_TIMEOUT_MS);
}

// "stop" pour les envois au clavier.
function setSendButtonState(mode) {
  disarmStop();
  const btn = document.getElementById("chat-send-btn");
  if (!btn) return;
  if (mode === "stop") {
    btn.classList.add("stop");
    btn.textContent = "\u25A0";
    btn.title = "Réponse de l'agent en cours...";
    btn.setAttribute("aria-label", "Réponse de l'agent en cours");
  } else {
    btn.classList.remove("stop");
    btn.textContent = "\u2191";
    btn.title = "Envoyer";
    btn.setAttribute("aria-label", "Envoyer");
    btn.disabled = false;
  }
}

function toggleAttachPanel() {
  const btn = document.getElementById("chat-attach-btn");
  const panel = document.getElementById("chat-attach-panel");
  if (!btn || !panel) return;
  const show = panel.style.display !== "block";
  panel.style.display = show ? "block" : "none";
}

// ---------------------------------------------------------------------------
// Pièces jointes uploadées via le bouton « + » : liste composée
// [{url: dataUrl, filename, mime, size}]. Envoys pendant le prochain prompt
// au serveur (OpenCodeServer.prompt_async) qui convertit en parts OpenCode
// avec mime + url + mention.
// ---------------------------------------------------------------------------
var composerAttachments = [];

function formatFileSize(n) {
  if (!n || n < 0) return "";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " KB";
  return (n / (1024 * 1024)).toFixed(1) + " MB";
}

function renderComposerAttachments() {
  var list = document.getElementById("chat-attach-list");
  if (!list) return;
  if (!composerAttachments.length) {
    list.innerHTML = "";
    list.hidden = true;
    return;
  }
  var imageHtml = "";
  var fileHtml = "";
  composerAttachments.forEach(function (a, i) {
    var clearBtn = '<button type="button" class="chat-attach-chip-clear" data-idx="' + i + '" title="Retirer"><span aria-hidden="true">&times;</span></button>';
    if (isImageAttachment(a)) {
      imageHtml += '<div class="chat-attach-thumb">'
        + '<img src="' + esc(a.url || "") + '" alt="' + esc(a.filename || "image") + '" />'
        + clearBtn + '</div>';
    } else {
      var folderIcon = a.directory ? "📁 " : "";
      var label = folderIcon + esc(a.filename || "fichier") + (a.size ? ' <span class="chat-attach-chip-size">' + esc(formatFileSize(a.size)) + "</span>" : "");
      fileHtml += '<div class="chat-attach-chip">'
        + '<span class="chat-attach-chip-name">' + label + '</span>'
        + clearBtn + '</div>';
    }
  });
  var html = "";
  if (imageHtml) html += '<div class="chat-attach-images">' + imageHtml + '</div>';
  if (fileHtml) html += '<div class="chat-attach-files">' + fileHtml + '</div>';
  list.innerHTML = html;
  list.innerHTML = html;
  list.hidden = false;
  list.querySelectorAll(".chat-attach-chip-clear").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var idx = parseInt(btn.getAttribute("data-idx"), 10);
      if (!isNaN(idx)) {
        composerAttachments.splice(idx, 1);
        renderComposerAttachments();
        updateSendButtonAvailability();
      }
    });
  });
}

function clearComposerAttachments() {
  composerAttachments = [];
  renderComposerAttachments();
}

function readFileAsDataUrl(file) {
  if (!file) return;
  var isImage = String(file.type || "").toLowerCase().indexOf("image/") === 0 ||
    /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i.test(String(file.name || ""));
  if (isImage) {
    var imgCount = composerAttachments.filter(function (a) { return isImageAttachment(a); }).length;
    if (imgCount >= 10) {
      showMentionWarning("Limite atteinte : 10 images maximum par message.");
      return;
    }
  }
  if (file.size > 10 * 1024 * 1024) {
    showMentionWarning("Pièce jointe trop grande pour Sayrhazi (>10 MB) : " + esc(file.name || "fichier") + ".");
    return;
  }
  var reader = new FileReader();
  reader.onload = function () {
    composerAttachments.push({
      url: String(reader.result || ""),
      filename: file.name || "fichier",
      mime: file.type || "text/plain",
    });
    renderComposerAttachments();
    updateSendButtonAvailability();
  };
  reader.onerror = function () {
    showMentionWarning("Erreur de lecture du fichier : " + esc(file.name || "fichier") + ".");
  };
  reader.readAsDataURL(file);
}

function toggleAttachPanel() {
  const btn = document.getElementById("chat-attach-btn");
  const panel = document.getElementById("chat-attach-panel");
  if (!btn || !panel) return;
  const show = panel.style.display !== "block";
  panel.style.display = show ? "block" : "none";
}

function startAttachFilePicker() {
  const input = document.getElementById("chat-attach-file-input");
  if (!input) return;
  input.value = "";
  input.click();
}

function startAttachDirectoryPicker() {
  const input = document.getElementById("chat-attach-dir-input");
  if (!input) return;
  input.value = "";
  input.click();
}

function attachDirectoryFromPicker(files) {
  var list = files ? Array.prototype.slice.call(files) : [];
  if (!list.length) {
    showMentionWarning("Répertoire vide : aucune pièce jointe.");
    return;
  }
  var top = "";
  for (var i = 0; i < list.length; i++) {
    var rel = list[i].webkitRelativePath || list[i].name || "";
    var first = rel.split("/")[0] || rel.split("\\")[0] || "";
    if (first) { top = first; break; }
  }
  if (!top) {
    showMentionWarning("Impossible de déterminer le répertoire sélectionné.");
    return;
  }
  composerAttachments.push({ path: top, filename: top, directory: true });
  renderComposerAttachments();
  updateSendButtonAvailability();
}

function wireAttachInputs() {
  const fileInput = document.getElementById("chat-attach-file-input");
  const dirInput = document.getElementById("chat-attach-dir-input");
  if (fileInput && !fileInput.__wiredAttach) {
    fileInput.__wiredAttach = true;
    fileInput.addEventListener("change", function () {
      var files = fileInput.files ? Array.prototype.slice.call(fileInput.files) : [];
      fileInput.value = "";
      const panel = document.getElementById("chat-attach-panel");
      if (panel) panel.style.display = "none";
      files.forEach(readFileAsDataUrl);
    });
  }
  if (dirInput && !dirInput.__wiredAttach) {
    dirInput.__wiredAttach = true;
    dirInput.addEventListener("change", function () {
      var files = dirInput.files ? Array.prototype.slice.call(dirInput.files) : [];
      dirInput.value = "";
      const panel = document.getElementById("chat-attach-panel");
      if (panel) panel.style.display = "none";
      attachDirectoryFromPicker(files);
    });
  }
}

function handleAttachAction(action) {
  const panel = document.getElementById("chat-attach-panel");
  if (panel) panel.style.display = "none";
  if (action === "files") startAttachFilePicker();
  else if (action === "directory") startAttachDirectoryPicker();
}

function closeAttachPanelOnClickOutside(e) {
  const btn = document.getElementById("chat-attach-btn");
  const panel = document.getElementById("chat-attach-panel");
  if (!btn || !panel) return;
  if (!btn.contains(e.target) && !panel.contains(e.target)) {
    panel.style.display = "none";
  }
}

/* Modèle dans le composer : le modèle de l'agent (défaut) ou celui choisi
   dans la modal « Sélectionner un modèle » (listes la catalogue OpenCode). */
let agentDefaultModel = null;   // {providerID, modelID, name, providerName, free} - défaut agent
let selectedModel = null;       // même forme - choix explicite (priorité à l'affichage et à l'envoi)

const MODEL_STASH_KEY = "sayrhazi.model";
const MODEL_RECENT_KEY = "sayrhazi.model.recent";

function readModelStash() {
  try { return JSON.parse(sessionStorage.getItem(MODEL_STASH_KEY) || "null"); } catch (e) { return null; }
}
function writeModelStash(sel) {
  try { sessionStorage.setItem(MODEL_STASH_KEY, JSON.stringify(sel)); } catch (e) { /* ignore */ }
}
function readModelRecent() {
  try {
    const raw = JSON.parse(sessionStorage.getItem(MODEL_RECENT_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter(x => x && typeof x.providerID === "string" && typeof x.modelID === "string").slice(0, 5) : [];
  } catch (e) { return []; }
}
function rememberRecent(sel) {
  if (!sel) return;
  function keyOf(x) { return x.providerID + "/" + x.modelID + "/" + (x.variant || ""); }
  var self = { providerID: sel.providerID, modelID: sel.modelID, variant: sel.variant || "" };
  var next = [self]
    .concat(readModelRecent().filter(x => keyOf(x) !== keyOf(self)))
    .slice(0, 5);
  try { sessionStorage.setItem(MODEL_RECENT_KEY, JSON.stringify(next)); } catch (e) { /* ignore */ }
}

function prettyVariant(v) {
  v = String(v || "");
  return v ? v.charAt(0).toUpperCase() + v.slice(1) : "";
}

function composerModel() {
  return selectedModel || agentDefaultModel || null;
}
function renderComposerModel() {
  const el = document.getElementById("chat-composer-model");
  if (!el) return;
  const agentLabel = selectedChatAgent ? getAgentDisplayName(selectedChatAgent) : "";
  const m = composerModel();
  var html = "";
  if (agentLabel) html += '<span class="chat-composer-model-agent">' + esc(agentLabel) + '</span>';
  if (m) {
    if (html) html += '<span class="chat-composer-model-sep" aria-hidden="true">•</span>';
    html += '<span class="chat-composer-model-name">' + esc(m.name) + '</span>';
    if (m.providerName || m.providerID) {
      html += '<span class="chat-composer-model-sep" aria-hidden="true">•</span>';
      html += '<span class="chat-composer-model-provider">' + esc(m.providerName || m.providerID) + '</span>';
    }
    if (m.variant) {
      html += '<span class="chat-composer-model-sep" aria-hidden="true">•</span>';
      html += '<span class="chat-composer-model-variant">' + esc(prettyVariant(m.variant)) + '</span>';
    }
  }
  if (!html) html = '<span class="chat-composer-model-name">Modèle par défaut</span>';
  el.innerHTML = html;
  el.title = m
    ? "Modèle et fournisseur de l'agent - cliquer pour changer"
    : "Catalogue indisponible - cliquer pour réessayer";
}

// Rafraîchit le modèle affiché (agent courant) et la modal si elle doit
// rester cohérente - idempotent, sans effet de bord sur l'envoi.
async function refreshComposerModel() {
  if (!selectedChatAgent) { renderComposerModel(); return; }
  try {
    const resp = await fetch("/api/chat/current-model?agent=" + encodeURIComponent(selectedChatAgent));
    const data = await resp.json().catch(function () { return {}; });
    if (resp.ok && data && data.ok && data.model) {
      agentDefaultModel = data.model;
    }
  } catch (err) {
    console.error("[model] défaut indisponible", err);
  }
  renderComposerModel();
}

// Modèle à envoyer avec le message : prioritaire sur celui de l'agent pour ce tour.
function composerModelForSend() {
  if (!selectedModel) return undefined;
  var out = { providerID: selectedModel.providerID, modelID: selectedModel.modelID };
  if (selectedModel.variant) out.variant = selectedModel.variant;
  return out;
}

function selectComposerModel(sel) {
  selectedModel = {
    providerID: sel.providerID,
    modelID: sel.modelID,
    name: sel.name || sel.modelID,
    providerName: sel.providerName || sel.providerID,
    free: !!sel.free,
    variant: sel.variant ? String(sel.variant).toLowerCase() : "",
  };
  writeModelStash(selectedModel);
  rememberRecent(selectedModel);
  renderComposerModel();
}

function formatTokens(n) {
  var num = Number(n);
  if (!Number.isFinite(num) || num <= 0) return "";
  return num.toLocaleString("fr-FR");
}

// ---------------------------------------------------------------------------
// Tooltip flottant modèle (façon OpenCode Desktop) — un seul élément ancré
// sur le row hovered/focused ; affiche Entrées et Raisonnement.
// ---------------------------------------------------------------------------
function getFloatTooltipCapabilities(item) {
  var rawCaps = item.getAttribute("data-capabilities");
  var rawMods = item.getAttribute("data-modalities");
  var caps = null;
  var mods = null;
  try { caps = rawCaps ? JSON.parse(rawCaps) : null; } catch (e) { caps = null; }
  try { mods = rawMods ? JSON.parse(rawMods) : null; } catch (e) { mods = null; }
  var inputs = [];
  if (caps && caps.input && typeof caps.input === "object") {
    ["text", "image", "audio", "video", "pdf"].forEach(function (k) {
      if (caps.input[k]) inputs.push(k);
    });
  } else if (mods && Array.isArray(mods)) {
    inputs = mods.map(function (v) { return String(v).toLowerCase(); });
  }
  var reasoning = caps && typeof caps.reasoning === "boolean" ? !!caps.reasoning : false;
  return { inputs: inputs, reasoning: reasoning };
}

function ensureFloatTooltip() {
  var tip = document.getElementById("model-float-tooltip");
  if (tip) return tip;
  tip = document.createElement("div");
  tip.id = "model-float-tooltip";
  tip.className = "model-float-tooltip";
  tip.hidden = true;
  document.body.appendChild(tip);
  return tip;
}

function showFloatTooltip(item) {
  var tip = ensureFloatTooltip();
  var info = getFloatTooltipCapabilities(item);
  var providerID = item.getAttribute("data-provider") || "";
  var modelID = item.getAttribute("data-id") || "";
  var fullModelId = providerID + (modelID ? "/" + modelID : "");
  var inputsText = info.inputs.length ? info.inputs.join(", ") : "aucun";
  var reasoningText = info.reasoning ? "Raisonnement" : "Pas de raisonnement";
  tip.innerHTML =
    '<div class="model-float-tooltip-line"><span class="model-float-tooltip-label">Entrées</span>' +
    '<span class="model-float-tooltip-value">' + esc(inputsText) + '</span></div>' +
    '<div class="model-float-tooltip-line"><span class="model-float-tooltip-label">Raisonnement</span>' +
    '<span class="model-float-tooltip-value">' + esc(reasoningText) + '</span></div>' +
    '<div class="model-float-tooltip-line model-float-tooltip-copy" data-copy="' + escAttr(fullModelId) + '"><span class="model-float-tooltip-label">ProviderID</span>' +
    '<span class="model-float-tooltip-value">' + esc(fullModelId || "—") + '</span></div>';
  tip.hidden = false;
  tip.style.visibility = "hidden";
  tip.style.left = "0px";
  tip.style.top = "0px";
  var r = item.getBoundingClientRect();
  var width = tip.offsetWidth || 240;
  var height = tip.offsetHeight || 60;
  // OpenCode Desktop : tooltip à droite du sélecteur, aligné en haut avec la ligne survolée.
  var left = r.right + 6;
  if (left + width > window.innerWidth - 8) {
    left = Math.max(8, r.left - width - 6);
  }
  var top = Math.max(8, Math.min(r.top, window.innerHeight - height - 8));
  tip.style.left = left + "px";
  tip.style.top = top + "px";
  tip.style.visibility = "visible";
}

function hideFloatTooltip() {
  var tip = document.getElementById("model-float-tooltip");
  if (tip) tip.hidden = true;
}

function modelRowHtml(item) {
  var providerName = item.providerName || item.providerID;
  var variants = Array.isArray(item.variants) ? item.variants : [];
  var currentVariant = selectedModel && selectedModel.providerID === item.providerID && selectedModel.modelID === item.modelID ? String(selectedModel.variant || "") : null;
  var isCurrent = currentVariant !== null;
  var contextHtml = item.context ? '<span class="model-item-ctx" title="Contexte">- ' + esc(formatTokens(item.context)) + '</span>' : '';
  var flags = '';
  if (isCurrent) flags += '<span class="model-item-check" aria-hidden="true">✓</span>';
  if (variants.length) flags += '<span class="model-item-arrow" aria-hidden="true">→</span>';
  if (item.free) flags += '<span class="model-badge model-badge--free">FREE</span>';
  var searchHay = (item.name + ' ' + item.modelID + ' ' + providerName).toLowerCase();
  var capsJson = item.capabilities ? escAttr(JSON.stringify(item.capabilities)) : "";
  var modsJson = item.modalities && Array.isArray(item.modalities.input) ? escAttr(JSON.stringify(item.modalities.input)) : "";
  return '<div class="model-item-wrap">'
    + '<button type="button" class="model-item' + (isCurrent ? ' model-item--current' : '') + (variants.length ? ' model-item--has-variants' : '') + '" data-provider="' + escAttr(item.providerID) + '" data-id="' + escAttr(item.modelID) + '" data-name="' + escAttr(item.name) + '" data-provider-name="' + escAttr(providerName) + '" data-free="' + (item.free ? '1' : '0') + '" data-context="' + escAttr(item.context || 0) + '" data-variants="' + escAttr(variants.join(",")) + '" data-search="' + escAttr(searchHay) + '" data-capabilities="' + capsJson + '" data-modalities="' + modsJson + '">'
    + '<span class="model-item-name">' + esc(item.name) + '</span>'
    + '<span class="model-item-sep" aria-hidden="true"> </span>'
    + '<span class="model-item-provider">' + esc(providerName) + '</span>'
    + contextHtml
    + '<span class="model-item-flags">' + flags + '</span>'
    + '</button>'
    + '</div>';
}

async function openModelModal() {
  var catalog;
  try {
    var resp = await fetch("/api/chat/providers");
    var data = await resp.json().catch(function () { return {}; });
    if (!resp.ok || !data || !data.ok) {
      showChatErrorModal("Modèles", (data && data.message) || "Catalogue indisponible - OpenCode ne répond pas.");
      return;
    }
    catalog = Array.isArray(data.providers) ? data.providers : [];
  } catch (err) {
    console.error("[model] catalogue indisponible", err);
    showChatErrorModal("Modèles", "Erreur de connexion au serveur lors de la récupération du catalogue.");
    return;
  }
  if (!catalog.length) {
    showChatErrorModal("Modèles", "OpenCode ne renvoie aucun modèle pour le moment.");
    return;
  }

  var available = {};
  catalog.forEach(function (p) {
    p.models.forEach(function (m) { available[p.id + "/" + m.id] = { providerID: p.id, modelID: m.id, name: m.name, providerName: p.name, free: !!m.free, context: m.context || 0, variants: m.variants || [], capabilities: m.capabilities || null, modalities: m.modalities || null }; });
  });
  var recent = readModelRecent().map(function (r) {
    var hit = available[r.providerID + "/" + r.modelID];
    return hit ? Object.assign({}, hit, { variant: r.variant || "" }) : null;
  }).filter(Boolean);

  var body = '<div class="model-step" id="model-step-picker">'
    + '<div class="model-search-wrap"><input type="text" id="model-search" class="model-search" placeholder="Rechercher un modèle…" autocomplete="off" spellcheck="false" /></div>'
    + '<div class="model-list" id="model-list" role="listbox" aria-label="Modèles disponibles">'
    + (recent.length
      ? '<div class="model-section" data-section="recent"><div class="model-section-title">Recent</div>' + recent.map(modelRowHtml).join("") + '</div>'
      : '')
    + catalog.map(function (p) {
        return '<div class="model-section" data-section="' + escAttr(p.id) + '"><div class="model-section-title">' + esc(p.name) + '</div>'
          + p.models.map(function (m) {
              return modelRowHtml({ providerID: p.id, modelID: m.id, name: m.name, providerName: p.name, free: m.free, context: m.context || 0, variants: m.variants || [], capabilities: m.capabilities || null, modalities: m.modalities || null });
            }).join("")
          + '</div>';
      }).join("")
    + '</div><p class="model-empty" id="model-empty" hidden>Aucun modèle ne correspond à la recherche.</p>'
    + '</div>'
    + '<div class="model-step" id="model-step-variant" hidden>'
    + '<div class="model-variant-header">'
    + '<button type="button" class="model-back-btn" id="model-variant-back" aria-label="Retour à la liste des modèles">‹ Modèles</button>'
    + '<div class="model-variant-model"><span class="model-variant-name" id="model-variant-name"></span><span class="model-variant-provider" id="model-variant-provider"></span></div>'
    + '</div>'
    + '<div class="model-list model-variant-list" id="model-variant-list" role="listbox" aria-label="Variantes du modèle"></div>'
    + '</div>';

  var modal = sayrhaziModal({
    icon: "?",
    title: "Sélectionner un modèle",
    bodyHTML: body,
    cancelLabel: "Fermer",
    hideConfirm: true,
  });
  var _close = modal && modal.close;
  if (_close) {
    modal.close = function () {
      hideFloatTooltip();
      _close();
    };
  }

  setTimeout(function () {
    var search = document.getElementById("model-search");
    var list = document.getElementById("model-list");
    var empty = document.getElementById("model-empty");
    if (search) search.focus();
    function applyFilter() {
      var q = (search.value || "").trim().toLowerCase();
      var any = false;
      list.querySelectorAll(".model-section").forEach(function (section) {
        var visible = 0;
        section.querySelectorAll(".model-item-wrap").forEach(function (wrap) {
          var item = wrap.querySelector(".model-item");
          var ok = !q || (item.getAttribute("data-search") || "").indexOf(q) >= 0;
          wrap.hidden = !ok;
          if (ok) visible++;
        });
        section.hidden = visible === 0;
        if (visible) any = true;
      });
      if (empty) empty.hidden = any;
    }
    if (search) search.addEventListener("input", applyFilter);

    function applyModelSelection(item, variant) {
      selectComposerModel({
        providerID: item.getAttribute("data-provider"),
        modelID: item.getAttribute("data-id"),
        name: item.getAttribute("data-name"),
        providerName: item.getAttribute("data-provider-name"),
        free: item.getAttribute("data-free") === "1",
        variant: variant || "",
      });
      if (modal && modal.close) modal.close();
    }

    function showVariantStep(item) {
      var providerID = item.getAttribute("data-provider");
      var modelID = item.getAttribute("data-id");
      var name = item.getAttribute("data-name");
      var providerName = item.getAttribute("data-provider-name");
      var variants = (item.getAttribute("data-variants") || "").split(",").filter(Boolean);
      var currentVariant = selectedModel && selectedModel.providerID === providerID && selectedModel.modelID === modelID
        ? String(selectedModel.variant || "") : null;
      var picker = document.getElementById("model-step-picker");
      var step = document.getElementById("model-step-variant");
      var nameEl = document.getElementById("model-variant-name");
      var providerEl = document.getElementById("model-variant-provider");
      var listEl = document.getElementById("model-variant-list");
      if (!picker || !step || !listEl) return;
      if (nameEl) nameEl.textContent = name;
      if (providerEl) providerEl.textContent = providerName;
      function variantRow(v) {
        var active = currentVariant !== null && currentVariant === v;
        return '<button type="button" class="model-item model-variant-item' + (active ? ' model-item--current' : '') + '" data-variant="' + escAttr(v) + '">'
          + '<span class="model-item-name">' + esc(v ? prettyVariant(v) : "Default") + '</span>'
          + (active ? '<span class="model-item-check" aria-hidden="true">✓</span>' : '')
          + '</button>';
      }
      listEl.innerHTML = variantRow("") + variants.map(variantRow).join("");
      picker.hidden = true;
      step.hidden = false;
      var backBtn = document.getElementById("model-variant-back");
      if (backBtn) backBtn.focus();
      listEl.querySelectorAll(".model-variant-item").forEach(function (btn) {
        btn.addEventListener("click", function () {
          selectComposerModel({
            providerID: providerID,
            modelID: modelID,
            name: name,
            providerName: providerName,
            free: item.getAttribute("data-free") === "1",
            variant: btn.getAttribute("data-variant") || "",
          });
          if (modal && modal.close) modal.close();
        });
      });
    }

    var backBtnMain = document.getElementById("model-variant-back");
    if (backBtnMain) {
      backBtnMain.addEventListener("click", function () {
        var picker = document.getElementById("model-step-picker");
        var step = document.getElementById("model-step-variant");
        if (picker) picker.hidden = false;
        if (step) step.hidden = true;
        var first = list.querySelector(".model-item-wrap:not([hidden]) .model-item");
        if (first) first.focus();
      });
    }

    list.querySelectorAll(".model-item").forEach(function (item) {
      item.addEventListener("mouseenter", function () { showFloatTooltip(item); });
      item.addEventListener("mouseleave", hideFloatTooltip);
      item.addEventListener("focus", function () { showFloatTooltip(item); });
      item.addEventListener("blur", hideFloatTooltip);
      item.addEventListener("click", hideFloatTooltip);
      item.addEventListener("click", function () {
        if (item.classList.contains("model-item--has-variants")) {
          // Modèle à variantes : ouvre la vue des variantes (comme la TUI).
          showVariantStep(item);
          return;
        }
        // Pas de variante : sélection directe, comme la liste simple.
        applyModelSelection(item, "");
      });
    });

    list.querySelectorAll(".model-variant").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var wrap = btn.closest(".model-item-wrap");
        var item = wrap ? wrap.querySelector(".model-item") : null;
        if (item) applyModelSelection(item, btn.getAttribute("data-variant") || "");
      });
    });
  }, 0);
}

/* Écouteurs du formulaire de chat */
function updateSendButtonAvailability() {
  const input = document.getElementById("chat-message-input");
  const btn = document.getElementById("chat-send-btn");
  if (!input || !btn) return;
  if (btn.classList.contains("stop")) return;
  var hasText = String(input.value || "").trim().length > 0;
  var hasAttachments = Array.isArray(composerAttachments) && composerAttachments.length > 0;
  btn.disabled = !(hasText || hasAttachments);
}

function initChatInput() {
  const input = document.getElementById("chat-message-input");
  const btn = document.getElementById("chat-send-btn");
  const btnNew = document.getElementById("btn-new-session");
  const composerModelBtn = document.getElementById("chat-composer-model");
  wireAttachInputs();
  if (composerModelBtn) {
    composerModelBtn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      openModelModal();
    });
  }
  renderComposerModel();
  if (input) {
    input.addEventListener("input", function () {
      if (input.tagName === "TEXTAREA") {
        input.style.height = "";
        input.style.height = input.scrollHeight + "px";
      }
      renderMentionHighlight();
      applyAutocompleteForInput();
      updateSendButtonAvailability();
      // Le texte édité en cours de revert doit survivre au rechargement de la page.
      if (pendingRevert) {
        writeRevertStash({ sessionId: currentSessionId, messageId: pendingRevert.messageId, text: input.value });
      }
    });
    input.addEventListener("scroll", function () {
      var pre = document.getElementById("chat-highlight");
      if (pre) { pre.scrollTop = input.scrollTop; pre.scrollLeft = input.scrollLeft; }
    });
    input.addEventListener("paste", function (e) {
      var cd = e.clipboardData || window.clipboardData;
      if (!cd) return;
      var files = [];
      // Préférer les items du presse-papiers (évite la duplication avec cd.files)
      // ; repli sur cd.files si aucun item fichier.
      var itemFiles = [];
      if (cd.items && cd.items.length) {
        for (var i = 0; i < cd.items.length; i++) {
          var it = cd.items[i];
          if (it && it.kind === "file") {
            var f = it.getAsFile();
            if (f) itemFiles.push(f);
          }
        }
      }
      if (itemFiles.length) {
        files = itemFiles;
      } else if (cd.files && cd.files.length) {
        for (var i = 0; i < cd.files.length; i++) files.push(cd.files[i]);
      }
      if (!files.length) return;
      e.preventDefault();
      files.forEach(readFileAsDataUrl);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && isAutocompleteOpen()) {
        e.preventDefault();
        closeAutocomplete();
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        var caret = input.selectionStart || 0;
        var activeMention = getActiveMentionEnd(input.value, caret);
        if (activeMention) {
          e.preventDefault();
          if (!isAutocompleteOpen()) {
            openMentionPalette(activeMention.start, activeMention.end, activeMention.query);
          }
          return;
        }
        e.preventDefault();
        sendChatMessage();
      }
    });
  }
  if (btn) {
    btn.addEventListener("click", (e) => {
      // Mode « stop » pendant la réponse : premier clic = armer, second = aborter.
      if (btn.classList.contains("stop")) {
        e.preventDefault();
        e.stopPropagation();
        if (stopArmed) {
          disarmStop();
          abortAgent();
        } else {
          armStop();
        }
        return;
      }
      e.preventDefault();
      sendChatMessage();
    });
  }
  const errorInfoBtn = document.getElementById("agent-error-banner-info");
  if (errorInfoBtn) {
    errorInfoBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      openUpstreamErrorModal();
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
  updateSendButtonAvailability();

  // Chips de prompts de test retirées : la fonctionnalité question/permission est en place.

  var stashedModel = readModelStash();
  if (stashedModel && stashedModel.providerID && stashedModel.modelID) selectedModel = stashedModel;
  renderComposerModel();
  refreshComposerModel();

  // Bandeau « Revert en cours » : le bouton ouvre la modal B (jamais de
  // commande /redo côté Sayrhazi - seul un futur POST /unrevert sera fait).
  var bannerUndo = document.getElementById("revert-banner-undo");
  if (bannerUndo) {
    bannerUndo.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      openUnrevertModal();
    });
  }
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

// esc() pour un attribut double-quote (échappé comme esc, plus les guillemets).
function escAttr(text) {
  return esc(text).replace(/"/g, "&quot;");
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
  // Titres en début de ligne : # ## etc. - couleur uniforme sur toute la ligne, h1 souligné
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
  // Listes en début de ligne - * 1. - seul le symbole est coloré
  t = t.replace(/^([\-*])\s(.*)$/gm, '<span class="md-bullet">$1</span> $2');
  t = t.replace(/^(\d+\.)\s(.*)$/gm, '<span class="md-bullet">$1</span> $2');
  // Liens [texte](url)
  t = t.replace(/\[([^\]]*)\]\(([^)]*)\)/g, '<span class="md-link">$1</span>');
  return t;
}

function isImageAttachment(it) {
  if (!it) return false;
  var mime = String(it.mime || "").toLowerCase();
  if (mime.indexOf("image/") === 0) return true;
  var name = String(it.file || it.filename || "").toLowerCase();
  return /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/.test(name);
}

function colorizeUserMentions(html) {
  if (!html) return html;
  return String(html).replace(/@([A-Za-z0-9_.\/-]+)(?:#[0-9]+(?:-[0-9]+)?)?/g, function (m) {
    return '<span class="mention-accent">' + esc(m) + '</span>';
  });
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
// === Question banner (agent asks) ================================================================
function openQuestionBanner(props) {
  if (!props || !props.id) return;
  var questions = Array.isArray(props.questions) ? props.questions : [];
  if (props.id && pendingQuestion && pendingQuestion.requestID === props.id) return;
  pendingQuestion = {
    requestID: props.id,
    sessionID: props.sessionID || currentSessionId,
    questions: questions,
    answers: [],
    customs: [],
    tab: 0,
  };
  renderQuestionBanner();
  installQuestionKeys();
}

function renderQuestionBanner() {
  var banner = document.getElementById("question-banner");
  if (!banner) return;
  if (!pendingQuestion) {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  renderQuestionTabs();
  renderQuestionBody();
  var dismiss = document.getElementById("question-foot-dismiss");
  if (dismiss) dismiss.onclick = function () { cancelQuestion(); };
}

function questionEntriesSingle(pending) {
  return pending && pending.questions.length === 1 && pending.questions[0] && pending.questions[0].multiple !== true;
}

// Comportement aligné sur OpenCode (Enter du TUI) : une seule question
// single-select -> sélectionner une option soumet immédiatement.
function shouldAutoSubmit(pending) {
  return questionEntriesSingle(pending);
}

function renderQuestionTabs() {
  var strip = document.getElementById("question-tabs");
  if (!strip || !pendingQuestion) return;
  var html = "";
  var total = pendingQuestion.questions.length;
  var single = shouldAutoSubmit(pendingQuestion);
  for (var i = 0; i < total; i++) {
    var q = pendingQuestion.questions[i];
    var label = ((q && q.header && String(q.header).trim()) || ("Série " + (i + 1))).slice(0, 30);
    html += '<button type="button" class="question-tab' + (pendingQuestion.tab === i ? " question-tab--active" : "") + '" data-tab="' + i + '">' + esc(label) + '</button>';
  }
  if (!single) {
    html += '<button type="button" class="question-tab question-tab-confirm' + (pendingQuestion.tab === total ? " question-tab--active" : "") + '" data-tab="' + total + '">Confirm</button>';
  }
  strip.innerHTML = html;
  strip.querySelectorAll(".question-tab").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var idx = parseInt(btn.getAttribute("data-tab"), 10);
      if (!isNaN(idx) && pendingQuestion) {
        flushCustomText();
        pendingQuestion.tab = idx;
        renderQuestionBanner();
      }
    });
  });
}

function flushCustomText() {
  if (!pendingQuestion) return;
  var input = document.getElementById("question-custom-input");
  if (input) pendingQuestion.customs[pendingQuestion.tab] = input.value.slice(0, 2000);
}

function renderQuestionBody() {
  var body = document.getElementById("question-body");
  if (!body || !pendingQuestion) return;
  var total = pendingQuestion.questions.length;
  if (pendingQuestion.tab < total) {
    renderQuestionScreen(body, pendingQuestion.tab);
  } else {
    renderQuestionConfirm(body);
  }
}

function renderQuestionScreen(body, idx) {
  var q = pendingQuestion.questions[idx];
  var ans = pendingQuestion.answers[idx] || (pendingQuestion.answers[idx] = []);
  var html = '<div class="question-title">' + esc((q && q.question) || "") + '</div>';
  html += '<div class="question-options" role="group">';
  var opts = (q && q.options) || [];
  opts.forEach(function (opt) {
    var picked = ans.indexOf(opt.label) >= 0;
    html += '<div class="question-option' + (picked ? " question-option--picked" : "") + '" data-label="' + escAttr(opt.label || "") + '" role="checkbox" aria-checked="' + (picked ? "true" : "false") + '" tabindex="0">'
      + '<span class="question-option-mark" aria-hidden="true">' + (picked ? "✓" : "") + '</span>'
      + '<span class="question-option-label">' + esc(opt.label || "") + '</span>'
      + (opt.description ? '<span class="question-option-desc">' + esc(opt.description) + '</span>' : "")
      + "</div>";
  });
  html += "</div>";
  if (q && q.custom !== false) {
    var customText = pendingQuestion.customs[idx] || "";
    html += '<div class="question-custom-row"><label class="question-custom-label" for="question-custom-input">Type your answer :</label>'
      + '<input type="text" id="question-custom-input" class="question-custom-input" placeholder="Votre réponse..." autocomplete="off" spellcheck="false" value="' + escAttr(customText) + '" />'
      + "</div>";
  }
  body.innerHTML = html;
  body.querySelectorAll(".question-option").forEach(function (row) {
    row.addEventListener("click", function () { toggleQuestionOption(row); });
    row.addEventListener("keydown", function (ev) {
      if (ev.key === " " || ev.key === "Enter") { ev.preventDefault(); toggleQuestionOption(row); }
    });
  });
  var input = document.getElementById("question-custom-input");
  if (input) {
    input.addEventListener("input", function () {
      pendingQuestion.customs[idx] = input.value.slice(0, 2000);
    });
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); submitQuestion(); }
    });
  }
}

function toggleQuestionOption(row) {
  if (!pendingQuestion) return;
  var total = pendingQuestion.questions.length;
  var idx = pendingQuestion.tab;
  var q = pendingQuestion.questions[idx];
  var multiple = q && q.multiple === true;
  var ans = pendingQuestion.answers[idx] || (pendingQuestion.answers[idx] = []);
  var label = row.getAttribute("data-label") || "";
  var i = ans.indexOf(label);
  if (i >= 0) pendingQuestion.answers[idx] = ans.filter(function (a) { return a !== label; });
  else pendingQuestion.answers[idx] = multiple ? ans.concat([label]) : [label];
  var wasPick = i < 0;
  var autoSubmit = shouldAutoSubmit(pendingQuestion);
  flushCustomText();
  if (wasPick && !multiple) {
    if (autoSubmit) {
      submitQuestion();
      return;
    }
    pendingQuestion.tab = Math.min(idx + 1, total);
  }
  renderQuestionBanner();
}

function renderQuestionConfirm(body) {
  var html = '<div class="question-review">';
  pendingQuestion.questions.forEach(function (q, i) {
    var ans = pendingQuestion.answers[i] || [];
    var customText = pendingQuestion.customs[i] || "";
    var chosen = ans.join(", ");
    if (customText) chosen = (chosen ? chosen + ", " : "") + customText;
    if (!chosen) chosen = "(aucune réponse)";
    html += '<div class="question-review-item">';
    html += '<div class="question-review-q">' + (i + 1) + ". " + esc((q && q.question) || "") + "</div>";
    html += '<div class="question-review-a">' + esc(chosen) + "</div>";
    html += "</div>";
  });
  html += "</div>";
  html += '<div class="question-confirm-row"><button type="button" class="question-confirm-btn" id="question-submit-btn">Confirmer les réponses</button></div>';
  body.innerHTML = html;
  var btn = document.getElementById("question-submit-btn");
  if (btn) btn.addEventListener("click", function () { submitQuestion(); });
}

var questionKeysInstalled = false;
function installQuestionKeys() {
  if (questionKeysInstalled) return;
  questionKeysInstalled = true;
  document.addEventListener("keydown", function (e) {
    if (!pendingQuestion) return;
    var tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : "";
    if (tag === "textarea" || tag === "input" || tag === "select") return;
    if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); submitQuestion(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); cancelQuestion(); }
  }, true);
}

function submitQuestion() {
  if (!pendingQuestion) return;
  flushCustomText();
  var answers = pendingQuestion.questions.map(function (q, i) {
    var parts = [];
    var a = pendingQuestion.answers[i];
    if (a && a.length) parts = parts.concat(a);
    var ct = pendingQuestion.customs[i];
    if (ct) parts.push(ct);
    return parts;
  });
  var rid = pendingQuestion.requestID;
  var sid = pendingQuestion.sessionID;
  fetch("/api/chat/session/" + encodeURIComponent(sid) + "/question/" + encodeURIComponent(rid) + "/reply", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({ answers: answers })
  }).then(function () {
    pendingQuestion = null;
    renderQuestionBanner();
  }).catch(function (err) { console.error("[question] submit erreur", err); });
}

function cancelQuestion() {
  if (!pendingQuestion) return;
  var rid = pendingQuestion.requestID;
  var sid = pendingQuestion.sessionID;
  fetch("/api/chat/session/" + encodeURIComponent(sid) + "/question/" + encodeURIComponent(rid) + "/reject", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({})
  }).finally(function () {
    pendingQuestion = null;
    renderQuestionBanner();
  });
}
