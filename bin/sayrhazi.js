#!/usr/bin/env node
// Lanceur `sayrhazi` : appelle le Python local sur cli/main.py.
// Noyau résolu relativement au paquet (auto-localisé, pas de SAYRHAZI_HOME).
"use strict";

const { spawn } = require("child_process");
const path = require("path");

const root = path.resolve(__dirname, "..");
const entry = path.join(root, "cli", "main.py");
const py = process.platform === "win32" ? "python" : "python3";

const child = spawn(py, [entry, ...process.argv.slice(2)], { stdio: "inherit" });

child.on("error", (err) => {
  console.error("sayrhazi : Python introuvable (requis >= 3.10).");
  process.exit(2);
});

child.on("exit", (code) => {
  process.exit(code === null || code === undefined ? 1 : code);
});

// Transmettre les signaux au processus enfant
process.on("SIGINT", () => child.kill("SIGINT"));
process.on("SIGTERM", () => child.kill("SIGTERM"));
