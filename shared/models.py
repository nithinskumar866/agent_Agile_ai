"""Model selection shared by every AgentCorp agent.

A model name without a slash (e.g. 'gemini-3.6-flash') goes to Gemini natively.
A name with a slash (e.g. 'nvidia/nemotron-3.5-super-vl-120b-a12b') goes to
NVIDIA's OpenAI-compatible endpoint through LiteLLM — useful for development
when the Gemini free tier's rate limits get in the way.
"""

from __future__ import annotations

import os

from google.adk.models.base_llm import BaseLlm

NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
# Reasoning counts against this limit. At 16384 the QA agent spent all of it thinking about
# the tests and never produced the tool call (finish_reason MAX_TOKENS), three times running.
DEFAULT_MAX_TOKENS = 49152


def build_model(name: str) -> str | BaseLlm:
    if "/" not in name:
        return name

    from google.adk.models.lite_llm import LiteLlm

    thinking = os.getenv("NVIDIA_THINKING", "true").lower() == "true"
    return LiteLlm(
        # 'openai/' tells LiteLLM to speak the OpenAI protocol to api_base.
        model=f"openai/{name}",
        api_base=NVIDIA_BASE_URL,
        # A placeholder rather than None, so a missing key fails with a clear 401
        # instead of LiteLLM silently falling back to OPENAI_API_KEY.
        api_key=os.getenv("NVIDIA_API_KEY") or "missing-NVIDIA_API_KEY",
        top_p=0.95,
        max_tokens=int(os.getenv("NVIDIA_MAX_TOKENS", DEFAULT_MAX_TOKENS)),
        extra_body={"chat_template_kwargs": {"enable_thinking": thinking}},
    )
