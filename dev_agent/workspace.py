"""The Dev agent's file sandbox, reached only through the official filesystem MCP server.

Two layers keep every file operation inside workspace/<project>/:

1. The server boundary. Each server process is started with exactly one allowed
   directory, workspace/<project>/, and refuses any path outside it (including via
   symlinks). This holds even if everything in Python went wrong.
2. Validation here, before any call. Paths must be relative, plain, and resolve inside
   the project folder, so a bad path is rejected with a clear message and never reaches
   the server.

Tool exposure is split on purpose:
- The model sees READ_TOOLS only, through ADK's McpToolset with a tool_filter.
- Writes happen only inside implement_scaffold (dev_agent/tools.py), through
  McpFilesystemClient, which allows WRITE_TOOLS and nothing else. So every write passes
  the architecture prerequisite and is verified and logged.
No delete, move, shell, or code-execution tool exists in either set.
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import AsyncExitStack
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.base_toolset import BaseToolset

logger = logging.getLogger(__name__)

# Pinned: the newest release (2.0.0) requires Node >= 24; this one runs on Node 22.
FS_SERVER_PACKAGE = "@modelcontextprotocol/server-filesystem@2026.8.31"

# What the model may call directly: look, never touch.
READ_TOOLS = ("list_directory", "directory_tree", "read_text_file", "get_file_info")
# What implement_scaffold may call: create folders, write, read back to verify.
WRITE_TOOLS = ("create_directory", "write_file", "read_text_file")
# Offered by the server but never exposed to anything here.
FORBIDDEN_TOOLS = frozenset({"move_file", "edit_file", "search_files", "read_multiple_files",
                             "read_media_file", "read_file", "list_directory_with_sizes",
                             "list_allowed_directories"})

MAX_FILES_PER_CALL = 40
MAX_FILE_BYTES = 200_000
_MAX_PATH_CHARS = 200

_PROJECT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
_WINDOWS_RESERVED = re.compile(r"^(?:con|prn|aux|nul|com\d|lpt\d)(?:\..*)?$", re.IGNORECASE)


class SandboxViolation(ValueError):
    """A path or project name that could leave, or confuse, the sandbox."""


class McpUnavailable(RuntimeError):
    """The filesystem MCP server could not be started or reached."""


class McpToolFailed(RuntimeError):
    """The filesystem MCP server reported an error for a tool call."""


# ── locations ────────────────────────────────────────────────────────────────

def workspace_root() -> Path:
    """Root of all project sandboxes. Read on every call so tests can point it elsewhere."""
    default = Path(__file__).resolve().parent.parent / "workspace"
    return Path(os.getenv("AGENTCORP_WORKSPACE", str(default))).resolve()


def project_slug(name: str) -> str:
    """Turn a project name into a safe folder name, e.g. 'Todo App' -> 'todo-app'."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:40].strip("-")
    if not _PROJECT_RE.match(slug):
        raise SandboxViolation(f"Project name {name!r} does not give a usable folder name.")
    return slug


def project_dir(project: str, root: Optional[Path] = None) -> Path:
    """workspace/<project>/, checked to sit directly inside the workspace root."""
    if not _PROJECT_RE.match(project):
        raise SandboxViolation(f"Invalid project folder {project!r}.")
    root = (root or workspace_root()).resolve()
    path = (root / project).resolve()
    if path.parent != root:
        raise SandboxViolation(f"Project folder {project!r} escapes the workspace.")
    return path


def safe_relative_path(path: str) -> PurePosixPath:
    """Validate a file path the model supplied. Returns it as a clean relative path.

    Rejects: empty or overlong paths, control characters, absolute paths, drive letters,
    UNC paths, '~', '..' anywhere, ':' (drives and NTFS streams), Windows device names,
    segments ending in '.' or ' ', and directory paths (trailing '/').
    """
    if not path or not path.strip():
        raise SandboxViolation("Empty path.")
    if len(path) > _MAX_PATH_CHARS:
        raise SandboxViolation(f"Path longer than {_MAX_PATH_CHARS} characters.")
    if any(ord(ch) < 32 for ch in path):
        raise SandboxViolation(f"Path {path!r} contains control characters.")
    normalised = path.strip().replace("\\", "/")
    if normalised.startswith(("/", "~")) or ":" in normalised:
        raise SandboxViolation(f"Path {path!r} must be relative to the project folder.")
    if normalised.endswith("/"):
        raise SandboxViolation(f"Path {path!r} is a directory; give a file path.")
    parts = [p for p in normalised.split("/") if p not in ("", ".")]
    if not parts:
        raise SandboxViolation(f"Path {path!r} names no file.")
    for part in parts:
        if part == "..":
            raise SandboxViolation(f"Path {path!r} tries to leave the project folder.")
        if _WINDOWS_RESERVED.match(part) or part.endswith((".", " ")):
            raise SandboxViolation(f"Path {path!r} uses a name Windows cannot store safely.")
    return PurePosixPath(*parts)


def resolve_in_sandbox(sandbox: Path, relative: PurePosixPath) -> Path:
    """Absolute path for `relative` inside `sandbox`, after resolving symlinks."""
    sandbox = sandbox.resolve()
    target = (sandbox / Path(*relative.parts)).resolve()
    if target != sandbox and sandbox not in target.parents:
        raise SandboxViolation(f"Path {relative} resolves outside the project folder.")
    return target


# ── MCP connection ───────────────────────────────────────────────────────────

def _startup_timeout() -> float:
    # The first run downloads the server through npx, so allow more than ADK's 5s default.
    return float(os.getenv("AGENTCORP_MCP_TIMEOUT", "60"))


def server_params(sandbox: Path):
    """stdio launch parameters: the server may touch `sandbox` and nothing else."""
    from mcp import StdioServerParameters

    return StdioServerParameters(command="npx", args=["-y", FS_SERVER_PACKAGE, str(sandbox)])


class McpFilesystemClient:
    """A short-lived MCP session for implement_scaffold's writes.

    Only WRITE_TOOLS can be called. Startup problems raise McpUnavailable and tool errors
    raise McpToolFailed, so a caller can never mistake a failure for success.

        async with McpFilesystemClient(sandbox) as fs:
            await fs.call("write_file", {"path": ..., "content": ...})
    """

    def __init__(self, sandbox: Path):
        self.sandbox = sandbox
        self._stack: Optional[AsyncExitStack] = None
        self._session = None

    async def __aenter__(self) -> "McpFilesystemClient":
        import anyio
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        self._stack = AsyncExitStack()
        try:
            read, write = await self._stack.enter_async_context(stdio_client(server_params(self.sandbox)))
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            # Timeout only the handshake: a cancel scope must not span the contexts above,
            # which stay open after this block.
            with anyio.fail_after(_startup_timeout()):
                await self._session.initialize()
                offered = {t.name for t in (await self._session.list_tools()).tools}
        except BaseException as exc:  # noqa: BLE001 — includes anyio's cancellation on timeout
            await self._stack.aclose()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise McpUnavailable(f"Could not start the filesystem MCP server ({FS_SERVER_PACKAGE}): "
                                 f"{type(exc).__name__}: {exc}") from exc
        missing = set(WRITE_TOOLS) - offered
        if missing:
            await self._stack.aclose()
            raise McpUnavailable(f"Filesystem MCP server is missing tools {sorted(missing)}.")
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._stack is not None:
            await self._stack.aclose()

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        """Call an allowed tool and return its text. Raises on anything but success."""
        if name not in WRITE_TOOLS:
            raise PermissionError(f"Tool {name!r} is not allowed for implementation.")
        try:
            result = await self._session.call_tool(name, arguments)
        except Exception as exc:  # transport died mid-call
            raise McpToolFailed(f"{name} failed: {type(exc).__name__}: {exc}") from exc
        text = "\n".join(getattr(c, "text", "") for c in result.content)
        if result.is_error:
            raise McpToolFailed(f"{name} refused: {text}")
        return text


# ── read-only tools for the model ────────────────────────────────────────────

class ProjectWorkspaceToolset(BaseToolset):
    """Read-only MCP file tools, scoped per session to that session's project folder.

    The project is the one implement_scaffold wrote to (state["tech_design"]). Before
    that, the model gets no file tools at all. One McpToolset (and one server process)
    exists per project folder, each started with only that folder allowed.
    """

    def __init__(self, design_state_key: str = "tech_design"):
        super().__init__(tool_filter=list(READ_TOOLS))
        self._design_state_key = design_state_key
        self._toolsets: dict[Path, Any] = {}

    def _project_from(self, readonly_context: Optional[ReadonlyContext]) -> Optional[str]:
        if readonly_context is None:
            return None
        design = readonly_context.state.get(self._design_state_key) or {}
        return ((design.get("implementation") or {}).get("project")) or None

    def toolset_for(self, sandbox: Path):
        from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams

        if sandbox not in self._toolsets:
            self._toolsets[sandbox] = McpToolset(
                connection_params=StdioConnectionParams(server_params=server_params(sandbox),
                                                        timeout=_startup_timeout()),
                tool_filter=list(READ_TOOLS),
            )
        return self._toolsets[sandbox]

    async def get_tools(self, readonly_context: Optional[ReadonlyContext] = None) -> list[BaseTool]:
        project = self._project_from(readonly_context)
        if not project:
            return []
        try:
            sandbox = project_dir(project)
            if not sandbox.is_dir():
                return []
            return await self.toolset_for(sandbox).get_tools(readonly_context)
        except Exception as exc:  # a broken server must not take the whole agent down
            logger.warning("Workspace file tools unavailable for %r: %s", project, exc)
            return []

    async def close(self) -> None:
        for toolset in self._toolsets.values():
            try:
                await toolset.close()
            except Exception as exc:  # closing is best effort
                logger.warning("Error closing MCP toolset: %s", exc)
        self._toolsets.clear()
