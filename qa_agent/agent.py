"""AgentCorp — QA agent: writes and locks acceptance tests, runs them in Docker."""

import os

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.genai import types

from dev_agent.config import MODEL as DEV_MODEL  # also loads pm_agent/.env
from shared.models import build_model

from .prompt import instruction
from .tools import ALL_QA_TOOLS

# QA_MODEL overrides; by default QA runs on the Dev agent's model.
MODEL = build_model(os.environ["QA_MODEL"]) if os.getenv("QA_MODEL") else DEV_MODEL

qa_agent = LlmAgent(
    name="qa_agent",
    description="QA Engineer. Writes acceptance tests from the approved PRD before coding, locks them, "
                "runs them against the implemented code in an isolated container, and reports the verdict.",
    model=MODEL,
    instruction=instruction,
    tools=ALL_QA_TOOLS,
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
)

# Standalone copy for adk web; the original becomes the PM's sub-agent.
app = App(name="qa_agent", root_agent=qa_agent.clone())
