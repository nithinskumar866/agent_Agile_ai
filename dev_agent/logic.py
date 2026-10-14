"""Deterministic core logic for technical architecture design, rendering, and status."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from pm_agent.schema import RequirementsSpec

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


def status_line(design: TechDesign, spec: Optional[RequirementsSpec] = None) -> str:
    """Short summary of current technical design status for the instruction prompt."""
    spec_v = f"PRD v{spec.version}" if spec and spec.version > 0 else "PRD draft"
    return (
        f"Base: {spec_v} | Architecture v{design.version} | "
        f"Stack items: {len(design.tech_stack)} | Components: {len(design.components)} | "
        f"Scaffold entries: {len(design.project_scaffold)}"
    )


def render_architecture_markdown(design: TechDesign, spec: Optional[RequirementsSpec] = None) -> str:
    """Renders the technical architecture document as professional Markdown."""
    lines = [
        f"# {design.title} (v{design.version})",
        "",
        f"**Published:** {design.published_at or 'Draft'}",
    ]

    if spec and spec.engagement:
        lines.append(f"**Based on PRD:** {spec.engagement.summary} (v{spec.meta.version})")
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
            lines.append(f"| {item.category} | `{item.technology}` | {item.rationale} |")
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
    if spec:
        lines.extend([
            "## Requirements Traceability",
            "",
            f"- **Target Goals:** {len(spec.goals)}",
            f"- **Functional Requirements Addressed:** {len(spec.functional_requirements)}",
            f"- **Non-Functional Requirements Addressed:** {len(spec.non_functional_requirements)}",
            f"- **Key Constraints Respected:** {len(spec.constraints)}",
            "",
        ])

    if design.change_log:
        lines.extend(["## Change Log", ""])
        for chg in design.change_log:
            lines.append(f"- {chg}")
        lines.append("")

    return "\n".join(lines)
