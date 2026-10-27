"""TestSuite — the QA agent's locked acceptance tests, and the contract the Dev agent obeys.

Lives in `shared` because both agents read it: the QA agent writes and locks the suite,
the Dev agent may only implement once it is locked and may never write test files.
Importing this must never pull in an agent package.

Rules carried by this module:
- Tests live in TEST_DIR, written by the QA agent only. The Dev agent cannot write
  there, nor any conftest.py (which could patch tests from outside).
- Once locked, the suite changes only when the PRD changes (is_stale), i.e. through the
  client. A wrong test is fixed by fixing the requirement, not by the coder.
- The locked file contents are kept here, in state. Runs use these copies, so editing
  the files on disk cannot change what is tested.
"""

from __future__ import annotations

from typing import Any, List, Literal, MutableMapping, Optional
from pathlib import PurePosixPath

from pydantic import BaseModel, Field

TEST_SUITE_STATE_KEY = "test_suite"
TEST_DIR = "qa_tests"
MAX_TEST_RUNS = 5
"""Runs allowed per suite version before the loop stops and a human must step in."""


class TestFile(BaseModel):
    """A test file the QA agent wants in the suite."""
    __test__ = False  # not a pytest test class
    path: str = Field(..., description="Path under qa_tests/, e.g. qa_tests/test_tasks.py")
    content: str = Field(..., description="Full text of the file")


class TestCase(BaseModel):
    """One test function, traced to the acceptance criterion it checks."""
    __test__ = False
    criterion_id: str = Field(..., description="Acceptance criterion ID from inspect_acceptance_criteria, e.g. FR-1.AC1")
    test: str = Field(..., description="pytest node ID, e.g. qa_tests/test_tasks.py::test_add_task")


class ManualCheck(BaseModel):
    """A criterion that cannot be checked by an automated test, with the reason."""
    criterion_id: str
    reason: str


class LockedFile(BaseModel):
    path: str
    sha256: str
    content: str


class CaseResult(BaseModel):
    test: str
    criterion_id: str
    outcome: Literal["passed", "failed", "missing"]
    message: str = ""


class TestRun(BaseModel):
    __test__ = False
    number: int
    at: str
    suite_version: int
    outcome: Literal["passed", "failed", "error"]
    """passed = every locked case passed; failed = some did not; error = the run itself broke."""
    passed: int = 0
    failed: int = 0
    missing: int = 0
    cases: List[CaseResult] = Field(default_factory=list)
    error: str = ""
    output_tail: str = ""


class TestSuite(BaseModel):
    __test__ = False
    version: int = Field(default=0, description="Incremented on every lock")
    locked: bool = False
    locked_at: Optional[str] = None
    based_on_prd_version: int = 0
    app_dir: str = Field(default="", description="Folder of the Python app inside the project, e.g. 'backend'")
    test_dependencies: List[str] = Field(default_factory=list, description="Pinned packages the tests need, e.g. httpx==0.27.2")
    files: List[LockedFile] = Field(default_factory=list)
    cases: List[TestCase] = Field(default_factory=list)
    manual_checks: List[ManualCheck] = Field(default_factory=list)
    runs: List[TestRun] = Field(default_factory=list)
    report_version: int = 0
    change_log: List[str] = Field(default_factory=list)


# ── session state I/O ────────────────────────────────────────────────────────

def load_suite(state: MutableMapping[str, Any]) -> TestSuite:
    raw = state.get(TEST_SUITE_STATE_KEY)
    return TestSuite.model_validate(raw) if raw else TestSuite()


def save_suite(state: MutableMapping[str, Any], suite: TestSuite) -> None:
    state[TEST_SUITE_STATE_KEY] = suite.model_dump(mode="json")


# ── rules both agents rely on ────────────────────────────────────────────────

def is_stale(suite: TestSuite, prd_version: int) -> bool:
    """A locked suite was written from an older PRD than the current one."""
    return suite.locked and suite.based_on_prd_version != prd_version


def is_protected_path(path: str) -> bool:
    """Paths the Dev agent may never write: the test folder and any conftest.py."""
    parts = PurePosixPath(path.strip().replace("\\", "/")).parts
    parts = [p for p in parts if p not in ("", ".")]
    return bool(parts) and (parts[0].lower() == TEST_DIR or parts[-1].lower() == "conftest.py")


def implementation_gate(suite: TestSuite, prd_version: int) -> Optional[str]:
    """Why code may not be written yet, or None: tests come first, and must be current."""
    if not suite.locked:
        return ("No locked acceptance tests yet. Tests are written before code: transfer to "
                "qa_agent to write and lock the test suite first.")
    if is_stale(suite, prd_version):
        return (f"The locked tests were written for PRD v{suite.based_on_prd_version}, but the PRD is now "
                f"v{prd_version}. Transfer to qa_agent to update and re-lock them first.")
    return None
