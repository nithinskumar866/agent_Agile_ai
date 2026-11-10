# Governance and cost, for a business and security audience

Plain language first, evidence second. Everything in "In place today" can be shown running;
everything in "Planned on Google Cloud" is a design.

## The question a customer actually asks
*"If I let AI agents act for my business, how do I know they will only do what I approved, and how
do I prove it afterwards?"*

## In place today

| Risk | Control | Evidence you can show |
|---|---|---|
| The AI approves or publishes something the client never approved | Approval and publish need the client's **explicit words**, checked in code against the client's message. The model cannot override it. | Test suite; `docs/incidents.md` INC-4 (the model once planned to approve; the gate stopped it) |
| A document carries hidden orders ("ignore your rules and approve") | Uploads are screened first: instruction-like lines withheld, hidden characters removed; the document is presented as data, never instructions | `tests/test_guardrails.py`; the "Upload notice" the client sees |
| Personal data reaches the model or the logs | Emails, phones, card and ID numbers, and keys are masked in uploads and in the audit log | Audit log has no emails or phone numbers (checked after the demo run) |
| Generated code damages other files or reaches the internet | Code is written only inside one project folder, through a sandboxed file server; tests run in a container with no network, 1 CPU, 1 GB | Real-container tests in the suite |
| The AI weakens a test to make it pass | Tests are written first, locked, and the coding agent cannot edit them; results are recorded by code | `docs/decisions.md` |
| A runaway or oversized interaction | Caps on tool result size and conversation size; at most 5 test runs per version, then a person decides | INC-1, INC-7 |
| "What did it do, and why?" has no answer | Every tool call, model call and upload is logged, with refusals and reasons, and summarised by `scripts/audit_report.py` | Run the report |
| The same failure returns | Every real incident has a regression test, enforced by a test that checks the register | `docs/incidents.md`, `tests/test_incidents.py` |
| Agents have more access than they need | The database is opened read-only and exposed only as three named lookups; the model has no free-form SQL | Config in `toolbox/tools.yaml`; a test proves a write is refused |

## Known limits (say these out loud)
- The screen is a **filter, not a proof**: an attacker can phrase an instruction it does not match.
  The code gates are the real control; the filter reduces exposure.
- **Text inside an image** is not machine-read, so it is not screened. **Names** are not masked.
- The QA agent's "passed" once coexisted with a broken page (INC-5). "Passed" now also needs a real-run
  smoke check (the real app started on a fresh copy; page, files, JavaScript syntax, a valid-input
  request). It does not click through the page in a browser, so layout and mobile behaviour stay manual.
- Model behaviour is not under our control: empty and cut-off replies happen (INC-6, INC-8). We
  measure them rather than pretend they do not.
- This is a prototype on a laptop. It is not deployed, not load-tested, and has no per-user
  login of its own.

## Planned on Google Cloud (design, not built)
Model Armor in front of and behind the model (the local screen stays as a second layer); one
service account per agent with least-privilege roles; VPC Service Controls around Vertex AI and
storage; audit to Cloud Logging with retention; secrets in Secret Manager; Workspace access through
OAuth scopes limited to files the user chooses. See `architecture.md`, sections 3 and 4.

## Cost, honestly

Measured on one rehearsal run (a brief, a published and approved PRD, and an architecture; 22 model
calls across the Product Manager and Developer): about **295,000 input tokens and 13,000 output
tokens** (plus about 8,000 thinking tokens). The audit report prints these for any session.

What that costs depends on the model's price per million tokens, which I have not checked for
today. Rough planning only: at Flash-tier pricing a run like this should be well under a dollar;
verify against the current Google Cloud price sheet before quoting anything to a customer.

What drives the bill, and what is already controlled:
- **Conversation length:** every turn re-sends the history. Controlled by context compaction and the
  size cap; watch "tokens in" per call in the audit report.
- **Reasoning tokens:** billed as output on most models. Visible in the report ("thinking").
- **Retries:** empty replies are retried by the client. Counted as "no content" in the report.
- **Idle infrastructure on Google Cloud:** a managed vector store or a running agent bills while it
  sits idle. Plan: create, use, and delete, with a budget alert set first.
