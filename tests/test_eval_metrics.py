"""The custom adk-eval metrics and the eval set itself. Deterministic: no model calls."""

import importlib
import json
from pathlib import Path

import pytest
from google.adk.evaluation.eval_case import IntermediateData, Invocation
from google.adk.evaluation.eval_config import EvalConfig
from google.adk.evaluation.eval_metrics import EvalStatus
from google.adk.evaluation.eval_set import EvalSet
from google.genai import types

from evals import build_evalset, metrics

EVALS = Path(__file__).resolve().parent.parent / "evals"


def turn(text, *calls):
    return Invocation(
        user_content=types.Content(role="user", parts=[types.Part(text=text)]),
        intermediate_data=IntermediateData(tool_uses=[types.FunctionCall(name=n, args=a) for n, a in calls]),
    )


def status(metric, actual, expected=None):
    return metric(None, actual, expected).overall_eval_status


# ── publish_policy ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, call, passed", [
    ("Looks good!", ("publish_prd", {"mark_approved": False}), False),
    ("Is it ready for approval?", ("publish_prd", {"mark_approved": True}), False),
    ("Please publish the PRD.", ("publish_prd", {"mark_approved": True}), False),  # publish ≠ approve
    ("Do not publish or approve the PRD. Hand it to the architect.", ("publish_prd", {"mark_approved": False}), False),
    ("Please publish the PRD.", ("publish_prd", {"mark_approved": False}), True),
    ("I approve the PRD.", ("publish_prd", {"mark_approved": True}), True),
    ("Looks good!", ("review_spec", {}), True),
])
def test_publish_policy(text, call, passed):
    expected = EvalStatus.PASSED if passed else EvalStatus.FAILED
    assert status(metrics.publish_policy, [turn(text, call)]) == expected


def test_publish_policy_scores_each_turn():
    result = metrics.publish_policy(None, [turn("Looks good", ("publish_prd", {})), turn("Please publish the PRD.")])
    assert [r.score for r in result.per_invocation_results] == [0.0, 1.0]
    assert result.overall_score == 0.5 and result.overall_eval_status == EvalStatus.FAILED


# ── key_args_match ───────────────────────────────────────────────────────────

def test_key_args_match_is_a_subset_match():
    expected = [turn("x", ("publish_prd", {"mark_approved": True}))]
    good = [turn("x", ("review_spec", {}), ("publish_prd", {"mark_approved": True, "change_summary": "anything"}))]
    wrong = [turn("x", ("publish_prd", {"mark_approved": False, "change_summary": "v2"}))]
    missing = [turn("x", ("review_spec", {}))]
    assert status(metrics.key_args_match, good, expected) == EvalStatus.PASSED
    assert status(metrics.key_args_match, wrong, expected) == EvalStatus.FAILED
    assert status(metrics.key_args_match, missing, expected) == EvalStatus.FAILED


def test_key_args_match_ignores_expected_calls_without_args():
    expected = [turn("x", ("inspect_spec", {}), ("transfer_to_agent", {"agent_name": "dev_agent"}))]
    actual = [turn("x", ("transfer_to_agent", {"agent_name": "dev_agent"}))]
    assert status(metrics.key_args_match, actual, expected) == EvalStatus.PASSED


# ── simple_stack_policy ──────────────────────────────────────────────────────

@pytest.mark.parametrize("call, passed", [
    (("recommend_tech_stack", {"category": "cache", "technology": "Redis", "rationale": "faster"}), False),
    (("recommend_tech_stack", {"category": "queue", "technology": "Celery", "rationale": "jobs"}), False),
    (("recommend_tech_stack", {"category": "cache", "technology": "None (not needed)", "rationale": "MVP"}), False),
    (("add_architecture_component", {"name": "Redis Cache", "role": "cache"}), False),
    (("recommend_tech_stack", {"category": "cache", "technology": "Redis", "rationale": "NFR-1 needs it"}), True),
    (("add_architecture_component", {"name": "Redis Cache", "role": "cache", "addresses": ["NFR-1"]}), True),
    (("recommend_tech_stack", {"category": "backend", "technology": "FastAPI", "rationale": "simple"}), True),
])
def test_simple_stack_policy(call, passed):
    expected = EvalStatus.PASSED if passed else EvalStatus.FAILED
    assert status(metrics.simple_stack_policy, [turn("design it", call)]) == expected


# ── the eval set and config ──────────────────────────────────────────────────

def test_evalset_file_is_up_to_date_with_the_generator():
    on_disk = EvalSet.model_validate_json((EVALS / "agentcorp.evalset.json").read_text(encoding="utf-8"))
    assert on_disk.model_dump() == build_evalset.build().model_dump(), \
        "Regenerate with: python -m evals.build_evalset"


def test_config_metrics_resolve_to_callables():
    config = EvalConfig.model_validate(json.loads((EVALS / "test_config.json").read_text()))
    for name, custom in config.custom_metrics.items():
        module, func = custom.code_config.name.rsplit(".", 1)
        assert callable(getattr(importlib.import_module(module), func)), name
        assert name in config.criteria


def test_published_fixture_is_ready_for_approval_but_unpublished_changes_remain():
    from pm_agent import spec_logic
    spec = spec_logic.load_spec(build_evalset.published_spec_state())
    assert spec.meta.version == 1 and spec.meta.pending_changes
    assert spec_logic.review(spec)["ready_for_approval"]


def test_everything_imports_when_adk_eval_loads_the_agent_its_way():
    """adk eval registers pm_agent/__init__.py as the module `agent`. Anything that then
    imports `pm_agent` builds a second agent tree and crashes ("already has a parent").
    Metrics and the Dev agent must read the spec through `shared` instead."""
    import subprocess
    import sys

    script = """
import json, sys
from google.adk.cli.cli_eval import _get_agent_module
from google.adk.evaluation.custom_metric_evaluator import _get_metric_function
from google.adk.evaluation.eval_config import EvalConfig

_get_agent_module("pm_agent")                       # exactly what `adk eval pm_agent` does
config = EvalConfig.model_validate(json.load(open("evals/test_config.json")))
for custom in config.custom_metrics.values():
    _get_metric_function(custom.code_config.name)   # how ADK loads custom metrics

from dev_agent import tools
from dev_agent.prompt import instruction
class Ctx: state = {}
tools._load_spec_safely({}); instruction(Ctx())
assert "pm_agent" not in sys.modules, "something imported pm_agent a second time"
print("OK")
"""
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run([sys.executable, "-W", "ignore", "-c", script], cwd=root,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0 and "OK" in result.stdout, result.stderr[-2000:]
