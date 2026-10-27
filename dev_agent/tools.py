"""Tools called by the Developer / Architect agent.

Like the PM tools, bad input returns {"error": ...} instead of raising, so the
model can read the message and correct itself on the next call.
"""

from datetime import datetime, timezone
from typing import List, Literal, Optional

from google.adk.tools import ToolContext
from google.genai import types

from shared.args import as_list
from shared.spec import load_spec
from shared.testsuite import TEST_DIR, implementation_gate, is_protected_path, load_suite

from . import logic, workspace
from .schema import ArchitectureComponent, GeneratedFile, ImplementedFile, ScaffoldEntry, TechStackItem

DesignItemKind = Literal["tech_stack", "component", "scaffold"]


def _changed(design, note: str) -> None:
    """Record a design edit. The design then differs from the published architecture."""
    design.change_log.append(note)
    design.has_unpublished_changes = True


def _load_spec_safely(state: dict):
    # Via shared, never pm_agent: importing pm_agent builds a second agent tree when
    # adk eval has already loaded it under another module name.
    return load_spec(state)


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
            _changed(design, f"Updated tech stack [{category}]: {technology}")
            logic.save_tech_design(tool_context.state, design)
            return {"ok": True, "action": "updated", "category": category, "technology": technology}

    item = TechStackItem(category=category.strip(), technology=technology.strip(), rationale=rationale.strip())
    design.tech_stack.append(item)
    _changed(design, f"Added tech stack [{category}]: {technology}")
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
            _changed(design, f"Updated architecture component: {comp.name}")
            logic.save_tech_design(tool_context.state, design)
            return {"ok": True, "action": "updated", "name": comp.name}

    design.components.append(comp)
    _changed(design, f"Added architecture component: {comp.name}")
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
        parsed = [ScaffoldEntry.model_validate(e) for e in as_list(entries)]
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

    _changed(design, f"Scaffold: {len(added)} added, {len(updated)} updated")
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
            _changed(design, f"Removed {kind} [{key}]: {reason}")
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
    design.has_unpublished_changes = False
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


# Indirection so tests can swap in a fake or point at a temporary workspace.
open_filesystem = workspace.McpFilesystemClient


async def implement_scaffold(files: List[GeneratedFile], tool_context: ToolContext, project: str = "") -> dict:
    """Write real source files for the published architecture into the project sandbox
    (workspace/<project>/), through the filesystem MCP server.

    Only paths from the published scaffold are accepted (a file entry, or a file under a
    directory entry). Nothing is written unless every path is valid. Each file is read
    back to verify it. Needs the locked acceptance tests from qa_agent first; test files
    (qa_tests/, any conftest.py) are never yours to write. Call it again with fixed files
    to repair code after failing tests.

    Args:
        files: The files to write, each with `path` (as in the scaffold, relative) and
            `content` (the complete file text). At most 40 per call; call again for more.
        project: Folder name for the sandbox, e.g. 'todo-app'. Required the first time;
            after that it is fixed and may be omitted.
    """
    state = tool_context.state
    design = logic.load_tech_design(state)
    spec = _load_spec_safely(state)
    if blocker := logic.implementation_blocker(design, spec):
        return {"error": blocker}
    if blocker := implementation_gate(load_suite(state), spec.meta.version):
        return {"error": blocker}

    impl = design.implementation
    try:
        slug = workspace.project_slug(project) if project.strip() else impl.project
        if not slug:
            return {"error": "Give a project folder name the first time, e.g. project='todo-app'."}
        if impl.project and slug != impl.project:
            return {"error": f"This design is implemented in workspace/{impl.project}/; it cannot move to {slug!r}."}
        sandbox = workspace.project_dir(slug)
    except workspace.SandboxViolation as exc:
        return {"error": str(exc)}

    # Validate everything before writing anything.
    try:
        parsed = [GeneratedFile.model_validate(f) for f in as_list(files)]
    except ValueError as exc:
        return {"error": f"Each file needs a path and content: {exc}"}
    if not parsed:
        return {"error": "No files given."}
    if len(parsed) > workspace.MAX_FILES_PER_CALL:
        return {"error": f"At most {workspace.MAX_FILES_PER_CALL} files per call; split the rest into another call."}
    problems, planned, seen = [], [], set()
    for f in parsed:
        try:
            rel = workspace.safe_relative_path(f.path)
            target = workspace.resolve_in_sandbox(sandbox, rel)
        except workspace.SandboxViolation as exc:
            problems.append(str(exc))
            continue
        size = len(f.content.encode("utf-8"))
        if is_protected_path(str(rel)):
            problems.append(f"{rel} is a test file. Tests belong to qa_agent and are locked; fix the code instead.")
        elif str(rel) in seen:
            problems.append(f"{rel} is listed twice.")
        elif not logic.is_planned(design, str(rel)):
            problems.append(f"{rel} is not in the published scaffold. Add it, republish, then implement.")
        elif size > workspace.MAX_FILE_BYTES:
            problems.append(f"{rel} is {size} bytes; the limit is {workspace.MAX_FILE_BYTES}.")
        else:
            seen.add(str(rel))
            planned.append((str(rel), target, f.content, size))
    if problems:
        return {"error": "Nothing was written. Fix these files first.", "problems": problems}

    # Write, then read back. Stop at the first failure and report exactly what happened.
    sandbox.mkdir(parents=True, exist_ok=True)
    results: list[ImplementedFile] = []
    failure = ""
    try:
        async with open_filesystem(sandbox) as fs:
            for rel, target, content, size in planned:
                try:
                    await fs.call("create_directory", {"path": str(target.parent)})
                    await fs.call("write_file", {"path": str(target), "content": content})
                    if await fs.call("read_text_file", {"path": str(target)}) != content:
                        raise workspace.McpToolFailed("read-back did not match what was written")
                except Exception as exc:  # any error on this file: it may be partly written
                    failure = f"{rel}: {exc}" if isinstance(exc, workspace.McpToolFailed) \
                        else f"{rel}: {type(exc).__name__}: {exc}"
                    results.append(ImplementedFile(path=rel, status="failed", error=str(exc)[:300]))
                    break
                results.append(ImplementedFile(path=rel, bytes=size, status="written"))
    except workspace.McpUnavailable as exc:
        failure = str(exc)
    except Exception as exc:  # e.g. the server died while the session closed
        failure = failure or f"MCP session error: {type(exc).__name__}: {exc}"
    attempted = {r.path for r in results}
    results += [ImplementedFile(path=rel, status="not_attempted") for rel, *_ in planned if rel not in attempted]

    # Record every attempt in the change history, successful or not.
    impl.project = slug
    impl.architecture_version = design.version
    by_path = {f.path: f for f in impl.files}
    by_path.update({r.path: r for r in results})
    impl.files = list(by_path.values())
    written = [r.path for r in results if r.status == "written"]
    if failure:
        design.change_log.append(
            f"Implementation FAILED (architecture v{design.version}): {len(written)} of {len(planned)} "
            f"files written to workspace/{slug}/ — {failure[:200]}")
    else:
        shown = ", ".join(written[:8]) + (f" (+{len(written) - 8} more)" if len(written) > 8 else "")
        design.change_log.append(
            f"Implemented {len(written)} files in workspace/{slug}/ from architecture v{design.version}: {shown}")
    logic.save_tech_design(state, design)

    if failure:
        return {
            "error": f"Implementation did not complete: {failure}",
            "written": written,
            "failed": [r.path for r in results if r.status == "failed"],
            "not_attempted": [r.path for r in results if r.status == "not_attempted"],
        }
    return {"ok": True, "workspace": f"workspace/{slug}/", "written": written,
            "note": f"Files written and verified, not yet tested. Call run_tests to check them "
                    f"against the locked acceptance tests in {TEST_DIR}/."}


ALL_DEV_TOOLS = [
    inspect_spec,
    recommend_tech_stack,
    add_architecture_component,
    add_project_scaffold,
    remove_design_item,
    get_tech_design,
    publish_architecture,
    implement_scaffold,
]
