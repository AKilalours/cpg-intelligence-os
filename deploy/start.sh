#!/usr/bin/env bash
# Start Postgres, then the API (read-only), then the UI on the public port. If any stops, the container stops,
# so the host restarts it rather than serving a page that cannot reach its data.
set -euo pipefail
pg_ctl -D /pgdata -o "-c listen_addresses=127.0.0.1 -c unix_socket_directories=/tmp" -l /tmp/pg.log -w start
cd /app
/venv/bin/python -m uvicorn cpgos.api.app:app --host 127.0.0.1 --port 8100 --workers 2 &
for _ in $(seq 1 60); do (exec 3<>/dev/tcp/127.0.0.1/8100) 2>/dev/null && break; sleep 0.5; done
cd /app/web
node server.js &
wait -n
exit 1
