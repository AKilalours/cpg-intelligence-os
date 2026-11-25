# syntax=docker/dockerfile:1.7
# The public, read-only demo: Postgres + API + UI in one container, for a free Hugging Face Docker Space.
# Code comes from this repository; data comes from a PRIVATE Hugging Face dataset made by
# `make demo-snapshot` and fetched at build time with the HF_TOKEN build secret, so the data is never
# in a public repository. Writes are refused by the API (CPGOS_READ_ONLY=1, tests/test_read_only.py).

FROM node:22-bookworm-slim AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
ENV NEXT_TELEMETRY_DISABLED=1 CPGOS_STANDALONE=1
RUN npm run build

FROM postgres:16-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=web /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN python3 -m venv /venv && /venv/bin/pip install --no-cache-dir -r requirements.lock "huggingface_hub>=0.30,<2"
COPY cpgos ./cpgos
COPY db ./db
COPY reports ./reports
COPY --from=web /app/web/.next/standalone ./web
COPY --from=web /app/web/.next/static ./web/.next/static

# Data, restored into the image so a cold start takes seconds, not minutes.
ARG DEMO_DATA_REPO
RUN --mount=type=secret,id=HF_TOKEN,mode=0444,required=true \
    test -n "$DEMO_DATA_REPO" || { echo "Set the Space variable DEMO_DATA_REPO"; exit 1; }; \
    /venv/bin/python -c "import os; from huggingface_hub import snapshot_download as d; \
d(repo_id=os.environ['DEMO_DATA_REPO'], repo_type='dataset', local_dir='/demo', token=open('/run/secrets/HF_TOKEN').read().strip())" \
    && tar -xzf /demo/files.tar.gz -C /app \
    && mkdir -p /pgdata && chown postgres:postgres /pgdata \
    && su postgres -c "initdb -D /pgdata -U postgres --auth=trust >/dev/null" \
    && su postgres -c "pg_ctl -D /pgdata -o '-c listen_addresses=127.0.0.1' -w start" \
    && psql -U postgres -h 127.0.0.1 -v ON_ERROR_STOP=1 -c "CREATE ROLE cpgos LOGIN SUPERUSER" \
         -c "CREATE ROLE cpgos_agent NOLOGIN" -c "CREATE ROLE cpgos_reviewer NOLOGIN" \
         -c "CREATE ROLE cpgos_engine NOLOGIN" -c "CREATE ROLE cpgos_monitor NOLOGIN" \
         -c "CREATE DATABASE cpgos OWNER cpgos" \
    && pg_restore -U postgres -h 127.0.0.1 -d cpgos --exit-on-error /demo/db.dump \
    && su postgres -c "pg_ctl -D /pgdata -w stop" \
    && rm -rf /demo \
    && chown -R 1000:1000 /pgdata /app

COPY deploy/start.sh /start.sh
# Hugging Face runs the container as user 1000.
RUN usermod -u 1000 postgres 2>/dev/null || true; chmod +x /start.sh
USER 1000
ENV CPGOS_READ_ONLY=1 CPGOS_DATABASE_URL=postgresql://cpgos@127.0.0.1:5432/cpgos \
    CPGOS_API_URL=http://127.0.0.1:8100 PYTHONPATH=/app PORT=7860 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
EXPOSE 7860
CMD ["/start.sh"]
