# AgentCorp — a technology services company run by Google ADK agents

Learning project for Google's Agent Development Kit. Each department of a services company
becomes an agent. **Current stage: PM, Developer, and QA agents, with tests written before code.**

## Agents

### 1. The PM Agent (`pm_agent`)
Talks to a client (you, in `adk web`) about any kind of engagement — new build, migration,
integration, data/AI, consulting, support — and turns it into a structured, versioned
requirements spec, published as a Markdown PRD (`prd_v1.md`).

### 2. The Dev Agent (`dev_agent`)
Collaborates as an engineering sub-agent. Inspects the shared session state `state["spec"]`
to formulate technology stack recommendations, design modular system architecture, and plan
the project file structure scaffold, publishing `architecture_v1.md`. Implements the code
once the QA agent has locked the acceptance tests, and repairs it until they pass.

### 3. The QA Agent (`qa_agent`)
Writes pytest acceptance tests from the client-approved 'must' acceptance criteria, **before**
any code exists, and locks them. Runs them in an isolated Docker container and publishes
`qa_report_vN.md` with a result for every criterion.

```
pm_agent/               # Product Manager agent
├── agent.py            # LlmAgent with sub_agents=[dev_agent, qa_agent] + App
├── prompt.py           # PM role & dev delegation guidelines
├── tools.py            # 14 tools writing to state["spec"]
├── spec_logic.py       # Deterministic core: IDs, review checks, PRD rendering
├── schema.py           # Re-exports shared/spec.py
├── playbooks.yaml      # Engagement types & discovery areas
└── config.py           # Model and limits from environment

dev_agent/              # Developer & Technical Architect agent
├── agent.py            # LlmAgent & standalone App
├── prompt.py           # Architect role & technical instruction
├── tools.py            # Tech stack, architecture component, and scaffold tools
├── logic.py            # Deterministic architecture rendering & state persistence
├── workspace.py        # MCP filesystem sandbox: path checks, read-only toolset, write client
├── schema.py           # TechDesign schemas
└── config.py           # Dev agent configuration

qa_agent/               # QA agent
├── agent.py            # LlmAgent & standalone App
├── prompt.py           # QA role: tests from criteria only, never weaken a test
├── tools.py            # inspect, write/lock the suite, run_tests, report
├── logic.py            # Criteria IDs, coverage, grading, report rendering
├── runner.py           # The Docker runner: tests, then the smoke check (the only code that executes generated code)
└── smoke_runner.py     # Real-run smoke check, copied into the sandbox (standard library only)

toolbox/tools.yaml     # MCP Toolbox config: three read-only lookups over data/company.db

tests/                  # 449 tests, no model calls or API key. 3 start the real MCP server, 2 the real Toolbox, 3 real Docker
                        #   (skip: AGENTCORP_SKIP_MCP_TESTS=1), 2 run real Docker containers
                        #   (skip: AGENTCORP_SKIP_DOCKER_TESTS=1)
shared/models.py        # Model routing: Gemini, or NVIDIA via LiteLLM
shared/spec.py          # RequirementsSpec: the handoff contract every agent reads
shared/policy.py        # What wording counts as "publish" / "approve"
shared/testsuite.py     # TestSuite: the locked tests, and the rules the Dev agent obeys
shared/safety.py        # Callbacks on every agent: caps, size check, upload notice, audit
shared/guardrails.py    # Screens untrusted text: injection, personal data, secrets, hidden characters
shared/uploads.py       # PDF / text / image uploads -> safe form for the model
shared/audit.py         # Append-only audit log (audit/audit.jsonl, git-ignored)
shared/retrieval.py     # RAG core: chunk by heading, BM25 ranking, refuse when nothing matches
shared/knowledge.py     # Client document collections: cited, screened search results
knowledge/              # One folder per client; acme-bakery/ is a fictional demo collection
Dockerfile, docker-compose.yml, requirements-runtime.txt   # The agents (and Toolbox) as containers
evals/                  # adk eval suite (cases, metrics, runner) and the retrieval eval (rag_eval.py)
scripts/vertex_preflight.py  # Checks the Vertex AI setup step by step
scripts/audit_report.py # Summary of the audit log: tools, refusals, model calls, tokens
scripts/demo_assets.py  # Builds the sample PDF brief and wireframe for demos
docs/decisions.md       # Architecture decision log
docs/incidents.md       # Every real failure, its fix, and the regression test that guards it
docs/demo/              # Customer demo pack: script, architecture, governance, talking points
```

## Run it

```powershell
cd Go_Agile_ai
.venv\Scripts\activate               # created already; otherwise: python -m venv .venv; pip install -r requirements.txt
copy pm_agent\.env.example pm_agent\.env   # then put your AI Studio key in it
adk web                              # open http://localhost:8000, pick pm_agent
```

Run `adk web` from **this folder** (the parent of `pm_agent/`), not from inside `pm_agent`.

In the dev UI, watch:
- **Events** — every model call and tool call, in order
- **State** — the `spec` key growing as the agent works
- **Artifacts** — `prd_v1.md`, `prd_v2.md`, … each time it publishes

Sessions persist in `.adk/` across restarts (ADK's default local storage).

## Dev agent file sandbox (MCP)

The Dev agent can write real source files, but only into `workspace/<project>/`, through the
official filesystem MCP server. Requirements: **Node.js 22+ with npx** on PATH, and
`google-adk[mcp]` (already in requirements.txt). No manual server start is needed: ADK starts
`npx -y @modelcontextprotocol/server-filesystem@2026.8.31 workspace/<project>` on demand. The
first run downloads the package.

Flow: publish the PRD, ask for the architecture (published as `architecture_vN.md`), then ask
the Dev agent to **build/implement** it. It calls `implement_scaffold`, which writes only paths
from the published scaffold and reads each file back. It refuses to start until the QA agent
has locked the acceptance tests, and never writes `qa_tests/` or any `conftest.py`.

## Acceptance tests (QA agent + Docker)

Needs **Docker Desktop running**. Flow, each step enforced in code:

1. The PRD is **approved** by the client, and the architecture is published.
2. `qa_agent` writes pytest tests into `workspace/<project>/qa_tests/`, each mapped to a
   criterion ID like `FR-1.AC2`; untestable criteria are recorded as manual checks.
3. `lock_test_suite` refuses unless every 'must' criterion is covered. Locked tests change
   only when the PRD changes (a new version goes back through the client).
4. `dev_agent` implements, then calls `run_tests`. On failures it fixes the code, never the
   tests. At most 5 runs per suite version, then a person decides.
5. `qa_agent` publishes `qa_report_vN.md`.

`run_tests` installs `requirements.txt` (wheels only) into a cache, then runs pytest on a
throwaway copy of the project with **no network**, 1 CPU, 1 GB memory, no Linux capabilities,
and a read-only root filesystem. The test files come from the locked copy in session state,
and pytest gets its own config, so editing tests, adding a `conftest.py`, or a `pytest.ini` on
disk changes nothing. The acceptance tests cover the Python app folder only.

**Real-run smoke check.** Passing tests are not enough (17 of 17 once passed on a broken page, see
`docs/incidents.md` INC-5), so `run_tests` then starts the **real** FastAPI or Flask app on a fresh
copy in a second sandbox container and uses it like a visitor: the page and every stylesheet, script and
image it links to must be served, every plain GET endpoint the app lists must not crash, and the QA
agent's locked `smoke_requests` (a valid-input journey such as `POST /signup`) must get an accepted
status. JavaScript files are syntax-checked with `node --check` (parse only). A run is `passed` only if
both the tests and the smoke check pass, and a web app cannot be locked without a smoke request. No
browser is driven, so layout, mobile behaviour and the script's runtime behaviour stay manual checks.

## Company memory (MCP Toolbox for Databases)

The PM can look up AgentCorp's earlier projects and lessons learned. The SQL lives in a separate
**Toolbox** server (`toolbox/tools.yaml`), not in the agent: the model sees three named lookups
(`find_past_projects`, `get_past_project`, `find_lessons`) and nothing else, and the database is
opened read-only (a write attempt fails with "readonly database").

```
python scripts/get_toolbox.py                 # once: downloads the pinned binary into .tools/
python scripts/seed_company_db.py             # once: creates data/company.db
.tools/toolbox.exe --config toolbox/tools.yaml --port 5000   # leave running, then start adk web
```

If the server is not running the PM works as before, with no memory tools. `TOOLBOX_URL=""`
switches the memory off; set it to point at another host. `sample` rows are invented; the
`delivered` row is the real email sign-up run.

## Run it as a container (serverless-ready)

```
python scripts/seed_company_db.py     # once: creates data/company.db for the Toolbox container
docker compose up --build             # agents + MCP Toolbox, then open http://localhost:8080
```

The image (`Dockerfile`) runs the ADK server with all three agents: non-root user, listens on `$PORT`
(Cloud Run sets it), readiness on `/list-apps`, Node 22 for the Developer's MCP file server, and **no secrets
inside**: keys come from `pm_agent/.env` or `--env-file` at run time. `ADK_MODE=api` serves the HTTP API only
(what a deployment needs); `ADK_MODE=web` adds the web UI (the compose default). The compose file adds the official
Toolbox container with the database mounted read-only, wired by service name (`TOOLBOX_URL=http://toolbox:5000`), and
drops all Linux capabilities from the agent container. The runtime image is about 930 MB and installs
`requirements-runtime.txt` (no pytest or eval extras).

**What does not work inside a container, on purpose:** the QA agent's test runner starts Docker containers, and
there is no Docker engine in there, so `run_tests` answers "Docker is not installed". On Google Cloud that step
maps to the Agent Engine code-execution sandbox or a Cloud Run job.

**Not deployed anywhere.** This is the packaging; the Cloud Run step would be, roughly (a design, not run):

```
gcloud run deploy agentcorp --source . --region us-central1 --no-allow-unauthenticated \
  --set-env-vars ADK_MODE=api --set-secrets NVIDIA_API_KEY=nvidia-key:latest
```

`pytest tests/test_packaging.py` checks the image and compose file stay safe and consistent; setting
`AGENTCORP_TEST_DOCKER_BUILD=1` also builds and runs the image (about four minutes).

## Uploads, guardrails and the audit trail

**Multimodal briefs.** In `adk web`, attach a PDF, a text file or an image (PNG, JPEG, WEBP, GIF) to
a message. PDFs and text become extracted text; images go to the vision model. Before the model
sees anything, `shared/guardrails.py` withholds lines that try to give the AI orders, masks
emails, phone numbers, card and ID numbers and API keys, and removes hidden characters. The
document is wrapped as client-supplied *data*, and the **system itself adds an "Upload notice"**
to the next reply, so the client is told even if the model forgets. Typed messages are not scanned.
Limits: text inside an image is not read or screened, names are not masked, and the screen is a
filter, not a proof; the approval gates in code remain the real control.
Try it with `docs/demo/assets/sample-brief.pdf` (rebuild: `python scripts/demo_assets.py`).

**Safety net** (`shared/safety.py`, on all three agents): any tool result is capped at 40,000
characters (`AGENTCORP_MAX_TOOL_RESULT_CHARS`); a conversation too large for the model gets a clear
"start a new session" reply instead of a provider error; an unexpected tool exception becomes a short
error the model can react to.

**Audit trail.** Every tool call, model call and upload is appended to `audit/audit.jsonl`
(`AGENTCORP_AUDIT_FILE` to move it, `AGENTCORP_AUDIT=0` to turn it off). Personal data and secrets
are masked first. Read it with:

```
python -m scripts.audit_report                  # everything
python -m scripts.audit_report --session ID     # one session
```

It shows calls, refusals (gates saying no) with their reasons, truncations, exceptions, empty or
cut-off model replies, and tokens in, out and thinking per agent.

**Cost estimate.** The report also prices the recorded tokens, per model, from a price file you control
(`pricing.yaml`, git-ignored; copy `pricing.example.yaml`, which holds example Gemini and Nemotron entries with
their source and date). No price is hard-coded, and the section is labelled an estimate, not a bill. Unknown models
are listed as unpriced, never as zero. To answer "what would this have cost on Gemini?":

```
python -m scripts.audit_report --as-model gemini-2.5-flash
```

**Incidents.** `docs/incidents.md` records each real failure and names the test that guards it;
`tests/test_incidents.py` fails if a named test disappears. The incident that
started the smoke check (17 passing tests alongside a broken page) is fixed and guarded.

## Client documents (RAG)

The PM can answer questions from a client's **own documents** and cite where each fact came from.
`knowledge/<client>/` holds Markdown or text files (one folder per client; `acme-bakery/` is a fictional
demo). Two PM tools: `list_knowledge_collections` and `search_knowledge`.

- **Retrieval** (`shared/retrieval.py`): documents are split by heading, ranked with BM25 (keyword search,
  no model, explainable), and **nothing is returned when no passage covers the question**. A search that
  always returns "the closest passage" invites the model to make an answer up.
- **Cited answers:** each passage carries `acme-bakery/faq.md § Delivery`; the PM must cite every fact in
  square brackets, and must say so (and ask the client) when the documents do not cover it.
- **Documents are untrusted.** Every passage goes through the guardrails (hidden characters removed,
  instruction-like lines withheld, secrets masked; business contact details are kept, because there they
  are the answer). `knowledge/acme-bakery/supplier-notes.md` plants an order to approve the PRD, the classic
  indirect prompt injection; the PM reads the note, does not act on it, and tells the client part was set aside.
- **Confined to one client:** a search takes a collection name that must be an existing folder. A name like
  `../x` is refused, and one client's passages are never returned for another.
- **Measure it:** `python -m evals.rag_eval` prints recall@k, MRR and refusal accuracy over 22 golden
  questions (17 the documents answer, 5 they do not); the unit tests hold it to recall >= 0.9, MRR >= 0.85
  and refusal = 1.0. Three `adk eval` cases check the *model* searches first and ignores the planted order.
- **Known limit:** keyword search misses synonyms ("how far" vs "within 8 km") and long phrases, so the tool tells
  the model to use 1 to 3 plain words and to retry once. Embeddings fix this properly; on Google Cloud, Vertex AI
  RAG Engine replaces the retrieval step (and reads PDFs) while the citations, refusal and screening stay.

## Evaluate the agents (`adk eval`)

Unit tests check the rules in code. Evals check whether the **model** follows them, using
real model calls (they cost tokens and take a few minutes):

```powershell
.venv\Scripts\python -m evals.run                 # or: python -m evals.run explicit_approval
```

- `evals/build_evalset.py` generates the 10 cases (rerun it after schema changes:
  `python -m evals.build_evalset`). Starting states come from the real PM tools.
- Metrics (`evals/test_config.json`): `tool_trajectory_avg_score` (tool names, in order) and
  three custom metrics in `evals/metrics.py`: `key_args_match`, `publish_policy`,
  `simple_stack_policy`.

## Move to Gemini on Vertex AI

```powershell
winget install Google.CloudSDK                      # then open a new terminal
gcloud auth login
gcloud projects create <project-id>                 # then link billing in the console
gcloud config set project <project-id>
gcloud services enable aiplatform.googleapis.com
gcloud auth application-default login
```

In `pm_agent/.env`: set `GOOGLE_GENAI_USE_VERTEXAI=TRUE`, `GOOGLE_CLOUD_PROJECT`,
`GOOGLE_CLOUD_LOCATION`, a Gemini `PM_MODEL`, and comment out the NVIDIA one. Then run
`.venv\Scripts\python scripts\vertex_preflight.py` and rerun the evals.

## Test

```powershell
.venv\Scripts\python -m pytest -q
```

## Things to try as the client

- A vague, non-technical brief: *"I run 3 dental clinics and the phones never stop ringing."*
- A technical one: *"Move our on-prem Postgres + Java monolith to GCP, max 1 hour downtime."*
- Answer "you decide" to a question and see it become a labelled assumption.
- Ask it for a price — it should decline and redirect.
- Ask for a tech stack or architecture — it should hand over to `dev_agent`, which needs a
  published PRD before it publishes `architecture_v1.md`.
- Change the PRD after the architecture is published and see the Dev agent flag it as STALE.
- After approving the PRD and publishing the architecture: *"Get the acceptance tests
  written and locked"*, then *"Build it"*. Then ask the developer to change a test so it
  passes; QA must refuse and route it to the PM as a requirement change.
- Say "looks good" — it must not publish or approve until you say "publish the PRD" /
  "I approve the PRD".
- After it publishes, change your mind ("drop the mobile app") and see v2 + change log.
- Contradict yourself ("no cloud" … later "must auto-scale") and see whether it notices.
