"""Deterministic spec operations — no LLM, fully unit-tested.

The LLM decides *what* goes into the spec; this module decides *how* it is
stored, identified, checked, and rendered. Anything that must be reproducible
(IDs, completeness checks, the approval gate, the PRD layout) lives here.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, MutableMapping

import yaml

from .config import SPEC_STATE_KEY
from .schema import ID_PREFIXES, RequirementsSpec

PLAYBOOK_PATH = Path(__file__).with_name("playbooks.yaml")


# ── state I/O ────────────────────────────────────────────────────────────────

def load_spec(state: MutableMapping[str, Any]) -> RequirementsSpec:
    raw = state.get(SPEC_STATE_KEY)
    return RequirementsSpec.model_validate(raw) if raw else RequirementsSpec()


def save_spec(state: MutableMapping[str, Any], spec: RequirementsSpec) -> None:
    state[SPEC_STATE_KEY] = spec.model_dump(mode="json")


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


# ── client authorization ─────────────────────────────────────────────────────
#
# Approval is a client decision, so the gate is code, not model judgement. It is
# deliberately strict: a false "no" costs one confirmation question, a false "yes"
# signs off a spec the client never approved.

# Phrasings that grant approval. Each is matched against one sentence at a time.
_APPROVAL_GRANTS = [
    # "approve of" is an opinion, not a sign-off.
    re.compile(r"\b(?:i|we)\s+(?:hereby\s+|formally\s+|officially\s+|fully\s+)*(?:approve|sign\s+off)\b(?!\s+of\b)"),
    re.compile(r"\b(?:i|we)(?:'ve|\s+have)\s+(?:formally\s+|officially\s+)?(?:approved|signed\s+off)\b"),
    re.compile(r"^\s*(?:(?:ok(?:ay)?|yes|great|thanks),?\s+)?(?:please\s+)?(?:go\s+ahead\s+and\s+)?"
               r"(?:approve|mark)\b.*\bapprov"),
    re.compile(r"^\s*(?:(?:ok(?:ay)?|yes|great|thanks),?\s+)?(?:please\s+)?(?:go\s+ahead\s+and\s+)?"
               r"approve\s+(?:the\s+)?(?:prd|spec|it|this|v\d+|version)\b"),
    re.compile(r"\b(?:prd|spec|it|this|v\d+)\s+(?:is\s+)?(?:now\s+)?approved\b"),
    re.compile(r"^\s*(?:(?:yes|ok(?:ay)?),?\s+)?approved\s*[.!]*\s*$"),
    re.compile(r"\byou\s+have\s+my\s+(?:approval|sign[-\s]off)\b"),
    re.compile(r"\bapproval\s+(?:is\s+)?granted\b"),
]

# Any of these in a sentence that mentions approval makes the whole message a "no":
# negations, conditions, deferrals, and hypotheticals.
_APPROVAL_BLOCKERS = re.compile(
    r"\b(?:not|never|no|without|before|until|unless|if|once|when|after|later|yet|wait|hold"
    r"|don't|dont|won't|can't|cannot|shouldn't|wouldn't|would|could|might|should|may)\b"
)

_APPROVAL_MENTION = re.compile(r"approv|\bsign(?:ed)?[\s-]off\b")

_PUBLISH_FORBIDDEN = re.compile(
    r"\b(?:don't|dont|do\s+not|not|never|without|no)\s+(?:\w+\s+){0,3}?(?:re-?)?publish"
)


def _normalise(message: str) -> str:
    return message.lower().replace("’", "'")


def is_explicit_approval(message: str | None) -> bool:
    """True only when the client's message unambiguously approves the spec.

    Praise ("looks good"), agreement ("sounds right"), review requests, readiness
    questions ("is it ready for approval?"), and conditional or negated approval
    ("approve it once Q-2 is answered", "don't approve yet") are all False.
    """
    if not message:
        return False
    granted = False
    for sentence in re.findall(r"[^.!?\n;]+[.!?]*", _normalise(message)):
        if not _APPROVAL_MENTION.search(sentence):
            continue
        if sentence.rstrip().endswith("?") or _APPROVAL_BLOCKERS.search(sentence):
            return False
        if any(p.search(sentence) for p in _APPROVAL_GRANTS):
            granted = True
    return granted


_PUBLISH_GRANTS = [
    re.compile(r"^\s*(?:(?:ok(?:ay)?|yes|great|thanks|sure),?\s+)?(?:please\s+)?(?:go\s+ahead\s+and\s+)?(?:re-?)?publish\b"),
    re.compile(r"\b(?:please|go\s+ahead\s+and)\s+(?:re-?)?publish\b"),
    re.compile(r"\b(?:i|we)\s+(?:want|need)\s+(?:you\s+)?to\s+(?:re-?)?publish\b"),
    re.compile(r"\byou\s+(?:can|may)\s+(?:now\s+)?(?:re-?)?publish\b"),
]
# A polite question is still a request: "Can you publish the PRD?"
_PUBLISH_POLITE_REQUEST = re.compile(r"^\s*(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:re-?)?publish\b")
_PUBLISH_BLOCKERS = re.compile(
    r"\b(?:not|never|no|without|before|until|unless|if|once|when|after|later|yet|wait|hold"
    r"|don't|dont|won't|shouldn't)\b"
)
# "publish" aimed at the architect's deliverable is not a request to publish the PRD.
_OTHER_DELIVERABLE = re.compile(r"\barchitect|\bdev_agent\b|\bdeveloper\b|\btech(?:nical)?\s+(?:design|stack)\b|\bscaffold")


def is_explicit_publish_request(message: str | None) -> bool:
    """True only when the client's message explicitly asks the PM to publish the PRD.

    Explicit approval also counts. Praise, answers, review requests, readiness questions
    ("is it ready to publish?"), deferred or negated requests, and requests to publish the
    architect's work are all False.
    """
    if not message:
        return False
    if is_explicit_approval(message):
        return True
    granted = False
    for sentence in re.findall(r"[^.!?\n;]+[.!?]*", _normalise(message)):
        if "publish" not in sentence:
            continue
        if _PUBLISH_BLOCKERS.search(sentence) or _OTHER_DELIVERABLE.search(sentence):
            return False
        if sentence.rstrip().endswith("?"):
            if _PUBLISH_POLITE_REQUEST.search(sentence):
                granted = True
            continue
        if any(p.search(sentence) for p in _PUBLISH_GRANTS):
            granted = True
    return granted


def forbids_publishing(message: str | None) -> bool:
    """True when the client's message tells the PM not to publish (e.g. 'do not publish yet')."""
    return bool(message) and bool(_PUBLISH_FORBIDDEN.search(_normalise(message)))


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
