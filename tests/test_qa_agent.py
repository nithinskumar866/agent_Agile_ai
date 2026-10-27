"""QA agent: tests before code, locked tests, the Docker runner, and the run budget.

Most tests use FakeFilesystem and a fake runner. Tests marked `docker` run real
containers; they are skipped when the Docker engine is not running or
AGENTCORP_SKIP_DOCKER_TESTS is set.
"""

import asyncio
import os

import pytest

from dev_agent import tools as dev_tools
from dev_agent.agent import dev_agent
from pm_agent import tools as pm_tools
from qa_agent import logic, runner
from qa_agent import tools as qa_tools
from qa_agent.agent import qa_agent
from qa_agent.prompt import instruction as qa_instruction
from shared.spec import load_spec
from shared.testsuite import MAX_TEST_RUNS, TestSuite, load_suite
from tests.test_workspace import FakeContext, FakeFilesystem, ctx, say, ws  # noqa: F401 — fixtures

needs_docker = pytest.mark.skipif(
    bool(os.getenv("AGENTCORP_SKIP_DOCKER_TESTS")) or runner.docker_problem() is not None,
    reason="needs a running Docker engine",
)

TEST_FILE = '''\
from app.calc import add, parse_amount
import pytest


def test_adds_two_numbers():
    assert add(2, 3) == 5


@pytest.mark.parametrize("bad", ["", "abc"])
def test_rejects_invalid_amount(bad):
    with pytest.raises(ValueError):
        parse_amount(bad)


class TestNetwork:
    def test_no_network_in_the_sandbox(self):
        import socket
        with pytest.raises(OSError):
            socket.create_connection(("1.1.1.1", 53), timeout=3)
'''
GOOD_CODE = '''\
def add(a, b):
    return a + b


def parse_amount(text):
    if not text.strip().isdigit():
        raise ValueError(text)
    return int(text)
'''
CASES = [
    {"criterion_id": "FR-1.AC1", "test": "qa_tests/test_calc.py::test_adds_two_numbers"},
    {"criterion_id": "FR-1.AC2", "test": "qa_tests/test_calc.py::test_rejects_invalid_amount"},
    {"criterion_id": "NFR-1.AC1", "test": "qa_tests/test_calc.py::TestNetwork::test_no_network_in_the_sandbox"},
]


def use_fakes(monkeypatch, results=None, error=None, docker=None):
    monkeypatch.setattr(qa_tools, "open_filesystem", lambda sandbox: FakeFilesystem(sandbox))
    monkeypatch.setattr(dev_tools, "open_filesystem", lambda sandbox: FakeFilesystem(sandbox))
    monkeypatch.setattr(qa_tools, "docker_problem", lambda: docker)

    def fake_run(project_path, suite):
        if error:
            raise runner.RunnerError(error)
        return runner.RunOutcome(exit_code=0, results=results or {}, output="fake output")
    monkeypatch.setattr(qa_tools, "run_suite", fake_run)


def approved_project(ctx, approve=True):
    """An approved PRD (v1) and a published architecture for a small Python app."""
    pm_tools.set_engagement("new_build", "finance", "Calculator service", "", tool_context=ctx)
    pm_tools.add_text_items("goals", ["Do sums"], "client", tool_context=ctx)
    pm_tools.add_text_items("success_metrics", ["Correct sums"], "client", tool_context=ctx)
    pm_tools.add_text_items("in_scope", ["Adding"], "client", tool_context=ctx)
    pm_tools.add_text_items("out_of_scope", ["Division"], "client", tool_context=ctx)
    pm_tools.add_requirement("functional", "Add amounts", "must", "client", tool_context=ctx,
                             acceptance_criteria=["2 + 3 gives 5", "Invalid amounts are rejected"])
    pm_tools.add_requirement("non_functional", "Runs offline", "must", "client", tool_context=ctx,
                             category="security", acceptance_criteria=["No network access is needed"])
    say(ctx, "I approve the PRD." if approve else "Please publish the PRD.")
    res = asyncio.run(pm_tools.publish_prd("v1", approve, tool_context=ctx))
    assert res.get("ok"), res
    dev_tools.recommend_tech_stack("backend", "Python", "Simple", tool_context=ctx)
    dev_tools.add_project_scaffold([{"path": "backend/app/", "description": "App package"},
                                    {"path": "backend/requirements.txt", "description": "Deps"}], tool_context=ctx)
    assert asyncio.run(dev_tools.publish_architecture("v1", tool_context=ctx))["ok"]


def write(ctx, files=None, cases=None, **kw):
    kw.setdefault("app_dir", "backend")
    kw.setdefault("project", "calc")
    return asyncio.run(qa_tools.write_test_suite(
        files if files is not None else [{"path": "qa_tests/test_calc.py", "content": TEST_FILE}],
        cases if cases is not None else CASES, tool_context=ctx, **kw))


def locked_project(ctx, code=GOOD_CODE):
    approved_project(ctx)
    assert write(ctx)["ok"]
    assert qa_tools.lock_test_suite(tool_context=ctx)["ok"]
    res = asyncio.run(dev_tools.implement_scaffold(
        [{"path": "backend/app/__init__.py", "content": ""}, {"path": "backend/app/calc.py", "content": code},
         {"path": "backend/requirements.txt", "content": ""}], tool_context=ctx))
    assert res.get("ok"), res


def junit(*passed, failed=()):
    results = {logic.junit_key(t): ("passed", "") for t in passed}
    results.update({logic.junit_key(t): ("failed", "failure: assert 1 == 2") for t in failed})
    return results


ALL_PASS = junit(*(c["test"] for c in CASES))


# ── what must be tested ──────────────────────────────────────────────────────

def test_criteria_are_the_must_acceptance_criteria(ws, ctx):
    approved_project(ctx)
    pm_tools.add_requirement("functional", "Dark mode", "could", "client", tool_context=ctx,
                             acceptance_criteria=["Toggle exists"])
    out = qa_tools.inspect_acceptance_criteria(tool_context=ctx)
    assert [c["id"] for c in out["criteria"]] == ["FR-1.AC1", "FR-1.AC2", "NFR-1.AC1"]
    assert out["must_items_without_criteria"] == []


def test_must_without_criteria_blocks_the_lock(ws, ctx, monkeypatch):
    use_fakes(monkeypatch)
    approved_project(ctx)
    assert write(ctx)["ok"]
    suite = load_suite(ctx.state)
    spec = load_spec(ctx.state)
    spec.functional_requirements[0].acceptance_criteria = []
    assert "without acceptance criteria" in logic.lock_blocker(suite, spec, 1, False)


# ── writing the draft ────────────────────────────────────────────────────────

def test_write_refused_before_architecture(ws, ctx, monkeypatch):
    use_fakes(monkeypatch)
    say(ctx, "x")
    assert "architecture" in write(ctx)["error"]


@pytest.mark.parametrize("files, cases, kw, problem", [
    ([{"path": "backend/test_x.py", "content": "def test_a(): pass"}], [], {}, "under qa_tests/"),
    ([{"path": "qa_tests/notes.txt", "content": "x"}], [], {}, "under qa_tests/"),
    ([{"path": "qa_tests/../x.py", "content": "x"}], [], {}, "leave the project folder"),
    (None, [{"criterion_id": "FR-9.AC1", "test": "qa_tests/test_calc.py::test_adds_two_numbers"}], {},
     "not an acceptance criterion"),
    (None, [{"criterion_id": "FR-1.AC1", "test": "qa_tests/test_calc.py::test_missing"}], {}, "no such test"),
    (None, [{"criterion_id": "FR-1.AC1", "test": "qa_tests/other.py::test_a"}], {}, "not one of the files"),
    (None, [{"criterion_id": "FR-1.AC1", "test": "qa_tests/test_calc.py"}], {}, "node ID"),
    (None, CASES, {"test_dependencies": ["httpx"]}, "pinned"),
    (None, CASES, {"app_dir": "server"}, "not a folder in the published scaffold"),
    (None, CASES, {"manual_checks": [{"criterion_id": "FR-1.AC1", "reason": " "}]}, "needs a reason"),
])
def test_invalid_drafts_write_nothing(ws, ctx, monkeypatch, files, cases, kw, problem):
    use_fakes(monkeypatch)
    approved_project(ctx)
    res = write(ctx, files, cases, **kw)
    assert "error" in res and any(problem in p for p in res["problems"]), res
    assert FakeFilesystem.calls == [] and not load_suite(ctx.state).files


def test_write_puts_tests_in_the_sandbox_and_fixes_the_project(ws, ctx, monkeypatch):
    use_fakes(monkeypatch)
    approved_project(ctx)
    res = write(ctx, cases=CASES[:2])
    assert res["ok"] and res["uncovered_criteria"] == ["NFR-1.AC1"]
    assert (ws / "calc/qa_tests/test_calc.py").read_text(encoding="utf-8") == TEST_FILE
    assert dev_tools.logic.load_tech_design(ctx.state).implementation.project == "calc"
    suite = load_suite(ctx.state)
    assert not suite.locked and suite.app_dir == "backend" and len(suite.files[0].sha256) == 64


def test_write_failure_saves_no_draft(ws, ctx, monkeypatch):
    use_fakes(monkeypatch)
    monkeypatch.setattr(qa_tools, "open_filesystem",
                        lambda sandbox: FakeFilesystem(sandbox, fail_write_on="test_calc.py"))
    approved_project(ctx)
    assert "draft was not saved" in write(ctx)["error"]
    assert not load_suite(ctx.state).files


# ── locking ──────────────────────────────────────────────────────────────────

def test_lock_needs_an_approved_prd(ws, ctx, monkeypatch):
    use_fakes(monkeypatch)
    approved_project(ctx, approve=False)
    assert write(ctx)["ok"]
    assert "client must approve" in qa_tools.lock_test_suite(tool_context=ctx)["error"]


def test_lock_needs_every_criterion_covered(ws, ctx, monkeypatch):
    use_fakes(monkeypatch)
    approved_project(ctx)
    write(ctx, cases=CASES[:2])
    assert "NFR-1.AC1" in qa_tools.lock_test_suite(tool_context=ctx)["error"]
    write(ctx, cases=CASES[:2], manual_checks=[{"criterion_id": "NFR-1.AC1", "reason": "Needs a real network"}])
    res = qa_tools.lock_test_suite(tool_context=ctx)
    assert res["ok"] and res["manual_checks"] == ["NFR-1.AC1"]


def test_locked_tests_cannot_be_rewritten(ws, ctx, monkeypatch):
    use_fakes(monkeypatch)
    approved_project(ctx)
    write(ctx)
    assert qa_tools.lock_test_suite(tool_context=ctx)["version"] == 1
    assert "is locked" in write(ctx, files=[{"path": "qa_tests/test_calc.py", "content": "def test_adds_two_numbers(): pass"}])["error"]
    assert "already locked" in qa_tools.lock_test_suite(tool_context=ctx)["error"]


def test_a_prd_change_reopens_the_tests_and_blocks_runs(ws, ctx, monkeypatch):
    use_fakes(monkeypatch, results=ALL_PASS)
    locked_project(ctx)
    pm_tools.add_text_items("constraints", ["Python 3.12"], "client", tool_context=ctx)
    say(ctx, "I approve the PRD.")
    assert asyncio.run(pm_tools.publish_prd("v2", True, tool_context=ctx))["ok"]
    assert "re-lock" in asyncio.run(qa_tools.run_tests(tool_context=ctx))["error"]
    assert "STALE" in logic.status_line(load_suite(ctx.state), load_spec(ctx.state))
    asyncio.run(dev_tools.publish_architecture("v2", tool_context=ctx))
    assert write(ctx)["ok"]
    assert qa_tools.lock_test_suite(tool_context=ctx)["version"] == 2


# ── running ──────────────────────────────────────────────────────────────────

def test_run_refused_without_locked_tests_or_code(ws, ctx, monkeypatch):
    use_fakes(monkeypatch, results=ALL_PASS)
    approved_project(ctx)
    assert "lock the tests" in asyncio.run(qa_tools.run_tests(tool_context=ctx))["error"]
    write(ctx)
    qa_tools.lock_test_suite(tool_context=ctx)
    assert "implement_scaffold" in asyncio.run(qa_tools.run_tests(tool_context=ctx))["error"]


def test_run_refused_when_docker_is_down(ws, ctx, monkeypatch):
    use_fakes(monkeypatch, docker="The Docker engine is not running.")
    locked_project(ctx)
    assert "not running" in asyncio.run(qa_tools.run_tests(tool_context=ctx))["error"]
    assert load_suite(ctx.state).runs == []


def test_passing_run_is_recorded_by_the_system(ws, ctx, monkeypatch):
    use_fakes(monkeypatch, results=ALL_PASS)
    locked_project(ctx)
    res = asyncio.run(qa_tools.run_tests(tool_context=ctx))
    assert res["outcome"] == "passed" and res["passed"] == 3 and res["verdict"] == "passed"
    assert load_suite(ctx.state).runs[0].outcome == "passed"


def test_failing_and_missing_tests_fail_the_run(ws, ctx, monkeypatch):
    use_fakes(monkeypatch, results=junit(CASES[0]["test"], failed=[CASES[1]["test"]]))
    locked_project(ctx)
    res = asyncio.run(qa_tools.run_tests(tool_context=ctx))
    assert res["outcome"] == "failed" and (res["passed"], res["failed"], res["missing"]) == (1, 1, 1)
    assert "never the tests" in res["next"]


def test_runner_error_is_recorded_as_an_error_run(ws, ctx, monkeypatch):
    use_fakes(monkeypatch, error="Installing dependencies failed")
    locked_project(ctx)
    res = asyncio.run(qa_tools.run_tests(tool_context=ctx))
    assert res["outcome"] == "error" and "Installing" in res["error"]


def test_run_budget_stops_the_loop(ws, ctx, monkeypatch):
    use_fakes(monkeypatch, results=junit(failed=[c["test"] for c in CASES]))
    locked_project(ctx)
    for _ in range(MAX_TEST_RUNS):
        res = asyncio.run(qa_tools.run_tests(tool_context=ctx))
    assert res["runs_left"] == 0 and res["verdict"] == "escalated" and "report to the client" in res["next"]
    assert "budget" in asyncio.run(qa_tools.run_tests(tool_context=ctx))["error"]


def test_grading_rules():
    suite = TestSuite(cases=CASES)
    assert logic.junit_key(CASES[2]["test"]) == ("qa_tests.test_calc.TestNetwork", "test_no_network_in_the_sandbox")
    results = {("qa_tests.test_calc", "test_adds_two_numbers"): ("passed", ""),
               ("qa_tests.test_calc", "test_rejects_invalid_amount[]"): ("passed", ""),
               ("qa_tests.test_calc", "test_rejects_invalid_amount[abc]"): ("failed", "skipped: later")}
    outcomes = [c.outcome for c in logic.grade(suite, results)]
    assert outcomes == ["passed", "failed", "missing"]


def test_skipped_tests_do_not_pass():
    xml = ('<testsuites><testsuite><testcase classname="qa_tests.t" name="test_a">'
           '<skipped message="not ready"/></testcase></testsuite></testsuites>')
    assert runner.parse_junit(xml)[("qa_tests.t", "test_a")][0] == "failed"


def test_report_lists_every_criterion(ws, ctx, monkeypatch):
    use_fakes(monkeypatch, results=junit(CASES[0]["test"], CASES[2]["test"], failed=[CASES[1]["test"]]))
    locked_project(ctx)
    asyncio.run(qa_tools.run_tests(tool_context=ctx))
    res = asyncio.run(qa_tools.publish_test_report(tool_context=ctx))
    md = ctx.artifacts[res["artifact"]]
    assert res["artifact"] == "qa_report_v1.md" and "**Verdict:** FAILED" in md
    assert all(cid in md for cid in ("FR-1.AC1", "FR-1.AC2", "NFR-1.AC1")) and "## Failures" in md


# ── wiring and prompts ───────────────────────────────────────────────────────

def test_roles_are_separated():
    qa_names = {getattr(t, "__name__", "") for t in qa_agent.tools}
    dev_names = {getattr(t, "__name__", "") for t in dev_agent.tools}
    assert "implement_scaffold" not in qa_names and "write_test_suite" not in dev_names
    assert "run_tests" in qa_names and "run_tests" in dev_names
    assert qa_agent.parent_agent.name == "product_manager"


def test_qa_prompt_forbids_invented_conditions_and_test_weakening(ctx):
    text = qa_instruction(ctx)
    assert "Never invent a condition" in text and "do not change it" in text


# ── the real Docker runner ───────────────────────────────────────────────────

@pytest.mark.docker
@needs_docker
def test_real_run_passes_good_code_and_isolates_it(ws, ctx, monkeypatch):
    use_fakes(monkeypatch)
    monkeypatch.setattr(qa_tools, "run_suite", runner.run_suite)
    monkeypatch.setattr(qa_tools, "docker_problem", runner.docker_problem)
    locked_project(ctx)
    res = asyncio.run(qa_tools.run_tests(tool_context=ctx))
    # 3 cases pass, including the one proving the container has no network.
    assert res["outcome"] == "passed", (res.get("failures"), res.get("output_tail"))
    assert not (ws / ".qa-runs").exists() or not any((ws / ".qa-runs").iterdir())


@pytest.mark.docker
@needs_docker
def test_real_run_ignores_tampering_on_disk(ws, ctx, monkeypatch):
    """Broken code, plus every trick to make it look green: weakened test file on disk,
    a root conftest.py that skips everything, and a pytest config that deselects all."""
    use_fakes(monkeypatch)
    monkeypatch.setattr(qa_tools, "run_suite", runner.run_suite)
    monkeypatch.setattr(qa_tools, "docker_problem", runner.docker_problem)
    locked_project(ctx, code="def add(a, b):\n    return a - b\n\ndef parse_amount(t):\n    return 0\n")
    project = ws / "calc"
    (project / "qa_tests/test_calc.py").write_text("def test_adds_two_numbers(): pass\n", encoding="utf-8")
    (project / "conftest.py").write_text(
        "import pytest\ndef pytest_collection_modifyitems(items):\n"
        "    for i in items: i.add_marker(pytest.mark.skip)\n", encoding="utf-8")
    (project / "pytest.ini").write_text("[pytest]\naddopts = -k nothing_matches\n", encoding="utf-8")
    res = asyncio.run(qa_tools.run_tests(tool_context=ctx))
    assert res["outcome"] == "failed", res
    failed = {f["test"] for f in res["failures"]}
    assert failed == {CASES[0]["test"], CASES[1]["test"]}


# ── model quirks ─────────────────────────────────────────────────────────────

def test_list_arguments_sent_as_json_strings_are_accepted(ws, ctx, monkeypatch):
    """Seen live with Nemotron: `files` arrived as a JSON string and every batch was refused."""
    import json
    use_fakes(monkeypatch)
    approved_project(ctx)
    res = write(ctx, files=json.dumps([{"path": "qa_tests/test_calc.py", "content": TEST_FILE}]),
                cases=json.dumps(CASES), test_dependencies=json.dumps(["httpx==0.27.2"]))
    assert res["ok"], res
    qa_tools.lock_test_suite(tool_context=ctx)
    res = asyncio.run(dev_tools.implement_scaffold(json.dumps(
        [{"path": "backend/app/__init__.py", "content": ""}, {"path": "backend/app/calc.py", "content": GOOD_CODE}]),
        tool_context=ctx))
    assert res["ok"] and len(res["written"]) == 2


def test_qa_hands_over_to_dev_only_when_the_client_asks(ctx):
    assert "only when the client asks for the build" in qa_instruction(ctx)
