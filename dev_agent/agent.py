"""AgentCorp — Developer & Technical Architect agent.

Discoverable as a standalone agent or as a sub-agent of the Product Manager.
"""

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.genai import types

from .config import MODEL
from .prompt import instruction
from .tools import ALL_DEV_TOOLS
from .workspace import ProjectWorkspaceToolset

# Imported after .tools: qa_agent's tools import dev_agent.logic and .workspace.
from qa_agent.tools import run_tests  # noqa: E402

dev_agent = LlmAgent(
    name="dev_agent",
    description="Senior Software Architect and Lead Developer. Formulates technology stack recommendations, designs system architecture, generates project structure scaffolds, and implements code that passes the locked acceptance tests.",
    model=MODEL,
    instruction=instruction,
    # Python tools, the shared test runner, and read-only MCP file tools for the project sandbox.
    tools=[*ALL_DEV_TOOLS, run_tests, ProjectWorkspaceToolset()],
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
)

# Discoverable by adk web as a standalone app if selected directly. A clone, because
# `dev_agent` itself becomes the PM's sub-agent and an agent can only have one parent —
# the standalone copy must not offer a transfer to a product_manager that isn't there.
app = App(
    name="dev_agent",
    root_agent=dev_agent.clone(),
)
