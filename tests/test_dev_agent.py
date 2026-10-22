"""Deterministic tests for the Dev / Architect agent tools and logic. No LLM, no API key."""

import asyncio
import pytest
from google.genai import types

from dev_agent import logic as dev_logic
from dev_agent import tools as dev_tools
from dev_agent.agent import app as dev_app, dev_agent
from dev_agent.prompt import instruction as dev_instruction
from dev_agent.schema import TechDesign
from pm_agent import tools as pm_tools
from pm_agent.agent import root_agent


class FakeContext:
    """Stands in for ToolContext / ReadonlyContext: state dictionary and artifact sink."""

    def __init__(self):
        self.state = {}
        self.artifacts = {}

    async def save_artifact(self, filename, artifact):
        self.artifacts[filename] = artifact.inline_data.data.decode("utf-8")
        return 0


@pytest.fixture
def ctx():
    return FakeContext()


def setup_sample_prd(ctx):
    pm_tools.set_engagement("new_build", "healthcare clinics", "Dental appointment booking system", "", tool_context=ctx)
    pm_tools.add_text_items("goals", ["Reduce call center wait times"], "client", tool_context=ctx)
    pm_tools.add_requirement("functional", "Patients can schedule an appointment online", "must", "client",
                             tool_context=ctx, acceptance_criteria=["Slot confirmation sent via SMS"])
    pm_tools.add_requirement("non_functional", "P95 response time under 200ms", "must", "client",
                             tool_context=ctx, category="Performance",
                             acceptance_criteria=["Load test at 100 rps shows P95 < 200ms"])
    pm_tools.add_text_items("success_metrics", ["Half of bookings online"], "client", tool_context=ctx)
    pm_tools.add_text_items("in_scope", ["Online booking"], "client", tool_context=ctx)


def publish_prd(ctx):
    ctx.user_content = types.Content(role="user", parts=[types.Part(text="Please publish the PRD.")])
    return asyncio.run(pm_tools.publish_prd("first draft", False, tool_context=ctx))


def status(ctx):
    return dev_tools.get_tech_design(tool_context=ctx)["status"]


def publish_arch(ctx, summary="arch"):
    return asyncio.run(dev_tools.publish_architecture(summary, tool_context=ctx))


def test_inspect_spec_returns_empty_when_no_spec(ctx):
    res = dev_tools.inspect_spec(tool_context=ctx)
    assert res["status"] == "empty"


def test_inspect_spec_reads_pm_spec(ctx):
    setup_sample_prd(ctx)
    res = dev_tools.inspect_spec(tool_context=ctx)
    assert res["version"] == 0
    assert len(res["goals"]) == 1
    assert len(res["functional_requirements"]) == 1
    assert res["functional_requirements"][0]["text"] == "Patients can schedule an appointment online"


def test_recommend_tech_stack_add_and_update(ctx):
    r1 = dev_tools.recommend_tech_stack("backend", "FastAPI", "High performance async Python REST API", tool_context=ctx)
    assert r1["ok"] is True
    assert r1["action"] == "added"

    design = dev_logic.load_tech_design(ctx.state)
    assert len(design.tech_stack) == 1
    assert design.tech_stack[0].technology == "FastAPI"

    # Updating same category
    r2 = dev_tools.recommend_tech_stack("backend", "Django", "Includes built-in admin and auth", tool_context=ctx)
    assert r2["ok"] is True
    assert r2["action"] == "updated"

    design = dev_logic.load_tech_design(ctx.state)
    assert len(design.tech_stack) == 1
    assert design.tech_stack[0].technology == "Django"


def test_add_architecture_component_and_scaffold(ctx):
    c = dev_tools.add_architecture_component(
        name="API Gateway",
        role="Reverse proxy and rate limiting",
        responsibilities=["Route validation", "JWT auth verification"],
        dependencies=["Auth Service", "Booking Engine"],
        tool_context=ctx,
    )
    assert c["ok"] is True

    s = dev_tools.add_project_scaffold(
        [{"path": "src/gateway/main.py", "description": "Entry point for the API Gateway service"}],
        tool_context=ctx,
    )
    assert s["ok"] is True

    design = dev_logic.load_tech_design(ctx.state)
    assert len(design.components) == 1
    assert design.components[0].name == "API Gateway"
    assert len(design.project_scaffold) == 1
    assert design.project_scaffold[0].path == "src/gateway/main.py"


def test_publish_architecture_creates_artifact(ctx):
    setup_sample_prd(ctx)
    dev_tools.recommend_tech_stack("backend", "FastAPI", "Async endpoints", tool_context=ctx)
    dev_tools.recommend_tech_stack("database", "PostgreSQL", "ACID compliance for appointments", tool_context=ctx)
    dev_tools.add_architecture_component(
        name="Booking Service",
        role="Handles slot reservation and calendar synchronisation",
        responsibilities=["Prevent double booking"],
        dependencies=["PostgreSQL"],
        tool_context=ctx,
    )
    dev_tools.add_project_scaffold([{"path": "app/main.py", "description": "FastAPI application factory"}], tool_context=ctx)
    publish_prd(ctx)

    res = asyncio.run(dev_tools.publish_architecture("Initial architecture v1", tool_context=ctx))
    assert res["ok"] is True
    assert res["version"] == 1
    assert res["artifact"] == "architecture_v1.md"

    # Verify saved artifact
    assert "architecture_v1.md" in ctx.artifacts
    md = ctx.artifacts["architecture_v1.md"]
    assert "# Technical Architecture & Project Scaffold (v1)" in md
    assert "FastAPI" in md
    assert "PostgreSQL" in md
    assert "Booking Service" in md
    assert "app/main.py" in md
    assert "Initial architecture v1" in md


def test_multi_agent_wiring_sub_agents():
    # Verify PM root agent has dev_agent registered as a sub_agent
    assert len(root_agent.sub_agents) == 1
    sub = root_agent.sub_agents[0]
    assert sub.name == "dev_agent"
    assert sub.parent_agent.name == "product_manager"


def test_standalone_app_is_a_parentless_copy():
    assert dev_app.root_agent is not dev_agent
    assert dev_app.root_agent.parent_agent is None
    assert dev_app.root_agent.name == "dev_agent"


# ── regressions ──────────────────────────────────────────────────────────────

def test_instruction_renders_with_and_without_a_spec(ctx):
    assert "PRD not yet published" in dev_instruction(ctx)
    setup_sample_prd(ctx)
    publish_prd(ctx)
    assert "PRD v1" in dev_instruction(ctx)


def test_get_tech_design_lists_unaddressed_requirements(ctx):
    setup_sample_prd(ctx)
    dev_tools.add_architecture_component("Booking Service", "Bookings", tool_context=ctx, addresses=["FR-1"])
    res = dev_tools.get_tech_design(tool_context=ctx)
    assert res["unaddressed_requirements"] == ["NFR-1"]


def test_component_addresses_must_be_real_requirement_ids(ctx):
    setup_sample_prd(ctx)
    res = dev_tools.add_architecture_component("X", "y", tool_context=ctx, addresses=["FR-99"])
    assert "error" in res


def test_components_and_scaffold_upsert_and_remove(ctx):
    dev_tools.add_architecture_component("API", "v1", tool_context=ctx)
    assert dev_tools.add_architecture_component("api", "v2", tool_context=ctx)["action"] == "updated"
    dev_tools.add_project_scaffold([{"path": "app/main.py", "description": "a"}], tool_context=ctx)
    res = dev_tools.add_project_scaffold([{"path": "app/main.py", "description": "b"},
                                          {"path": "app/db.py", "description": "c"}], tool_context=ctx)
    assert res["updated"] == ["app/main.py"] and res["added"] == 1
    design = dev_logic.load_tech_design(ctx.state)
    assert [c.role for c in design.components] == ["v2"]
    assert [e.description for e in design.project_scaffold] == ["b", "c"]

    assert dev_tools.remove_design_item("component", "API", "merged", tool_context=ctx)["ok"]
    assert "error" in dev_tools.remove_design_item("scaffold", "nope.py", "x", tool_context=ctx)
    assert dev_logic.load_tech_design(ctx.state).components == []


def test_publish_refused_without_published_prd(ctx):
    setup_sample_prd(ctx)
    dev_tools.recommend_tech_stack("backend", "FastAPI", "Async", tool_context=ctx)
    assert "error" in publish_arch(ctx)


def test_architecture_goes_stale_when_prd_changes(ctx):
    setup_sample_prd(ctx)
    publish_prd(ctx)
    dev_tools.recommend_tech_stack("backend", "FastAPI", "Async", tool_context=ctx)
    publish_arch(ctx)
    assert "STALE" not in status(ctx)

    pm_tools.add_text_items("constraints", ["Must run on-prem"], "client", tool_context=ctx)
    publish_prd(ctx)
    assert "STALE" in status(ctx)
    publish_arch(ctx)
    assert "STALE" not in status(ctx)


def test_markdown_escapes_pipes_and_traces_requirements(ctx):
    setup_sample_prd(ctx)
    publish_prd(ctx)
    dev_tools.recommend_tech_stack("db", "Postgres", "fast | cheap", tool_context=ctx)
    dev_tools.add_architecture_component("Booking Service", "Bookings", tool_context=ctx, addresses=["FR-1"])
    publish_arch(ctx)
    md = ctx.artifacts["architecture_v1.md"]
    assert r"fast \| cheap" in md
    assert "| Booking Service |" in md
    assert "**Not yet addressed**" in md  # NFR-1


def test_scaffold_batch_is_one_change_log_line_and_validates(ctx):
    from dev_agent.schema import ScaffoldEntry
    entries = [{"path": f"src/f{i}.py", "description": "x"} for i in range(30)] + [ScaffoldEntry(path="README.md", description="docs")]
    assert dev_tools.add_project_scaffold(entries, tool_context=ctx)["total_entries"] == 31
    assert dev_logic.load_tech_design(ctx.state).change_log == ["Scaffold: 31 added, 0 updated"]
    assert "error" in dev_tools.add_project_scaffold([{"path": "a.py"}], tool_context=ctx)
    assert "error" in dev_tools.add_project_scaffold([{"path": " ", "description": "x"}], tool_context=ctx)
    assert "error" in dev_tools.add_project_scaffold([], tool_context=ctx)


# ── simplest stack ───────────────────────────────────────────────────────────

def test_heavy_infrastructure_needs_a_cited_requirement(ctx):
    setup_sample_prd(ctx)  # NFR-1: P95 under 200ms
    assert "error" in dev_tools.recommend_tech_stack("cache", "Redis", "Speeds things up", tool_context=ctx)
    assert "error" in dev_tools.recommend_tech_stack("queue", "Celery", "Background jobs (FR-99)", tool_context=ctx)
    assert dev_tools.recommend_tech_stack("cache", "Redis", "Needed to hit NFR-1 latency", tool_context=ctx)["ok"]


def test_heavy_infrastructure_components_need_addresses(ctx):
    setup_sample_prd(ctx)
    assert "error" in dev_tools.add_architecture_component("Redis Cache", "Caching", tool_context=ctx)
    assert "error" in dev_tools.add_architecture_component("Job Queue", "Async work", tool_context=ctx)
    res = dev_tools.add_architecture_component("Redis Cache", "Caching", tool_context=ctx, addresses=["NFR-1"])
    assert res["ok"]


def test_placeholder_stack_entries_are_refused(ctx):
    for tech in ("None (not required for MVP)", "N/A", "not needed"):
        assert "error" in dev_tools.recommend_tech_stack("cache", tech, "MVP scale", tool_context=ctx)
    assert dev_logic.load_tech_design(ctx.state).tech_stack == []


def test_plain_stack_is_unaffected(ctx):
    setup_sample_prd(ctx)
    for cat, tech in (("frontend", "React"), ("backend", "FastAPI"), ("database", "PostgreSQL")):
        assert dev_tools.recommend_tech_stack(cat, tech, "Simple and well known", tool_context=ctx)["ok"]
    assert dev_tools.add_architecture_component("Backend API", "REST API", tool_context=ctx)["ok"]


def test_prompt_asks_for_the_simplest_stack(ctx):
    text = dev_instruction(ctx)
    assert "simplest stack" in text and "Redis" in text
