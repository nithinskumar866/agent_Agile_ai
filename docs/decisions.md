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
