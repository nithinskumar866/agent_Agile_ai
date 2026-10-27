"""Tolerance for how models send tool arguments."""

import json
from typing import Any


def as_list(value: Any) -> Any:
    """Some models (seen with Nemotron via LiteLLM) send a list argument as a JSON string.
    Decode it; anything else is returned unchanged for normal validation to judge."""
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return value
        if isinstance(decoded, list):
            return decoded
    return value
