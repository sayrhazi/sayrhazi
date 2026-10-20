#!/usr/bin/env node
// Vérification post-install (jamais bloquante) : Python, OpenCode, yarn.
// Guide l'utilisateur, n'installe rien à sa place.
"use strict";

const { execSync } = require("child_process");

function check(cmd, minMsg) {
  try {
    const out = execSync(cmd, { encoding: "utf-8", stdio: ["ignore", "pipe", "ignore"] }).trim().split("\n")[0];
    console.log(`[ok] ${minMsg} : ${out}`);
    return true;
  } catch {
    return false;
  }
}

let ok = true;
console.log("sayrhazi : vérification de l'environnement...");
if (!check("python --version", "Python (>= 3.10 requis)")) {
  if (!check("python3 --version", "Python (>= 3.10 requis)")) {
    console.log("[manquant] Python >= 3.10 — Windows : winget install Python.Python.3 | Ubuntu : apt install python3");
    ok = false;
  }
}
if (!check("opencode --version", "OpenCode")) {
  console.log("[manquant] OpenCode — npm install -g opencode-ai (tier gratuit, sans login manuel)");
  ok = false;
}
if (!check("yarn --version", "yarn")) {
  console.log("[optionnel] yarn — npm install -g yarn (requis seulement si vos projets l'utilisent)");
}
if (!check("git --version", "Git")) {
  console.log("[optionnel] Git — conseillé pour la lecture frugale (repli horodatages sinon)");
}
console.log(ok ? "sayrhazi : prêt." : "sayrhazi : installé, complétez les prérequis ci-dessus.");
console.log("Commandes (sans profil à sourcer) : sayrhazi, sayrhazi check, sayrhazi tunda,");
console.log("sayrhazi info, sayrhazi update, sayrhazi remove, sayrhazi --version.");
