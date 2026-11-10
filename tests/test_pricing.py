"""Cost estimates: the price file, the arithmetic, and the audit report's COST section."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from shared import audit, pricing, safety
from tests.test_guardrails import user

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import audit_report  # noqa: E402

PRICES = """\
currency: USD
as_of: "2026-01-01"
source: https://example.test/prices
models:
  - {match: flash-lite, input_per_million: 0.10, output_per_million: 0.40}
  - {match: flash, input_per_million: 0.30, output_per_million: 2.50}
  - {match: free-model, input_per_million: 0, output_per_million: 0}
  - {match: no-think-billing, input_per_million: 1.0, output_per_million: 2.0, thinking_as_output: false}
"""


@pytest.fixture
def prices(tmp_path):
    path = tmp_path / "prices.yaml"
    path.write_text(PRICES, encoding="utf-8")
    return pricing.load(path)


def model_event(model, tin, tout, think=0, session="s1"):
    return {"ts": "2026-01-01T00:00:00+00:00", "event": "model", "session": session, "agent": "a", "model": model,
            "outcome": "ok", "tokens_in": tin, "tokens_out": tout, "tokens_thinking": think}


# ── arithmetic and matching ─────────────────────────────────────────────────

def test_cost_is_tokens_times_price_per_million_with_thinking_billed_as_output(prices):
    rate = prices.rate_for("vertex/gemini-flash")
    # 1,000,000 in x 0.30 + (400,000 out + 100,000 thinking) x 2.50 = 0.30 + 1.25
    assert rate.cost(1_000_000, 400_000, 100_000) == pytest.approx(1.55)


def test_an_entry_can_say_its_provider_does_not_bill_thinking(prices):
    rate = prices.rate_for("no-think-billing")
    assert rate.cost(1_000_000, 1_000_000, 5_000_000) == pytest.approx(1.0 + 2.0)


def test_the_longest_matching_name_wins_and_matching_ignores_case(prices):
    assert prices.rate_for("Gemini-Flash-Lite-001").input_per_million == 0.10
    assert prices.rate_for("GEMINI-FLASH").input_per_million == 0.30


def test_an_unknown_or_missing_model_is_unpriced_not_zero(prices):
    assert prices.rate_for("some-other-model") is None and prices.rate_for(None) is None
    assert prices.rate_for("free-model").cost(10**9, 10**9) == 0.0  # free is a price; unknown is not


# ── the price file ──────────────────────────────────────────────────────────

def test_the_shipped_example_file_loads_and_says_it_is_only_an_example():
    loaded = pricing.load(pricing.EXAMPLE_FILE)
    text = pricing.EXAMPLE_FILE.read_text(encoding="utf-8")
    assert loaded.rate_for("gemini-2.5-flash") and loaded.rate_for("nemotron")
    assert "EXAMPLE values" in text and "verify" in text.lower() and loaded.source.startswith("https://")


def test_your_own_pricing_yaml_wins_over_the_example(tmp_path, monkeypatch):
    mine = tmp_path / "pricing.yaml"
    mine.write_text(PRICES, encoding="utf-8")
    monkeypatch.delenv("AGENTCORP_PRICING_FILE", raising=False)
    monkeypatch.setattr(pricing, "USER_FILE", mine)
    assert pricing.default_path() == mine
    monkeypatch.setattr(pricing, "USER_FILE", tmp_path / "missing.yaml")
    assert pricing.default_path() == pricing.EXAMPLE_FILE
    monkeypatch.setenv("AGENTCORP_PRICING_FILE", str(mine))
    assert pricing.default_path() == mine


@pytest.mark.parametrize("content", ["models: [{match: x}]", "models: [{match: x, input_per_million: -1, output_per_million: 1}]",
                                     "models: [{match: '', input_per_million: 1, output_per_million: 1}]", "models: : :"])
def test_a_bad_price_file_gives_a_clear_error(tmp_path, content):
    bad = tmp_path / "bad.yaml"
    bad.write_text(content, encoding="utf-8")
    with pytest.raises(pricing.PricingError, match="could not be read"):
        pricing.load(bad)
    with pytest.raises(pricing.PricingError, match="not found"):
        pricing.load(tmp_path / "nope.yaml")


# ── the report ──────────────────────────────────────────────────────────────

def test_the_report_prices_each_model_and_totals_them(prices):
    events = [model_event("vertex/gemini-flash", 600_000, 100_000, 50_000), model_event("vertex/gemini-flash", 400_000, 0, 0),
              model_event("free-model", 9_000_000, 1_000_000)]
    text = audit_report.summarise(events, prices)
    # flash: 1,000,000 in x 0.30 + 150,000 out x 2.50 = 0.30 + 0.375 = 0.675; free-model costs nothing
    assert "COST (estimate)" in text and "USD 0.6750" in text and "Total (priced models): USD 0.6750" in text
    assert "0.3 / 2.5" in text and "as of 2026-01-01" in text and "This is not a bill" in text


def test_an_unpriced_model_is_listed_and_left_out_of_the_total(prices):
    text = audit_report.summarise([model_event("mystery-model", 1000, 100), model_event("free-model", 5, 5)], prices)
    assert "unpriced" in text and "Not priced (add to prices.yaml): mystery-model" in text
    assert "Total (priced models): USD 0.0000" in text  # the free model is priced, at zero


def test_when_nothing_can_be_priced_the_report_says_so_instead_of_showing_zero(prices):
    text = audit_report.summarise([model_event(None, 1000, 100)], prices)
    assert "Nothing could be priced yet" in text and "(model not recorded)" in text and "Total (priced" not in text


def test_the_what_if_option_prices_recorded_tokens_on_another_model(prices):
    events = [model_event("free-model", 1_000_000, 100_000, 100_000), model_event("free-model", 0, 0, 0, session="s2")]
    text = audit_report.summarise(events, prices, as_model="vertex/gemini-flash")
    assert "as if every call had run on vertex/gemini-flash" in text
    assert "USD 0.8000" in text  # 1,000,000 x 0.30 + 200,000 x 2.50 = 0.30 + 0.50
    assert "per session: USD 0.4000 over 2 sessions" in text


def test_without_a_price_file_the_report_shows_tokens_and_how_to_add_prices():
    text = audit_report.summarise([model_event("m", 1000, 100)], None)
    assert "No price file" in text and "pricing.yaml" in text and "1000" in text


def test_a_broken_price_file_does_not_break_the_report(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("models: [{match: x}]", encoding="utf-8")
    log = tmp_path / "a.jsonl"
    log.write_text('{"ts": "2026-01-01T00:00:00+00:00", "event": "model", "outcome": "ok", "tokens_in": 5}\n', encoding="utf-8")
    assert audit_report.main(["--file", str(log), "--pricing", str(bad)]) == 0
    captured = capsys.readouterr()
    assert "costs not shown" in captured.err and "MODEL CALLS" in captured.out and "No price file" in captured.out


# ── the model name reaches the log ──────────────────────────────────────────

def test_each_audited_model_call_records_which_model_it_went_to(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTCORP_AUDIT_FILE", str(tmp_path / "a.jsonl"))
    ctx = SimpleNamespace(session=SimpleNamespace(id="s1"), agent_name="pm", invocation_id="i9", function_call_id="f")
    request = LlmRequest(model="openai/nvidia/nemotron-test", contents=[user(types.Part(text="hi"))])
    usage = types.GenerateContentResponseUsageMetadata(prompt_token_count=10, candidates_token_count=5)
    asyncio.run(safety.before_model(ctx, request))
    asyncio.run(safety.after_model(ctx, LlmResponse(content=types.Content(role="model", parts=[types.Part(text="ok")]),
                                                     usage_metadata=usage)))
    (event,) = audit.read_events(tmp_path / "a.jsonl")
    assert event["model"] == "openai/nvidia/nemotron-test" and event["tokens_in"] == 10
