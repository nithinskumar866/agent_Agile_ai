"""Tools called by the QA agent (and run_tests, which the Dev agent also gets).

The flow they enforce: approved PRD -> published architecture -> tests written and
locked -> code implemented -> run_tests (at most MAX_TEST_RUNS per suite version).
Bad input returns {"error": ...} so the model can correct itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timezone
from typing import List, Optional

from google.adk.tools import ToolContext
from google.genai import types

from dev_agent import logic as dev_logic
from dev_agent import workspace
from shared.args import as_list
from shared.spec import load_spec
from shared.testsuite import (MAX_TEST_RUNS, TEST_DIR, LockedFile, ManualCheck, TestCase, TestFile, TestRun,
                              is_stale, load_suite, save_suite)

from . import logic, runner

MAX_TEST_FILES = 20
_PINNED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,._-]+\])?==[A-Za-z0-9.!+_-]+$")

# Indirections so tests can swap in fakes.
open_filesystem = workspace.McpFilesystemClient
run_suite = runner.run_suite
docker_problem = runner.docker_problem


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def inspect_acceptance_criteria(tool_context: ToolContext) -> dict:
    """Read what must be tested: every acceptance criterion of every 'must' requirement and
    user story in the PRD (each with an ID like FR-1.AC1), the published architecture's
    stack and scaffold, and the current test suite. Call this first.
    """
    spec = load_spec(tool_context.state)
    if not spec.engagement:
        return {"status": "empty", "message": "No PRD yet. Transfer to product_manager."}
    design = dev_logic.load_tech_design(tool_context.state)
    suite = load_suite(tool_context.state)
    return {
        "prd": {"version": spec.meta.version, "status": spec.meta.status, "summary": spec.engagement.summary},
        "criteria": [{"id": c.id, "requirement": c.item_id, "requirement_text": c.item_text, "criterion": c.text}
                     for c in logic.criteria(spec).values()],
        "must_items_without_criteria": logic.musts_without_criteria(spec),
        "constraints": [c.text for c in spec.constraints],
        "architecture": {
            "version": design.version,
            "tech_stack": [f"{t.category}: {t.technology}" for t in design.tech_stack],
            "scaffold": [f"{s.path} — {s.description}" for s in design.project_scaffold],
            "project_folder": design.implementation.project or None,
        },
        "test_suite": logic.status_line(suite, spec),
        "uncovered_criteria": logic.uncovered(suite, spec),
    }


def _validate_files(files) -> tuple[list[TestFile], list[str]]:
    parsed, problems, seen = [], [], set()
    for f in files:
        try:
            f = TestFile.model_validate(f)
            rel = str(workspace.safe_relative_path(f.path))
        except (ValueError, workspace.SandboxViolation) as exc:
            problems.append(str(exc))
            continue
        if not rel.startswith(f"{TEST_DIR}/") or not rel.endswith(".py"):
            problems.append(f"{rel}: test files must be .py files under {TEST_DIR}/.")
        elif rel in seen:
            problems.append(f"{rel} is listed twice.")
        elif len(f.content.encode("utf-8")) > workspace.MAX_FILE_BYTES:
            problems.append(f"{rel} is larger than {workspace.MAX_FILE_BYTES} bytes.")
        else:
            seen.add(rel)
            parsed.append(TestFile(path=rel, content=f.content))
    return parsed, problems


def _validate_cases(cases, files: list[TestFile], valid_ids) -> tuple[list[TestCase], list[str]]:
    contents = {f.path: f.content for f in files}
    parsed, problems, seen = [], [], set()
    for c in cases:
        try:
            c = TestCase.model_validate(c)
            path, classes, name = logic.parse_node_id(c.test)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if c.criterion_id not in valid_ids:
            problems.append(f"{c.criterion_id} is not an acceptance criterion ID from inspect_acceptance_criteria.")
        elif path not in contents:
            problems.append(f"{c.test}: {path} is not one of the files in this call.")
        elif not logic.defines(contents[path], classes, name):
            problems.append(f"{c.test}: no such test defined in {path}.")
        elif (c.criterion_id, c.test.strip()) not in seen:
            seen.add((c.criterion_id, c.test.strip()))
            parsed.append(TestCase(criterion_id=c.criterion_id, test=c.test.strip()))
    return parsed, problems


async def write_test_suite(
    files: List[TestFile],
    cases: List[TestCase],
    app_dir: str,
    tool_context: ToolContext,
    manual_checks: Optional[List[ManualCheck]] = None,
    test_dependencies: Optional[List[str]] = None,
    project: str = "",
) -> dict:
    """Write the draft acceptance tests (pytest) into workspace/<project>/qa_tests/,
    replacing any previous draft. Allowed until the suite is locked. Write tests BEFORE the
    code exists, from the acceptance criteria only, against the interfaces the published
    architecture names.

    Args:
        files: Test files, each with `path` (under qa_tests/, e.g. qa_tests/test_tasks.py)
            and `content`. A qa_tests/conftest.py for shared fixtures is allowed.
        cases: One entry per test function: `criterion_id` (e.g. FR-1.AC1) and `test`
            (pytest node ID, e.g. qa_tests/test_tasks.py::test_add_task). Every criterion
            needs a test or a manual check before the suite can be locked.
        app_dir: The Python app's folder inside the project, holding its requirements.txt
            and packages, e.g. 'backend'. It goes on the import path, so tests import the
            app's own modules, e.g. `from app.main import app`.
        manual_checks: Criteria that cannot be tested automatically, each with
            `criterion_id` and `reason`. They appear in the report for a person to verify.
        test_dependencies: Extra packages the tests need, pinned, e.g. ['httpx==0.27.2'].
            pytest is always installed.
        project: Folder name under workspace/, e.g. 'todo-app'. Needed only if no project
            folder exists yet.
    """
    state = tool_context.state
    spec = load_spec(state)
    suite = load_suite(state)
    if suite.locked and not is_stale(suite, spec.meta.version):
        return {"error": f"Suite v{suite.version} is locked. Tests change only when the PRD changes: if a test "
                         "or criterion is wrong, explain it to the client via product_manager."}
    design = dev_logic.load_tech_design(state)
    if design.version < 1:
        return {"error": "No published architecture yet; the tests need its interfaces. Transfer to dev_agent."}

    valid_ids = logic.criteria(spec)
    parsed_files, problems = _validate_files(as_list(files))
    parsed_cases, case_problems = _validate_cases(as_list(cases), parsed_files, valid_ids)
    problems += case_problems
    checks = []
    for m in as_list(manual_checks) or []:
        try:
            m = ManualCheck.model_validate(m)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if m.criterion_id not in valid_ids:
            problems.append(f"Manual check {m.criterion_id} is not an acceptance criterion ID.")
        elif not m.reason.strip():
            problems.append(f"Manual check {m.criterion_id} needs a reason.")
        else:
            checks.append(ManualCheck(criterion_id=m.criterion_id, reason=m.reason.strip()))
    deps = [str(d).strip() for d in as_list(test_dependencies) or []]
    problems += [f"Test dependency {d!r} must be pinned like 'httpx==0.27.2'." for d in deps if not _PINNED.match(d)]
    if not parsed_files and not problems:
        problems.append("No test files given.")
    if len(parsed_files) > MAX_TEST_FILES:
        problems.append(f"At most {MAX_TEST_FILES} test files.")
    app = app_dir.strip().strip("/\\") or "."
    try:
        if app != ".":
            app = str(workspace.safe_relative_path(app))
    except workspace.SandboxViolation as exc:
        problems.append(f"app_dir: {exc}")
    if app != "." and not any(dev_logic._plain(s.path).startswith(app + "/") for s in design.project_scaffold):
        problems.append(f"app_dir {app!r} is not a folder in the published scaffold.")

    impl = design.implementation
    try:
        slug = impl.project or (workspace.project_slug(project) if project.strip() else "")
        if not slug:
            problems.append("Give a project folder name the first time, e.g. project='todo-app'.")
        sandbox = workspace.project_dir(slug) if slug else None
    except workspace.SandboxViolation as exc:
        problems.append(str(exc))
    if problems:
        return {"error": "Nothing was written. Fix these first.", "problems": problems}

    sandbox.mkdir(parents=True, exist_ok=True)
    try:
        async with open_filesystem(sandbox) as fs:
            for f in parsed_files:
                target = workspace.resolve_in_sandbox(sandbox, workspace.safe_relative_path(f.path))
                await fs.call("create_directory", {"path": str(target.parent)})
                await fs.call("write_file", {"path": str(target), "content": f.content})
                if await fs.call("read_text_file", {"path": str(target)}) != f.content:
                    raise workspace.McpToolFailed(f"{f.path}: read-back did not match")
    except Exception as exc:  # nothing is recorded unless every file was written
        return {"error": f"Writing the tests failed; the draft was not saved: {type(exc).__name__}: {exc}"}

    if not impl.project:
        impl.project = slug
        dev_logic.save_tech_design(state, design)
    suite.locked = False
    suite.app_dir = app
    suite.test_dependencies = deps
    suite.files = [LockedFile(path=f.path, content=f.content,
                              sha256=hashlib.sha256(f.content.encode("utf-8")).hexdigest()) for f in parsed_files]
    suite.cases = parsed_cases
    suite.manual_checks = checks
    suite.change_log.append(f"Draft tests written: {len(parsed_files)} files, {len(parsed_cases)} cases, "
                            f"{len(checks)} manual checks")
    save_suite(state, suite)
    return {"ok": True, "workspace": f"workspace/{slug}/{TEST_DIR}/", "files": [f.path for f in parsed_files],
            "cases": len(parsed_cases), "uncovered_criteria": logic.uncovered(suite, spec),
            "next": "Fix any uncovered criteria, then call lock_test_suite."}


def lock_test_suite(tool_context: ToolContext) -> dict:
    """Lock the draft tests. After this the tests cannot change until the PRD changes, and
    dev_agent may implement. Refused unless the PRD is client-approved, the architecture is
    published, and every acceptance criterion has a test or a manual check.
    """
    state = tool_context.state
    spec, suite = load_spec(state), load_suite(state)
    design = dev_logic.load_tech_design(state)
    if blocker := logic.lock_blocker(suite, spec, design.version, design.has_unpublished_changes):
        return {"error": blocker}
    suite.version += 1
    suite.locked = True
    suite.locked_at = _now()
    suite.based_on_prd_version = spec.meta.version
    suite.change_log.append(f"Locked v{suite.version} for PRD v{spec.meta.version}: {len(suite.cases)} cases, "
                            f"{len(suite.manual_checks)} manual checks")
    save_suite(state, suite)
    return {"ok": True, "version": suite.version, "cases": len(suite.cases),
            "manual_checks": [m.criterion_id for m in suite.manual_checks],
            "next": "dev_agent can now implement the code, then call run_tests."}


async def run_tests(tool_context: ToolContext) -> dict:
    """Run the locked acceptance tests against the implemented code, in an isolated Docker
    container with no network. The result is recorded by the system, not reported by you.
    At most 5 runs per suite version; after that, stop and hand the decision to the client.
    """
    state = tool_context.state
    spec, suite = load_spec(state), load_suite(state)
    design = dev_logic.load_tech_design(state)
    if not suite.locked:
        return {"error": "No locked test suite. qa_agent must write and lock the tests first."}
    if is_stale(suite, spec.meta.version):
        return {"error": f"The tests are for PRD v{suite.based_on_prd_version}; the PRD is now "
                         f"v{spec.meta.version}. qa_agent must update and re-lock them."}
    impl = design.implementation
    if not any(f.status == "written" for f in impl.files):
        return {"error": "No implemented code yet. dev_agent must call implement_scaffold first."}
    if logic.runs_this_version(suite) >= MAX_TEST_RUNS:
        return {"error": f"The run budget ({MAX_TEST_RUNS} runs for suite v{suite.version}) is spent and the "
                         "tests still fail. Stop: report the failures to the client, who decides what next."}
    if problem := docker_problem():
        return {"error": f"Cannot run tests: {problem}"}

    run = TestRun(number=len(suite.runs) + 1, at=_now(), suite_version=suite.version, outcome="error")
    try:
        outcome = await asyncio.to_thread(run_suite, workspace.project_dir(impl.project), suite)
    except runner.RunnerError as exc:
        run.error = str(exc)[:3000]
    else:
        run.cases = logic.grade(suite, outcome.results)
        run.passed = sum(c.outcome == "passed" for c in run.cases)
        run.failed = sum(c.outcome == "failed" for c in run.cases)
        run.missing = sum(c.outcome == "missing" for c in run.cases)
        run.outcome = "passed" if run.passed == len(run.cases) else "failed"
        run.output_tail = outcome.output[-2000:]
    suite.runs.append(run)
    suite.change_log.append(f"Run {run.number}: {run.outcome} ({run.passed}/{len(suite.cases)} passed)")
    save_suite(state, suite)

    left = MAX_TEST_RUNS - logic.runs_this_version(suite)
    result = {"outcome": run.outcome, "run": run.number, "passed": run.passed, "failed": run.failed,
              "missing": run.missing, "total": len(suite.cases), "runs_left": left,
              "verdict": logic.verdict(suite)}
    if run.error:
        result["error"] = run.error
    if bad := [c for c in run.cases if c.outcome != "passed"]:
        result["failures"] = [c.model_dump() for c in bad]
        result["output_tail"] = run.output_tail
    if run.outcome != "passed":
        result["next"] = ("Run budget spent: stop and report to the client." if left == 0 else
                          "dev_agent fixes the code (never the tests) with implement_scaffold, then runs again.")
    return result


def get_test_report(tool_context: ToolContext) -> dict:
    """Current test suite status, coverage, manual checks, and the latest run's results."""
    spec, suite = load_spec(tool_context.state), load_suite(tool_context.state)
    last = suite.runs[-1] if suite.runs else None
    return {"status": logic.status_line(suite, spec), "verdict": logic.verdict(suite),
            "uncovered_criteria": logic.uncovered(suite, spec),
            "cases": [c.model_dump() for c in suite.cases],
            "manual_checks": [m.model_dump() for m in suite.manual_checks],
            "last_run": last.model_dump(exclude={"output_tail"}) if last else None}


async def publish_test_report(tool_context: ToolContext) -> dict:
    """Publish the acceptance test report (qa_report_vN.md) for the client: every
    criterion, its test and result, manual checks, failures, and run history."""
    state = tool_context.state
    spec, suite = load_spec(state), load_suite(state)
    if not suite.locked or logic.runs_this_version(suite) == 0:
        return {"error": "Nothing to report: lock the suite and run the tests first."}
    suite.report_version += 1
    save_suite(state, suite)
    project = dev_logic.load_tech_design(state).implementation.project
    content = logic.render_report_markdown(suite, spec, project)
    filename = f"qa_report_v{suite.report_version}.md"
    await tool_context.save_artifact(
        filename=filename, artifact=types.Part.from_bytes(data=content.encode("utf-8"), mime_type="text/markdown"))
    return {"ok": True, "artifact": filename, "verdict": logic.verdict(suite)}


ALL_QA_TOOLS = [
    inspect_acceptance_criteria,
    write_test_suite,
    lock_test_suite,
    run_tests,
    get_test_report,
    publish_test_report,
]
