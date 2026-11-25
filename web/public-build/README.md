Files here replace their namesakes under web/ in the public static build only (scripts/build_public.sh).
Each keeps the same exports as the server action it replaces: reads come from the recorded snapshot,
and every write returns a clear read-only message. `next build` type-checks them against their callers.
