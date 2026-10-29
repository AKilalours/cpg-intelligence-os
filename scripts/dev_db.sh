#!/usr/bin/env bash
# Start the local Postgres reliably: start the Docker engine if it's stopped
# (Colima or Docker Desktop), then start Postgres and wait until it accepts
# connections. Safe to run repeatedly.
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { echo "ERROR: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail "docker CLI not found. Install: brew install colima docker docker-compose"
docker compose version >/dev/null 2>&1 || fail "docker compose plugin not found. Install: brew install docker-compose"

if ! docker info >/dev/null 2>&1; then
  if command -v colima >/dev/null 2>&1; then
    echo "Docker engine is stopped. Starting Colima..."
    colima start
  elif [ -d "/Applications/Docker.app" ]; then
    echo "Docker engine is stopped. Starting Docker Desktop..."
    open -a Docker
  else
    fail "Docker engine is not running and neither Colima nor Docker Desktop was found."
  fi
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
  docker info >/dev/null 2>&1 || fail "Docker engine did not start within 2 minutes."
fi

port="$(grep -E '^CPGOS_DB_PORT=' .env 2>/dev/null | cut -d= -f2 || true)"; port="${port:-5432}"
if ! docker compose ps --status running --services 2>/dev/null | grep -qx db; then
  if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "port $port is already used by another program (often a local Postgres). Stop it, or set CPGOS_DB_PORT and the port in CPGOS_DATABASE_URL in .env."
  fi
fi

docker compose up -d db
echo -n "Waiting for Postgres"
for _ in $(seq 1 60); do
  if docker compose exec -T db pg_isready -U cpgos >/dev/null 2>&1; then echo " ready."; exit 0; fi
  echo -n "."; sleep 1
done
echo; fail "Postgres did not become ready. See: docker compose logs db"
