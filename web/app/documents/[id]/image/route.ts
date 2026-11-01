import { apiBase } from "@/lib/api";

// The browser never talks to the API directly: images are passed through this route.
export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  if (!/^\d+$/.test(id)) return new Response("bad document id", { status: 400 });
  const res = await fetch(`${apiBase()}/api/documents/${id}/image`, { cache: "no-store" });
  if (!res.ok) return new Response(await res.text(), { status: res.status });
  return new Response(res.body, {
    headers: { "content-type": res.headers.get("content-type") ?? "application/octet-stream" },
  });
}
