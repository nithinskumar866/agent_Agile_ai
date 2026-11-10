# AgentCorp agents as one container: the ADK server with the Product Manager, Developer and QA agents.
#
#   docker build -t agentcorp .
#   docker run --rm -p 8080:8080 --env-file pm_agent/.env agentcorp          # API server (default)
#   docker run --rm -p 8080:8080 --env-file pm_agent/.env -e ADK_MODE=web agentcorp   # with the web UI
#
# Serverless-ready: listens on $PORT (Cloud Run sets it), runs as a non-root user, keeps no secrets in the
# image (keys arrive as environment variables at run time), and reports readiness on /list-apps.
#
# What does NOT work inside the container, by design: the QA agent's test runner starts Docker containers,
# and no Docker engine is available in here, so run_tests answers "Docker is not installed". On Google Cloud
# that step maps to the Agent Engine code-execution sandbox or a Cloud Run job (docs/demo/architecture.md).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Node 22 for the filesystem MCP server the Developer agent uses (the pinned server needs Node 22; Debian's
# packaged Node is older). Copied from the official image so no installer script runs here.
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

COPY requirements-runtime.txt .
RUN pip install -r requirements-runtime.txt

COPY pm_agent pm_agent
COPY dev_agent dev_agent
COPY qa_agent qa_agent
COPY shared shared
COPY knowledge knowledge

# An unprivileged user, and the only places the agents write: generated projects, the audit log, ADK sessions.
RUN useradd --create-home --uid 10001 agent \
 && mkdir -p /app/workspace /app/audit /app/.adk \
 && chown -R agent:agent /app
USER agent

ENV PORT=8080 \
    ADK_MODE=api \
    AGENTCORP_WORKSPACE=/app/workspace \
    AGENTCORP_AUDIT_FILE=/app/audit/audit.jsonl \
    HOME=/home/agent
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/list-apps' % os.getenv('PORT', '8080'), timeout=3)"

# api: the HTTP API only (what a deployment needs). web: the same plus the ADK web UI, for demos.
CMD ["sh", "-c", "if [ \"$ADK_MODE\" = \"web\" ]; then exec adk web --host 0.0.0.0 --port \"$PORT\" .; else exec adk api_server --host 0.0.0.0 --port \"$PORT\" .; fi"]
