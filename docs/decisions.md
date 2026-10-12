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
