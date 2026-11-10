"""Turn recorded token counts into an ESTIMATED cost, from a price file you control.

    pricing.yaml (your copy, git-ignored)  ->  pricing.example.yaml (shipped examples)

No prices are hard-coded here: they change, and a number the code "knows" goes stale silently. The report says
which file it used and as of when, and calls the result an estimate (a provider's invoice is the only bill).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
USER_FILE = ROOT / "pricing.yaml"
EXAMPLE_FILE = ROOT / "pricing.example.yaml"


class PricingError(ValueError):
    """The price file is missing or malformed. The message says what to fix."""


@dataclass(frozen=True)
class Rate:
    match: str
    input_per_million: float
    output_per_million: float
    thinking_as_output: bool = True
    note: str = ""

    def cost(self, tokens_in: int, tokens_out: int, tokens_thinking: int = 0) -> float:
        billed_out = tokens_out + (tokens_thinking if self.thinking_as_output else 0)
        return (tokens_in * self.input_per_million + billed_out * self.output_per_million) / 1_000_000


@dataclass
class Pricing:
    currency: str = "USD"
    as_of: str = ""
    source: str = ""
    path: Optional[Path] = None
    rates: list[Rate] = field(default_factory=list)

    def rate_for(self, model: Optional[str]) -> Optional[Rate]:
        """The rate whose `match` is the longest substring of the model name, or None (unpriced)."""
        name = (model or "").lower()
        found = [r for r in self.rates if r.match.lower() in name]
        return max(found, key=lambda r: len(r.match)) if found else None


def default_path() -> Path:
    if env := os.getenv("AGENTCORP_PRICING_FILE"):
        return Path(env)
    return USER_FILE if USER_FILE.is_file() else EXAMPLE_FILE


def load(path: Optional[Path] = None) -> Pricing:
    path = Path(path) if path else default_path()
    if not path.is_file():
        raise PricingError(f"Price file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rates = []
        for entry in data.get("models", []):
            rate = Rate(match=str(entry["match"]), input_per_million=float(entry["input_per_million"]),
                        output_per_million=float(entry["output_per_million"]),
                        thinking_as_output=bool(entry.get("thinking_as_output", True)), note=str(entry.get("note", "")))
            if not rate.match.strip() or rate.input_per_million < 0 or rate.output_per_million < 0:
                raise ValueError(f"bad entry for {entry.get('match')!r}")
            rates.append(rate)
        return Pricing(currency=str(data.get("currency", "USD")), as_of=str(data.get("as_of", "")),
                       source=str(data.get("source", "")), path=path, rates=rates)
    except (yaml.YAMLError, KeyError, TypeError, ValueError) as exc:
        raise PricingError(f"Price file {path} could not be read ({type(exc).__name__}: {str(exc)[:120]}). "
                           "Each model needs match, input_per_million and output_per_million.") from exc
