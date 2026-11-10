# Demo script: about 12 minutes

**Message in one sentence:** an agent team that turns a request into tested software, where every
risky step is controlled in code and every action leaves evidence.

**Audience:** a technical customer or interviewer. For a non-technical audience use the same
flow and skip the "Show" lines marked (technical).

## Before you start (10 minutes ahead)
1. Docker Desktop running (QA's tests use it).
2. Terminal 1, from the repo root: `python scripts/seed_company_db.py` then
   `.tools\toolbox.exe --config toolbox/tools.yaml --port 5000` (company memory).
3. Terminal 2: `adk web --port 8001 .`, open `http://localhost:8001`, pick **pm_agent**, **New session**.
4. Have `docs/demo/assets/sample-brief.pdf` ready to drag in, and a second terminal tab ready for
   `python -m scripts.audit_report`.
5. Delete `audit/audit.jsonl` so the report shows only the demo.
6. The model sometimes returns an empty reply (INC-6). If it does, just resend the message; say so.

## Walkthrough

### 1. Frame it (1 min)
**Say:** "A client usually sends a vague request. This team of three agents, a Product Manager, a
Developer and a QA Engineer, turns it into requirements, then tested software. What I want to show
is not that it writes code; it is how it is *controlled*."
**Show:** the architecture diagram, section 1 of `architecture.md`.

### 2. A brief with a trap in it (3 min)
**Do:** attach `sample-brief.pdf` and type: *"Hi, here is our brief. Please read it."*
**Say while it runs:** "This PDF has a customer's phone number and email in it, and one line
that tells the AI to approve the requirements without asking. A person reading it would notice."
**Show:** the PM's reply ends with an **"Upload notice (added by the system...)"**: one line was
withheld and the email and phone number were masked, and the PM carries on with real requirements
and questions. It never repeats the withheld text.
**Point out:** "The document is data, never instructions. And note the notice is written by code,
not by the model, so it appears even if the model forgets." Technical: open the audit report
(`python -m scripts.audit_report`) and show the UPLOADS row: *Withheld 1 line ... masked 1 email,
1 phone number.* The log itself contains no personal data.
**Be honest:** "This is a filter, not a guarantee. The real control is in step 4."

### 3. Memory from earlier projects (1 min)
**Say:** "Before asking questions the PM checks the company's own past work through MCP Toolbox."
**Show:** the `find_past_projects` call in the trace (it may try one word, then another) and the
reply "we've delivered an email sign-up page before ... it had three defects, so we'll want solid QA".
**Point out:** the agent never sees SQL; it has three named lookups, and the database is opened
read-only. A write attempt fails.

### 4. The approval gate (3 min)
Rehearsed live; this is what to expect.
1. **Type:** *"Looks good to me."* **Expect:** the PM is pleased but publishes and approves nothing.
2. **Type:** *"Answers: 1) We will host it on our own website. 2) No consent checkbox is needed.
   3) I will approve it myself. 4) We expect about 500 subscribers in the first year, and success
   means reaching 500 subscribers in year one. 5) A plain, neutral design is fine."*
   **Expect:** the PM records each answer against its question. (A success metric is needed
   before a PRD can be published; that is also enforced in code.)
3. **Type:** *"Please publish the PRD."* **Expect:** published as version 1, status *in review*.
4. **Type:** *"I approve the PRD."* **Expect:** approved as version 2, and the Developer starts the
   architecture by itself (about a minute).
**Say:** "Whether a PRD may be published or approved is decided by code reading the client's actual
words, not by the model's judgement. In testing the model once planned to approve on its own; the
gate stopped it. 'Looks good' is pleasant, but it is not approval."

### 5. Evidence (2 min)
**Show (technical):** the trace tab for the turn; then `python -m scripts.audit_report`:
tools called, how many were refused, model calls, tokens in and out.
**Point out TOP REJECTIONS:** in the rehearsal these were real gate decisions, for example the
Developer being told *"Leave 'auth' out instead of recording 'None'"* and *"Unknown requirement IDs"*.
Nothing crashed; the model read the refusal and fixed its next call.
**Say:** "If a customer asks 'why did the agent do that?', this is how I answer."

### 6. The honest bit (1 min)
**Show:** `docs/incidents.md`, incident INC-5.
**Say:** "All 17 acceptance tests passed and the page was still broken. I found that by running it.
Every incident here has a regression test, except this one, which I have marked OPEN with the fix
planned. I would rather show you the gap than hide it."

### 7. Close (30 s)
**Say:** "Today this runs locally. The design for Google Cloud is on the last page of the
architecture doc: Gemini on Vertex AI, Agent Engine, Model Armor, Cloud Logging, one service
account per agent. The controls carry over because they are in code, not in the model."

## If something goes wrong
| Symptom | Do |
|---|---|
| Empty reply or "no content" | Resend the message. Say it is a known model quirk, measured in the audit report. |
| "Start a new session" message | The conversation got too large; open a new session (INC-7). |
| No "we built something similar" line | Toolbox is not running (the PM carries on without it). Start terminal 1. |
| QA tests will not run | Docker Desktop is not running. Start it, or skip step QA in the demo. |

## What not to claim
- Not "production ready": it is a working prototype with real controls.
- The cloud mapping is a design; nothing is deployed to Google Cloud yet.
- Text inside an uploaded *image* is not screened, and names are not masked.
- The full Developer-and-QA build takes several minutes and can need a retry; show a finished
  project in `workspace/email-signup/` and its QA report instead of building live.
