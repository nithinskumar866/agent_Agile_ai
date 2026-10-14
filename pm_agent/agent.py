"""AgentCorp — Product Manager agent.

`adk web` / `adk run` discover this module through `app` (preferred) or `root_agent`.
"""

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.apps._configs import EventsCompactionConfig
from google.genai import types

from dev_agent.agent import dev_agent
from .config import COMPACTION_KEEP_EVENTS, COMPACTION_TOKEN_THRESHOLD, MODEL
from .prompt import instruction
from .tools import ALL_TOOLS

root_agent = LlmAgent(
    name="product_manager",
    description="Turns any client request into a structured, versioned requirements specification. Collaborates with the dev_agent for technical architecture.",
    model=MODEL,
    instruction=instruction,
    tools=ALL_TOOLS,
    sub_agents=[dev_agent],
    generate_content_config=types.GenerateContentConfig(temperature=0.3),
)

app = App(
    name="pm_agent",
    root_agent=root_agent,
    events_compaction_config=EventsCompactionConfig(
        token_threshold=COMPACTION_TOKEN_THRESHOLD,
        event_retention_size=COMPACTION_KEEP_EVENTS,
    ),
)
