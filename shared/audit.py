"""An append-only audit trail: one JSON line per tool call, model call and upload.

    audit/audit.jsonl            (git-ignored; AGENTCORP_AUDIT_FILE overrides; AGENTCORP_AUDIT=0 turns it off)

Why it exists: when an agent misbehaves, "what exactly did it call, with what, and what came
back?" must be answerable from evidence, not memory. `python -m scripts.audit_report` summarises it.
Personal data and secrets are masked before anything is written, arguments are truncated, and a
write failure never breaks the agent turn.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .guardrails import mask_for_log

DEFAULT_FILE = Path(__file__).resolve().parent.parent / "audit" / "audit.jsonl"
MAX_FILE_BYTES = 5_000_000  # then the file rotates to audit.jsonl.1 (one generation kept)
FIELD_LIMIT = 300

_lock = threading.Lock()


def audit_file() -> Path:
    return Path(os.getenv("AGENTCORP_AUDIT_FILE") or DEFAULT_FILE)


def enabled() -> bool:
    return os.getenv("AGENTCORP_AUDIT", "1").lower() not in ("0", "false", "off", "no")


def clip(value: Any, limit: int = FIELD_LIMIT) -> str:
    """Text for the log: masked, one line, bounded."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    total = len(text)
    # Cut BEFORE masking so a multi-megabyte value costs nothing; the margin keeps a secret that
    # straddles the limit whole, so masking still sees all of it.
    text = " ".join(text[: limit + 200].split())
    text = mask_for_log(text)
    return text if total <= limit else text[:limit] + f"…(+{total - limit})"


def record(event: str, **fields: Any) -> None:
    """Append one event. Best effort: any failure is swallowed."""
    if not enabled():
        return
    try:
        row = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "event": event}
        row.update({k: v for k, v in fields.items() if v is not None})
        line = json.dumps(row, ensure_ascii=False, default=str)
        path = audit_file()
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > MAX_FILE_BYTES:
                path.replace(path.with_name(path.name + ".1"))
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:  # an audit problem must never become an agent problem
        pass


def read_events(path: Path | None = None) -> list[dict]:
    """All events in the file (and its rotated predecessor), oldest first; bad lines skipped."""
    path = path or audit_file()
    events: list[dict] = []
    for p in (path.with_name(path.name + ".1"), path):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events
