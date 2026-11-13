/* diff-view.js — rendu split view style VS Code pour Edit/Write/Patch.
 * Module ESM chargé par index.html. Expose window.renderDiffSplit pour les
 * appels depuis le script classique sayrhazi-server.js.
 */

import { diffLines } from "./diff.esm.js";

function esc(t) {
  const d = document.createElement("div");
  d.textContent = t ?? "";
  return d.innerHTML;
}

function lineHtml(num, content, type) {
  const n = num !== null ? `<span class="split-num-v2">${num}</span>` : `<span class="split-num-v2"> </span>`;
  return `<div class="split-line-v2 split-line-${type}-v2">${n}<span class="split-content-v2">${esc(content)}</span></div>`;
}

export function renderDiffSplit(oldContent, newContent, fileName) {
  const ops = diffLines(oldContent || "", newContent || "");

  let leftHtml = "";
  let rightHtml = "";
  let leftNo = 0;
  let rightNo = 0;

  for (const op of ops) {
    if (op.type === "equal") {
      leftNo++;
      rightNo++;
      leftHtml += lineHtml(leftNo, op.lines[0], "ctx");
      rightHtml += lineHtml(rightNo, op.lines[0], "ctx");
    } else if (op.type === "delete") {
      leftNo++;
      leftHtml += lineHtml(leftNo, op.lines[0], "rem");
      rightHtml += lineHtml(null, "", "empty");
    } else if (op.type === "insert") {
      rightNo++;
      leftHtml += lineHtml(null, "", "empty");
      rightHtml += lineHtml(rightNo, op.lines[0], "add");
    }
  }

  const del = ops.filter((o) => o.type === "delete").length;
  const add = ops.filter((o) => o.type === "insert").length;

  return (
    '<div class="tool-split-diff-v2">' +
    '<div class="tool-split-header-v2">' +
    '<span class="split-file-v2">' +
    esc(fileName || "") +
    "</span>" +
    '<span class="split-stats-v2">' +
    '<span class="split-stat-v2 split-stat-del">-' +
    del +
    "</span> " +
    '<span class="split-stat-v2 split-stat-add">+' +
    add +
    "</span>" +
    "</span>" +
    "</div>" +
    '<div class="split-grid-v2">' +
    '<div class="split-col-v2 split-col-old-v2">' +
    '<div class="split-col-header-v2">Ancien</div>' +
    '<div class="split-lines-v2">' +
    leftHtml +
    "</div>" +
    "</div>" +
    '<div class="split-col-v2 split-col-new-v2">' +
    '<div class="split-col-header-v2">Nouveau</div>' +
    '<div class="split-lines-v2">' +
    rightHtml +
    "</div>" +
    "</div>" +
    "</div>" +
    "</div>"
  );
}

window.renderDiffSplit = renderDiffSplit;
