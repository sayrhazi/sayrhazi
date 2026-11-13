// diff.esm.js — implémentation maison de diffLines (algorithme de LCS, O(n*m))
// Compatible ESM, aucune dépendance externe côté navigateur.
// Exporte diffLines(oldText, newText) -> tableau d'opérations {type:'equal'|'delete'|'insert', lines:string[]}

export function diffLines(oldText, newText) {
  const a = (oldText || "").split(/\r?\n/);
  const b = (newText || "").split(/\r?\n/);
  const n = a.length;
  const m = b.length;

  // Table DP pour la plus longue sous-séquence commune
  const dp = Array(n + 1).fill(null).map(() => Array(m + 1).fill(0));
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      if (a[i - 1] === b[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  // Backtrack pour produire les opérations
  const ops = [];
  let i = n;
  let j = m;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) {
      ops.unshift({ type: "equal", lines: [a[i - 1]] });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      ops.unshift({ type: "insert", lines: [b[j - 1]] });
      j--;
    } else if (i > 0) {
      ops.unshift({ type: "delete", lines: [a[i - 1]] });
      i--;
    } else {
      break;
    }
  }
  return ops;
}
