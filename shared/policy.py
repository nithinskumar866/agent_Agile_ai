"""Client-authorization policy: what wording counts as "publish" or "approve".

Shared by the PM's publish_prd guard and the eval metrics, so the code that enforces
the rule and the eval that measures the model against it can never disagree.
"""

from __future__ import annotations

import re

# ── client authorization ─────────────────────────────────────────────────────
#
# Approval is a client decision, so the gate is code, not model judgement. It is
# deliberately strict: a false "no" costs one confirmation question, a false "yes"
# signs off a spec the client never approved.

# Phrasings that grant approval. Each is matched against one sentence at a time.
_APPROVAL_GRANTS = [
    # "approve of" is an opinion, not a sign-off.
    re.compile(r"\b(?:i|we)\s+(?:hereby\s+|formally\s+|officially\s+|fully\s+)*(?:approve|sign\s+off)\b(?!\s+of\b)"),
    re.compile(r"\b(?:i|we)(?:'ve|\s+have)\s+(?:formally\s+|officially\s+)?(?:approved|signed\s+off)\b"),
    re.compile(r"^\s*(?:(?:ok(?:ay)?|yes|great|thanks),?\s+)?(?:please\s+)?(?:go\s+ahead\s+and\s+)?"
               r"(?:approve|mark)\b.*\bapprov"),
    re.compile(r"^\s*(?:(?:ok(?:ay)?|yes|great|thanks),?\s+)?(?:please\s+)?(?:go\s+ahead\s+and\s+)?"
               r"approve\s+(?:the\s+)?(?:prd|spec|it|this|v\d+|version)\b"),
    re.compile(r"\b(?:prd|spec|it|this|v\d+)\s+(?:is\s+)?(?:now\s+)?approved\b"),
    re.compile(r"^\s*(?:(?:yes|ok(?:ay)?),?\s+)?approved\s*[.!]*\s*$"),
    re.compile(r"\byou\s+have\s+my\s+(?:approval|sign[-\s]off)\b"),
    re.compile(r"\bapproval\s+(?:is\s+)?granted\b"),
]

# Any of these in a sentence that mentions approval makes the whole message a "no":
# negations, conditions, deferrals, and hypotheticals.
_APPROVAL_BLOCKERS = re.compile(
    r"\b(?:not|never|no|without|before|until|unless|if|once|when|after|later|yet|wait|hold"
    r"|don't|dont|won't|can't|cannot|shouldn't|wouldn't|would|could|might|should|may)\b"
)

_APPROVAL_MENTION = re.compile(r"approv|\bsign(?:ed)?[\s-]off\b")

_PUBLISH_FORBIDDEN = re.compile(
    r"\b(?:don't|dont|do\s+not|not|never|without|no)\s+(?:\w+\s+){0,3}?(?:re-?)?publish"
)


def _normalise(message: str) -> str:
    return message.lower().replace("’", "'")


def is_explicit_approval(message: str | None) -> bool:
    """True only when the client's message unambiguously approves the spec.

    Praise ("looks good"), agreement ("sounds right"), review requests, readiness
    questions ("is it ready for approval?"), and conditional or negated approval
    ("approve it once Q-2 is answered", "don't approve yet") are all False.
    """
    if not message:
        return False
    granted = False
    for sentence in re.findall(r"[^.!?\n;]+[.!?]*", _normalise(message)):
        if not _APPROVAL_MENTION.search(sentence):
            continue
        if sentence.rstrip().endswith("?") or _APPROVAL_BLOCKERS.search(sentence):
            return False
        if any(p.search(sentence) for p in _APPROVAL_GRANTS):
            granted = True
    return granted


_PUBLISH_GRANTS = [
    re.compile(r"^\s*(?:(?:ok(?:ay)?|yes|great|thanks|sure),?\s+)?(?:please\s+)?(?:go\s+ahead\s+and\s+)?(?:re-?)?publish\b"),
    re.compile(r"\b(?:please|go\s+ahead\s+and)\s+(?:re-?)?publish\b"),
    re.compile(r"\b(?:i|we)\s+(?:want|need)\s+(?:you\s+)?to\s+(?:re-?)?publish\b"),
    re.compile(r"\byou\s+(?:can|may)\s+(?:now\s+)?(?:re-?)?publish\b"),
]
# A polite question is still a request: "Can you publish the PRD?"
_PUBLISH_POLITE_REQUEST = re.compile(r"^\s*(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:re-?)?publish\b")
_PUBLISH_BLOCKERS = re.compile(
    r"\b(?:not|never|no|without|before|until|unless|if|once|when|after|later|yet|wait|hold"
    r"|don't|dont|won't|shouldn't)\b"
)
# "publish" aimed at the architect's deliverable is not a request to publish the PRD.
_OTHER_DELIVERABLE = re.compile(r"\barchitect|\bdev_agent\b|\bdeveloper\b|\btech(?:nical)?\s+(?:design|stack)\b|\bscaffold")


def is_explicit_publish_request(message: str | None) -> bool:
    """True only when the client's message explicitly asks the PM to publish the PRD.

    Explicit approval also counts. Praise, answers, review requests, readiness questions
    ("is it ready to publish?"), deferred or negated requests, and requests to publish the
    architect's work are all False.
    """
    if not message:
        return False
    if is_explicit_approval(message):
        return True
    granted = False
    for sentence in re.findall(r"[^.!?\n;]+[.!?]*", _normalise(message)):
        if "publish" not in sentence:
            continue
        if _PUBLISH_BLOCKERS.search(sentence) or _OTHER_DELIVERABLE.search(sentence):
            return False
        if sentence.rstrip().endswith("?"):
            if _PUBLISH_POLITE_REQUEST.search(sentence):
                granted = True
            continue
        if any(p.search(sentence) for p in _PUBLISH_GRANTS):
            granted = True
    return granted


def forbids_publishing(message: str | None) -> bool:
    """True when the client's message tells the PM not to publish (e.g. 'do not publish yet')."""
    return bool(message) and bool(_PUBLISH_FORBIDDEN.search(_normalise(message)))
