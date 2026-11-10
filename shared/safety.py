"""The safety net around every agent: callbacks for the model and for every tool.

Attach with `LlmAgent(..., **agent_callbacks())`. Together they:

- screen uploaded files before the model sees them (shared.uploads, shared.guardrails);
- cap any tool result at MAX_TOOL_RESULT_CHARS, because one oversized result can poison a session
  for good (two 2 MB tool errors once made a session unusable);
- refuse to call the model when the conversation is already too large for it, with a clear message
  instead of a provider error that quotes the whole prompt back;
- turn an unexpected tool exception into a short error the model can react to;
- write every tool call, model call and upload to the audit log (shared.audit).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

from google.adk.models.llm_response import LlmResponse
from google.genai import types

from . import audit
from .uploads import screen_contents

MAX_TOOL_RESULT_CHARS = int(os.getenv("AGENTCORP_MAX_TOOL_RESULT_CHARS", "40000"))
# About 750k tokens at 4 characters each, under the 1M-token window of the models we use.
MAX_CONTEXT_CHARS = int(os.getenv("AGENTCORP_MAX_CONTEXT_CHARS", "3000000"))

_started: dict[Any, float] = {}
_seen_uploads: set[str] = set()
_notified: dict[Any, set[str]] = {}   # session -> uploads already announced to the client
_pending: dict[Any, dict[str, str]] = {}  # session -> {upload sha: notice} waiting for the next reply


def _ms(key: Any) -> Optional[int]:
    began = _started.pop(key, None)
    return None if began is None else int((time.perf_counter() - began) * 1000)


def _who(ctx) -> dict:
    try:
        session = ctx.session.id
    except Exception:
        session = None
    return {"session": session, "agent": getattr(ctx, "agent_name", None)}


def size_of(value: Any) -> int:
    try:
        return len(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return len(str(value))


def _notice(report) -> str:
    if report.kind == "rejected":
        return f"'{report.name}' was not read: {report.note}."
    detail = " ".join(x for x in (report.summary, (report.note.capitalize() + ".") if report.note else "") if x)
    return f"'{report.name}': {detail}" if detail else ""


def estimate_context_chars(contents: list[types.Content]) -> int:
    total = 0
    for content in contents or []:
        for part in content.parts or []:
            if part.text:
                total += len(part.text)
            elif part.function_call or part.function_response:
                total += size_of((part.function_call or part.function_response).model_dump(mode="json"))
            elif part.inline_data and part.inline_data.data:
                total += min(len(part.inline_data.data), 10_000)  # images cost far fewer tokens than bytes
    return total


# ── model calls ──────────────────────────────────────────────────────────────

async def before_model(ctx, llm_request) -> Optional[LlmResponse]:
    who = _who(ctx)
    for report in screen_contents(llm_request.contents or []):
        if (text := _notice(report)) and report.sha not in _notified.get(who["session"], ()):
            _pending.setdefault(who["session"], {})[report.sha] = text
        if report.sha not in _seen_uploads:  # the same file is re-sent every turn; log it once
            _seen_uploads.add(report.sha)
            audit.record("upload", **who, file=audit.clip(report.name, 80), mime=report.mime, kind=report.kind,
                         sha=report.sha, chars=report.chars, guardrail=report.summary or None,
                         note=report.note or None)
    chars = estimate_context_chars(llm_request.contents)
    if chars > MAX_CONTEXT_CHARS:
        audit.record("model", **who, model=str(llm_request.model), outcome="blocked_context_too_large",
                     request_chars=chars)
        return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=(
            "This conversation has grown too large for the model to read, so I cannot continue in it. "
            "Please start a new session; the saved requirements are not lost if you keep the same project."))]))
    _started[("model", getattr(ctx, "invocation_id", None), getattr(ctx, "agent_name", None))] = time.perf_counter()
    return None


def _outcome(response: LlmResponse) -> str:
    if response.error_code:
        return f"error:{response.error_code}"
    parts = response.content.parts if response.content and response.content.parts else []
    useful = any(p.function_call or (p.text and not p.thought) for p in parts)
    if not useful:
        return "no_content"
    return "max_tokens" if getattr(response.finish_reason, "name", "") == "MAX_TOKENS" else "ok"


def _with_upload_notice(session: Any, response: LlmResponse) -> Optional[LlmResponse]:
    """Add what the guardrail did to the first real reply after an upload.

    The model is asked to tell the client, but a control that depends on the model remembering
    is not a control: the system says it itself, once per file per session.
    """
    pending = _pending.get(session)
    parts = response.content.parts if response.content and response.content.parts else []
    if not pending or not any(p.function_call or (p.text and not p.thought) for p in parts):
        return None
    lines = "\n".join(f"- {text}" for text in pending.values())
    _notified.setdefault(session, set()).update(pending)
    _pending.pop(session, None)
    notice = types.Part(text=f"\n\nUpload notice (added by the system, so you always see it):\n{lines}")
    content = response.content.model_copy(update={"parts": [*parts, notice]})
    return response.model_copy(update={"content": content})


async def after_model(ctx, response: LlmResponse) -> Optional[LlmResponse]:
    if response.partial:
        return None
    usage = response.usage_metadata
    audit.record(
        "model", **_who(ctx), outcome=_outcome(response),
        finish=getattr(response.finish_reason, "name", None),
        tokens_in=getattr(usage, "prompt_token_count", None), tokens_out=getattr(usage, "candidates_token_count", None),
        tokens_thinking=getattr(usage, "thoughts_token_count", None),
        error=audit.clip(response.error_message, 200) if response.error_message else None,
        ms=_ms(("model", getattr(ctx, "invocation_id", None), getattr(ctx, "agent_name", None))))
    return _with_upload_notice(_who(ctx)["session"], response)


async def on_model_error(ctx, llm_request, error: Exception) -> Optional[LlmResponse]:
    audit.record("model", **_who(ctx), outcome="exception", error=audit.clip(f"{type(error).__name__}: {error}", 200))
    return None  # let ADK raise it; the audit line is the useful part


# ── tool calls ───────────────────────────────────────────────────────────────

async def before_tool(tool, args: dict, ctx) -> Optional[dict]:
    _started[("tool", getattr(ctx, "function_call_id", None))] = time.perf_counter()
    return None


async def after_tool(tool, args: dict, ctx, tool_response: Any) -> Optional[dict]:
    size = size_of(tool_response)
    truncated = size > MAX_TOOL_RESULT_CHARS
    rejected = isinstance(tool_response, dict) and "error" in tool_response
    audit.record(
        "tool", **_who(ctx), tool=getattr(tool, "name", str(tool)), args=audit.clip(args),
        outcome="truncated" if truncated else ("rejected" if rejected else "ok"),
        message=audit.clip(tool_response["error"], 200) if rejected else None, result_chars=size,
        ms=_ms(("tool", getattr(ctx, "function_call_id", None))))
    if not truncated:
        return None
    text = tool_response if isinstance(tool_response, str) else json.dumps(tool_response, ensure_ascii=False, default=str)
    return {"truncated": True, "original_chars": size,
            "note": "The result was too large and was cut. Ask for something narrower.", "preview": text[:2000]}


async def on_tool_error(tool, args: dict, ctx, error: Exception) -> Optional[dict]:
    audit.record("tool", **_who(ctx), tool=getattr(tool, "name", str(tool)), args=audit.clip(args),
                 outcome="exception", message=audit.clip(f"{type(error).__name__}: {error}", 200),
                 ms=_ms(("tool", getattr(ctx, "function_call_id", None))))
    return {"error": f"The tool failed unexpectedly ({type(error).__name__}: {str(error)[:200]}). "
                     "Check the current state before retrying, or tell the client."}


def agent_callbacks() -> dict:
    """Keyword arguments for LlmAgent: the same safety net on every agent."""
    return {
        "before_model_callback": before_model, "after_model_callback": after_model,
        "on_model_error_callback": on_model_error, "before_tool_callback": before_tool,
        "after_tool_callback": after_tool, "on_tool_error_callback": on_tool_error,
    }
