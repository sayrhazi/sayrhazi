#!/usr/bin/env node
/*
 * build-hljs-bundle.js — genere static/hljs.bundle.js (UMD) a partir de
 * node_modules/highlight.js (paquet npm, licence BSD).
 *
 * Le paquet npm ne fournit pas de bundle navigateur : les fichiers lib/ sont
 * des modules CommonJS. On les concatene dans un seul fichier autonome que le
 * dashboard peut charger avec une simple balise <script>, sans dependance.
 *
 * Usage : node engine/build-hljs-bundle.js
 */

const fs = require("fs");
const path = require("path");

const REPO = path.resolve(__dirname, "..");
const PKG = path.join(REPO, "node_modules", "highlight.js", "lib");
const OUT = path.join(REPO, "runtimes", "opencode", "scripts", "static", "hljs.bundle.js");

// Langages embarques : couvrent l'usage courant du dashboard.
// Tous les langages du paquet restent accessibles si le bundle est regenere.
const LANGS = [
  "javascript", "typescript", "python", "css", "scss", "xml", "json",
  "bash", "shell", "yaml", "sql", "go", "rust", "java", "cpp", "csharp",
  "php", "ruby", "markdown", "dockerfile", "ini", "toml", "lua", "kotlin",
  "swift", "dart", "scala", "perl", "r", "haskell", "elixir", "clojure",
  "powershell", "nginx", "graphql", "protobuf", "makefile", "diff", "plaintext",
];

function readCore() {
  return fs.readFileSync(path.join(PKG, "core.js"), "utf8");
}

function readLang(name) {
  const file = path.join(PKG, "languages", name + ".js");
  if (!fs.existsSync(file)) return null;
  return fs.readFileSync(file, "utf8");
}

// Extrait le nom declare par le module (const javascript = ... / function x)
function langName(code, fallback) {
  const m = code.match(/const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:function|\()/);
  if (m) return m[1];
  const m2 = code.match(/function\s+([A-Za-z_$][\w$]*)\s*\(/);
  if (m2) return m2[1];
  return fallback.replace(/[^A-Za-z0-9_]/g, "_");
}

const core = readCore();
const parts = [];
const registered = [];

for (const name of LANGS) {
  const code = readLang(name);
  if (!code) {
    console.warn("  (ignore, absent) " + name);
    continue;
  }
  const varName = langName(code, name);
  parts.push(
    "  __register(function (module, exports) {\n" +
    code.replace(/\s+$/, "") + "\n  }, " + JSON.stringify(name) + ");"
  );
  registered.push(name);
}

const bundle = `/*!
 * hljs.bundle.js — highlight.js ${require(path.join(REPO, "node_modules", "highlight.js", "package.json")).version} (BSD-3-Clause)
 * Bundle navigateur genere par engine/build-hljs-bundle.js (core + ${registered.length} langages).
 * Ne pas editer a la main : regenerer avec node engine/build-hljs-bundle.js.
 */
(function (root, factory) {
  if (typeof exports === "object" && typeof module !== "undefined") {
    module.exports = factory();
  } else if (typeof define === "function" && define.amd) {
    define([], factory);
  } else {
    root.hljs = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var hljs;

  (function () {
    var module = { exports: {} };
    var exports = module.exports;
${core.replace(/\s+$/, "")}
    hljs = module.exports;
  })();

  function __register(factory, name) {
    var module = { exports: {} };
    var exports = module.exports;
    factory(module, exports);
    hljs.registerLanguage(name, module.exports);
  }

${parts.join("\n\n")}

  return hljs;
});
`;

fs.writeFileSync(OUT, bundle, "utf8");
const size = (fs.statSync(OUT).size / 1024).toFixed(0);
console.log("Bundle genere : " + OUT);
console.log("Langages embarques : " + registered.length + " (" + registered.join(", ") + ")");
console.log("Taille : " + size + " KB");
