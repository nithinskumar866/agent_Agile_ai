"""Deterministic core of the QA agent: what must be tested, coverage, grading, reports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from shared.spec import RequirementsSpec
from shared.testsuite import MAX_TEST_RUNS, TEST_DIR, CaseResult, TestSuite, is_stale


@dataclass(frozen=True)
class Criterion:
    id: str
    """e.g. FR-1.AC2: the second acceptance criterion of FR-1."""
    item_id: str
    item_text: str
    text: str


def _must_items(spec: RequirementsSpec):
    for r in spec.functional_requirements + spec.non_functional_requirements:
        if r.priority == "must":
            yield r.id, r.text, r.acceptance_criteria
    for s in spec.user_stories:
        if s.priority == "must":
            yield s.id, f"As a {s.as_a}, I want {s.i_want}, so that {s.so_that}", s.acceptance_criteria


def criteria(spec: RequirementsSpec) -> dict[str, Criterion]:
    """Every acceptance criterion of every 'must' requirement and user story, by ID."""
    found = {}
    for item_id, item_text, acs in _must_items(spec):
        for n, text in enumerate(acs, start=1):
            c = Criterion(f"{item_id}.AC{n}", item_id, item_text, text)
            found[c.id] = c
    return found


def musts_without_criteria(spec: RequirementsSpec) -> list[str]:
    return [item_id for item_id, _, acs in _must_items(spec) if not acs]


def uncovered(suite: TestSuite, spec: RequirementsSpec) -> list[str]:
    """Criterion IDs with neither a test nor a recorded manual check."""
    covered = {c.criterion_id for c in suite.cases} | {m.criterion_id for m in suite.manual_checks}
    return [cid for cid in criteria(spec) if cid not in covered]


def lock_blocker(suite: TestSuite, spec: RequirementsSpec, design_version: int,
                 design_dirty: bool) -> Optional[str]:
    """Why the suite may not be locked yet, or None."""
    if spec.meta.status != "approved":
        return ("Tests are written from client-approved acceptance criteria, and the PRD is "
                f"'{spec.meta.status}'. The client must approve it through product_manager first.")
    if design_version < 1 or design_dirty:
        return "Needs a published, up-to-date architecture from dev_agent first."
    if suite.locked and not is_stale(suite, spec.meta.version):
        return f"Suite v{suite.version} is already locked for PRD v{suite.based_on_prd_version}."
    if missing := musts_without_criteria(spec):
        return (f"'must' items without acceptance criteria: {missing}. Nothing to test them against; "
                "transfer to product_manager to have them added.")
    if not suite.files or not suite.cases:
        return "No draft tests yet. Call write_test_suite first."
    if gaps := uncovered(suite, spec):
        return f"Acceptance criteria with no test and no manual check: {gaps}."
    return None


# ── pytest node IDs ──────────────────────────────────────────────────────────

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_node_id(node_id: str) -> tuple[str, list[str], str]:
    """'qa_tests/test_a.py::TestX::test_y' -> ('qa_tests/test_a.py', ['TestX'], 'test_y')."""
    path, *rest = node_id.strip().split("::")
    if not rest or not all(_NAME.match(p) for p in rest):
        raise ValueError(f"{node_id!r} is not a pytest node ID like '{TEST_DIR}/test_x.py::test_name'.")
    if not rest[-1].startswith("test"):
        raise ValueError(f"{node_id!r}: test function names must start with 'test'.")
    return path, rest[:-1], rest[-1]


def defines(content: str, classes: list[str], name: str) -> bool:
    """Roughly: does the file define these classes and this test function?"""
    found = all(re.search(rf"^\s*class\s+{c}\b", content, re.M) for c in classes)
    return found and bool(re.search(rf"^\s*(?:async\s+)?def\s+{name}\s*\(", content, re.M))


def junit_key(node_id: str) -> tuple[str, str]:
    """The (classname, name) pytest's JUnit XML gives this node."""
    path, classes, name = parse_node_id(node_id)
    module = path[:-3].replace("/", ".") if path.endswith(".py") else path.replace("/", ".")
    return ".".join([module, *classes]), name


def grade(suite: TestSuite, results: dict[tuple[str, str], tuple[str, str]]) -> list[CaseResult]:
    """One result per locked case. A case passes only if pytest ran it (every
    parametrisation) and it passed; a skipped or never-collected test does not pass."""
    graded = []
    for case in suite.cases:
        classname, name = junit_key(case.test)
        runs = [v for (cls, n), v in results.items() if cls == classname and (n == name or n.startswith(name + "["))]
        if not runs:
            graded.append(CaseResult(test=case.test, criterion_id=case.criterion_id, outcome="missing",
                                     message="pytest did not run this test (not collected, or crashed before it)."))
            continue
        failed = [msg for outcome, msg in runs if outcome != "passed"]
        graded.append(CaseResult(test=case.test, criterion_id=case.criterion_id,
                                 outcome="failed" if failed else "passed", message=(failed or [""])[0]))
    return graded


# ── status and reporting ─────────────────────────────────────────────────────

def runs_this_version(suite: TestSuite) -> int:
    return sum(r.suite_version == suite.version for r in suite.runs)


def verdict(suite: TestSuite) -> str:
    """'not_run', 'passed', 'failed', or 'escalated' (run budget spent, still failing)."""
    runs = [r for r in suite.runs if r.suite_version == suite.version]
    if not runs:
        return "not_run"
    if runs[-1].outcome == "passed":
        return "passed"
    return "escalated" if len(runs) >= MAX_TEST_RUNS else "failed"


def status_line(suite: TestSuite, spec: RequirementsSpec) -> str:
    if not suite.files:
        return "No acceptance tests yet."
    state = f"locked v{suite.version} (PRD v{suite.based_on_prd_version})" if suite.locked else "DRAFT, not locked"
    line = f"Test suite {state} | Cases: {len(suite.cases)} | Manual checks: {len(suite.manual_checks)}"
    if is_stale(suite, spec.meta.version):
        line += f" | STALE: PRD is now v{spec.meta.version}; update and re-lock the tests"
    if suite.locked:
        line += f" | Runs: {runs_this_version(suite)}/{MAX_TEST_RUNS} | Verdict: {verdict(suite)}"
    return line


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_report_markdown(suite: TestSuite, spec: RequirementsSpec, project: str) -> str:
    all_criteria = criteria(spec)
    last = next((r for r in reversed(suite.runs) if r.suite_version == suite.version), None)
    results = {c.test: c for c in last.cases} if last else {}
    title = spec.engagement.summary if spec.engagement else project
    lines = [
        f"# Acceptance Test Report v{suite.report_version}: {title}",
        "",
        f"**Test suite:** v{suite.version}, locked {suite.locked_at}, written from PRD v{suite.based_on_prd_version}",
        f"**Project:** workspace/{project}/ (app folder: {suite.app_dir or '.'})",
        f"**Verdict:** {verdict(suite).upper()}"
        + (f" (run {last.number}: {last.passed} passed, {last.failed} failed, {last.missing} missing)" if last else ""),
        "",
        "## Acceptance criteria",
        "",
        "| Criterion | Requirement | Check | Result |",
        "| :--- | :--- | :--- | :--- |",
    ]
    for cid, c in all_criteria.items():
        tests = [t for t in suite.cases if t.criterion_id == cid]
        manual = [m for m in suite.manual_checks if m.criterion_id == cid]
        for t in tests:
            r = results.get(t.test)
            lines.append(f"| **{cid}** {_cell(c.text)} | {c.item_id} | `{_cell(t.test)}` | "
                         f"{r.outcome.upper() if r else 'not run'} |")
        for m in manual:
            lines.append(f"| **{cid}** {_cell(c.text)} | {c.item_id} | Manual: {_cell(m.reason)} | "
                         "MANUAL CHECK NEEDED |")
    lines.append("")

    if last and (bad := [c for c in last.cases if c.outcome != "passed"]):
        lines.extend(["## Failures", ""])
        lines.extend(f"- `{c.test}` ({c.criterion_id}): {c.outcome}. {c.message}" for c in bad)
        lines.append("")
    if last and last.error:
        lines.extend(["## Run error", "", "```text", last.error, "```", ""])

    lines.extend(["## Run history", ""])
    lines.extend(f"- Run {r.number} (suite v{r.suite_version}, {r.at}): {r.outcome}, "
                 f"{r.passed} passed, {r.failed} failed, {r.missing} missing" for r in suite.runs)
    lines.extend(["", "## Not covered by this report", "",
                  "- Only the Python app folder is tested; frontend code is not executed.",
                  "- Criteria marked as manual checks need a person to verify them.",
                  "- Passing tests show the code meets these acceptance criteria as tested, nothing more.",
                  ""])
    if suite.change_log:
        lines.extend(["## Change log", ""])
        lines.extend(f"- {c}" for c in suite.change_log)
        lines.append("")
    return "\n".join(lines)
