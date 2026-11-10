"""Input guardrails for untrusted text: uploaded documents and the audit log.

Three things are looked for, and none of the matched text is ever repeated back:

- Prompt injection: lines that try to give the AI new orders ("ignore previous instructions",
  chat-template tags, names of our own gated tools). The whole line is withheld.
- Personal data and secrets: emails, phone numbers, card numbers (Luhn-checked), government IDs,
  API keys. They are masked, so the model still sees that "an email was here".
- Hidden text: zero-width, bidi-control and Unicode tag characters, which can smuggle
  instructions past a human reader. They are removed.

This is the local counterpart of Model Armor: same ideas (screen input, mask sensitive data,
report what was found), no cloud needed. It is a filter, not a proof of safety: a determined
attacker can phrase things this does not match, so the gates in the tool code remain the real
control. Text the client types themselves is NOT scanned (a client may legitimately type an email
address); only material that arrives as a document is.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

INJECTION_PLACEHOLDER = "[removed: possible instruction aimed at the AI]"

_INJECTION = {
    "override-instructions": re.compile(
        r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(previous|prior|above|earlier|all|any|your|the|these)\b"
        r".{0,30}\b(instructions?|rules?|prompts?|guidelines?|polic(?:y|ies)|constraints?)\b", re.I),
    "role-reassignment": re.compile(
        r"\b(you are now|from now on,? you|pretend (?:to be|you are)|new persona)\b", re.I),
    "reveal-secrets": re.compile(
        r"\b(reveal|show|print|repeat|leak|output|tell me)\b.{0,30}\b(system prompt|your instructions|"
        r"hidden instructions|api[ _-]?keys?|passwords?|credentials)\b", re.I),
    "tool-call-instruction": re.compile(
        r"\b(mark_approved|publish_prd|transfer_to_agent|lock_test_suite|write_test_suite|"
        r"implement_scaffold|run_tests|set_engagement)\b", re.I),
    "chat-template-tag": re.compile(
        r"(<\s*/?\s*(system|assistant|instructions?)\s*>|\[/?INST\]|<\|[^|>]{1,30}\|>|^\s*(system|assistant|developer)\s*:)",
        re.I | re.M),
    "secrecy-request": re.compile(
        r"\b(do not|don't|never)\b.{0,20}\b(tell|inform|show|mention)\b.{0,20}\b(the )?(user|client|human)\b", re.I),
    "new-instructions": re.compile(r"\bnew (instructions?|task|rules?)\s*:", re.I),
    "data-exfiltration": re.compile(
        r"\b(send|post|forward|exfiltrate|upload)\b.{0,50}\b(conversation|chat history|secrets?|credentials|"
        r"api keys?|environment variables?)\b", re.I),
    "approval-bypass": re.compile(
        r"\b(without|no need to)\b.{0,25}\b(ask(?:ing)?|confirm(?:ation|ing)?|approval|permission)\b", re.I),
}

_SECRETS = re.compile(
    r"(nvapi-[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{35}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?i:api[_-]?key)\s*[:=]\s*\S{12,})")
# Bounded quantifiers: an unbounded one backtracks quadratically on a long run of letters.
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,24}")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
# Aadhaar-style 4-4-4 and US SSN 3-2-4, but never a slice of a longer run of digits (a card number).
_GOV_ID = re.compile(
    r"(?<!\d)(?<!\d[ -])(\d{4}[ -]\d{4}[ -]\d{4}|\d{3}-\d{2}-\d{4})(?![ -]?\d)|\b[A-Z]{5}\d{4}[A-Z]\b")
_PHONE = re.compile(
    r"(?<![\w+])(?:\+\d{1,3}[\s.-]?(?:\d[\s.-]?){8,12}\d"
    r"|(?:\+91[\s-]?|0)?[6-9]\d{9}"
    r"|\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})(?![\w])")

# zero-width, bidi controls, BOM, and the Unicode "tag" block used for invisible text
_HIDDEN = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿\U000e0000-\U000e007f]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Scan:
    text: str
    findings: Counter = field(default_factory=Counter)  # (kind, label) -> count

    @property
    def clean(self) -> bool:
        return not self.findings

    def count(self, kind: str) -> int:
        return sum(n for (k, _), n in self.findings.items() if k == kind)

    def summary(self) -> str:
        """A plain sentence for the client. Never contains the matched text."""
        if self.clean:
            return ""
        parts = []
        if n := self.count("injection"):
            parts.append(f"withheld {n} line{'s' if n != 1 else ''} that looked like instructions to the AI")
        if n := self.count("hidden"):
            parts.append(f"removed {n} hidden character{'s' if n != 1 else ''}")
        pii = [(label, n) for (kind, label), n in self.findings.items() if kind in ("pii", "secret")]
        if pii:
            parts.append("masked " + ", ".join(f"{n} {label}{'s' if n != 1 else ''}" for label, n in sorted(pii)))
        return (lambda t: t[0].upper() + t[1:])("; ".join(parts)) + "."


def _luhn_ok(digits: str) -> bool:
    total, flip = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if flip:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        flip = not flip
    return total % 10 == 0


def mask_sensitive(text: str, findings: Counter, personal: bool = True) -> str:
    """Mask secrets and, unless `personal` is False, emails, cards, government IDs and phone numbers;
    record counts only."""
    def sub(kind: str, label: str, pattern: re.Pattern, token: str, text: str, check=None) -> str:
        def repl(m: re.Match) -> str:
            if check and not check(m.group(0)):
                return m.group(0)
            findings[(kind, label)] += 1
            return token
        return pattern.sub(repl, text)

    text = sub("secret", "secret", _SECRETS, "[SECRET]", text)
    if not personal:
        return text
    text = sub("pii", "email", _EMAIL, "[EMAIL]", text)
    text = sub("pii", "card number", _CARD, "[CARD]", text,
               check=lambda s: 13 <= len(re.sub(r"\D", "", s)) <= 19 and _luhn_ok(re.sub(r"\D", "", s)))
    text = sub("pii", "government ID", _GOV_ID, "[GOV-ID]", text)
    text = sub("pii", "phone number", _PHONE, "[PHONE]", text)
    return text


def _scrub(segment: str, findings: Counter) -> tuple[str, int]:
    """Replace every sentence of `segment` that looks like an instruction to the AI."""
    kept, hits = [], 0
    for sentence in _SENTENCE_END.split(segment):
        hit = next((name for name, rx in _INJECTION.items() if rx.search(sentence)), None)
        if hit:
            findings[("injection", hit)] += 1
            hits += 1
            kept.append(INJECTION_PLACEHOLDER)
        else:
            kept.append(sentence)
    return " ".join(kept), hits


def _quarantine_injection(text: str, findings: Counter) -> str:
    """Each paragraph is read twice: flattened to one line, because an attacker can split a phrase
    across line breaks ("ignore / all previous / instructions") to slip past a line-by-line check, and
    line by line, because chat-template tags like "system:" only count at the start of a line.
    A paragraph with nothing in it is returned untouched, line breaks and all."""
    parts = re.split(r"(\n\s*\n)", text)
    for i, para in enumerate(parts):
        if not para.strip() or i % 2:  # blank, or a separator between paragraphs
            continue
        flat_findings, line_findings = Counter(), Counter()
        flat, flat_hits = _scrub(" ".join(para.split()), flat_findings)
        scrubbed = [_scrub(line, line_findings) for line in para.split("\n")]
        line_hits = sum(hits for _, hits in scrubbed)
        if not flat_hits and not line_hits:
            continue
        # Prefer the line-by-line result: it removes only the offending sentence and keeps the other
        # lines of a list. Fall back to the flattened one when it found more (a phrase split by line breaks).
        if line_hits >= flat_hits:
            parts[i], found = "\n".join(text for text, _ in scrubbed), line_findings
        else:
            parts[i], found = flat, flat_findings
        findings.update(found)
    return "".join(parts)


def scan_text(text: str, mask_personal: bool = True) -> Scan:
    """Screen untrusted document text. Returns cleaned text plus counts of what was found.

    `mask_personal=False` keeps emails and phone numbers: for a client's own business documents, where
    "orders@shop.example" is the answer, not a leak. Injection, hidden text and secrets are always handled."""
    findings: Counter = Counter()
    text = unicodedata.normalize("NFKC", text)  # fullwidth and compatibility tricks
    stripped = _HIDDEN.sub("", text)
    if hidden := len(text) - len(stripped):
        findings[("hidden", "hidden characters")] += hidden
    text = _quarantine_injection(stripped, findings)
    text = mask_sensitive(text, findings, personal=mask_personal)
    return Scan(text=text, findings=findings)


def mask_for_log(text: str) -> str:
    """For the audit log: hide secrets and personal data, leave everything else as it was."""
    return mask_sensitive(text, Counter())
