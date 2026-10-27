"""Runs the locked acceptance tests inside a throwaway Docker container.

This is the only code in AgentCorp that executes generated code, and no model controls
it: the image, the command, the limits, and the test files are all fixed here.

Two containers per run:
1. Install (network on, no project code): pip installs the app's requirements.txt plus
   pytest and the suite's pinned test dependencies into a cache folder. Wheels only
   (--only-binary), so no package build script runs. Cached by the requirements' hash.
2. Test (network off): a fresh copy of the project, with the locked test files written
   from state over it, runs pytest. No network, capped memory/CPU/processes, no Linux
   capabilities, read-only root filesystem, killed on timeout, deleted afterwards.

What this does not defend against: code under test runs in the same process as pytest,
so deliberately hostile code could fake a result. The rules here stop a coder that takes
the easy way out (editing or skipping tests); the human gates cover the rest.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dev_agent.workspace import workspace_root
from shared.testsuite import TEST_DIR, TestSuite

IMAGE = "python:3.12-slim"
PYTEST = "pytest==8.3.3"
MAX_PROJECT_BYTES = 50_000_000
_OUTPUT_TAIL = 4000
_COPY_IGNORE = shutil.ignore_patterns(TEST_DIR, "node_modules", ".git", "__pycache__", ".venv", "venv",
                                      "*.pyc", ".pytest_cache")


class RunnerError(RuntimeError):
    """The run could not happen: Docker missing, install failed, timeout, bad project."""


@dataclass
class RunOutcome:
    exit_code: int
    results: dict[tuple[str, str], tuple[str, str]]
    """(classname, name) -> (outcome, message), from pytest's JUnit XML."""
    output: str


def _timeout(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _docker(args: list[str], timeout: int, name: Optional[str] = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except FileNotFoundError as exc:
        raise RunnerError("Docker is not installed (no 'docker' command).") from exc
    except subprocess.TimeoutExpired as exc:
        if name:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
        raise RunnerError(f"Timed out after {timeout}s.") from exc


def docker_problem() -> Optional[str]:
    """Why Docker cannot be used right now, or None."""
    try:
        proc = _docker(["info", "--format", "{{.ServerVersion}}"], timeout=30)
    except RunnerError as exc:
        return str(exc)
    if proc.returncode != 0 or not proc.stdout.strip():
        return "The Docker engine is not running. Start Docker Desktop and try again."
    return None


def _mount(source: Path, target: str, readonly: bool = False) -> list[str]:
    spec = f"type=bind,source={source},target={target}" + (",readonly" if readonly else "")
    return ["--mount", spec]


def _tail(text: str) -> str:
    return text[-_OUTPUT_TAIL:]


def install_dependencies(app_path: Path, test_dependencies: list[str]) -> Path:
    """Install the app's requirements plus test tools into a cached folder; return it."""
    req_file = app_path / "requirements.txt"
    requirements = req_file.read_text(encoding="utf-8") if req_file.is_file() else ""
    lines = [requirements.strip(), PYTEST, *test_dependencies]
    wanted = "\n".join(line for line in lines if line) + "\n"
    key = hashlib.sha256(f"{IMAGE}\n{wanted}".encode()).hexdigest()[:16]
    cache = workspace_root() / ".qa-deps"
    target = cache / key
    if (target / ".complete").is_file():
        return target

    partial, ctl = cache / f"{key}.partial", cache / f"{key}.req"
    for d in (partial, ctl):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    (ctl / "requirements.txt").write_text(wanted, encoding="utf-8")
    name = f"agentcorp-qa-install-{uuid.uuid4().hex[:8]}"
    try:
        proc = _docker(["run", "--rm", "--name", name, *_mount(ctl, "/req", readonly=True),
                        *_mount(partial, "/deps"), IMAGE,
                        "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "--quiet",
                        "--only-binary=:all:", "--target", "/deps", "-r", "/req/requirements.txt"],
                       timeout=_timeout("AGENTCORP_QA_INSTALL_TIMEOUT", 600), name=name)
    finally:
        shutil.rmtree(ctl, ignore_errors=True)
    if proc.returncode != 0:
        shutil.rmtree(partial, ignore_errors=True)
        raise RunnerError(f"Installing dependencies failed:\n{_tail(proc.stdout + proc.stderr)}")
    shutil.rmtree(target, ignore_errors=True)
    partial.rename(target)
    (target / ".complete").write_text(wanted, encoding="utf-8")
    return target


def _copy_project(project_path: Path, work: Path) -> None:
    shutil.copytree(project_path, work, ignore=_COPY_IGNORE, symlinks=False)
    size = sum(p.stat().st_size for p in work.rglob("*") if p.is_file())
    if size > MAX_PROJECT_BYTES:
        raise RunnerError(f"The project is {size} bytes; the limit is {MAX_PROJECT_BYTES}.")


def parse_junit(xml_text: str) -> dict[tuple[str, str], tuple[str, str]]:
    results = {}
    for case in ET.fromstring(xml_text).iter("testcase"):
        key = (case.get("classname", ""), case.get("name", ""))
        outcome, message = "passed", ""
        for tag in ("failure", "error", "skipped"):
            node = case.find(tag)
            if node is not None:
                outcome = "failed"
                detail = node.get("message") or (node.text or "")
                message = f"{tag}: {detail}".strip()[:500]
                break
        results[key] = (outcome, message)
    return results


def run_suite(project_path: Path, suite: TestSuite) -> RunOutcome:
    """Run the locked suite against a copy of `project_path`. Raises RunnerError if the
    run could not take place; test failures are a normal RunOutcome."""
    app_dir = suite.app_dir.strip("/") or "."
    app_path = (project_path / app_dir).resolve()
    if project_path.resolve() not in [app_path, *app_path.parents] or not app_path.is_dir():
        raise RunnerError(f"App folder {suite.app_dir!r} does not exist in the project.")
    deps = install_dependencies(app_path, suite.test_dependencies)

    run_root = workspace_root() / ".qa-runs" / uuid.uuid4().hex[:12]
    work, ctl, out = run_root / "work", run_root / "ctl", run_root / "out"
    name = f"agentcorp-qa-run-{run_root.name}"
    try:
        _copy_project(project_path, work)
        # The tests that run are the locked copies, never whatever is on disk.
        for f in suite.files:
            target = work / f.path
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(f.content)
        ctl.mkdir()
        out.mkdir()
        # Our own config: -c makes pytest ignore any pytest.ini/pyproject the coder wrote.
        # pythonpath is resolved against the ini file's folder, so give it absolutely.
        (ctl / "pytest.ini").write_text(f"[pytest]\npythonpath = /work/{app_dir}\n", encoding="utf-8")
        proc = _docker(
            ["run", "--rm", "--name", name, "--network", "none", "--memory", "1g", "--cpus", "1",
             "--pids-limit", "256", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
             "--read-only", "--tmpfs", "/tmp", "-e", "HOME=/tmp", "-e", "PYTHONPATH=/deps",
             "-e", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "-e", "PYTHONDONTWRITEBYTECODE=1",
             *_mount(work, "/work"), *_mount(deps, "/deps", readonly=True),
             *_mount(ctl, "/ctl", readonly=True), *_mount(out, "/out"), "-w", "/work", IMAGE,
             # -P: the project folder is not put first on sys.path, so it cannot shadow pytest.
             "python", "-P", "-m", "pytest", "-c", "/ctl/pytest.ini", "--rootdir", "/work",
             f"--confcutdir=/work/{TEST_DIR}", "-p", "no:cacheprovider", "-o", "junit_family=xunit2",
             "--junitxml", "/out/junit.xml", "-q", "--tb=short", TEST_DIR],
            timeout=_timeout("AGENTCORP_QA_RUN_TIMEOUT", 180), name=name)
        output = _tail(proc.stdout + proc.stderr)
        junit = out / "junit.xml"
        if not junit.is_file():
            raise RunnerError(f"pytest produced no results (exit code {proc.returncode}):\n{output}")
        try:
            results = parse_junit(junit.read_text(encoding="utf-8"))
        except ET.ParseError as exc:
            raise RunnerError(f"pytest results were unreadable: {exc}") from exc
        return RunOutcome(exit_code=proc.returncode, results=results, output=output)
    finally:
        shutil.rmtree(run_root, ignore_errors=True)
