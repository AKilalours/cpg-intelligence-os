// What to tell a person when a page's data did not load. Only a 404 means "not published yet";
// anything else (API down, a server error) must say so, or the person runs the wrong fix.
export type Failure = { error: string; status: number };
export type NoticeSpec = { kind: "empty" | "error"; title: string; body: string };

export function failureNotice(f: Failure, emptyTitle: string, emptyHint: string): NoticeSpec {
  if (f.status === 404) return { kind: "empty", title: emptyTitle, body: emptyHint };
  if (f.status === 0) return { kind: "error", title: "The API is not running", body: f.error };
  return { kind: "error", title: `The API returned an error (${f.status})`, body: f.error };
}
