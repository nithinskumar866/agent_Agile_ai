# Talking points by audience

The same project, three conversations. Do not mix them: executives want outcomes and risk,
engineers want mechanisms, security reviewers want evidence and limits.

## Executive or business sponsor (3 minutes)
- **Outcome:** a client request becomes approved requirements, then a design and tested software, with a
  person approving each step that matters.
- **Control:** the AI cannot approve or publish by itself. That is enforced in software, not
  requested politely in a prompt.
- **Evidence:** every action is logged. If something goes wrong we can show exactly what the agent
  did, and the same mistake cannot return unnoticed because each incident becomes a permanent test.
- **Honesty:** it is a working prototype. It found real problems with itself, and I can show you the
  one that is still open.
- **Ask:** which workflow would you trust it with first, and who would be the human approver?

## Engineer or architect (10 minutes)
- **Shape:** three Google ADK agents with hand-offs. Work product lives in session state, not in the
  chat, so compaction cannot lose a requirement.
- **Principle:** "the model decides content; code decides structure." IDs, completeness review,
  approval, versioning and the implementation gate are deterministic and unit-tested.
- **Tools:** MCP for the filesystem (writes only through a gated tool) and MCP Toolbox for a read-only
  database; Docker for running locked tests with no network.
- **Reliability:** it runs on a model with known quirks (list arguments sent as text, empty replies,
  reasoning eating the output budget). Each one is an incident with a test; the safety net caps
  results and context and logs every model call.
- **Evals:** `adk eval` scores the model's *attempts* (did it try to approve without words?), not only
  the final result, because the gate would hide a bad attempt.
- **Next:** swap the model to Gemini on Vertex AI with one config line, rerun the same evals, compare.

## Security reviewer (10 minutes, expect hard questions)
Lead with limits, then controls.
- **Limits first:** the injection screen is a filter; image text is not screened; names are not
  masked; it is not deployed; no identity layer of its own yet.
- **Controls:** explicit-approval gate in code; sandboxed writes (two boundaries: server and Python);
  container without network; read-only data access through named queries; masked audit log; caps.
- **Evidence:** `docs/incidents.md`, the test suite, `scripts/audit_report.py`.
- **Cloud design:** per-agent service accounts, VPC Service Controls, Model Armor, CMEK if required,
  Cloud Logging retention (`architecture.md` sections 3 and 4).

## Questions you will probably get
| Question | Honest answer |
|---|---|
| "Can the AI be tricked into approving?" | Not by text alone: approval is a code check on the client's own words. A hostile document can still waste a turn, which is why uploads are screened too. |
| "What if the model is wrong?" | Requirements and designs are versioned and shown to the client; tests are written from approved criteria before code; failures go back as requirement changes, never as weaker tests. I also showed you a case where tests passed and the product was broken, and what I am doing about it. |
| "Where does our data go?" | Today: to the model provider named in the config, nowhere else. On Google Cloud: pinned region, inside a perimeter. Uploaded personal data is masked before the model sees it. |
| "Why not just use a chatbot?" | A chatbot answers. This one is bound by checks: it cannot skip steps, and each step leaves evidence. |
| "How much does it cost?" | Measured per run in tokens (see `governance-and-cost.md`); I will give a price after checking the current rate card for the model you choose. |
| "Is it production ready?" | No. It is a prototype with real controls. The path to production is in the cloud design and the open incident. |
| "What would you do first for us?" | Pick one workflow, run the same evals on your data, keep a human approver, and measure refusals and retries for a fortnight. |
