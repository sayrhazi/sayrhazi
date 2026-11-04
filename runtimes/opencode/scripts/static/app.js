/*
 * watch-work.js — interface du dashboard Sayrhazi
 * Lit l'API /api/data et /report/:agent depuis watch-web.py
 * Une seule page : sidebar à gauche, contenu principal à droite.
 */

let currentData = null;
let intervalSeconds = 2;
let selectedAgent = null;

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
  const script = document.querySelector('script[src*="app.js"]');
  if (script && script.dataset.interval) {
    intervalSeconds = parseInt(script.dataset.interval, 10) || 2;
  }

  initNavigation();

  // Restaurer l'agent sélectionné depuis la session, sinon Vue d'ensemble par défaut
  const savedAgent = sessionStorage.getItem('sayrhazi_selected_agent');
  if (savedAgent) {
    selectedAgent = savedAgent;
  } else {
    const dashboardLink = document.querySelector('.nav-list a[data-view="dashboard"]');
    if (dashboardLink) dashboardLink.classList.add('active');
  }

  await loadData();
  startRefresh();

  // Si un agent était sélectionné, afficher sa vue au rechargement
  if (selectedAgent) {
    showAgent(selectedAgent);
  }
});

function initNavigation() {
  document.querySelectorAll(".nav-list a").forEach(link => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      const view = link.dataset.view;
      const agent = link.dataset.agent;

      if (view) {
        selectedAgent = null;
        sessionStorage.removeItem('sayrhazi_selected_agent');
        showView(view);
        highlightNavLink(link);
      } else if (agent) {
        console.log("[app.js] clic sur agent sidebar:", agent);
        selectedAgent = agent;
        sessionStorage.setItem('sayrhazi_selected_agent', agent);
        showAgent(agent);
        highlightNavLink(link);
      }
    });
  });
}

function showView(viewName) {
  document.querySelectorAll(".view").forEach(el => el.classList.remove("active"));
  const view = document.getElementById("view-" + viewName);
  if (view) {
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

  // Tableau des agents
  const tbody = document.getElementById("dashboard-table-body");
  tbody.innerHTML = "";

  data.agents.forEach(a => {
    const tr = document.createElement("tr");
    tr.dataset.agentFile = a.agent_file;
    tr.innerHTML = `
      <td class="agent">${esc(a.agent_file)}</td>
      <td class="role">${esc(a.role)}</td>
      <td>${esc(a.sid)}</td>
      <td>${esc(a.origine)}</td>
      <td>${esc(a.tokens)}</td>
      <td>${esc(a.usage_pct)}</td>
      <td>${esc(a.cout_sess)}</td>
      <td>${esc(a.calls)}</td>
      <td>${esc(a.manual_calls)}</td>
      <td>${esc(a.cout_projet)}</td>
      <td>${esc(a.report)}</td>
      <td class="etat" style="color:${a.etat_color}">${esc(a.etat)}</td>
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
      selectedAgent = a.agent_file;
      sessionStorage.setItem('sayrhazi_selected_agent', a.agent_file);
      showAgent(a.agent_file);
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
    if (selectedAgent) {
      dashboardLink.classList.remove('active');
    } else {
      dashboardLink.classList.add('active');
    }
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
  console.log("[app.js] showAgent appelé avec:", agentFile);

  if (!currentData) {
    console.error("[app.js] currentData est null!");
    return;
  }

  const agent = currentData.agents.find(a => a.agent_file === agentFile);
  if (!agent) {
    console.error("[app.js] agent non trouvé:", agentFile);
    return;
  }

  console.log("[app.js] agent trouvé:", agent);

  // Afficher la vue agent
  document.querySelectorAll(".view").forEach(el => el.classList.remove("active"));
  const agentView = document.getElementById("view-agent");
  agentView.classList.remove("hidden");
  agentView.classList.add("active");

  // Mettre à jour le contenu avec vérification de nullité
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

function getAgentDisplayName(agentFile) {
  const name = AGENT_DISPLAY_NAME[agentFile] || agentFile;
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
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