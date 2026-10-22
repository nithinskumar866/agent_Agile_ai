"""Tools called by the Developer / Architect agent.

Like the PM tools, bad input returns {"error": ...} instead of raising, so the
model can read the message and correct itself on the next call.
"""

from datetime import datetime, timezone
from typing import List, Literal, Optional

from google.adk.tools import ToolContext
from google.genai import types

from . import logic
from .schema import ArchitectureComponent, ScaffoldEntry, TechStackItem

DesignItemKind = Literal["tech_stack", "component", "scaffold"]


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

    design = logic.load_tech_design(tool_context.state)
    return {
        "status": spec.meta.status,
        "version": spec.meta.version,
        "architecture_is_stale": logic.is_stale(design, spec),
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
    """Recommend or update a technology choice for the project. One entry per category;
    calling again with the same category replaces it.

    Args:
        category: Area of the stack, e.g. 'frontend', 'backend', 'database', 'cache', 'auth', 'hosting'.
        technology: Specific technology, library, or framework, e.g. 'FastAPI', 'PostgreSQL', 'Next.js', 'Redis'.
        rationale: Technical rationale explaining why this choice satisfies the project's requirements & constraints.
    """
    if not category.strip() or not technology.strip() or not rationale.strip():
        return {"error": "category, technology, and rationale cannot be empty."}
    if logic.is_placeholder(technology):
        return {"error": f"Leave '{category}' out instead of recording '{technology}'; "
                         "mention deliberate omissions in the publish summary."}
    spec = _load_spec_safely(tool_context.state)
    # The justifying requirement ID, if any, is cited in the rationale.
    if err := logic.infra_refusal(f"{category} {technology}", logic.cited_ids(rationale), spec):
        return {"error": err}

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
    tool_context: ToolContext,
    responsibilities: Optional[List[str]] = None,
    dependencies: Optional[List[str]] = None,
    addresses: Optional[List[str]] = None,
) -> dict:
    """Define or update a system architecture component, service, or module. Calling again
    with the same name replaces that component.

    Args:
        name: Name of the component, e.g. 'API Gateway', 'User Authentication Service', 'Booking Engine'.
        role: Primary purpose in one or two sentences.
        responsibilities: Bullet points of key responsibilities.
        dependencies: Names of other components, databases, or external APIs it depends upon.
        addresses: IDs of the PRD requirements this component delivers, e.g. ['FR-1', 'NFR-2'].
            Every requirement should be addressed by at least one component.
    """
    if not name.strip() or not role.strip():
        return {"error": "name and role cannot be empty."}

    spec = _load_spec_safely(tool_context.state)
    unknown = [i for i in addresses or [] if i not in logic.requirement_ids(spec)]
    if unknown:
        return {"error": f"Unknown requirement IDs {unknown}. Use FR-/NFR- IDs from inspect_spec."}
    if err := logic.infra_refusal(name, addresses or [], spec):
        return {"error": err}

    design = logic.load_tech_design(tool_context.state)
    comp = ArchitectureComponent(
        name=name.strip(),
        role=role.strip(),
        responsibilities=responsibilities or [],
        dependencies=dependencies or [],
        addresses=addresses or [],
    )
    for i, existing in enumerate(design.components):
        if existing.name.lower() == comp.name.lower():
            design.components[i] = comp
            design.change_log.append(f"Updated architecture component: {comp.name}")
            logic.save_tech_design(tool_context.state, design)
            return {"ok": True, "action": "updated", "name": comp.name}

    design.components.append(comp)
    design.change_log.append(f"Added architecture component: {comp.name}")
    logic.save_tech_design(tool_context.state, design)
    return {"ok": True, "action": "added", "name": comp.name}


def add_project_scaffold(entries: List[ScaffoldEntry], tool_context: ToolContext) -> dict:
    """Define or update files and directories in the project scaffold plan. Send the whole
    structure (or a large part of it) in ONE call; an existing path is replaced.

    Args:
        entries: Each entry has `path` (relative, e.g. 'backend/app/main.py' or
            'frontend/src/pages/') and `description` (purpose of that file or directory).
    """
    try:
        parsed = [ScaffoldEntry.model_validate(e) for e in entries]
    except ValueError as exc:
        return {"error": f"Each entry needs a path and a description: {exc}"}
    parsed = [ScaffoldEntry(path=e.path.strip(), description=e.description.strip()) for e in parsed]
    if not parsed or any(not e.path or not e.description for e in parsed):
        return {"error": "Provide at least one entry; path and description cannot be empty."}

    design = logic.load_tech_design(tool_context.state)
    index = {e.path: i for i, e in enumerate(design.project_scaffold)}
    added, updated = [], []
    for entry in parsed:
        if entry.path in index:
            design.project_scaffold[index[entry.path]] = entry
            updated.append(entry.path)
        else:
            index[entry.path] = len(design.project_scaffold)
            design.project_scaffold.append(entry)
            added.append(entry.path)

    design.change_log.append(f"Scaffold: {len(added)} added, {len(updated)} updated")
    logic.save_tech_design(tool_context.state, design)
    return {"ok": True, "added": len(added), "updated": updated, "total_entries": len(design.project_scaffold)}


def remove_design_item(kind: DesignItemKind, key: str, reason: str, tool_context: ToolContext) -> dict:
    """Remove a tech stack entry, component, or scaffold entry that is no longer wanted.

    Args:
        kind: 'tech_stack' (key = category), 'component' (key = name), or 'scaffold' (key = path).
        key: The category, component name, or path to remove.
        reason: Why — goes into the change log.
    """
    fields = {"tech_stack": ("tech_stack", "category"), "component": ("components", "name"),
              "scaffold": ("project_scaffold", "path")}
    if kind not in fields:
        return {"error": f"kind must be one of {list(fields)}, got {kind!r}."}
    attr, key_field = fields[kind]

    design = logic.load_tech_design(tool_context.state)
    items = getattr(design, attr)
    for i, item in enumerate(items):
        if getattr(item, key_field).lower() == key.strip().lower():
            items.pop(i)
            design.change_log.append(f"Removed {kind} [{key}]: {reason}")
            logic.save_tech_design(tool_context.state, design)
            return {"ok": True}
    return {"error": f"No {kind} entry {key!r}."}


def get_tech_design(tool_context: ToolContext) -> dict:
    """Return the current technical architecture and scaffold design from state, plus any
    PRD requirements that no component addresses yet."""
    design = logic.load_tech_design(tool_context.state)
    spec = _load_spec_safely(tool_context.state)
    covered = {i for c in design.components for i in c.addresses}
    return {
        "status": logic.status_line(design, spec),
        "unaddressed_requirements": sorted(logic.requirement_ids(spec) - covered),
        "design": design.model_dump(),
    }


async def publish_architecture(summary: str, tool_context: ToolContext) -> dict:
    """Publish the complete technical architecture and project scaffold document as an artifact.

    Bumps the architecture version and creates architecture_v{version}.md. Refused until
    the Product Manager has published at least one version of the PRD.

    Args:
        summary: High-level overview of the architectural decisions made in this version.
    """
    design = logic.load_tech_design(tool_context.state)
    if not design.tech_stack and not design.components:
        return {"error": "Cannot publish architecture with empty tech stack and zero components. Add recommendations first."}

    spec = _load_spec_safely(tool_context.state)
    if spec.meta.version < 1:
        return {"error": "No published PRD yet. Transfer back to product_manager to publish the PRD first."}

    design.version += 1
    design.summary = summary
    design.based_on_prd_version = spec.meta.version
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    design.published_at = now_iso
    logic.save_tech_design(tool_context.state, design)

    content = logic.render_architecture_markdown(design, spec)
    filename = f"architecture_v{design.version}.md"

    artifact = types.Part.from_bytes(data=content.encode("utf-8"), mime_type="text/markdown")
    await tool_context.save_artifact(filename=filename, artifact=artifact)

    covered = {i for c in design.components for i in c.addresses}
    return {
        "ok": True,
        "version": design.version,
        "artifact": filename,
        "summary": summary,
        "unaddressed_requirements": sorted(logic.requirement_ids(spec) - covered),
    }


ALL_DEV_TOOLS = [
    inspect_spec,
    recommend_tech_stack,
    add_architecture_component,
    add_project_scaffold,
    remove_design_item,
    get_tech_design,
    publish_architecture,
]
