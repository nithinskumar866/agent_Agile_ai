"""Deterministic spec operations — no LLM, fully unit-tested.

The LLM decides *what* goes into the spec; this module decides *how* it is
stored, identified, checked, and rendered. Anything that must be reproducible
(IDs, completeness checks, the approval gate, the PRD layout) lives here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, MutableMapping

import yaml

from shared.policy import forbids_publishing, is_explicit_approval, is_explicit_publish_request  # noqa: F401
from shared.spec import ID_PREFIXES, RequirementsSpec, load_spec, save_spec  # noqa: F401

PLAYBOOK_PATH = Path(__file__).with_name("playbooks.yaml")


# ── IDs and change tracking ──────────────────────────────────────────────────

def next_id(spec: RequirementsSpec, section: str) -> str:
    prefix = ID_PREFIXES[section]
    n = spec.meta.counters.get(prefix, 0) + 1
    spec.meta.counters[prefix] = n
    return f"{prefix}-{n}"


def find_item(spec: RequirementsSpec, item_id: str) -> tuple[str, int] | None:
    """Return (section, index) for an item ID, or None."""
    for section in ID_PREFIXES:
        for i, item in enumerate(getattr(spec, section)):
            if item.id == item_id:
                return section, i
    return None


def record_change(spec: RequirementsSpec, note: str) -> None:
    spec.meta.pending_changes.append(note)
    # Any edit to an approved spec reopens it — approval covers a version, not the future.
    if spec.meta.status == "approved":
        spec.meta.status = "in_review"


# ── playbooks ────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _playbooks() -> dict[str, Any]:
    return yaml.safe_load(PLAYBOOK_PATH.read_text(encoding="utf-8"))


def engagement_types() -> dict[str, str]:
    return {k: v["description"] for k, v in _playbooks()["engagement_types"].items()}


def playbook(engagement_type: str) -> dict[str, Any]:
    """Merge the common playbook with the type-specific one; unknown types fall back to `other`."""
    data = _playbooks()
    types = data["engagement_types"]
    key = engagement_type if engagement_type in types else "other"
    specific, common = types[key], data["common"]
    return {
        "engagement_type": key,
        "description": specific["description"],
        "discovery_areas": common["discovery_areas"] + specific["discovery_areas"],
        "expected_nfr_categories": sorted(
            set(common["expected_nfr_categories"]) | set(specific["expected_nfr_categories"])
        ),
    }


# ── review ───────────────────────────────────────────────────────────────────

def review(spec: RequirementsSpec) -> dict[str, Any]:
    """Structural completeness check. Returns blocking gaps and softer warnings.

    Deliberately structural only: it can tell that a must-have has no acceptance
    criteria, not whether the criteria are any good — that judgement is the LLM's.
    """
    gaps: list[str] = []
    warnings: list[str] = []

    if spec.engagement is None:
        gaps.append("Engagement type/summary not set.")
    if not spec.goals:
        gaps.append("No goals recorded.")
    if not spec.success_metrics:
        gaps.append("No success metrics — how will the client judge success?")
    if not spec.in_scope:
        gaps.append("Nothing marked in scope.")
    if not spec.out_of_scope:
        warnings.append("Nothing marked out of scope — unstated exclusions cause disputes later.")
    if not spec.functional_requirements and not spec.user_stories:
        gaps.append("No functional requirements or user stories.")
    if not spec.stakeholders:
        warnings.append("No stakeholders recorded — who signs off?")

    for req in spec.functional_requirements:
        if req.priority == "must" and not req.acceptance_criteria:
            gaps.append(f"{req.id} is a must-have with no acceptance criteria.")
    for story in spec.user_stories:
        if story.priority == "must" and not story.acceptance_criteria:
            gaps.append(f"{story.id} is a must-have with no acceptance criteria.")

    if spec.engagement is not None:
        expected = set(playbook(spec.engagement.type)["expected_nfr_categories"])
        covered = {r.category.strip().lower() for r in spec.non_functional_requirements}
        for category in sorted(expected - covered):
            warnings.append(f"No non-functional requirement for '{category}' (expected for this engagement type).")

    known_ids = {item.id for s in ("functional_requirements", "user_stories") for item in getattr(spec, s)}
    for m in spec.milestones:
        for ref in m.includes:
            if ref not in known_ids:
                gaps.append(f"Milestone {m.id} references unknown item {ref}.")

    open_qs = [q.id for q in spec.open_questions if q.answer is None]
    if open_qs:
        warnings.append(f"Open questions still unanswered: {', '.join(open_qs)}.")

    # Every sourced section counts, except risks: a risk is the PM's own reading by nature.
    unconfirmed = [
        item.id
        for section in ID_PREFIXES
        if section != "risks"
        for item in getattr(spec, section)
        if getattr(item, "source", None) == "inferred"
    ]
    if unconfirmed:
        warnings.append(f"Inferred, not yet confirmed by the client: {', '.join(unconfirmed)}.")

    return {
        "ready_for_review": not gaps,
        "ready_for_approval": not gaps and not open_qs and not unconfirmed,
        "gaps": gaps,
        "warnings": warnings,
        "counts": {s: len(getattr(spec, s)) for s in ID_PREFIXES},
    }


def status_line(spec: RequirementsSpec) -> str:
    """One compact line re-injected into every prompt so the agent never loses its place."""
    if spec.engagement is None:
        return "No spec started yet."
    open_qs = [q.id for q in spec.open_questions if q.answer is None]
    return (
        f"Engagement: {spec.engagement.type} / {spec.engagement.domain} — {spec.engagement.summary} | "
        f"version {spec.meta.version}, status {spec.meta.status} | "
        f"{len(spec.functional_requirements)} FR, {len(spec.non_functional_requirements)} NFR, "
        f"{len(spec.user_stories)} stories, {len(spec.assumptions)} assumptions | "
        f"open questions: {', '.join(open_qs) or 'none'} | "
        f"unpublished changes: {len(spec.meta.pending_changes)}"
    )


# ── rendering ────────────────────────────────────────────────────────────────

_SOURCE_TAG = {"client": "", "inferred": " *(inferred — to confirm)*", "assumption": " *(assumption)*"}


def _text_list(items) -> list[str]:
    return [f"- **{i.id}** {i.text}{_SOURCE_TAG[i.source]}" for i in items] or ["- _None recorded._"]


def _criteria(criteria: list[str]) -> list[str]:
    return [f"    - [ ] {c}" for c in criteria]


def render_markdown(spec: RequirementsSpec) -> str:
    e = spec.engagement
    out: list[str] = [
        f"# Requirements Specification — {e.summary if e else 'Untitled'}",
        "",
        f"**Version:** {spec.meta.version} · **Status:** {spec.meta.status}",
        "",
    ]
    if e:
        out += ["## Engagement", f"- **Type:** {e.type}", f"- **Domain:** {e.domain}"]
        if e.client_context:
            out.append(f"- **Client context:** {e.client_context}")
        out.append("")

    out += ["## Goals", *_text_list(spec.goals), "", "## Success metrics", *_text_list(spec.success_metrics), ""]

    out.append("## Stakeholders")
    out += [f"- **{s.id}** {s.role} — {s.needs}{_SOURCE_TAG[s.source]}" for s in spec.stakeholders] or ["- _None recorded._"]
    out.append("")

    out += ["## Scope", "### In scope", *_text_list(spec.in_scope), "", "### Out of scope", *_text_list(spec.out_of_scope), ""]

    for title, reqs in (("Functional requirements", spec.functional_requirements),
                        ("Non-functional requirements", spec.non_functional_requirements)):
        out.append(f"## {title}")
        if not reqs:
            out.append("- _None recorded._")
        for r in reqs:
            category = f" [{r.category}]" if r.category else ""
            out.append(f"- **{r.id}** ({r.priority}){category} {r.text}{_SOURCE_TAG[r.source]}")
            out += _criteria(r.acceptance_criteria)
        out.append("")

    out.append("## User stories")
    if not spec.user_stories:
        out.append("- _None recorded._")
    for s in spec.user_stories:
        out.append(f"- **{s.id}** ({s.priority}) As a {s.as_a}, I want {s.i_want}, so that {s.so_that}.{_SOURCE_TAG[s.source]}")
        out += _criteria(s.acceptance_criteria)
    out.append("")

    out += ["## Constraints", *_text_list(spec.constraints), "", "## Assumptions", *_text_list(spec.assumptions), ""]

    out.append("## Risks")
    out += [f"- **{r.id}** ({r.impact} impact) {r.text} — _Mitigation:_ {r.mitigation or 'TBD'}" for r in spec.risks] or ["- _None recorded._"]
    out.append("")

    out.append("## Open questions")
    out += [
        f"- **{q.id}** {q.text}" + (f" — **Answer:** {q.answer}" if q.answer else " — _open_")
        for q in spec.open_questions
    ] or ["- _None._"]
    out.append("")

    out.append("## Milestones")
    out += [f"- **{m.id}** {m.name} (size {m.relative_size}): {', '.join(m.includes) or '—'}" for m in spec.milestones] or ["- _None yet._"]
    out.append("")

    if spec.meta.change_log:
        out.append("## Change log")
        for c in reversed(spec.meta.change_log):
            out.append(f"- **v{c.version}** {c.summary}")
            out += [f"    - {line}" for line in c.changes]
        out.append("")

    return "\n".join(out)
