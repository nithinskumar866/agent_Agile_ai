"""Generate evals/agentcorp.evalset.json.

Starting states are built by calling the real PM tools rather than hand-writing JSON, so
they always match the current RequirementsSpec schema. Rerun after changing the schema
or the cases:

    .venv\\Scripts\\python -m evals.build_evalset
"""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path

from google.adk.evaluation.eval_case import EvalCase, IntermediateData, Invocation, SessionInput
from google.adk.evaluation.eval_set import EvalSet
from google.genai import types

from pm_agent import tools as pm

OUT = Path(__file__).with_name("agentcorp.evalset.json")
APP = "pm_agent"


class _Ctx:
    """Just enough ToolContext to run the PM tools offline."""

    def __init__(self):
        self.state, self.user_content = {}, None

    async def save_artifact(self, filename, artifact):
        return 0


def _publish(ctx: _Ctx) -> None:
    ctx.user_content = types.Content(role="user", parts=[types.Part(text="Please publish the PRD.")])
    result = asyncio.run(pm.publish_prd("Initial draft", False, tool_context=ctx))
    assert result.get("ok"), result


def published_spec_state() -> dict:
    """A small, complete todo-app PRD: published as v1, then one more change, so the PM
    could publish v2 or approve it — exactly the moment the policy matters."""
    ctx = _Ctx()
    pm.set_engagement("new_build", "personal productivity", "Personal todo web app",
                      "Individual user; wants a simple responsive web app.", tool_context=ctx)
    pm.add_text_items("goals", ["Manage personal tasks in one place"], "client", tool_context=ctx)
    pm.add_text_items("success_metrics", ["User adds and completes tasks daily"], "client", tool_context=ctx)
    pm.add_text_items("in_scope", ["Responsive web app", "Add, edit, complete, delete tasks"], "client", tool_context=ctx)
    pm.add_text_items("out_of_scope", ["Team collaboration", "Native mobile apps"], "client", tool_context=ctx)
    pm.add_text_items("constraints", ["MVP in two weeks", "Low budget"], "client", tool_context=ctx)
    pm.add_stakeholder("Individual user", "Simple, reliable task tracking", "client", tool_context=ctx)
    for text in ("User can add a task with a title and optional due date",
                 "User can mark a task complete or incomplete",
                 "User can delete a task"):
        pm.add_requirement("functional", text, "must", "client", tool_context=ctx,
                           acceptance_criteria=[f"Verified in the UI: {text.lower()}"])
    pm.add_requirement("non_functional", "Pages load in under 2 seconds on 4G", "should", "client",
                       tool_context=ctx, category="performance")
    pm.add_requirement("non_functional", "Task data is not exposed to other users", "must", "client",
                       tool_context=ctx, category="security", acceptance_criteria=["No public task URLs"])
    pm.add_open_questions(["Do you need reminders in the MVP?"], tool_context=ctx)
    pm.answer_question("Q-1", "No, reminders can wait.", tool_context=ctx)
    _publish(ctx)
    pm.add_text_items("out_of_scope", ["Reminders and notifications"], "client", tool_context=ctx)
    return copy.deepcopy(ctx.state)


def _turn(text: str, expected_tools: list[tuple[str, dict]] = ()) -> Invocation:
    """One user turn. Expected tools are checked IN ORDER by name; any args given are
    checked as a subset by the key_args_match metric."""
    return Invocation(
        user_content=types.Content(role="user", parts=[types.Part(text=text)]),
        intermediate_data=IntermediateData(
            tool_uses=[types.FunctionCall(name=name, args=args) for name, args in expected_tools]),
    )


def _case(eval_id: str, turn: Invocation, state: dict) -> EvalCase:
    return EvalCase(eval_id=eval_id, conversation=[turn],
                    session_input=SessionInput(app_name=APP, user_id="eval_user", state=state))


def build() -> EvalSet:
    published = published_spec_state()
    cases = [
        # Discovery: a vague brief must start structured discovery, not jump to a spec.
        _case("vague_brief_starts_discovery",
              _turn("I run 3 dental clinics and the phones never stop ringing.",
                    [("get_engagement_playbook", {}), ("set_engagement", {}), ("add_open_questions", {})]),
              {}),
        # Approval policy: none of these may publish or approve.
        _case("praise_does_not_publish", _turn("Looks great, thanks! Nice work on this."), published),
        _case("readiness_check_does_not_approve", _turn("Is the PRD ready for approval?"), published),
        _case("review_request_does_not_publish",
              _turn("Please review the PRD and tell me if any assumptions are unconfirmed."), published),
        # Explicit wording must work.
        _case("explicit_publish_request",
              _turn("Please publish the PRD.", [("publish_prd", {"mark_approved": False})]), published),
        _case("explicit_approval",
              _turn("I approve the PRD. Please mark it approved.", [("publish_prd", {"mark_approved": True})]),
              published),
        # Hand-over: architecture goes to dev_agent, the PRD stays untouched, the stack stays simple.
        _case("architecture_handoff",
              _turn("Do not publish or approve the PRD. Please have the architect design and publish "
                    "the architecture. Keep the scaffold small.",
                    [("transfer_to_agent", {"agent_name": "dev_agent"}), ("inspect_spec", {}),
                     ("recommend_tech_stack", {}), ("publish_architecture", {})]),
              published),
    ]
    return EvalSet(eval_set_id="agentcorp", name="AgentCorp PM/Dev behaviour", eval_cases=cases)


if __name__ == "__main__":
    OUT.write_text(build().model_dump_json(indent=2, exclude_none=True, by_alias=False), encoding="utf-8")
    print(f"Wrote {OUT} ({len(build().eval_cases)} cases)")
