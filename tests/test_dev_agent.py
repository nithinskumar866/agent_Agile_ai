"""Deterministic tests for the Dev / Architect agent tools and logic. No LLM, no API key."""

import asyncio
import pytest

from dev_agent import logic as dev_logic
from dev_agent import tools as dev_tools
from dev_agent.agent import dev_agent
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
                             tool_context=ctx, category="Performance")


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
        path="src/gateway/main.py",
        description="Entry point for the API Gateway service",
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
    dev_tools.add_project_scaffold("app/main.py", "FastAPI application factory", tool_context=ctx)

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
