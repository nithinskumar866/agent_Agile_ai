"""Deterministic tests for the PM agent's tools and spec logic. No LLM, no API key."""

import asyncio

import pytest

from pm_agent import spec_logic as logic
from pm_agent import tools
from pm_agent.agent import app, root_agent
from pm_agent.prompt import instruction


class FakeContext:
    """Stands in for ToolContext / ReadonlyContext: just state and an artifact sink."""

    def __init__(self):
        self.state = {}
        self.artifacts = {}

    async def save_artifact(self, filename, artifact):
        self.artifacts[filename] = artifact.inline_data.data.decode("utf-8")
        return 0


@pytest.fixture
def ctx():
    return FakeContext()


def publish(ctx, summary="v", approved=False):
    return asyncio.run(tools.publish_prd(summary, approved, tool_context=ctx))


def minimal_complete_spec(ctx):
    tools.set_engagement("new_build", "healthcare clinics", "Appointment booking app", "", tool_context=ctx)
    tools.add_text_items("goals", ["Cut phone bookings"], "client", tool_context=ctx)
    tools.add_text_items("success_metrics", ["50% of bookings online in 3 months"], "client", tool_context=ctx)
    tools.add_text_items("in_scope", ["Patient self-booking"], "client", tool_context=ctx)
    tools.add_requirement("functional", "Patients can book a slot", "must", "client",
                          tool_context=ctx, acceptance_criteria=["Booked slot disappears for others"])


# ── IDs and capture ──────────────────────────────────────────────────────────

def test_ids_increment_per_section_and_are_never_reused(ctx):
    a = tools.add_requirement("functional", "one", "should", "client", tool_context=ctx)
    b = tools.add_requirement("functional", "two", "should", "client", tool_context=ctx)
    nfr = tools.add_requirement("non_functional", "fast", "should", "client", tool_context=ctx, category="Performance")
    assert (a["id"], b["id"], nfr["id"]) == ("FR-1", "FR-2", "NFR-1")

    tools.remove_item("FR-2", "dropped", tool_context=ctx)
    c = tools.add_requirement("functional", "three", "should", "client", tool_context=ctx)
    assert c["id"] == "FR-3"
    assert logic.load_spec(ctx.state).non_functional_requirements[0].category == "performance"


def test_assumptions_are_always_labelled_assumption(ctx):
    tools.add_text_items("assumptions", ["Users have smartphones"], "client", tool_context=ctx)
    assert logic.load_spec(ctx.state).assumptions[0].source == "assumption"


def test_invalid_input_returns_error_not_exception(ctx):
    assert "error" in tools.add_text_items("budget", ["x"], "client", tool_context=ctx)
    assert "error" in tools.add_requirement("functional", "x", "urgent", "client", tool_context=ctx)
    assert "error" in tools.set_engagement("teleportation", "x", "x", "", tool_context=ctx)
    assert "error" in tools.answer_question("Q-99", "x", tool_context=ctx)
    assert ctx.state.get("spec") is None or not logic.load_spec(ctx.state).functional_requirements


def test_questions_track_answers(ctx):
    ids = tools.add_open_questions(["Who signs off?", "Deadline?"], tool_context=ctx)["ids"]
    tools.answer_question(ids[0], "The clinic owner", tool_context=ctx)
    qs = logic.load_spec(ctx.state).open_questions
    assert [q.answer for q in qs] == ["The clinic owner", None]


# ── revision ─────────────────────────────────────────────────────────────────

def test_update_keeps_id_and_logs_reason(ctx):
    tools.add_requirement("functional", "Export to CSV", "could", "inferred", tool_context=ctx)
    assert tools.update_item("FR-1", "client confirmed", tool_context=ctx, priority="must", source="client") == {"ok": True}
    spec = logic.load_spec(ctx.state)
    fr = spec.functional_requirements[0]
    assert (fr.id, fr.priority, fr.source, fr.text) == ("FR-1", "must", "client", "Export to CSV")
    assert "client confirmed" in spec.meta.pending_changes[-1]


def test_update_rejects_fields_the_item_does_not_have(ctx):
    tools.add_text_items("goals", ["g"], "client", tool_context=ctx)
    assert "error" in tools.update_item("G-1", "x", tool_context=ctx, i_want="something")


def test_removing_item_drops_it_from_milestones(ctx):
    tools.add_requirement("functional", "a", "should", "client", tool_context=ctx)
    tools.add_milestone("MVP", ["FR-1"], "M", tool_context=ctx)
    tools.remove_item("FR-1", "descoped", tool_context=ctx)
    assert logic.load_spec(ctx.state).milestones[0].includes == []


# ── review ───────────────────────────────────────────────────────────────────

def test_empty_spec_has_blocking_gaps(ctx):
    report = tools.review_spec(tool_context=ctx)["review"]
    assert not report["ready_for_review"]
    assert any("Engagement" in g for g in report["gaps"])


def test_must_have_without_acceptance_criteria_is_a_gap(ctx):
    minimal_complete_spec(ctx)
    tools.add_requirement("functional", "Reminders", "must", "client", tool_context=ctx)
    gaps = logic.review(logic.load_spec(ctx.state))["gaps"]
    assert any("FR-2" in g for g in gaps)


def test_expected_nfr_categories_come_from_the_playbook(ctx):
    minimal_complete_spec(ctx)
    warnings = logic.review(logic.load_spec(ctx.state))["warnings"]
    for category in logic.playbook("new_build")["expected_nfr_categories"]:
        assert any(f"'{category}'" in w for w in warnings)


def test_unknown_engagement_type_falls_back_to_other():
    assert logic.playbook("quantum_gardening")["engagement_type"] == "other"


def test_every_playbook_entry_is_well_formed():
    for key in logic.engagement_types():
        pb = logic.playbook(key)
        assert pb["description"] and pb["discovery_areas"]


# ── publish & approval gate ──────────────────────────────────────────────────

def test_publish_refused_while_gaps_exist(ctx):
    assert "error" in publish(ctx)
    assert ctx.artifacts == {}


def test_publish_bumps_version_and_moves_changes_to_log(ctx):
    minimal_complete_spec(ctx)
    result = publish(ctx, "first draft")
    spec = logic.load_spec(ctx.state)
    assert result["artifact"] == "prd_v1.md" and spec.meta.version == 1
    assert spec.meta.status == "in_review" and spec.meta.pending_changes == []
    assert spec.meta.change_log[0].summary == "first draft"
    assert "Patients can book a slot" in ctx.artifacts["prd_v1.md"]
    assert "error" in publish(ctx, "nothing new")


def test_approval_blocked_by_open_questions_and_unconfirmed_items(ctx):
    minimal_complete_spec(ctx)
    tools.add_open_questions(["Deadline?"], tool_context=ctx)
    assert "error" in publish(ctx, approved=True)
    tools.answer_question("Q-1", "End of quarter", tool_context=ctx)
    tools.add_text_items("in_scope", ["SMS reminders"], "inferred", tool_context=ctx)
    assert "error" in publish(ctx, approved=True)
    tools.update_item("IN-2", "confirmed", tool_context=ctx, source="client")
    assert publish(ctx, "approved", approved=True)["status"] == "approved"


def test_editing_an_approved_spec_reopens_it(ctx):
    minimal_complete_spec(ctx)
    publish(ctx, "approved", approved=True)
    tools.add_text_items("constraints", ["Must run on clinic tablets"], "client", tool_context=ctx)
    assert logic.load_spec(ctx.state).meta.status == "in_review"


# ── wiring ───────────────────────────────────────────────────────────────────

def test_instruction_carries_live_spec_status(ctx):
    assert "No spec started yet" in instruction(ctx)
    minimal_complete_spec(ctx)
    assert "new_build / healthcare clinics" in instruction(ctx)


def test_agent_is_wired():
    assert app.root_agent is root_agent
    assert len(root_agent.tools) == len(tools.ALL_TOOLS)
