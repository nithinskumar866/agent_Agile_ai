"""Runtime configuration for the Developer / Architect agent."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Run standalone, adk only loads dev_agent/.env (or one in a parent folder), so fall back
# to the PM's .env for keys and model. override=False: anything already set wins.
load_dotenv(Path(__file__).resolve().parent.parent / "pm_agent" / ".env", override=False)

from shared.models import build_model  # noqa: E402 — must read the env loaded above

MODEL_NAME = os.getenv("DEV_MODEL", os.getenv("PM_MODEL", "gemini-3.6-flash"))
MODEL = build_model(MODEL_NAME)
TECH_DESIGN_STATE_KEY = "tech_design"
