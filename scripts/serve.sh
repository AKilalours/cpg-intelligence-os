#!/usr/bin/env bash
# Start the API or the UI on its configured port, refusing loudly if the port
# is taken. (Next.js otherwise drifts silently from 3000 to 3001, and port 3000
# often belongs to Grafana or another dev server.)
set -euo pipefail
cd "$(dirname "$0")/.."
kind="${1:?usage: serve.sh api|web}"

env_get() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true; }
api_port="${CPGOS_API_PORT:-$(env_get CPGOS_API_PORT)}"; api_port="${api_port:-8100}"
web_port="${CPGOS_WEB_PORT:-$(env_get CPGOS_WEB_PORT)}"; web_port="${web_port:-3100}"
port=$([ "$kind" = api ] && echo "$api_port" || echo "$web_port")

if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
  owner=""
  command -v lsof >/dev/null 2>&1 && owner="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -Fc 2>/dev/null | sed -n 's/^c//p' | head -1)"
  echo "ERROR: port $port is already in use${owner:+ by '$owner'}, so the $kind was not started." >&2
  var=$([ "$kind" = api ] && echo CPGOS_API_PORT || echo CPGOS_WEB_PORT)
  echo "Fix: stop that program, or set a free port in .env, e.g. $var=$((port + 1))" >&2
  exit 1
fi

# The UI needs Node >= 20.9 (Next.js 16). The tested version is in web/.nvmrc.
# Check BEFORE npm install, and use nvm to switch automatically when available.
node_ok() {
  command -v node >/dev/null 2>&1 || return 1
  local v; v="$(node --version 2>/dev/null | sed 's/^v//')"
  local major="${v%%.*}" rest="${v#*.}"; local minor="${rest%%.*}"
  [ "${major:-0}" -gt 20 ] || { [ "${major:-0}" -eq 20 ] && [ "${minor:-0}" -ge 9 ]; }
}
ensure_node() {
  node_ok && return 0
  local have; have="$(node --version 2>/dev/null || echo none)"
  local want; want="$(cat .nvmrc 2>/dev/null || echo 22)"
  local nvm_sh="${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  if [ -s "$nvm_sh" ]; then
    echo "Node $have is too old for the UI (needs 20.9+). Switching to Node $want with nvm..."
    set +u; . "$nvm_sh"; nvm install "$want" >/dev/null && nvm use "$want" >/dev/null; set -u
    node_ok && { echo "Using Node $(node --version)."; return 0; }
  fi
  echo "ERROR: the UI needs Node.js 20.9 or newer; found $have." >&2
  echo "Fix: install nvm (https://github.com/nvm-sh/nvm), then run: cd web && nvm install && nvm use" >&2
  echo "     or: brew install node@$want" >&2
  exit 1
}

case "$kind" in
  api)
    echo "API: http://localhost:$api_port/api/health"
    CPGOS_CORS_ORIGINS="http://localhost:$web_port" exec .venv/bin/python -m uvicorn cpgos.api.app:app --port "$api_port" --reload
    ;;
  web)
    cd web
    ensure_node
    [ -d node_modules ] && [ node_modules -nt package-lock.json ] || npm install
    echo "UI: http://localhost:$web_port   (reading the API at http://127.0.0.1:$api_port)"
    CPGOS_API_URL="http://127.0.0.1:$api_port" exec npx next dev -p "$web_port"
    ;;
  *) echo "usage: serve.sh api|web" >&2; exit 2 ;;
esac
