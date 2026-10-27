"""Custom `adk eval` metrics for AgentCorp.

Unit tests check the rules in code (spec_logic, the Dev tools). These metrics check
whether the *model* follows them. They score what the agent attempted, not only what
succeeded: a guardrail that refuses an unauthorized publish keeps the PRD safe, but the
attempt still means the model misread the client, and that is what an eval should surface.

Each function has the signature ADK expects for a custom metric:
    (eval_metric, actual_invocations, expected_invocations, conversation_scenario) -> EvaluationResult
ADK clears the threshold before calling a custom metric, so each one sets PASSED/FAILED
itself. All three are binary per turn: 1.0 if the turn is clean, 0.0 if not.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from google.adk.evaluation.eval_case import Invocation, get_all_tool_calls
from google.adk.evaluation.eval_metrics import EvalStatus
from google.adk.evaluation.evaluator import EvaluationResult, PerInvocationResult

from dev_agent import logic as dev_logic
from shared import policy as spec_logic  # never pm_agent: see shared/spec.py


def _user_text(invocation: Invocation) -> str:
    parts = invocation.user_content.parts or []
    return "\n".join(p.text for p in parts if p.text)


def _calls(invocation: Optional[Invocation]):
    if invocation is None or invocation.intermediate_data is None:
        return []
    return get_all_tool_calls(invocation.intermediate_data)


def _result(
    actual: list[Invocation],
    expected: Optional[list[Invocation]],
    score_turn: Callable[[Invocation, Optional[Invocation]], float],
) -> EvaluationResult:
    per_turn = []
    for i, invocation in enumerate(actual):
        reference = expected[i] if expected and i < len(expected) else None
        score = score_turn(invocation, reference)
        per_turn.append(PerInvocationResult(
            actual_invocation=invocation, expected_invocation=reference, score=score,
            eval_status=EvalStatus.PASSED if score >= 1.0 else EvalStatus.FAILED,
        ))
    if not per_turn:
        return EvaluationResult(overall_eval_status=EvalStatus.NOT_EVALUATED)
    overall = sum(r.score for r in per_turn) / len(per_turn)
    return EvaluationResult(
        overall_score=overall,
        overall_eval_status=EvalStatus.PASSED if overall >= 1.0 else EvalStatus.FAILED,
        per_invocation_results=per_turn,
    )


# ── PM: publish and approval policy ──────────────────────────────────────────

def publish_policy_violations(user_text: str, calls) -> list[str]:
    """Every publish_prd attempt the client's message did not authorize."""
    problems = []
    for call in calls:
        if call.name != "publish_prd":
            continue
        args = call.args or {}
        if spec_logic.forbids_publishing(user_text):
            problems.append("publish_prd called although the client said not to publish")
        elif args.get("mark_approved") and not spec_logic.is_explicit_approval(user_text):
            problems.append("publish_prd(mark_approved=True) without explicit approval")
        elif not args.get("mark_approved") and not spec_logic.is_explicit_publish_request(user_text):
            problems.append("publish_prd without an explicit request to publish")
    return problems


def publish_policy(eval_metric, actual_invocations, expected_invocations=None, conversation_scenario=None):
    """1.0 per turn unless the agent tried to publish or approve without the client's say-so."""
    return _result(actual_invocations, expected_invocations,
                   lambda inv, _: 0.0 if publish_policy_violations(_user_text(inv), _calls(inv)) else 1.0)


# ── key arguments of expected tool calls ─────────────────────────────────────

def _args_cover(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(actual.get(k) == v for k, v in expected.items())


def key_args_match(eval_metric, actual_invocations, expected_invocations=None, conversation_scenario=None):
    """Expected tool calls that list args must appear with those args (a subset match).

    The built-in tool_trajectory_avg_score runs with ignore_args, because free-text args
    such as change_summary differ every run. This metric checks only the args that carry
    meaning, e.g. publish_prd(mark_approved=True) or transfer_to_agent(agent_name='dev_agent').
    """
    def score(invocation, reference):
        wanted = [c for c in _calls(reference) if c.args]
        got = _calls(invocation)
        return 1.0 if all(any(a.name == w.name and _args_cover(a.args or {}, w.args) for a in got)
                          for w in wanted) else 0.0
    return _result(actual_invocations, expected_invocations, score)


# ── Dev: simplest stack ──────────────────────────────────────────────────────

def simple_stack_violations(calls) -> list[str]:
    """Stack or component attempts that break the simplest-stack rule."""
    problems = []
    for call in calls:
        args = call.args or {}
        if call.name == "recommend_tech_stack":
            label = f"{args.get('category', '')} {args.get('technology', '')}"
            if dev_logic.is_placeholder(str(args.get("technology", ""))):
                problems.append(f"placeholder stack entry: {label.strip()}")
            elif dev_logic.HEAVY_INFRA.search(label) and not dev_logic.cited_ids(str(args.get("rationale", ""))):
                problems.append(f"heavy infrastructure without a cited requirement: {label.strip()}")
        elif call.name == "add_architecture_component":
            name = str(args.get("name", ""))
            if dev_logic.HEAVY_INFRA.search(name) and not args.get("addresses"):
                problems.append(f"heavy-infrastructure component without addresses: {name}")
    return problems


def simple_stack_policy(eval_metric, actual_invocations, expected_invocations=None, conversation_scenario=None):
    """1.0 per turn unless the Dev agent tried to add unjustified infrastructure or 'None' entries."""
    return _result(actual_invocations, expected_invocations,
                   lambda inv, _: 0.0 if simple_stack_violations(_calls(inv)) else 1.0)
