"""Phase 2.5: the MCP filesystem sandbox and implement_scaffold.

Most tests use FakeFilesystem (fast, deterministic, no Node). Tests marked `mcp_server`
start the real @modelcontextprotocol/server-filesystem through npx against a temporary
workspace; they are skipped when npx is unavailable or AGENTCORP_SKIP_MCP_TESTS is set.
"""

import asyncio
import os
import shutil
from pathlib import Path, PurePosixPath

import pytest
from google.genai import types

from dev_agent import logic as dev_logic
from dev_agent import tools as dev_tools
from dev_agent import workspace
from dev_agent.agent import dev_agent
from dev_agent.prompt import instruction as dev_instruction
from pm_agent import tools as pm_tools
from shared.spec import load_spec
from shared.testsuite import TestSuite, save_suite

needs_npx = pytest.mark.skipif(
    shutil.which("npx") is None or bool(os.getenv("AGENTCORP_SKIP_MCP_TESTS")),
    reason="needs npx (Node.js) to start the filesystem MCP server",
)


class FakeContext:
    def __init__(self):
        self.state = {}
        self.artifacts = {}
        self.user_content = None

    async def save_artifact(self, filename, artifact):
        self.artifacts[filename] = artifact.inline_data.data.decode("utf-8")
        return 0


class FakeFilesystem:
    """Stands in for McpFilesystemClient: same allowlist, real disk, injectable faults."""

    calls: list = []

    def __init__(self, sandbox, fail_write_on=None, corrupt_read_on=None, unavailable=False):
        self.sandbox, self.fail_write_on, self.corrupt_read_on = sandbox, fail_write_on, corrupt_read_on
        self.unavailable = unavailable

    async def __aenter__(self):
        if self.unavailable:
            raise workspace.McpUnavailable("server failed to start (simulated)")
        return self

    async def __aexit__(self, *exc):
        return None

    async def call(self, name, arguments):
        if name not in workspace.WRITE_TOOLS:
            raise PermissionError(name)
        FakeFilesystem.calls.append(name)
        path = Path(arguments["path"])
        assert self.sandbox.resolve() in [path.resolve(), *path.resolve().parents], "left the sandbox"
        if name == "create_directory":
            path.mkdir(parents=True, exist_ok=True)
            return "ok"
        if name == "write_file":
            if self.fail_write_on and path.name == self.fail_write_on:
                raise workspace.McpToolFailed("write_file refused: disk full (simulated)")
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(arguments["content"])
            return "ok"
        with open(path, encoding="utf-8", newline="") as fh:
            text = fh.read()
        return text + "CORRUPT" if self.corrupt_read_on == path.name else text


@pytest.fixture
def ctx():
    return FakeContext()


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A temporary workspace root; the real one is never touched."""
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("AGENTCORP_WORKSPACE", str(root))
    FakeFilesystem.calls = []
    return root


def use_fake(monkeypatch, **faults):
    monkeypatch.setattr(dev_tools, "open_filesystem", lambda sandbox: FakeFilesystem(sandbox, **faults))


def say(ctx, text):
    ctx.user_content = types.Content(role="user", parts=[types.Part(text=text)])


def lock_tests(ctx):
    """Stand-in for qa_agent: a locked suite for the current PRD (tests come before code)."""
    save_suite(ctx.state, TestSuite(version=1, locked=True, based_on_prd_version=load_spec(ctx.state).meta.version))


def published_architecture(ctx, scaffold=None, with_tests=True):
    """A published PRD, a published architecture with a small scaffold, and locked tests."""
    pm_tools.set_engagement("new_build", "productivity", "Todo app", "", tool_context=ctx)
    pm_tools.add_text_items("goals", ["Track tasks"], "client", tool_context=ctx)
    pm_tools.add_text_items("success_metrics", ["Daily use"], "client", tool_context=ctx)
    pm_tools.add_text_items("in_scope", ["Task list"], "client", tool_context=ctx)
    pm_tools.add_requirement("functional", "User can add a task", "must", "client", tool_context=ctx,
                             acceptance_criteria=["Task appears in the list"])
    say(ctx, "Please publish the PRD.")
    asyncio.run(pm_tools.publish_prd("v1", False, tool_context=ctx))
    dev_tools.recommend_tech_stack("backend", "FastAPI", "Simple", tool_context=ctx)
    dev_tools.add_project_scaffold(scaffold or [
        {"path": "backend/app/main.py", "description": "API entry point"},
        {"path": "backend/app/models/", "description": "Data models"},
        {"path": "README.md", "description": "How to run"},
    ], tool_context=ctx)
    assert asyncio.run(dev_tools.publish_architecture("v1", tool_context=ctx))["ok"]
    if with_tests:
        lock_tests(ctx)


def implement(ctx, files, project="todo-app"):
    return asyncio.run(dev_tools.implement_scaffold(files, tool_context=ctx, project=project))


def design(ctx):
    return dev_logic.load_tech_design(ctx.state)


# ── sandbox boundaries ───────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "../escape.py", "backend/../../escape.py", "a/b/../../../x", "..\\escape.py", "backend\\..\\..\\x",
    "/etc/passwd", "\\Windows\\win.ini", "C:/Windows/win.ini", "C:escape.py", "\\\\server\\share\\x",
    "~/x.py", "", "   ", "a/\x00b.py", "a/\nb.py", "notes.txt:hidden", "con.py", "backend/NUL",
    "lpt1.txt", "backend./main.py", "backend /main.py", "backend/", ".", "./", "x" * 300,
])
def test_rejected_paths(path):
    with pytest.raises(workspace.SandboxViolation):
        workspace.safe_relative_path(path)


@pytest.mark.parametrize("path, expected", [
    ("backend/app/main.py", "backend/app/main.py"),
    ("./README.md", "README.md"),
    ("backend\\app\\main.py", "backend/app/main.py"),
    ("a//b.py", "a/b.py"),
    (".github/workflows/ci.yml", ".github/workflows/ci.yml"),
])
def test_accepted_paths_are_normalised(path, expected):
    assert str(workspace.safe_relative_path(path)) == expected


@pytest.mark.parametrize("name, slug", [("Todo App", "todo-app"), ("  my_project!! ", "my-project"),
                                        ("todo-app", "todo-app")])
def test_project_slug(name, slug):
    assert workspace.project_slug(name) == slug


@pytest.mark.parametrize("name", ["", "!!!", "../", "..", "  "])
def test_project_slug_rejects_unusable_names(name):
    with pytest.raises(workspace.SandboxViolation):
        workspace.project_slug(name)


@pytest.mark.parametrize("project", ["../evil", "..", "a/b", "A", "-x", "x-", "a\\b", ""])
def test_project_dir_rejects_escaping_names(ws, project):
    with pytest.raises(workspace.SandboxViolation):
        workspace.project_dir(project)


def test_project_dir_is_directly_inside_the_workspace(ws):
    assert workspace.project_dir("todo-app") == (ws / "todo-app").resolve()


def test_symlink_escape_is_caught(ws, tmp_path):
    sandbox = ws / "todo-app"
    sandbox.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, sandbox / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("creating symlinks needs extra privileges on this machine")
    with pytest.raises(workspace.SandboxViolation):
        workspace.resolve_in_sandbox(sandbox, PurePosixPath("link/x.py"))


# ── tool allowlisting ────────────────────────────────────────────────────────

def test_allowlists_exclude_destructive_and_broad_tools():
    for tools in (workspace.READ_TOOLS, workspace.WRITE_TOOLS):
        assert not set(tools) & workspace.FORBIDDEN_TOOLS
        assert not any(word in name for name in tools for word in ("move", "delete", "remove", "exec", "shell", "run"))
    assert "write_file" not in workspace.READ_TOOLS  # the model can never write directly


def test_model_facing_toolset_is_filtered_to_read_tools(ws):
    toolset = workspace.ProjectWorkspaceToolset()
    assert toolset.toolset_for(ws).tool_filter == list(workspace.READ_TOOLS)


def test_dev_agent_has_no_direct_write_or_exec_tools():
    names = [getattr(t, "__name__", type(t).__name__) for t in dev_agent.tools]
    assert "ProjectWorkspaceToolset" in names
    assert not any(n in names for n in ("write_file", "move_file", "edit_file", "run_code", "execute"))


def test_write_client_refuses_tools_outside_its_allowlist(ws):
    client = workspace.McpFilesystemClient(ws)
    for name in ("move_file", "edit_file", "list_directory", "delete_file"):
        with pytest.raises(PermissionError):
            asyncio.run(client.call(name, {}))


def test_no_file_tools_before_a_project_exists(ws, ctx):
    assert asyncio.run(workspace.ProjectWorkspaceToolset().get_tools(ctx)) == []


# ── architecture prerequisite ────────────────────────────────────────────────

def test_implement_refused_without_published_architecture(ws, ctx, monkeypatch):
    use_fake(monkeypatch)
    res = implement(ctx, [{"path": "README.md", "content": "x"}])
    assert "error" in res and "publish_architecture" in res["error"]
    assert not any(ws.iterdir()) and FakeFilesystem.calls == []


def test_implement_refused_with_unpublished_design_changes(ws, ctx, monkeypatch):
    use_fake(monkeypatch)
    published_architecture(ctx)
    dev_tools.recommend_tech_stack("database", "SQLite", "Simple", tool_context=ctx)
    res = implement(ctx, [{"path": "README.md", "content": "x"}])
    assert "error" in res and "changed since" in res["error"]
    assert not any(ws.iterdir())


def test_implement_refused_when_prd_changed_since_architecture(ws, ctx, monkeypatch):
    use_fake(monkeypatch)
    published_architecture(ctx)
    pm_tools.add_text_items("constraints", ["Must work offline"], "client", tool_context=ctx)
    say(ctx, "Please publish the PRD.")
    asyncio.run(pm_tools.publish_prd("v2", False, tool_context=ctx))
    res = implement(ctx, [{"path": "README.md", "content": "x"}])
    assert "error" in res and "PRD has changed" in res["error"]


def test_implement_refused_before_tests_are_locked(ws, ctx, monkeypatch):
    use_fake(monkeypatch)
    published_architecture(ctx, with_tests=False)
    res = implement(ctx, [{"path": "README.md", "content": "x"}])
    assert "error" in res and "qa_agent" in res["error"] and "before code" in res["error"]
    assert not any(ws.iterdir()) and FakeFilesystem.calls == []


def test_implement_refused_when_tests_are_for_an_older_prd(ws, ctx, monkeypatch):
    use_fake(monkeypatch)
    published_architecture(ctx)
    pm_tools.add_text_items("constraints", ["Must work offline"], "client", tool_context=ctx)
    say(ctx, "Please publish the PRD.")
    asyncio.run(pm_tools.publish_prd("v2", False, tool_context=ctx))
    asyncio.run(dev_tools.publish_architecture("v2", tool_context=ctx))
    res = implement(ctx, [{"path": "README.md", "content": "x"}])
    assert "error" in res and "re-lock" in res["error"]


@pytest.mark.parametrize("path", ["qa_tests/test_tasks.py", "QA_TESTS/x.py", "backend/conftest.py", "conftest.py"])
def test_dev_can_never_write_test_files(ws, ctx, monkeypatch, path):
    """Even when the scaffold lists them: tests belong to qa_agent."""
    use_fake(monkeypatch)
    published_architecture(ctx, scaffold=[{"path": "qa_tests/", "description": "tests"},
                                          {"path": "QA_TESTS/", "description": "tests"},
                                          {"path": "backend/", "description": "app"},
                                          {"path": "conftest.py", "description": "x"}])
    res = implement(ctx, [{"path": path, "content": "def test_x(): pass"}])
    assert "error" in res and "test file" in res["problems"][0]
    assert FakeFilesystem.calls == []


def test_republishing_clears_the_unpublished_flag(ws, ctx, monkeypatch):
    use_fake(monkeypatch)
    published_architecture(ctx)
    dev_tools.recommend_tech_stack("database", "SQLite", "Simple", tool_context=ctx)
    asyncio.run(dev_tools.publish_architecture("v2", tool_context=ctx))
    assert implement(ctx, [{"path": "README.md", "content": "x"}])["ok"]


# ── rejected files: nothing is written ───────────────────────────────────────

@pytest.mark.parametrize("files, problem", [
    ([{"path": "../escape.py", "content": "x"}], "leave the project folder"),
    ([{"path": "C:/Windows/evil.py", "content": "x"}], "relative"),
    ([{"path": "backend/app/other.py", "content": "x"}], "not in the published scaffold"),
    ([{"path": "README.md", "content": "a"}, {"path": "./README.md", "content": "b"}], "twice"),
    ([{"path": "README.md", "content": "x" * (workspace.MAX_FILE_BYTES + 1)}], "limit"),
])
def test_invalid_files_reject_the_whole_batch(ws, ctx, monkeypatch, files, problem):
    use_fake(monkeypatch)
    published_architecture(ctx)
    res = implement(ctx, [{"path": "backend/app/main.py", "content": "ok"}, *files])
    assert "error" in res and any(problem in p for p in res["problems"])
    assert FakeFilesystem.calls == [] and not (ws / "todo-app").exists()
    assert not (ws / "escape.py").exists() and not (ws.parent / "escape.py").exists()


def test_too_many_files_in_one_call(ws, ctx, monkeypatch):
    use_fake(monkeypatch)
    published_architecture(ctx)
    files = [{"path": f"backend/app/models/m{i}.py", "content": ""} for i in range(workspace.MAX_FILES_PER_CALL + 1)]
    assert "At most" in implement(ctx, files)["error"]


def test_project_name_is_required_then_fixed(ws, ctx, monkeypatch):
    use_fake(monkeypatch)
    published_architecture(ctx)
    assert "project folder name" in implement(ctx, [{"path": "README.md", "content": "x"}], project="")["error"]
    assert implement(ctx, [{"path": "README.md", "content": "x"}])["ok"]
    assert "cannot move" in implement(ctx, [{"path": "README.md", "content": "y"}], project="other")["error"]
    assert implement(ctx, [{"path": "README.md", "content": "y"}], project="")["ok"]  # reuses todo-app


# ── implementation + logging ─────────────────────────────────────────────────

def test_implement_writes_verifies_and_logs(ws, ctx, monkeypatch):
    use_fake(monkeypatch)
    published_architecture(ctx)
    res = implement(ctx, [
        {"path": "backend/app/main.py", "content": "from fastapi import FastAPI\napp = FastAPI()\n"},
        {"path": "backend/app/models/task.py", "content": "class Task: ...\n"},  # under a directory entry
    ], project="Todo App")
    assert res["ok"] and res["workspace"] == "workspace/todo-app/"
    assert (ws / "todo-app/backend/app/main.py").read_text() == "from fastapi import FastAPI\napp = FastAPI()\n"
    assert (ws / "todo-app/backend/app/models/task.py").exists()

    d = design(ctx)
    assert d.change_log[-1].startswith("Implemented 2 files in workspace/todo-app/ from architecture v1")
    assert [(f.path, f.status) for f in d.implementation.files] == [
        ("backend/app/main.py", "written"), ("backend/app/models/task.py", "written")]
    assert d.implementation.architecture_version == 1
    assert not d.has_unpublished_changes  # implementing is not a design change
    assert "Implemented: 2 files in workspace/todo-app/" in dev_logic.status_line(d)


def test_failed_write_stops_reports_and_logs_partial_result(ws, ctx, monkeypatch):
    use_fake(monkeypatch, fail_write_on="task.py")
    published_architecture(ctx)
    res = implement(ctx, [
        {"path": "backend/app/main.py", "content": "a"},
        {"path": "backend/app/models/task.py", "content": "b"},
        {"path": "README.md", "content": "c"},
    ])
    assert "ok" not in res and "disk full" in res["error"]
    assert (res["written"], res["failed"], res["not_attempted"]) == (
        ["backend/app/main.py"], ["backend/app/models/task.py"], ["README.md"])
    assert not (ws / "todo-app/README.md").exists()
    d = design(ctx)
    assert d.change_log[-1].startswith("Implementation FAILED (architecture v1): 1 of 3 files written")
    assert {f.path: f.status for f in d.implementation.files}["README.md"] == "not_attempted"


def test_read_back_mismatch_is_a_failure(ws, ctx, monkeypatch):
    use_fake(monkeypatch, corrupt_read_on="main.py")
    published_architecture(ctx)
    res = implement(ctx, [{"path": "backend/app/main.py", "content": "a"}])
    assert "read-back did not match" in res["error"] and res["written"] == []


def test_mcp_startup_failure_writes_nothing_and_is_logged(ws, ctx, monkeypatch):
    use_fake(monkeypatch, unavailable=True)
    published_architecture(ctx)
    res = implement(ctx, [{"path": "README.md", "content": "x"}])
    assert "failed to start" in res["error"] and res["written"] == []
    assert design(ctx).change_log[-1].startswith("Implementation FAILED (architecture v1): 0 of 1")


def test_real_client_reports_unstartable_server(ws, ctx, monkeypatch):
    """No fake: a server command that does not exist must surface as McpUnavailable."""
    from mcp import StdioServerParameters

    monkeypatch.setattr(workspace, "server_params",
                        lambda sandbox: StdioServerParameters(command="agentcorp-no-such-command", args=[]))
    monkeypatch.setenv("AGENTCORP_MCP_TIMEOUT", "10")
    published_architecture(ctx)
    res = implement(ctx, [{"path": "README.md", "content": "x"}])
    assert "Could not start the filesystem MCP server" in res["error"]
    assert not (ws / "todo-app/README.md").exists()


def test_prompt_ties_claims_to_run_tests(ctx):
    text = dev_instruction(ctx)
    assert "implement_scaffold" in text and "never claim tests pass unless" in text
    assert "never edit the tests" in text


# ── the real filesystem MCP server ───────────────────────────────────────────

@pytest.mark.mcp_server
@needs_npx
def test_real_server_offers_the_allowlisted_tools_only(ws, ctx):
    sandbox = ws / "todo-app"
    sandbox.mkdir()
    ctx.state["tech_design"] = {"implementation": {"project": "todo-app"}}
    toolset = workspace.ProjectWorkspaceToolset()

    async def run():
        try:
            return sorted(t.name for t in await toolset.get_tools(ctx))
        finally:
            await toolset.close()

    assert asyncio.run(run()) == sorted(workspace.READ_TOOLS)


@pytest.mark.mcp_server
@needs_npx
def test_real_server_boundary_refuses_escape(ws):
    sandbox = ws / "todo-app"
    sandbox.mkdir()
    escape = ws / "escape.txt"

    async def run():
        async with workspace.McpFilesystemClient(sandbox) as fs:
            with pytest.raises(workspace.McpToolFailed, match="outside allowed directories"):
                await fs.call("write_file", {"path": str(escape), "content": "x"})
            with pytest.raises(workspace.McpToolFailed):
                await fs.call("write_file", {"path": str(sandbox / ".." / "escape.txt"), "content": "x"})
            with pytest.raises(PermissionError):
                await fs.call("move_file", {"source": str(sandbox), "destination": str(escape)})

    asyncio.run(run())
    assert not escape.exists()


@pytest.mark.mcp_server
@needs_npx
def test_real_server_end_to_end_implementation(ws, ctx):
    published_architecture(ctx)
    res = implement(ctx, [
        {"path": "backend/app/main.py", "content": "print('hello')\n"},
        {"path": "README.md", "content": "# Todo\n"},
    ])
    assert res["ok"], res
    assert (ws / "todo-app/backend/app/main.py").read_text() == "print('hello')\n"
    assert design(ctx).change_log[-1].startswith("Implemented 2 files")


def test_unexpected_error_marks_the_file_failed_not_skipped(ws, ctx, monkeypatch):
    class Exploding(FakeFilesystem):
        async def call(self, name, arguments):
            if name == "read_text_file":
                raise TypeError("transport closed (simulated)")
            return await super().call(name, arguments)

    monkeypatch.setattr(dev_tools, "open_filesystem", lambda sandbox: Exploding(sandbox))
    published_architecture(ctx)
    res = implement(ctx, [{"path": "README.md", "content": "a"}, {"path": "backend/app/main.py", "content": "b"}])
    assert res["failed"] == ["README.md"] and res["not_attempted"] == ["backend/app/main.py"]
    assert "TypeError" in res["error"]
