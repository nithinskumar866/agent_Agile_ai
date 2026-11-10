# Customer demo pack

| File | Use it for | Audience |
|---|---|---|
| [demo-script.md](demo-script.md) | A 12-minute live demo with exact prompts, what to say, and fallbacks | Any |
| [architecture.md](architecture.md) | Diagrams: the agents, one request through the controls, the Google Cloud mapping, tenancy and networking | Engineers, architects |
| [governance-and-cost.md](governance-and-cost.md) | Risks, controls and evidence, known limits, measured token use | Business and security |
| [talking-points.md](talking-points.md) | What to say to an executive, an engineer, a security reviewer, and likely questions | You |
| [../incidents.md](../incidents.md) | Every real failure, its fix and its regression test | Show it, do not hide it |
| `assets/sample-brief.pdf`, `assets/wireframe.png` | Demo uploads (all names and numbers invented). Rebuild with `python scripts/demo_assets.py` | Demo |

## How the pack maps to the Cloud AI Engineer role
| What the role asks for | Where it shows here |
|---|---|
| Build and orchestrate agents with ADK | Three ADK agents with hand-offs and shared state (`architecture.md` 1) |
| Securely connect LLMs to internal databases and productivity tools | MCP Toolbox, read-only, named lookups; Workspace is still to do |
| Multimodal solutions | PDF and image briefs (`demo-script.md` step 2); video and voice not yet |
| Security, governance and compliance | Gates, screening, masking, audit, sandboxes (`governance-and-cost.md`) |
| Monitor, debug and troubleshoot agent behaviour | Audit log and report, ADK trace, incident register with regression tests |
| Serverless, tenancy, secure networking | Design only (`architecture.md` 3 and 4); to be built on Google Cloud |
| Deploy and manage Gemini Enterprise | Not yet: this needs the Google Cloud phase |
| Technical presentations at different levels | This pack, with separate talking points per audience |

Status words used throughout: **built** (runs and has tests), **design** (planned, not built),
**open** (a known gap). Keep to them when you present.
