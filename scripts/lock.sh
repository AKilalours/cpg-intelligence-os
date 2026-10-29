#!/usr/bin/env bash
# Re-resolve exact dependency versions for every supported Python (3.10+).
# After running, test on both 3.10 and 3.12 before committing the new lock.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null 2>&1 || { echo "uv not found. Install: pip install uv" >&2; exit 1; }
tmp="$(mktemp)"
uv pip compile pyproject.toml --extra dev --universal --python-version 3.10 --no-header -q -o "$tmp"
{
  echo "# Exact versions for setup and CI. Regenerate with: make lock"
  echo "# Markers pick the right version per Python (e.g. DuckDB 2.x needs Python 3.11+)."
  cat "$tmp"
} > requirements.lock
rm -f "$tmp"
echo "requirements.lock updated. Now run the tests on Python 3.10 and 3.12 before committing."
