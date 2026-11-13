// Character alignment of two references (edit-distance backtrace), so a person sees which characters agree
// and which one was mistyped, dropped or swapped. Pure; tested in lib/align.test.ts.
export type Cell = { a: string | null; b: string | null; op: "same" | "change" | "insert" | "delete" | "swap" };

export function align(a: string, b: string): Cell[] {
  const n = a.length, m = b.length;
  const D = Array.from({ length: n + 1 }, (_, i) => Array.from({ length: m + 1 }, (_, j) => (i === 0 ? j : j === 0 ? i : 0)));
  for (let i = 1; i <= n; i++) for (let j = 1; j <= m; j++) {
    D[i][j] = Math.min(D[i - 1][j] + 1, D[i][j - 1] + 1, D[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) D[i][j] = Math.min(D[i][j], D[i - 2][j - 2] + 1);
  }
  const out: Cell[] = [];
  let i = n, j = m;
  while (i > 0 || j > 0) {
    if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1] && a[i - 1] !== a[i - 2] && D[i][j] === D[i - 2][j - 2] + 1) {
      out.push({ a: a[i - 1], b: b[j - 1], op: "swap" }, { a: a[i - 2], b: b[j - 2], op: "swap" }); i -= 2; j -= 2;
    } else if (i > 0 && j > 0 && D[i][j] === D[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)) {
      out.push({ a: a[i - 1], b: b[j - 1], op: a[i - 1] === b[j - 1] ? "same" : "change" }); i--; j--;
    } else if (i > 0 && D[i][j] === D[i - 1][j] + 1) {
      out.push({ a: a[i - 1], b: null, op: "delete" }); i--;
    } else {
      out.push({ a: null, b: b[j - 1], op: "insert" }); j--;
    }
  }
  return out.reverse();
}
