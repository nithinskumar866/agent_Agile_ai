"""Deterministic core logic for technical architecture design, rendering, and status."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from shared.spec import RequirementsSpec

from .config import TECH_DESIGN_STATE_KEY
from .schema import ArchitectureComponent, ScaffoldEntry, TechDesign, TechStackItem


def load_tech_design(state: Dict[str, Any]) -> TechDesign:
    """Loads the TechDesign from session state or returns a blank instance."""
    raw = state.get(TECH_DESIGN_STATE_KEY)
    if isinstance(raw, TechDesign):
        return raw
    if isinstance(raw, dict):
        return TechDesign.model_validate(raw)
    return TechDesign()


def save_tech_design(state: Dict[str, Any], design: TechDesign) -> None:
    """Persists the TechDesign into session state as a dict."""
    state[TECH_DESIGN_STATE_KEY] = design.model_dump()


def is_stale(design: TechDesign, spec: Optional[RequirementsSpec]) -> bool:
    """True when a published architecture was built from an older PRD version than the current one."""
    return bool(spec) and design.version > 0 and design.based_on_prd_version != spec.meta.version


def status_line(design: TechDesign, spec: Optional[RequirementsSpec] = None) -> str:
    """Short summary of current technical design status for the instruction prompt."""
    spec_v = f"PRD v{spec.meta.version}" if spec and spec.meta.version > 0 else "PRD not yet published"
    line = (
        f"Base: {spec_v} | Architecture v{design.version} | "
        f"Stack items: {len(design.tech_stack)} | Components: {len(design.components)} | "
        f"Scaffold entries: {len(design.project_scaffold)}"
    )
    if is_stale(design, spec):
        line += (f" | STALE: architecture v{design.version} was built on PRD v{design.based_on_prd_version}"
                 " — review the changes and republish")
    if design.has_unpublished_changes and design.version > 0:
        line += " | Unpublished design changes"
    impl = design.implementation
    if impl.project:
        written = sum(f.status == "written" for f in impl.files)
        line += f" | Implemented: {written} files in workspace/{impl.project}/"
    return line


def implementation_blocker(design: TechDesign, spec: Optional[RequirementsSpec]) -> Optional[str]:
    """Why implementation may not start yet, or None. Code is only written from a
    published architecture that matches both the current design and the current PRD."""
    if design.version < 1:
        return "No published architecture. Call publish_architecture first."
    if design.has_unpublished_changes:
        return (f"The design changed since architecture v{design.version} was published. "
                "Publish it again before implementing.")
    if is_stale(design, spec):
        return (f"Architecture v{design.version} was built on PRD v{design.based_on_prd_version}, "
                "but the PRD has changed. Re-check the spec and republish first.")
    if not design.project_scaffold:
        return "The published architecture has no project scaffold to implement."
    return None


def _plain(path: str) -> str:
    p = path.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def is_planned(design: TechDesign, path: str) -> bool:
    """True if `path` is a file in the scaffold, or sits under a scaffold directory entry."""
    target = _plain(path)
    for entry in design.project_scaffold:
        planned = _plain(entry.path)
        if planned.endswith("/"):
            if target.startswith(planned):
                return True
        elif target == planned:
            return True
    return False


def requirement_ids(spec: Optional[RequirementsSpec]) -> set[str]:
    if not spec:
        return set()
    return {r.id for r in spec.functional_requirements + spec.non_functional_requirements}


# Infrastructure that is easy to add and costly to run. It is only accepted when the
# rationale cites a real PRD requirement or constraint — simplest stack by default.
HEAVY_INFRA = re.compile(
    r"\b(?:cache|caching|redis|memcached|valkey|queue|queues|queueing|kafka|rabbitmq|celery|sqs"
    r"|pub/?sub|message\s+broker|event\s+bus|kubernetes|k8s|elasticsearch|opensearch"
    r"|microservices?|service\s+mesh|cdn)\b",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(r"^\s*(?:none|n/?a|not\s+needed|not\s+required|-)\b", re.IGNORECASE)
_ID = re.compile(r"\b(?:FR|NFR|C)-\d+\b")


def justifying_ids(spec: Optional[RequirementsSpec]) -> set[str]:
    """IDs a heavy-infrastructure choice may cite: requirements and constraints."""
    if not spec:
        return set()
    return requirement_ids(spec) | {c.id for c in spec.constraints}


def cited_ids(text: str) -> list[str]:
    return _ID.findall(text)


def infra_refusal(text: str, cited: list[str], spec: Optional[RequirementsSpec]) -> Optional[str]:
    """Error message if `text` names heavy infrastructure without a real PRD ID in `cited`, else None."""
    match = HEAVY_INFRA.search(text)
    if not match:
        return None
    if set(cited) & justifying_ids(spec):
        return None
    return (f"'{match.group(0)}' is extra infrastructure. Prefer the simplest stack: leave it out, "
            "or cite the FR-/NFR-/C- ID that requires it.")


def is_placeholder(technology: str) -> bool:
    """True for entries like 'None (not required)' that should simply be left out."""
    return bool(_PLACEHOLDER.match(technology))


def _cell(text: str) -> str:
    """Make text safe inside a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def render_architecture_markdown(design: TechDesign, spec: Optional[RequirementsSpec] = None) -> str:
    """Renders the technical architecture document as professional Markdown."""
    lines = [
        f"# {design.title} (v{design.version})",
        "",
        f"**Published:** {design.published_at or 'Draft'}",
    ]

    if spec and spec.engagement:
        lines.append(f"**Based on PRD:** {spec.engagement.summary} (v{design.based_on_prd_version})")
        if spec.engagement.domain:
            lines.append(f"**Domain:** {spec.engagement.domain}")
        if spec.engagement.client_context:
            lines.append(f"**Client Context:** {spec.engagement.client_context}")
    lines.append("")

    if design.summary:
        lines.extend(["## Overview", "", design.summary, ""])

    # Tech Stack table
    lines.extend([
        "## Technology Stack Recommendations",
        "",
        "| Category | Technology | Rationale |",
        "| :--- | :--- | :--- |",
    ])
    if design.tech_stack:
        for item in design.tech_stack:
            lines.append(f"| {_cell(item.category)} | `{_cell(item.technology)}` | {_cell(item.rationale)} |")
    else:
        lines.append("| - | *Pending analysis* | *No recommendations recorded yet* |")
    lines.append("")

    # Architecture Components
    lines.extend(["## System Architecture & Components", ""])
    if design.components:
        for comp in design.components:
            lines.append(f"### {comp.name}")
            lines.append(f"**Role:** {comp.role}")
            if comp.responsibilities:
                lines.append("**Responsibilities:**")
                for resp in comp.responsibilities:
                    lines.append(f"- {resp}")
            if comp.dependencies:
                lines.append(f"**Dependencies:** {', '.join(comp.dependencies)}")
            if comp.addresses:
                lines.append(f"**Addresses:** {', '.join(comp.addresses)}")
            lines.append("")
    else:
        lines.extend(["*No architecture components recorded yet.*", ""])

    # Project Scaffold
    lines.extend(["## Recommended Project Structure Scaffold", "", "```text"])
    if design.project_scaffold:
        for s in design.project_scaffold:
            lines.append(f"{s.path:<35} # {s.description}")
    else:
        lines.append("# Project scaffold pending")
    lines.extend(["```", ""])

    # Traceability back to Requirements Spec
    reqs = (spec.functional_requirements + spec.non_functional_requirements) if spec else []
    if reqs:
        lines.extend(["## Requirements Traceability", "", "| Requirement | Priority | Delivered by |",
                      "| :--- | :--- | :--- |"])
        for r in reqs:
            owners = [c.name for c in design.components if r.id in c.addresses]
            delivered = ", ".join(_cell(o) for o in owners) or "**Not yet addressed**"
            lines.append(f"| **{r.id}** {_cell(r.text)} | {r.priority} | {delivered} |")
        lines.append("")

    if design.change_log:
        lines.extend(["## Change Log", ""])
        for chg in design.change_log:
            lines.append(f"- {chg}")
        lines.append("")

    return "\n".join(lines)
