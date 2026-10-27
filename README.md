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
└── runner.py           # The Docker test runner (the only code that executes generated code)

tests/                  # 250 tests, no model calls or API key. 3 start the real MCP server
                        #   (skip: AGENTCORP_SKIP_MCP_TESTS=1), 2 run real Docker containers
                        #   (skip: AGENTCORP_SKIP_DOCKER_TESTS=1)
shared/models.py        # Model routing: Gemini, or NVIDIA via LiteLLM
shared/spec.py          # RequirementsSpec: the handoff contract every agent reads
shared/policy.py        # What wording counts as "publish" / "approve"
shared/testsuite.py     # TestSuite: the locked tests, and the rules the Dev agent obeys
evals/                  # adk eval suite: cases, config, custom metrics, runner
scripts/vertex_preflight.py  # Checks the Vertex AI setup step by step
docs/decisions.md       # Architecture decision log
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
disk changes nothing. Only the Python app folder is tested; frontend code is not executed.

## Evaluate the agents (`adk eval`)

Unit tests check the rules in code. Evals check whether the **model** follows them, using
real model calls (they cost tokens and take a few minutes):

```powershell
.venv\Scripts\python -m evals.run                 # or: python -m evals.run explicit_approval
```

- `evals/build_evalset.py` generates the 7 cases (rerun it after schema changes:
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
