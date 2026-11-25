#!/usr/bin/env bash
# Build the public, read-only copy of the app as static files for GitHub Pages (free, no server, no card).
# Same pages and components as the local app; data comes from public-snapshot/ (make public-snapshot), and the
# server actions are swapped for the read-only twins in web/public-build/. The local app is not touched:
# everything happens in a throwaway copy.
#   bash scripts/build_public.sh [snapshot_dir] [base_path]      -> .public-build/out/
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
SNAP="$(cd "${1:-public-snapshot}" && pwd)"
BASE_PATH="${2-/cpg-intelligence-os}"
B="${CPGOS_PUBLIC_BUILD_DIR:-$ROOT/.public-build}"
test -f "$SNAP/manifest.json" || { echo "No snapshot at $SNAP: run make public-snapshot first" >&2; exit 1; }

rm -rf "$B"; mkdir -p "$B"
(cd web && tar --exclude=./node_modules --exclude=./.next --exclude=./public-build -cf - .) | (cd "$B" && tar -xf -)
cp -R web/public-build/app/. "$B/app/"                                  # read-only twins of the server actions
rm -rf "$B/app/documents/[id]/image"                                     # images come from the snapshot
find "$B/app" -name page.tsx -exec perl -0pi -e 's/export const dynamic = "force-dynamic";\n//g' {} +
perl -0pi -e 's/const params = await searchParams;/const params: Search = {}; void searchParams;/' "$B/app/planning/page.tsx"
mkdir -p "$B/public/snap" && cp -R "$SNAP/snap/." "$B/public/snap/"
# A real node_modules inside the copy: the bundler does not follow a symlink out of the project root.
if [ -n "${CPGOS_NODE_MODULES:-}" ]; then cp -R "$CPGOS_NODE_MODULES" "$B/node_modules"; else (cd "$B" && npm ci --no-audit --no-fund); fi

cd "$B"
CPGOS_PUBLIC=1 NEXT_PUBLIC_CPGOS_PUBLIC=1 NEXT_PUBLIC_BASE_PATH="$BASE_PATH" CPGOS_SNAPSHOT_DIR="$SNAP" \
  CPGOS_READ_ONLY=1 NEXT_TELEMETRY_DISABLED=1 npx next build
touch out/.nojekyll
echo "Public build: $B/out ($(du -sh out | cut -f1))"
