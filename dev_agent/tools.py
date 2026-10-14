"""Tools called by the Developer / Architect agent."""

from datetime import datetime, timezone
from typing import List, Optional

from google.adk.tools import ToolContext
from google.genai import types

from . import logic
from .schema import ArchitectureComponent, ScaffoldEntry, TechStackItem


def _load_spec_safely(state: dict):
    from pm_agent import spec_logic as pm_logic
    return pm_logic.load_spec(state)


def inspect_spec(tool_context: ToolContext) -> dict:
    """Read the latest Product Requirements Spec (PRD) prepared by the Product Manager.

    Call this first to understand the engagement, user goals, functional requirements,
    non-functional requirements, constraints, and assumptions.
    """
    spec = _load_spec_safely(tool_context.state)
    if not spec.engagement:
        return {
            "status": "empty",
            "message": "No requirements spec initialized yet by the PM agent.",
        }

    return {
        "status": spec.meta.status,
        "version": spec.meta.version,
        "engagement": spec.engagement.model_dump() if spec.engagement else None,
        "goals": [g.model_dump() for g in spec.goals],
        "functional_requirements": [fr.model_dump() for fr in spec.functional_requirements],
        "non_functional_requirements": [nfr.model_dump() for nfr in spec.non_functional_requirements],
        "constraints": [c.model_dump() for c in spec.constraints],
        "assumptions": [a.model_dump() for a in spec.assumptions],
        "in_scope": [s.model_dump() for s in spec.in_scope],
        "out_of_scope": [s.model_dump() for s in spec.out_of_scope],
    }


def recommend_tech_stack(category: str, technology: str, rationale: str, tool_context: ToolContext) -> dict:
    """Recommend or update a technology choice for the project.

    Args:
        category: Area of the stack, e.g. 'frontend', 'backend', 'database', 'cache', 'auth', 'hosting'.
        technology: Specific technology, library, or framework, e.g. 'FastAPI', 'PostgreSQL', 'Next.js', 'Redis'.
        rationale: Technical rationale explaining why this choice satisfies the project's requirements & constraints.
    """
    if not category.strip() or not technology.strip() or not rationale.strip():
        return {"error": "category, technology, and rationale cannot be empty."}

    design = logic.load_tech_design(tool_context.state)
    cat_lower = category.strip().lower()

    # Update existing category if already present
    for item in design.tech_stack:
        if item.category.lower() == cat_lower:
            item.technology = technology.strip()
            item.rationale = rationale.strip()
            design.change_log.append(f"Updated tech stack [{category}]: {technology}")
            logic.save_tech_design(tool_context.state, design)
            return {"ok": True, "action": "updated", "category": category, "technology": technology}

    item = TechStackItem(category=category.strip(), technology=technology.strip(), rationale=rationale.strip())
    design.tech_stack.append(item)
    design.change_log.append(f"Added tech stack [{category}]: {technology}")
    logic.save_tech_design(tool_context.state, design)
    return {"ok": True, "action": "added", "category": category, "technology": technology}


def add_architecture_component(
    name: str,
    role: str,
    responsibilities: Optional[List[str]] = None,
    dependencies: Optional[List[str]] = None,
    tool_context: Optional[ToolContext] = None,
) -> dict:
    """Define a system architecture component, service, or module.

    Args:
        name: Name of the component, e.g. 'API Gateway', 'User Authentication Service', 'Booking Engine'.
        role: Primary purpose in one or two sentences.
        responsibilities: Bullet points of key responsibilities.
        dependencies: Names of other components, databases, or external APIs it depends upon.
    """
    if tool_context is None:
        return {"error": "tool_context required"}
    if not name.strip() or not role.strip():
        return {"error": "name and role cannot be empty."}

    design = logic.load_tech_design(tool_context.state)
    comp = ArchitectureComponent(
        name=name.strip(),
        role=role.strip(),
        responsibilities=responsibilities or [],
        dependencies=dependencies or [],
    )
    design.components.append(comp)
    design.change_log.append(f"Added architecture component: {name}")
    logic.save_tech_design(tool_context.state, design)
    return {"ok": True, "name": comp.name}


def add_project_scaffold(path: str, description: str, tool_context: ToolContext) -> dict:
    """Define a file or directory path in the project scaffold plan.

    Args:
        path: Relative path, e.g. 'backend/app/main.py', 'backend/app/models/', 'frontend/src/pages/'.
        description: Purpose of this file or directory.
    """
    if not path.strip() or not description.strip():
        return {"error": "path and description cannot be empty."}

    design = logic.load_tech_design(tool_context.state)
    entry = ScaffoldEntry(path=path.strip(), description=description.strip())
    design.project_scaffold.append(entry)
    design.change_log.append(f"Scaffold entry added: {path}")
    logic.save_tech_design(tool_context.state, design)
    return {"ok": True, "path": entry.path}


def get_tech_design(tool_context: ToolContext) -> dict:
    """Return the current technical architecture and scaffold design from state."""
    design = logic.load_tech_design(tool_context.state)
    spec = _load_spec_safely(tool_context.state)
    return {
        "status": logic.status_line(design, spec),
        "design": design.model_dump(),
    }


async def publish_architecture(summary: str, tool_context: ToolContext) -> dict:
    """Publish the complete technical architecture and project scaffold document as an artifact.

    Bumps the architecture version and creates architecture_v{version}.md.

    Args:
        summary: High-level overview of the architectural decisions made in this version.
    """
    design = logic.load_tech_design(tool_context.state)
    if not design.tech_stack and not design.components:
        return {"error": "Cannot publish architecture with empty tech stack and zero components. Add recommendations first."}

    spec = _load_spec_safely(tool_context.state)
    design.version += 1
    design.summary = summary
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    design.published_at = now_iso
    logic.save_tech_design(tool_context.state, design)

    content = logic.render_architecture_markdown(design, spec)
    filename = f"architecture_v{design.version}.md"

    artifact = types.Part.from_bytes(data=content.encode("utf-8"), mime_type="text/markdown")
    await tool_context.save_artifact(filename=filename, artifact=artifact)

    return {
        "ok": True,
        "version": design.version,
        "artifact": filename,
        "summary": summary,
    }


ALL_DEV_TOOLS = [
    inspect_spec,
    recommend_tech_stack,
    add_architecture_component,
    add_project_scaffold,
    get_tech_design,
    publish_architecture,
]
