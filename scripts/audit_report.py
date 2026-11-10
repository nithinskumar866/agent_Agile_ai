"""Summarise the audit trail: what the agents called, what was refused, what the models did.

    python -m scripts.audit_report                  # everything in audit/audit.jsonl
    python -m scripts.audit_report --session ID     # one session
    python -m scripts.audit_report --file other.jsonl
    python -m scripts.audit_report --pricing my-prices.yaml   # price the tokens (default: pricing.yaml, else the example)
    python -m scripts.audit_report --as-model gemini-2.5-flash   # what would these same tokens cost on another model?

"Rejected" means a tool returned an error: a gate said no (not approved, tests not locked, a
bad path...) or the input was invalid. Both are worth reading; neither crashed anything.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shared import audit, pricing as pricing_module  # noqa: E402


def _table(headers: list[str], rows: list[list]) -> str:
    cells = [[str(c) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)] if cells else [len(h) for h in headers]
    line = lambda r: "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()
    return "\n".join([line(headers), line(["-" * w for w in widths]), *(line(r) for r in cells)])


def _money(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.4f}" if amount < 1 else f"{currency} {amount:,.2f}"


def _cost_section(models: list[dict], session_count: int, pricing: "pricing_module.Pricing | None",
                  as_model: "str | None" = None) -> list[str]:
    """Tokens priced from a price file the user controls. Always labelled an estimate."""
    if pricing is None:
        return ["COST (estimate)", "  No price file: showing tokens only. Copy pricing.example.yaml to pricing.yaml "
                "and fill in your model's prices.", ""]
    by_model: dict[str, list[dict]] = defaultdict(list)
    for e in models:
        by_model[as_model or e.get("model") or "(model not recorded)"].append(e)
    rows, total, unpriced = [], 0.0, []
    for model, evs in sorted(by_model.items()):
        tin, tout, think = (sum(e.get(k) or 0 for e in evs) for k in ("tokens_in", "tokens_out", "tokens_thinking"))
        rate = pricing.rate_for(model)
        if rate is None:
            unpriced.append(model)
            rows.append([model, len(evs), tin, tout, think, "-", "unpriced"])
            continue
        cost = rate.cost(tin, tout, think)
        total += cost
        rows.append([model, len(evs), tin, tout, think, f"{rate.input_per_million:g} / {rate.output_per_million:g}",
                     _money(cost, pricing.currency)])
    title = f"COST (estimate, as if every call had run on {as_model})" if as_model else "COST (estimate)"
    out = [title, _table(["model", "calls", "tokens in", "tokens out", "thinking",
                                     f"{pricing.currency}/M in / out", "cost"], rows)]
    if len(unpriced) < len(by_model):
        out.append(f"  Total (priced models): {_money(total, pricing.currency)}"
                   + (f"   |   per session: {_money(total / session_count, pricing.currency)} over {session_count} sessions"
                      if session_count > 1 else ""))
    else:
        out.append("  Nothing could be priced yet.")
    if unpriced:
        out.append(f"  Not priced (add to {pricing.path.name}): {', '.join(unpriced)}")
    out.append(f"  Estimate from {pricing.path.name} (as of {pricing.as_of or 'unknown date'}; {pricing.source or 'no source given'}). "
               "Thinking tokens are billed as output unless the entry says otherwise. This is not a bill.")
    return out + [""]


def summarise(events: list[dict], pricing: "pricing_module.Pricing | None" = None, as_model: "str | None" = None) -> str:
    if not events:
        return "No audit events yet."
    tools = [e for e in events if e["event"] == "tool"]
    models = [e for e in events if e["event"] == "model"]
    uploads = [e for e in events if e["event"] == "upload"]
    out = [f"{len(events)} events, {len({e.get('session') for e in events} - {None})} session(s), "
           f"{events[0]['ts'][:19]} to {events[-1]['ts'][:19]} UTC", ""]

    per_tool: dict[str, list[dict]] = defaultdict(list)
    for e in tools:
        per_tool[e.get("tool", "?")].append(e)
    rows = []
    for name, evs in sorted(per_tool.items(), key=lambda kv: -len(kv[1])):
        outcomes, ms = Counter(e["outcome"] for e in evs), [e["ms"] for e in evs if e.get("ms") is not None]
        rows.append([name, len(evs), outcomes["rejected"], outcomes["truncated"], outcomes["exception"],
                     f"{sum(ms) // len(ms)} ms" if ms else "-"])
    out += ["TOOLS", _table(["tool", "calls", "rejected", "truncated", "exceptions", "avg time"], rows), ""]

    reasons = Counter((e.get("tool", "?"), (e.get("message") or "")[:90]) for e in tools if e["outcome"] == "rejected")
    if reasons:
        out += ["TOP REJECTIONS", _table(["tool", "count", "reason"], [[t, n, m] for (t, m), n in reasons.most_common(5)]), ""]

    per_agent: dict[str, list[dict]] = defaultdict(list)
    for e in models:
        per_agent[e.get("agent", "?")].append(e)
    rows = []
    for name, evs in sorted(per_agent.items()):
        c = Counter(e["outcome"] for e in evs)
        rows.append([name, len(evs), c["ok"], c["no_content"], c["max_tokens"],
                     sum(n for k, n in c.items() if k.startswith("error") or k == "exception"),
                     sum(e.get("tokens_in") or 0 for e in evs), sum(e.get("tokens_out") or 0 for e in evs),
                     sum(e.get("tokens_thinking") or 0 for e in evs)])
    out += ["MODEL CALLS", _table(["agent", "calls", "ok", "no content", "max tokens", "errors", "tokens in", "tokens out",
                                   "thinking"], rows), ""]

    if models:
        sessions = len({e.get("session") for e in models} - {None})
        out += _cost_section(models, sessions, pricing, as_model)

    if uploads:
        out += ["UPLOADS", _table(["file", "kind", "guardrail / note"], [
            [e.get("file", "?"), e.get("kind", "?"), e.get("guardrail") or e.get("note") or "clean"] for e in uploads]), ""]

    flags = []
    if n := sum(1 for e in tools if e["outcome"] == "exception"):
        flags.append(f"{n} tool call(s) raised an unexpected exception")
    if n := sum(1 for e in tools if e["outcome"] == "truncated"):
        flags.append(f"{n} tool result(s) were cut for size")
    if n := sum(1 for e in models if e["outcome"] == "no_content"):
        flags.append(f"{n} model call(s) returned no usable content (thought-only replies)")
    if n := sum(1 for e in models if e["outcome"] == "max_tokens"):
        flags.append(f"{n} model call(s) hit the output limit")
    if n := sum(1 for e in models if e["outcome"] == "blocked_context_too_large"):
        flags.append(f"{n} model call(s) were refused because the conversation was too large")
    out += ["LOOK AT", *("  - " + f for f in flags)] if flags else ["LOOK AT", "  nothing flagged"]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, help="audit file (default: audit/audit.jsonl)")
    parser.add_argument("--session", help="only this session id")
    parser.add_argument("--pricing", type=Path, help="price file (default: pricing.yaml, else pricing.example.yaml)")
    parser.add_argument("--as-model", help="price every recorded call as if it had run on this model, e.g. gemini-2.5-flash")
    args = parser.parse_args(argv)
    events = audit.read_events(args.file)
    if args.session:
        events = [e for e in events if e.get("session") == args.session]
    try:
        prices = pricing_module.load(args.pricing)
    except pricing_module.PricingError as exc:
        print(f"Note: costs not shown. {exc}\n", file=sys.stderr)
        prices = None
    print(summarise(events, prices, args.as_model))
    return 0


if __name__ == "__main__":
    sys.exit(main())
