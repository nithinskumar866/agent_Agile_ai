"""The PM's read-only "company memory", served by MCP Toolbox for Databases.

Toolbox is a separate server (`.tools/toolbox.exe --config toolbox/tools.yaml`) that owns the
SQL. The agent sees three named lookups from the `company_memory` toolset and nothing else:
no query text, no write tool, and the database file is opened read-only.

The memory is a convenience, never a dependency: if the server is not running the PM simply
has no memory tools that turn and carries on. Set TOOLBOX_URL="" to switch it off.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.base_toolset import BaseToolset

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:5000"
TOOLSET_NAME = "company_memory"


class OptionalToolset(BaseToolset):
    """Wraps another toolset so that a failure to load it yields no tools, not a crash."""

    def __init__(self, inner: BaseToolset, label: str):
        super().__init__()
        self._inner = inner
        self._label = label
        self._warned = False

    async def get_tools(self, readonly_context: Optional[ReadonlyContext] = None) -> list[BaseTool]:
        try:
            return await self._inner.get_tools(readonly_context)
        except Exception as exc:  # server down, wrong URL, protocol mismatch
            if not self._warned:
                logger.warning("%s unavailable, continuing without it: %s", self._label, str(exc)[:200])
                self._warned = True
            return []

    async def close(self) -> None:
        try:
            await self._inner.close()
        except Exception:  # closing a toolset that never connected is not an error
            pass


def company_memory_toolset() -> Optional[BaseToolset]:
    """The memory toolset, or None when TOOLBOX_URL is set to an empty string."""
    url = os.getenv("TOOLBOX_URL", DEFAULT_URL).strip()
    if not url:
        return None
    from google.adk.tools.toolbox_toolset import ToolboxToolset

    return OptionalToolset(ToolboxToolset(url, toolset_name=TOOLSET_NAME), "Company memory (Toolbox)")
