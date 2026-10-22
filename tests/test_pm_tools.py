"""Deterministic tests for the PM agent's tools and spec logic. No LLM, no API key."""

import asyncio

import pytest
from google.genai import types

from pm_agent import spec_logic as logic
from pm_agent import tools
from pm_agent.agent import app, root_agent
from pm_agent.prompt import instruction


class FakeContext:
    """Stands in for ToolContext / ReadonlyContext: just state and an artifact sink."""

    def __init__(self):
        self.state = {}
        self.artifacts = {}
        self.user_content = None  # the client message that started this turn

    async def save_artifact(self, filename, artifact):
        self.artifacts[filename] = artifact.inline_data.data.decode("utf-8")
        return 0


@pytest.fixture
def ctx():
    return FakeContext()


def say(ctx, message):
    """Set the client message the current turn is answering."""
    ctx.user_content = types.Content(role="user", parts=[types.Part(text=message)])


def publish(ctx, summary="v", approved=False, message=None):
    if message is None:
        message = "I approve the PRD." if approved else "Please publish the PRD."
    say(ctx, message)
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


def test_milestone_rejects_unknown_ids(ctx):
    assert "error" in tools.add_milestone("MVP", ["FR-9"], "M", tool_context=ctx)
    tools.add_requirement("functional", "a", "should", "client", tool_context=ctx)
    tools.add_milestone("MVP", ["FR-1"], "M", tool_context=ctx)
    assert "error" in tools.update_item("M-1", "x", tool_context=ctx, includes=["US-7"])


def test_update_covers_risk_milestone_story_and_category_fields(ctx):
    tools.add_requirement("non_functional", "fast", "should", "client", tool_context=ctx, category="perf")
    tools.add_user_story("patient", "book", "save time", "should", "client", tool_context=ctx)
    tools.add_risk("r", "low", "", tool_context=ctx)
    tools.add_milestone("MVP", [], "S", tool_context=ctx)
    assert tools.update_item("NFR-1", "x", tool_context=ctx, category=" Performance ")["ok"]
    assert tools.update_item("US-1", "x", tool_context=ctx, as_a="receptionist")["ok"]
    assert tools.update_item("R-1", "x", tool_context=ctx, impact="high", mitigation="pilot")["ok"]
    assert tools.update_item("M-1", "x", tool_context=ctx, name="Pilot", includes=["US-1"], relative_size="L")["ok"]
    assert "error" in tools.update_item("R-1", "x", tool_context=ctx, impact="huge")
    spec = logic.load_spec(ctx.state)
    assert spec.non_functional_requirements[0].category == "performance"
    assert spec.user_stories[0].as_a == "receptionist"
    assert (spec.risks[0].impact, spec.risks[0].mitigation) == ("high", "pilot")
    assert (spec.milestones[0].name, spec.milestones[0].includes, spec.milestones[0].relative_size) == ("Pilot", ["US-1"], "L")


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


def test_approval_blocked_by_inferred_items_in_any_sourced_section(ctx):
    minimal_complete_spec(ctx)
    tools.add_stakeholder("Practice owner", "Signs off", "inferred", tool_context=ctx)
    assert "error" in publish(ctx, approved=True)
    tools.update_item("ST-1", "confirmed", tool_context=ctx, source="client")
    tools.add_risk("Low adoption", "medium", "", tool_context=ctx)  # risks never block approval
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


# ── approval requires explicit client authorization ──────────────────────────

NOT_APPROVAL = [
    # casual praise / agreement
    "Looks good", "looks good!", "LGTM", "Great work, thanks.", "Sounds right to me.",
    "I agree with all of it.", "Perfect, that's exactly what I wanted.",
    "I approve of the direction, but tweak FR-2.",
    # review requests
    "Review PRD v1 before approval.", "Please review the PRD and check the assumptions.",
    "Check that the spec is ready for approval.",
    # readiness checks / questions
    "Is it ready for approval?", "Is the spec ready?", "Can you approve it?", "Ready for approval?",
    "What does approval involve?",
    # negated, deferred, conditional
    "Don't approve yet.", "Do not approve or publish the PRD yet.", "Not approved.",
    "I'll approve once Q-2 is answered.", "I would approve if the budget fits.",
    "Approve it after the review.", "We can approve it later.",
    # the message that wrongly triggered approval in a live run
    "The PRD looks good. Please hand this over to the architect to design the architecture.",
    "", None,
]

EXPLICIT_APPROVAL = [
    "I approve the PRD.", "I approve.", "Approved.", "Yes, approved", "Approve v2",
    "Please approve the PRD.", "Go ahead and mark it approved.", "We hereby approve version 2.",
    "I've signed off.", "We sign off on the spec.", "You have my approval.", "PRD approved - thanks!",
    "Yes, I approve v3. Please publish it.", "I approve the PRD’s v2.",
]


@pytest.mark.parametrize("message", NOT_APPROVAL)
def test_is_not_explicit_approval(message):
    assert logic.is_explicit_approval(message) is False


@pytest.mark.parametrize("message", EXPLICIT_APPROVAL)
def test_is_explicit_approval(message):
    assert logic.is_explicit_approval(message) is True


@pytest.mark.parametrize("message", [
    "Looks good!",
    "Review PRD v1 before approval. Check every assumption is marked.",
    "Is it ready for approval?",
    "The PRD looks good. Please hand this over to the architect.",
])
def test_publish_refuses_approval_without_explicit_authorization(ctx, message):
    minimal_complete_spec(ctx)
    publish(ctx, "draft")
    before = logic.load_spec(ctx.state).model_dump()

    res = publish(ctx, "approve", approved=True, message=message)
    assert "error" in res and "not an explicit approval" in res["error"]
    assert logic.load_spec(ctx.state).model_dump() == before  # no version bump, no log entry
    assert list(ctx.artifacts) == ["prd_v1.md"]


def test_approval_refused_when_the_client_message_is_unavailable(ctx):
    minimal_complete_spec(ctx)
    ctx.user_content = None
    res = asyncio.run(tools.publish_prd("v", True, tool_context=ctx))
    assert "error" in res
    assert logic.load_spec(ctx.state).meta.version == 0


def test_explicit_approval_approves_and_keeps_versioning_and_history(ctx):
    minimal_complete_spec(ctx)
    publish(ctx, "first draft")
    res = publish(ctx, "client approved", approved=True, message="Yes, I approve v1.")
    assert (res["version"], res["status"]) == (2, "approved")
    log = logic.load_spec(ctx.state).meta.change_log
    assert [(c.version, c.summary) for c in log] == [(1, "first draft"), (2, "client approved")]
    assert set(ctx.artifacts) == {"prd_v1.md", "prd_v2.md"}


def test_publish_refused_when_client_says_not_to_publish(ctx):
    minimal_complete_spec(ctx)
    res = publish(ctx, "draft", message="Make those edits, but do not publish the PRD yet.")
    assert "error" in res
    assert logic.load_spec(ctx.state).meta.version == 0 and not ctx.artifacts


NOT_PUBLISH_REQUEST = [
    "Looks good", "Here are my answers.", "Thanks, that covers it.",
    "Review the PRD and check the assumptions.", "Is it ready to publish?", "Should we publish it?",
    "Publish it once Q-2 is answered.", "Don't publish yet.", "Make the edits but do not publish.",
    "Please hand this over to the architect and publish it.", "Publish the architecture.",
    "The PRD looks good. Please hand this over to the architect to design the technical "
    "architecture, tech stack and project structure, and publish it.",
    "", None,
]

PUBLISH_REQUEST = [
    "Please publish the PRD.", "Publish it.", "Yes, publish v2", "Go ahead and publish the draft.",
    "OK, republish the spec.", "I want you to publish the PRD now.", "Can you publish the PRD?",
    "Thanks! Please publish it so I can review.", "I approve the PRD.",
]


@pytest.mark.parametrize("message", NOT_PUBLISH_REQUEST)
def test_is_not_publish_request(message):
    assert logic.is_explicit_publish_request(message) is False


@pytest.mark.parametrize("message", PUBLISH_REQUEST)
def test_is_publish_request(message):
    assert logic.is_explicit_publish_request(message) is True


@pytest.mark.parametrize("message", ["Looks good!", "Here are my answers.",
                                     "Please hand this over to the architect and publish it."])
def test_draft_publish_refused_without_explicit_request(ctx, message):
    minimal_complete_spec(ctx)
    res = publish(ctx, "draft", message=message)
    assert "error" in res and "not explicitly asked" in res["error"]
    assert logic.load_spec(ctx.state).meta.version == 0 and not ctx.artifacts


def test_draft_publish_on_explicit_request(ctx):
    minimal_complete_spec(ctx)
    res = publish(ctx, "draft", message="Please publish the PRD so I can review it.")
    assert (res["version"], res["status"]) == (1, "in_review")
