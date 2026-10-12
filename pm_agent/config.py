"""Runtime configuration. Everything tunable lives here, read from the environment."""

import os

MODEL = os.getenv("PM_MODEL", "gemini-3.5-flash")

# Compaction: once a prompt reaches this many tokens, older turns are summarised
# and only the last N raw events are kept. The spec itself is never at risk —
# it lives in session state, not in the conversation.
COMPACTION_TOKEN_THRESHOLD = int(os.getenv("PM_COMPACTION_TOKENS", "60000"))
COMPACTION_KEEP_EVENTS = int(os.getenv("PM_COMPACTION_KEEP_EVENTS", "20"))

# How many clarifying questions to ask per turn.
MAX_QUESTIONS_PER_TURN = int(os.getenv("PM_MAX_QUESTIONS", "5"))

SPEC_STATE_KEY = "spec"
