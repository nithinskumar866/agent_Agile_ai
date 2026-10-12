# AgentCorp — a technology services company run by Google ADK agents

Learning project for Google's Agent Development Kit. Each department of a services company
becomes an agent. **Current stage: foundation — the Product Manager agent only.**

## The PM agent

Talks to a client (you, in `adk web`) about any kind of engagement — new build, migration,
integration, data/AI, consulting, support — and turns it into a structured, versioned
requirements spec, published as a Markdown PRD.

```
pm_agent/
├── agent.py         # LlmAgent + App (with context compaction)
├── prompt.py        # role + working method; live spec status injected every turn
├── tools.py         # 14 tools the model calls — thin wrappers, return {"error"} on bad input
├── spec_logic.py    # deterministic core: IDs, review checks, approval gate, PRD rendering
├── schema.py        # RequirementsSpec — the handoff contract for future agents
├── playbooks.yaml   # engagement types + what to ask for each (data, not code)
└── config.py        # model, limits — all from environment variables
tests/               # deterministic tests, no API key needed
docs/decisions.md    # why things are the way they are
```

## Run it

```powershell
cd "agent builder"
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

## Test

```powershell
.venv\Scripts\python -m pytest -q
```

## Things to try as the client

- A vague, non-technical brief: *"I run 3 dental clinics and the phones never stop ringing."*
- A technical one: *"Move our on-prem Postgres + Java monolith to GCP, max 1 hour downtime."*
- Answer "you decide" to a question and see it become a labelled assumption.
- Ask it for a price or a tech stack — it should decline and redirect.
- After it publishes, change your mind ("drop the mobile app") and see v2 + change log.
- Contradict yourself ("no cloud" … later "must auto-scale") and see whether it notices.
