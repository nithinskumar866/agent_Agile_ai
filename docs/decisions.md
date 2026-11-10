# Decision log

## 2026-10-12 — PM agent foundation

**One agent, text only, human client.** The foundation is a single `LlmAgent`. The client is
played by a person in `adk web`. A reviewer agent, a simulated client, and attachments come later.

**The spec lives in session state, not in the conversation.** Tools write a structured
`RequirementsSpec` to `state["spec"]`. The model's context window holds the *conversation*;
state holds the *work product*. Long chats can be compacted or summarised without any risk
of losing a requirement.

**Instruction is a function, not a string.** `prompt.instruction(ctx)` appends a one-line
status (engagement, version, counts, open question IDs) on every turn, so the agent keeps
its place even after older turns are compacted.

**LLM decides content; code decides structure.** IDs, the completeness review, the approval
gate, versioning, and PRD rendering are deterministic (`spec_logic.py`) and unit-tested.
The model chooses what to ask and what to write down.

**Engagement types are data.** `playbooks.yaml` lists types, discovery areas, and expected
non-functional categories. A new kind of service is a YAML edit. Unknown types fall back
to `other`, so no request is unsupported.

**Traceability via `source`.** Every item is `client`, `inferred`, or `assumption`. Approval
is refused while anything is still `inferred` or a question is open.

**Strict structured output is not used here.** The spec is built incrementally through tools
rather than with `output_schema`, because the agent needs tools and a multi-turn conversation.
Pydantic validation still happens on every tool call.

**Context compaction enabled** (`EventsCompactionConfig`, token-triggered; experimental in
ADK 2.11). Safe because of the state-first design above.

## 2026-10-14 — Dev agent & hierarchical multi-agent collaboration

**Hierarchical delegation with shared state.** The PM agent (`product_manager`) remains the
primary client-facing agent, with the Developer agent (`dev_agent`) attached via ADK's native
`sub_agents` hierarchy.

**Work product lives in session state.** The Dev agent reads `state["spec"]` created by the PM
agent, ensuring zero loss of requirements context during handoff. Technical design, stack
choices, components, and project scaffolding are stored in `state["tech_design"]` and published
as `architecture_v1.md` artifacts.

## 2026-10-22 — Model routing, Dev agent fixes, stricter approval

**Model routing in `shared/models.py`.** A model name without `/` goes to Gemini; with `/` it
goes to NVIDIA's OpenAI-compatible endpoint through LiteLLM (dev-tier fallback for Gemini
rate limits). `DEV_MODEL` defaults to `PM_MODEL`. Run standalone, `dev_agent` falls back to
`pm_agent/.env` for keys.

**Architecture is tied to a PRD version.** `TechDesign.based_on_prd_version` is set on
publish; publishing needs a published PRD, and the Dev status line flags the design STALE
when the PRD has moved on. Components carry `addresses` (FR-/NFR- IDs, validated), and the
traceability section maps each requirement to the components that deliver it.

**Dev design is revisable.** Components (by name) and scaffold entries (by path) upsert like
the tech stack; `remove_design_item` removes any of them.

**Approval covers every sourced section except risks.** Inferred stakeholders, metrics,
constraints, and exclusions now block approval too. Risks are exempt: they are the PM's own
reading by nature. `update_item` covers all editable fields, and milestone `includes` are
validated when written, not only at review.

**Standalone `dev_agent` app uses a clone**, because the original is the PM's sub-agent and an
agent can have only one parent.

**Publishing and approval need explicit client words, checked in code.** `publish_prd` reads
the client message that started the turn (`tool_context.user_content`). A draft publish needs
an explicit request ("please publish the PRD"); `mark_approved=True` needs an explicit approval
("I approve the PRD"). Praise, agreement, review requests, readiness questions, conditional or
negated wording, and requests to publish the architect's work are refused, and "do not publish"
blocks publishing outright. The matcher (`spec_logic.is_explicit_approval` /
`is_explicit_publish_request`) is deliberately strict: a false refusal costs one confirmation
question, a false approval signs off a spec the client never approved. The PM now asks before
publishing instead of publishing proactively. Versioning and change history are unchanged.

**Simplest stack by default, checked in code.** Heavy infrastructure (caches, queues, brokers,
Kubernetes, search engines, microservices, CDNs) is refused unless the rationale or
`addresses` cites a real FR-/NFR-/C- ID. "None" placeholder stack entries are refused; the
agent leaves the layer out and explains the omission in the publish summary.

## 2026-10-23 — Phase 2.5: Dev agent writes code through the filesystem MCP server

**Official server, pinned.** `@modelcontextprotocol/server-filesystem@2026.8.31`, started with
`npx` over stdio. The newest release (2.0.0) requires Node 24; 2026.8.31 runs on Node 22.
Python side: `google-adk[mcp]` (MCP SDK 2.3.0).

**Two boundaries around `workspace/<project>/`.** Each server process gets exactly one allowed
directory, so the server itself refuses anything outside it, including through symlinks. Before
any call, Python rejects absolute paths, drive letters, UNC paths, `..`, `~`, `:`, control
characters, Windows device names, and anything that resolves outside the folder.

**The model reads, a gated tool writes.** The model sees only `list_directory`,
`directory_tree`, `read_text_file`, and `get_file_info`, through `McpToolset(tool_filter=...)`,
wrapped in `ProjectWorkspaceToolset`, which scopes the server to the session's project folder.
Writes happen only inside `implement_scaffold`, whose client can call only `create_directory`,
`write_file`, and `read_text_file`. Giving the model `write_file` directly would let it skip the
prerequisite and the log. No delete, move, edit, shell, or execution tool is reachable.

**Implementation is gated, verified, and logged.** It needs a published architecture with no
unpublished design edits and an unchanged PRD. Only scaffold paths are accepted, and one bad
path rejects the whole batch before anything is written. Every file is read back and compared.
The first failure stops the run, and the result lists written, failed, and not-attempted files.
Every attempt, successful or failed, is added to the change log. An MCP startup failure is
reported, never treated as success.

**No execution in this phase.** No code runs, nothing installs, no tests execute. Running
model-written code safely needs a proper sandbox (added later by the QA agent; see 2026-10-27).

## 2026-10-26 — Phase 3A: `adk eval` suite; the spec contract moves to `shared/`

**Unit tests check the code; evals check the model.** `evals/` holds 7 cases generated by
`build_evalset.py`, whose starting states come from running the real PM tools, so they cannot
drift from the schema. Metrics: built-in `tool_trajectory_avg_score` (`IN_ORDER`,
`ignore_args`, because free-text args differ every run) plus three custom metrics:
`key_args_match` (only the args that matter, as a subset), `publish_policy`, and
`simple_stack_policy`. The policy metrics score what the model *attempted*: the code guardrail
still blocks a bad publish, but the attempt is the model misreading the client.

**`RequirementsSpec` and the authorization policy live in `shared/`.** `adk eval` imports
`pm_agent/__init__.py` under the module name `agent`. Anything that then imports `pm_agent`
builds a second agent tree, and ADK rejects it ("already has a parent"). That broke the custom
metrics, and it would have broken the Dev agent's spec reads mid-eval. The handoff contract
(`shared/spec.py`) and the wording policy (`shared/policy.py`) are now shared modules;
`pm_agent/schema.py` and `spec_logic` re-export them. A regression test loads the agent the way
`adk eval` does.

**A metric that cannot run must fail the eval.** ADK reports a crashed custom metric as
`NOT_EVALUATED` and still marks the case PASSED. `python -m evals.run` treats any
`NOT_EVALUATED` metric as a failure. It also sets `PYTHONPATH`, which `adk eval`, unlike
`adk web`, does not.

**Vertex AI is a config switch.** `scripts/vertex_preflight.py` checks env, application-default
credentials, project, and one real Gemini call on Vertex AI, and names the step to fix.

## 2026-10-27 — QA agent: locked acceptance tests, run in Docker

**Tests come from the client, through the PRD.** The QA agent tests only the acceptance
criteria of 'must' items, each with an ID (`FR-1.AC2`). The suite can be locked only when the
PRD is *approved* and every criterion has a test or a recorded manual check. A 'must' item
without criteria blocks the lock and goes back to the PM: QA does not invent conditions.

**Tests before code, and the coder cannot touch them.** `implement_scaffold` refuses until a
suite is locked for the current PRD version, and refuses `qa_tests/` and any `conftest.py`.
A locked suite changes only when the PRD version changes. So "the test is wrong" becomes a
requirement change the client approves, never an edit by whoever is failing it. This blocks
the reward-hacking shortcut of weakening a test to make it pass.

**Results are recorded by code, not reported by the model.** `run_tests` is one fixed tool
(given to QA and Dev). It grades every locked case from pytest's JUnit XML: a test that was
skipped or never collected does not pass. Every run is stored in `state["test_suite"]`.
At most 5 runs per suite version, then the verdict is `escalated` and a person decides.

**The runner trusts nothing on disk.** Tests run from the locked copies in state, written over
a throwaway copy of the project; pytest gets its own `-c` config, `--confcutdir`, plugin
autoload off, and `python -P`. Real-Docker tests prove a weakened test file, a skip-all
`conftest.py`, and a deselect-all `pytest.ini` all fail to turn broken code green. Limits:
the container has no network, 1 CPU, 1 GB, 256 processes, no capabilities, read-only root.
Dependencies install in a separate container, wheels only, so no package build script runs.
Not defended: deliberately hostile code in the same process as pytest could fake a result.

**Two small agents, not a pipeline.** QA is a peer sub-agent of the PM next to Dev. A
LoopAgent would hard-wire coder -> runner -> tester; here the order is enforced by the
gates instead, and the client decides when to build. On Google Cloud the same runner
contract maps onto the Agent Engine code-execution sandbox.

**Model quirk.** Nemotron sometimes sends a list argument as a JSON string; list-taking
tools now decode it (`shared/args.py`) instead of refusing the batch.

## 2026-10-30 — Company memory through MCP Toolbox for Databases

**The agent gets named lookups, not SQL.** Toolbox v1.15.0 runs as its own server over
`data/company.db`. `toolbox/tools.yaml` defines three `sqlite-sql` tools with fixed, parameterized
SELECTs, grouped as the `company_memory` toolset. There is no `sqlite-execute-sql` and no template
parameter, so the model cannot write its own query. Tests assert both rules.

**Read-only is enforced by the database, not by trust.** The source opens
`file:data/company.db?mode=ro`. A test sends an INSERT through a real Toolbox and checks that it
fails with "readonly database" and leaves no row. Only `scripts/seed_company_db.py` writes.

**Memory is a convenience, never a dependency.** `OptionalToolset` turns any load failure
(server down, wrong URL) into "no tools this turn" with one warning. The PM prompt says past
projects are never the client's requirements: a detail enters the spec only after the client
confirms it, with source 'client'.

**Seeded from a real run.** The one `delivered` project and its four lessons come from the email
sign-up build, including the three defects that passed all 17 tests (static files not served,
a JavaScript syntax error, the table never created). `sample` rows are labelled as invented.

**Windows detail.** `with sqlite3.connect()` commits but does not close, which locked the file
for the next unlink; the seed script and tests close explicitly.

## 2026-11-10 — Safety net, audit trail, upload guardrails, incident register

**One safety net on every agent, through ADK callbacks.** Model and tool callbacks (`shared/safety.py`)
cap tool results, refuse to call the model on an oversized conversation, turn unexpected tool
exceptions into short errors, and write the audit log. They exist because two 2 MB tool errors once
made a session permanently unusable.

**The audit log is evidence, not a debug print.** One JSON line per tool call, model call and
upload; personal data and secrets masked; values cut *before* masking, because masking a multi-megabyte
string with an unbounded regex took minutes (a test caught it). `scripts/audit_report.py` summarises it,
including refusals by reason and empty or cut-off model replies, so model quality becomes a number.

**Uploads are untrusted and rewritten before the model sees them.** PDFs and text become screened,
wrapped extracted text; images pass through after real-bytes, size and count checks. The screen
withholds instruction-like lines, masks personal data and secrets, and removes hidden characters. It
is a filter, not a proof. The gates in tool code remain the control. Typed messages are not scanned,
because a client may legitimately type an email address.

**The client is told by code, not by the model.** A live rehearsal showed the model mentioned the
withheld line in one run and not in the next. The system now appends an "Upload notice" to the first
real reply, once per file per session. A control that depends on the model remembering is not a control.

**Quarantine, not blocking.** Risky text is withheld and the rest of the document is used; blocking
whole documents would reject most real briefs. Picture-derived requirements are recorded as
`inferred`, so the existing approval gate forces the client to confirm them.

**Incidents become tests.** `docs/incidents.md` lists real failures with a regression test each;
`tests/test_incidents.py` fails if a named test is missing, or if an incident has no test and is not
marked OPEN. INC-5 (17 tests passed on a broken page) was OPEN until the smoke check below.

**Not done on purpose:** machine-reading text inside images, masking names (needs NER), A2A.

## 2026-11-10 — QA real-run smoke check (closes INC-5)

**"Passed" now means the real app worked, not only that tests passed.** 17 of 17 acceptance tests once
passed on a page with three defects the tests could not see. `run_tests` now adds a third sandbox
container: the actual app is started on a fresh copy of the project (no database, no test setup) and
used like a visitor. A run is `passed` only if the acceptance tests and the smoke check both pass.

**Code decides what is checked; the model supplies only what it can know.** Fixed in code
(`qa_agent/smoke_runner.py`): the app starts; `GET /` is not a 5xx; every local stylesheet, script and
image the page links to is served; every plain GET endpoint the app's OpenAPI document lists is not a
5xx. Supplied by the QA agent and locked with the tests: smoke requests with valid input (for example
`POST /signup`), because only QA knows what valid input is. A web app (FastAPI or Flask in the stack)
cannot be locked without at least one, so the coder cannot loosen the check.

**JavaScript is syntax-checked on the host with `node --check`.** The sandbox image has no Node, and
`--check` only parses. If Node is missing the report says JavaScript was NOT checked, rather than
passing silently.

**Same sandbox limits as the tests** (no network except the container's own loopback, 1 CPU, 1 GB, no
capabilities, read-only root). The smoke script is standard-library only and is copied into the container
from the repository, never taken from the project.

**Limits, stated in the report:** no browser, so layout, mobile behaviour and what the script does at
runtime remain manual checks; only FastAPI and Flask apps can be started; the smoke requests are only as
good as the QA agent's reading of the criteria.

## 2026-11-10 — Client documents (RAG), built locally first

**Retrieval is separable from the rest.** `shared/retrieval.py` (chunk by heading, rank, refuse) and
`shared/knowledge.py` (collections, citations, screening) are the parts that matter; the ranking function is
the only piece Vertex AI RAG Engine replaces. Both embedding models tried on the NVIDIA key were retired
(HTTP 410), so local retrieval is BM25: no model, no download, and every ranking is explainable.

**Refusing is a feature, tested.** A question the documents do not answer must return nothing. Measured
on 22 golden questions: recall@3 1.00, MRR 0.96, refusal 1.00. The evaluation found a real flaw on its first
run: the client's own name sits in every title, so "how many employees does Acme Bakery have" matched every
document. Query words found in 60% or more of the passages are now ignored when judging coverage.

**Documents are untrusted input, like uploads.** Passages pass through the guardrails with personal-data
masking off (an FAQ's `orders@shop.example` is the answer, not a leak) but injection, hidden text and
secrets always handled. Reviewing this exposed an evasion: a phrase split across line breaks slipped past
the line-by-line check. Paragraphs are now read flattened as well as line by line; the line result wins when it
finds everything, so a list keeps its other items.

**Tenancy by folder.** One folder per client; a search names its collection, validated against the folders
that exist, so no path can leave the knowledge root. On Google Cloud this becomes one corpus per tenant with IAM.

**Keyword search has a known blind spot,** seen live: "delivery how far delivery area distance" missed a
passage that says "within 8 km". Mitigated by telling the model to use short plain keywords and to retry once
(INC-12); fixed properly by embeddings in the cloud phase.

## 2026-11-10 — Packaging for serverless (container, not yet deployed)

**One image, two modes.** The same image serves the HTTP API (`ADK_MODE=api`, for a deployment) or the API plus web
UI (`web`, for demos). It listens on `$PORT`, runs as a non-root user, answers readiness on `/list-apps`, and
carries no secret: keys are environment variables at run time. A runtime requirements file keeps pytest and the
eval extra out of the image, and a test keeps its `google-adk` pin identical to development.

**Verified by running it, not by reading it.** Built, started and checked: non-root (uid 10001), Node 22 present, no
tests, docs or `.git` inside, healthy, RAG answers from inside the container, and in compose the agent container
reaches the Toolbox container by service name and runs a real query. The build found a gap worth keeping: the
example env file was being copied in; it is now excluded.

**The QA test runner cannot run in the container,** because it needs a Docker engine. It fails cleanly ("Docker is not
installed"). On Google Cloud it maps to the Agent Engine code-execution sandbox or a Cloud Run job. Docker-in-Docker
or mounting the host socket would have worked but hands the container control of the host, which defeats the
sandbox it exists to provide.

**Not deployed.** The Cloud Run command in the README is a design. Sessions are local SQLite files inside the
container, so a real deployment needs a session service (Agent Engine sessions or Cloud SQL).

## 2026-11-10 — Cost estimate in the audit report

**No price lives in code.** Prices change and differ by region and tier, so a number the code "knows" goes stale
silently. `shared/pricing.py` reads a price file you own (`pricing.yaml`, else the shipped `pricing.example.yaml`
with each example's source and date), matches the model name by longest substring, and bills thinking tokens as
output unless the entry says otherwise. The report labels the result an estimate and names the file and its date.

**Unpriced is not zero.** A model with no entry is listed as unpriced and left out of the total; a model that is
free is priced at zero. The report says "Nothing could be priced yet" rather than printing a reassuring 0.

**The model name is now recorded on every audited model call,** which is what makes the pricing possible. Older
log lines without it show as "(model not recorded)".

**`--as-model` is the useful part.** It prices the recorded tokens as if they had run on another model, so the
Nemotron-to-Gemini decision and the customer's first question, what it costs, are answered from measured usage
and not guessed. A full day of live testing came to about $0.34 on Gemini 2.5 Flash at the example list prices.
