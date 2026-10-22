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
