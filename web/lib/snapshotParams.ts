import "server-only";

// Which ids the public static build pre-renders (from public-snapshot/manifest.json). The local app renders
// every id on demand, so this returns nothing there.
export async function snapshotIds(kind: "cases" | "deductions" | "documents"): Promise<{ id: string }[]> {
  const dir = process.env.CPGOS_SNAPSHOT_DIR;
  if (!dir) return [];
  const { readFile } = await import("node:fs/promises");
  const m = JSON.parse(await readFile(`${dir}/manifest.json`, "utf8")) as Record<string, number[]>;
  return (m[kind] ?? []).map((id) => ({ id: String(id) }));
}
