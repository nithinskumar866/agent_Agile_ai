"""Runtime configuration for the Developer / Architect agent."""

import os

MODEL = os.getenv("DEV_MODEL", os.getenv("PM_MODEL", "gemini-2.5-flash"))
TECH_DESIGN_STATE_KEY = "tech_design"
