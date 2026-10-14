"""AgentCorp — Developer & Technical Architect agent.

Discoverable as a standalone agent or as a sub-agent of the Product Manager.
"""

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.genai import types

from .config import MODEL
from .prompt import instruction
from .tools import ALL_DEV_TOOLS

dev_agent = LlmAgent(
    name="dev_agent",
    description="Senior Software Architect and Lead Developer. Formulates technology stack recommendations, designs system architecture, and generates project structure scaffolds.",
    model=MODEL,
    instruction=instruction,
    tools=ALL_DEV_TOOLS,
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
)

# Discoverable by adk web as a standalone app if selected directly
app = App(
    name="dev_agent",
    root_agent=dev_agent,
)
