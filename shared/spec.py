"""RequirementsSpec — the PM agent's output and the handoff contract.

Lives in `shared` because every agent reads it: importing it must never pull in
pm_agent (whose package import builds the agent tree).

Every downstream department (Architect, Dev, QA, Sales) will read this shape,
so it is the most stable thing in the project. Change it deliberately and
record the change in docs/decisions.md.

The spec lives in session state as a plain dict (state must be JSON-serialisable);
tools load it into these models, mutate it, and dump it back.
"""

from __future__ import annotations

from typing import Any, Literal, MutableMapping, Optional

from pydantic import BaseModel, Field

Source = Literal["client", "inferred", "assumption"]
"""Where an item came from. `client` = stated by the client; `inferred` = PM's
reading that the client has not confirmed; `assumption` = a gap the PM filled
so work could proceed."""

Priority = Literal["must", "should", "could", "wont"]
Impact = Literal["low", "medium", "high"]
RelativeSize = Literal["S", "M", "L", "XL"]
Status = Literal["draft", "in_review", "approved"]


class TextItem(BaseModel):
    id: str
    text: str
    source: Source = "client"


class Stakeholder(BaseModel):
    id: str
    role: str
    needs: str
    source: Source = "client"


class Requirement(BaseModel):
    id: str
    text: str
    priority: Priority = "should"
    category: str = ""
    """Free text. For non-functional requirements: performance, security, ..."""
    acceptance_criteria: list[str] = Field(default_factory=list)
    source: Source = "client"


class UserStory(BaseModel):
    id: str
    as_a: str
    i_want: str
    so_that: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    priority: Priority = "should"
    source: Source = "client"


class Risk(BaseModel):
    id: str
    text: str
    impact: Impact = "medium"
    mitigation: str = ""
    source: Source = "inferred"


class OpenQuestion(BaseModel):
    id: str
    text: str
    answer: Optional[str] = None


class Milestone(BaseModel):
    id: str
    name: str
    includes: list[str] = Field(default_factory=list)
    """IDs of requirements / user stories delivered in this milestone."""
    relative_size: RelativeSize = "M"


class Engagement(BaseModel):
    type: str
    domain: str
    summary: str
    client_context: str = ""


class ChangeEntry(BaseModel):
    version: int
    summary: str
    changes: list[str] = Field(default_factory=list)


class Meta(BaseModel):
    version: int = 0
    """0 = never published. Incremented by publish_prd."""
    status: Status = "draft"
    pending_changes: list[str] = Field(default_factory=list)
    change_log: list[ChangeEntry] = Field(default_factory=list)
    counters: dict[str, int] = Field(default_factory=dict)
    """Per-prefix ID counters, so a removed ID is never reused."""


class RequirementsSpec(BaseModel):
    engagement: Optional[Engagement] = None
    goals: list[TextItem] = Field(default_factory=list)
    success_metrics: list[TextItem] = Field(default_factory=list)
    stakeholders: list[Stakeholder] = Field(default_factory=list)
    in_scope: list[TextItem] = Field(default_factory=list)
    out_of_scope: list[TextItem] = Field(default_factory=list)
    functional_requirements: list[Requirement] = Field(default_factory=list)
    non_functional_requirements: list[Requirement] = Field(default_factory=list)
    user_stories: list[UserStory] = Field(default_factory=list)
    constraints: list[TextItem] = Field(default_factory=list)
    assumptions: list[TextItem] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    milestones: list[Milestone] = Field(default_factory=list)
    meta: Meta = Field(default_factory=Meta)


# Section name -> ID prefix. The single source of truth for IDs.
ID_PREFIXES: dict[str, str] = {
    "goals": "G",
    "success_metrics": "SM",
    "stakeholders": "ST",
    "in_scope": "IN",
    "out_of_scope": "OUT",
    "functional_requirements": "FR",
    "non_functional_requirements": "NFR",
    "user_stories": "US",
    "constraints": "C",
    "assumptions": "A",
    "risks": "R",
    "open_questions": "Q",
    "milestones": "M",
}

# Sections whose items are plain text — handled by one generic tool.
TEXT_SECTIONS = ("goals", "success_metrics", "in_scope", "out_of_scope", "constraints", "assumptions")


# ── session state I/O ────────────────────────────────────────────────────────

SPEC_STATE_KEY = "spec"
"""Session-state key holding the spec, as a JSON-serialisable dict."""


def load_spec(state: MutableMapping[str, Any]) -> RequirementsSpec:
    raw = state.get(SPEC_STATE_KEY)
    return RequirementsSpec.model_validate(raw) if raw else RequirementsSpec()


def save_spec(state: MutableMapping[str, Any], spec: RequirementsSpec) -> None:
    state[SPEC_STATE_KEY] = spec.model_dump(mode="json")
