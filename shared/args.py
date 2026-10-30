"""Tolerance for how models send tool arguments, and a bound on how much we say back."""

import json
from typing import Any, List


class ArgumentFormatError(ValueError):
    """A list argument arrived in a form that cannot be read. Short on purpose: it goes
    straight back into the model's context."""


def as_list(value: Any, name: str = "argument") -> List[Any]:
    """Return `value` as a list.

    Some models (seen with Nemotron via LiteLLM) send a list argument as JSON text, and
    sometimes with junk after it, such as a trailing comma. Read the first JSON value and
    ignore trailing commas. Anything that still is not a list raises ArgumentFormatError;
    a string is never treated as a sequence of characters.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            decoded, end = json.JSONDecoder(strict=False).raw_decode(text)
        except ValueError as exc:
            raise ArgumentFormatError(
                f"{name} arrived as text that is not valid JSON ({str(exc)[:80]}). "
                "Send it as a real list, not a quoted string.") from exc
        if not isinstance(decoded, list) or text[end:].strip(" \t\r\n,;"):
            raise ArgumentFormatError(
                f"{name} must be ONE JSON list such as [\"a\", \"b\"]; got something else or extra text after it.")
        return decoded
    raise ArgumentFormatError(f"{name} must be a list, not {type(value).__name__}.")


def brief(problems: List[str], limit: int = 10, width: int = 300) -> List[str]:
    """At most `limit` problems of at most `width` characters each."""
    shown = [p if len(p) <= width else p[:width] + "…" for p in problems[:limit]]
    if len(problems) > limit:
        shown.append(f"…and {len(problems) - limit} more.")
    return shown
