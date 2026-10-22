"""Tools the PM agent calls. Thin wrappers: validate, delegate to spec_logic, record the change.

Each docstring is what the model reads to decide when and how to call the tool,
so docstrings here are prompt text — keep them precise.

Tools return a dict. Bad input returns {"error": ...} instead of raising, so the
model can read the message and correct itself on the next call.
"""

from __future__ import annotations

from typing import Literal, Optional, get_args

from google.adk.tools import ToolContext
from google.genai import types

from . import spec_logic as logic
from .schema import (
    ChangeEntry,
    TEXT_SECTIONS,
    Engagement,
    Impact,
    Milestone,
    OpenQuestion,
    Priority,
    RelativeSize,
    Requirement,
    Risk,
    Source,
    Stakeholder,
    TextItem,
    UserStory,
)

_SOURCES = get_args(Source)
_PRIORITIES = get_args(Priority)
_IMPACTS = get_args(Impact)
_SIZES = get_args(RelativeSize)

RequirementKind = Literal["functional", "non_functional"]
TextSection = Literal["goals", "success_metrics", "in_scope", "out_of_scope", "constraints", "assumptions"]


def _invalid(name: str, value: object, allowed) -> Optional[dict]:
    if value not in allowed:
        return {"error": f"{name} must be one of {list(allowed)}, got {value!r}."}
    return None


def _user_message(tool_context: ToolContext) -> Optional[str]:
    """Text of the client message that started this turn, or None if unavailable."""
    content = getattr(tool_context, "user_content", None)
    if content is None or not content.parts:
        return None
    return "\n".join(p.text for p in content.parts if p.text) or None


def _unknown_deliverables(spec, ids: list[str]) -> Optional[dict]:
    """Milestones may only include functional requirements and user stories that exist."""
    known = {i.id for s in ("functional_requirements", "user_stories") for i in getattr(spec, s)}
    unknown = [i for i in ids if i not in known]
    if unknown:
        return {"error": f"Unknown functional requirement / user story IDs: {unknown}."}
    return None


# ── discovery ────────────────────────────────────────────────────────────────

def get_engagement_playbook(engagement_type: str) -> dict:
    """Return what to find out for an engagement type, plus the list of all known types.

    Call this once you have a first reading of the client's request, before asking
    clarifying questions. Use the returned discovery_areas to choose which questions
    matter most. Unknown types fall back to the generic 'other' playbook.

    Args:
        engagement_type: Best-fit type key, e.g. 'new_build', 'migration', 'data_ai'.
    """
    return {**logic.playbook(engagement_type), "all_types": logic.engagement_types()}


def set_engagement(
    engagement_type: str, domain: str, summary: str, client_context: str, tool_context: ToolContext
) -> dict:
    """Set or change what this engagement is. Call first, and again if the picture changes.

    Args:
        engagement_type: A type key from get_engagement_playbook's all_types.
        domain: The client's industry or business area, e.g. 'healthcare clinics', 'retail'.
        summary: One sentence naming what is being delivered.
        client_context: Who the client is and anything relevant about their situation. May be empty.
    """
    if engagement_type not in logic.engagement_types():
        return {"error": f"Unknown engagement_type {engagement_type!r}. Use one of {list(logic.engagement_types())}."}
    spec = logic.load_spec(tool_context.state)
    spec.engagement = Engagement(type=engagement_type, domain=domain, summary=summary, client_context=client_context)
    logic.record_change(spec, f"Engagement set: {engagement_type} / {domain}")
    logic.save_spec(tool_context.state, spec)
    return {"ok": True, "status": logic.status_line(spec)}


def add_open_questions(questions: list[str], tool_context: ToolContext) -> dict:
    """Record clarifying questions you are about to ask the client, so they are tracked until answered.

    Args:
        questions: The questions, highest-impact first.
    """
    spec = logic.load_spec(tool_context.state)
    ids = []
    for text in questions:
        q = OpenQuestion(id=logic.next_id(spec, "open_questions"), text=text)
        spec.open_questions.append(q)
        ids.append(q.id)
    logic.record_change(spec, f"Questions raised: {', '.join(ids)}")
    logic.save_spec(tool_context.state, spec)
    return {"ok": True, "ids": ids}


def answer_question(question_id: str, answer: str, tool_context: ToolContext) -> dict:
    """Record the client's answer to an open question. Then capture what the answer implies
    (requirements, scope, constraints) with the other tools.

    Args:
        question_id: e.g. 'Q-3'.
        answer: The answer, in the client's terms. If the client said 'you decide', record
            that here and add an assumption with add_text_items.
    """
    spec = logic.load_spec(tool_context.state)
    for q in spec.open_questions:
        if q.id == question_id:
            q.answer = answer
            logic.record_change(spec, f"{question_id} answered")
            logic.save_spec(tool_context.state, spec)
            return {"ok": True}
    return {"error": f"No open question {question_id!r}."}


# ── capture ──────────────────────────────────────────────────────────────────

def add_text_items(section: TextSection, texts: list[str], source: Source, tool_context: ToolContext) -> dict:
    """Add plain-text items to a section of the spec.

    Args:
        section: One of goals, success_metrics, in_scope, out_of_scope, constraints, assumptions.
        texts: One item per entry.
        source: 'client' if the client said it, 'inferred' if it is your reading that the client
            has not confirmed, 'assumption' if you are filling a gap. Items in the
            'assumptions' section are always stored as 'assumption'.
    """
    if err := _invalid("section", section, TEXT_SECTIONS) or _invalid("source", source, _SOURCES):
        return err
    if section == "assumptions":
        source = "assumption"
    spec = logic.load_spec(tool_context.state)
    ids = []
    for text in texts:
        item = TextItem(id=logic.next_id(spec, section), text=text, source=source)
        getattr(spec, section).append(item)
        ids.append(item.id)
    logic.record_change(spec, f"Added to {section}: {', '.join(ids)}")
    logic.save_spec(tool_context.state, spec)
    return {"ok": True, "ids": ids}


def add_requirement(
    kind: RequirementKind,
    text: str,
    priority: Priority,
    source: Source,
    tool_context: ToolContext,
    category: str = "",
    acceptance_criteria: Optional[list[str]] = None,
) -> dict:
    """Add a functional or non-functional requirement.

    Args:
        kind: 'functional' (what the system does) or 'non_functional' (how well: performance,
            security, availability, ...).
        text: The requirement, as one testable statement.
        priority: 'must', 'should', 'could', or 'wont' (explicitly not this time).
        source: 'client', 'inferred', or 'assumption'.
        category: For non-functional requirements, the quality area, e.g. 'performance',
            'security', 'availability'. Optional for functional ones.
        acceptance_criteria: Concrete, checkable conditions. Required in practice for 'must'.
    """
    if err := (_invalid("kind", kind, ("functional", "non_functional"))
               or _invalid("priority", priority, _PRIORITIES) or _invalid("source", source, _SOURCES)):
        return err
    section = "functional_requirements" if kind == "functional" else "non_functional_requirements"
    spec = logic.load_spec(tool_context.state)
    req = Requirement(
        id=logic.next_id(spec, section), text=text, priority=priority, source=source,
        category=category.strip().lower(), acceptance_criteria=acceptance_criteria or [],
    )
    getattr(spec, section).append(req)
    logic.record_change(spec, f"Added {req.id}")
    logic.save_spec(tool_context.state, spec)
    return {"ok": True, "id": req.id}


def add_user_story(
    as_a: str,
    i_want: str,
    so_that: str,
    priority: Priority,
    source: Source,
    tool_context: ToolContext,
    acceptance_criteria: Optional[list[str]] = None,
) -> dict:
    """Add a user story: As a <as_a>, I want <i_want>, so that <so_that>.

    Args:
        as_a: The user role.
        i_want: What they want to do.
        so_that: Why — the value to them.
        priority: 'must', 'should', 'could', or 'wont'.
        source: 'client', 'inferred', or 'assumption'.
        acceptance_criteria: Concrete, checkable conditions.
    """
    if err := _invalid("priority", priority, _PRIORITIES) or _invalid("source", source, _SOURCES):
        return err
    spec = logic.load_spec(tool_context.state)
    story = UserStory(
        id=logic.next_id(spec, "user_stories"), as_a=as_a, i_want=i_want, so_that=so_that,
        priority=priority, source=source, acceptance_criteria=acceptance_criteria or [],
    )
    spec.user_stories.append(story)
    logic.record_change(spec, f"Added {story.id}")
    logic.save_spec(tool_context.state, spec)
    return {"ok": True, "id": story.id}


def add_stakeholder(role: str, needs: str, source: Source, tool_context: ToolContext) -> dict:
    """Add a stakeholder — anyone who uses, pays for, approves, or is affected by the result.

    Args:
        role: e.g. 'clinic receptionist', 'CFO', 'IT security team'.
        needs: What they need from this engagement.
        source: 'client', 'inferred', or 'assumption'.
    """
    if err := _invalid("source", source, _SOURCES):
        return err
    spec = logic.load_spec(tool_context.state)
    s = Stakeholder(id=logic.next_id(spec, "stakeholders"), role=role, needs=needs, source=source)
    spec.stakeholders.append(s)
    logic.record_change(spec, f"Added {s.id}")
    logic.save_spec(tool_context.state, spec)
    return {"ok": True, "id": s.id}


def add_risk(text: str, impact: Impact, mitigation: str, tool_context: ToolContext) -> dict:
    """Add a delivery or business risk you have spotted.

    Args:
        text: What could go wrong.
        impact: 'low', 'medium', or 'high'.
        mitigation: How to reduce it. May be empty if unknown.
    """
    if err := _invalid("impact", impact, _IMPACTS):
        return err
    spec = logic.load_spec(tool_context.state)
    r = Risk(id=logic.next_id(spec, "risks"), text=text, impact=impact, mitigation=mitigation)
    spec.risks.append(r)
    logic.record_change(spec, f"Added {r.id}")
    logic.save_spec(tool_context.state, spec)
    return {"ok": True, "id": r.id}


def add_milestone(name: str, includes: list[str], relative_size: RelativeSize, tool_context: ToolContext) -> dict:
    """Group requirements/user stories into a delivery milestone. Sizes are relative, never dates.

    Args:
        name: e.g. 'MVP', 'Phase 2 — reporting'.
        includes: IDs of functional requirements or user stories in this milestone.
        relative_size: 'S', 'M', 'L', or 'XL'.
    """
    if err := _invalid("relative_size", relative_size, _SIZES):
        return err
    spec = logic.load_spec(tool_context.state)
    if err := _unknown_deliverables(spec, includes):
        return err
    m = Milestone(id=logic.next_id(spec, "milestones"), name=name, includes=includes, relative_size=relative_size)
    spec.milestones.append(m)
    logic.record_change(spec, f"Added {m.id}")
    logic.save_spec(tool_context.state, spec)
    return {"ok": True, "id": m.id}


# ── revise ───────────────────────────────────────────────────────────────────

def update_item(
    item_id: str,
    change_reason: str,
    tool_context: ToolContext,
    text: Optional[str] = None,
    priority: Optional[Priority] = None,
    source: Optional[Source] = None,
    acceptance_criteria: Optional[list[str]] = None,
    i_want: Optional[str] = None,
    so_that: Optional[str] = None,
    as_a: Optional[str] = None,
    category: Optional[str] = None,
    impact: Optional[Impact] = None,
    mitigation: Optional[str] = None,
    name: Optional[str] = None,
    includes: Optional[list[str]] = None,
    relative_size: Optional[RelativeSize] = None,
) -> dict:
    """Change an existing item in place, keeping its ID. Only pass the fields that change.

    Use this when the client corrects or refines something (e.g. confirms an inferred item:
    pass source='client'). Do not delete-and-re-add — that loses traceability.

    Args:
        item_id: e.g. 'FR-2', 'US-1', 'A-3', 'R-1', 'M-1'.
        change_reason: Why it changed, in a few words — goes into the change log.
        text: New text (not for user stories; use as_a / i_want / so_that).
        priority: 'must', 'should', 'could', or 'wont'.
        source: 'client', 'inferred', or 'assumption'.
        acceptance_criteria: Replaces the whole list.
        i_want: User stories only.
        so_that: User stories only.
        as_a: User stories only.
        category: Requirements only, e.g. 'performance'.
        impact: Risks only — 'low', 'medium', or 'high'.
        mitigation: Risks only.
        name: Milestones only.
        includes: Milestones only — replaces the whole list of FR / US IDs.
        relative_size: Milestones only — 'S', 'M', 'L', or 'XL'.
    """
    if priority is not None and (err := _invalid("priority", priority, _PRIORITIES)):
        return err
    if source is not None and (err := _invalid("source", source, _SOURCES)):
        return err
    if impact is not None and (err := _invalid("impact", impact, _IMPACTS)):
        return err
    if relative_size is not None and (err := _invalid("relative_size", relative_size, _SIZES)):
        return err
    spec = logic.load_spec(tool_context.state)
    found = logic.find_item(spec, item_id)
    if found is None:
        return {"error": f"No item {item_id!r}."}
    if includes is not None and (err := _unknown_deliverables(spec, includes)):
        return err
    section, index = found
    item = getattr(spec, section)[index]
    if category is not None:
        category = category.strip().lower()
    changes = {k: v for k, v in dict(text=text, priority=priority, source=source,
                                      acceptance_criteria=acceptance_criteria,
                                      i_want=i_want, so_that=so_that, as_a=as_a, category=category,
                                      impact=impact, mitigation=mitigation, name=name,
                                      includes=includes, relative_size=relative_size).items()
               if v is not None}
    unsupported = [k for k in changes if k not in type(item).model_fields]
    if unsupported:
        return {"error": f"{item_id} has no field(s) {unsupported}."}
    getattr(spec, section)[index] = item.model_copy(update=changes)
    logic.record_change(spec, f"Updated {item_id} ({', '.join(changes)}): {change_reason}")
    logic.save_spec(tool_context.state, spec)
    return {"ok": True}


def remove_item(item_id: str, reason: str, tool_context: ToolContext) -> dict:
    """Remove an item the client no longer wants. If it was descoped rather than dropped,
    also add it to out_of_scope so the decision stays visible.

    Args:
        item_id: e.g. 'FR-4'.
        reason: Why — goes into the change log.
    """
    spec = logic.load_spec(tool_context.state)
    found = logic.find_item(spec, item_id)
    if found is None:
        return {"error": f"No item {item_id!r}."}
    section, index = found
    getattr(spec, section).pop(index)
    for m in spec.milestones:
        if item_id in m.includes:
            m.includes.remove(item_id)
    logic.record_change(spec, f"Removed {item_id}: {reason}")
    logic.save_spec(tool_context.state, spec)
    return {"ok": True}


# ── check & publish ──────────────────────────────────────────────────────────

def review_spec(tool_context: ToolContext) -> dict:
    """Check the spec for structural gaps and return it. Call before publishing, and whenever
    you need to see the full current spec.
    """
    spec = logic.load_spec(tool_context.state)
    return {"review": logic.review(spec), "spec": spec.model_dump(mode="json", exclude={"meta": {"counters"}})}


async def publish_prd(change_summary: str, mark_approved: bool, tool_context: ToolContext) -> dict:
    """Publish a new version of the PRD as a Markdown artifact.

    Publishing bumps the version and moves all unpublished changes into the change log.
    Only call this when the client's latest message explicitly asks you to publish the PRD
    (or explicitly approves it). Otherwise ask them first: "Shall I publish version N?".

    Args:
        change_summary: One sentence on what this version changes.
        mark_approved: True only if the client's latest message explicitly approves the spec
            (e.g. 'I approve the PRD'). Praise such as 'looks good', agreement, review requests,
            and readiness questions are NOT approval — ask the client instead. Also refused
            while there are gaps, unanswered questions, or unconfirmed inferred items.
    """
    message = _user_message(tool_context)
    if logic.forbids_publishing(message):
        return {"error": "The client asked you not to publish. Do not publish this turn."}
    if not mark_approved and not logic.is_explicit_publish_request(message):
        return {"error": "Publishing refused: the client has not explicitly asked you to publish the PRD. "
                         "Tell them it is ready and ask: 'Shall I publish it for your review?'"}
    if mark_approved and not logic.is_explicit_approval(message):
        return {"error": "Approval refused: the client's latest message is not an explicit approval. "
                         "Do not publish on their behalf; ask them whether they approve "
                         f"version {logic.load_spec(tool_context.state).meta.version}."}

    spec = logic.load_spec(tool_context.state)
    report = logic.review(spec)
    if not report["ready_for_review"]:
        return {"error": "Spec has blocking gaps; fix them first.", "gaps": report["gaps"]}
    if mark_approved and not report["ready_for_approval"]:
        return {"error": "Cannot mark approved yet.", "warnings": report["warnings"]}
    if not spec.meta.pending_changes and spec.meta.version > 0 and not mark_approved:
        return {"error": f"Nothing changed since v{spec.meta.version}."}

    spec.meta.version += 1
    spec.meta.status = "approved" if mark_approved else "in_review"
    spec.meta.change_log.append(ChangeEntry(
        version=spec.meta.version, summary=change_summary, changes=spec.meta.pending_changes,
    ))
    spec.meta.pending_changes = []
    logic.save_spec(tool_context.state, spec)

    filename = f"prd_v{spec.meta.version}.md"
    markdown = logic.render_markdown(spec)
    await tool_context.save_artifact(filename, types.Part.from_bytes(data=markdown.encode("utf-8"), mime_type="text/markdown"))
    return {"ok": True, "artifact": filename, "version": spec.meta.version, "status": spec.meta.status,
            "warnings": report["warnings"]}


ALL_TOOLS = [
    get_engagement_playbook,
    set_engagement,
    add_open_questions,
    answer_question,
    add_text_items,
    add_requirement,
    add_user_story,
    add_stakeholder,
    add_risk,
    add_milestone,
    update_item,
    remove_item,
    review_spec,
    publish_prd,
]
