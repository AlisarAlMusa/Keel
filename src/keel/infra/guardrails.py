"""In-process platform guardrails — hardcoded, not weakenable by tenant config.

Two rail pairs:

INPUT (call before handing the message to the agent/router):
  - Injection refusal: heuristic pattern match on common prompt-injection signatures.
  - Cross-tenant probe refusal: message explicitly references a different tenant_id.

OUTPUT (call at every egress: response body, log fields, trace attributes):
  - PII redaction: email addresses, API key-shaped strings, national IDs (SSN).
    Returns the redacted string. Never raises.

Platform invariant: these rails run on EVERY message/response regardless of tenant
config. A tenant cannot opt out, override patterns, or lower thresholds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from keel.logging import get_logger

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Input rail — injection detection
# ---------------------------------------------------------------------------

# Ordered from most to least specific.
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(  # noqa: E501
        r"ignore\s+(previous|all|any|above|your)\s+(instructions?|prompt|system|guidelines?|rules?)",
        re.I,
    ),
    re.compile(  # noqa: E501
        r"(disregard|forget|override)\s+(?:your|all|the|previous|above|any)(?:\s+(?:previous|all|the|above|any))?\s+(instructions?|prompt|constraints?|guidelines?|rules?)",
        re.I,
    ),
    re.compile(r"you\s+are\s+now\s+(a|an)\s+\w+", re.I),
    re.compile(r"act\s+as\s+(?:if\s+you\s+(?:are|were)\s+)?(?:a|an)\s+\w+", re.I),
    re.compile(r"pretend\s+(you\s+(are|were)|that\s+you)", re.I),
    re.compile(r"new\s+(task|instructions?|prompt|role)\s*:", re.I),
    re.compile(r"system\s+(prompt|message|instructions?)\s*:", re.I),
    re.compile(r"reveal\s+(your|the)\s+(system|instructions?|prompt|context)", re.I),
    re.compile(r"what\s+are\s+your\s+(instructions?|system\s+prompt|guidelines?)", re.I),
    re.compile(r"print\s+(your|the)\s+(system|instructions?|prompt)", re.I),
    re.compile(
        r"(repeat|output|echo)\s+(your|the)\s+(system\s+(?:prompt|instructions?)|instructions?)",
        re.I,
    ),
    # Delimiter-style jailbreaks
    re.compile(r"\[INST\]", re.I),
    re.compile(r"<\|system\|>", re.I),
    re.compile(r"###\s*instruction", re.I),
    re.compile(r"---+\s*\n\s*#\s*(instructions?|system|task)", re.I | re.M),
    re.compile(r"<system>", re.I),
    re.compile(r"\bDAN\b"),  # "Do Anything Now" jailbreak
    re.compile(r"jailbreak", re.I),
]

# Cross-tenant probe: message explicitly names a different tenant_id UUID.
# Pattern: a UUID-shaped string (to avoid false positives from generic text).
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)

# Phrases that signal an *intentional* cross-tenant data access attempt.
_CROSS_TENANT_PHRASES: list[re.Pattern[str]] = [
    re.compile(
        r"(access|query|get|fetch|retrieve|read)\s+(tenant|university|school|org)\s*[:\-]?\s*",
        re.I,
    ),
    re.compile(r"(switch|change|set)\s+tenant", re.I),
    re.compile(r"tenant[_\s]?id\s*[=:]\s*", re.I),
    re.compile(
        r"for\s+(?:an?\s+)?(another|other|different)\s+(tenant|university|school|org)",
        re.I,
    ),
]


@dataclass
class GuardrailDecision:
    safe: bool
    reason: str | None = None  # populated only when safe=False


def check_input(
    message: str,
    *,
    tenant_id: str,
    other_tenant_names: list[tuple[str, str]] | None = None,
) -> GuardrailDecision:
    """Run input rails on an inbound message.

    Args:
        message:            The raw student message (pre-any LLM call).
        tenant_id:          The authenticated tenant this message belongs to.
        other_tenant_names: List of (slug, name) tuples for every OTHER tenant.
                            When provided, any mention of another institution's
                            slug or name triggers a cross-tenant refusal.

    Returns:
        GuardrailDecision(safe=True) if the message passes both rails.
        GuardrailDecision(safe=False, reason=...) if it fails either rail.
    """
    msg_lower = message.lower()

    # --- Injection rail ---
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(message):
            _log.warning(
                "guardrail.injection_refused",
                pattern=pattern.pattern,
                tenant_id=tenant_id,
                message_snippet=message[:80],
            )
            return GuardrailDecision(safe=False, reason="injection_attempt")

    # --- Cross-tenant probe rail (UUID-based) ---
    uuids_in_message = _UUID_RE.findall(message)
    for uid in uuids_in_message:
        if uid.lower() != tenant_id.lower():
            for phrase_pat in _CROSS_TENANT_PHRASES:
                if phrase_pat.search(message):
                    _log.warning(
                        "guardrail.cross_tenant_refused",
                        foreign_id=uid,
                        tenant_id=tenant_id,
                        message_snippet=message[:80],
                    )
                    return GuardrailDecision(safe=False, reason="cross_tenant_probe")

    # --- Cross-tenant probe rail (name/slug-based) ---
    if other_tenant_names:
        for slug, name in other_tenant_names:
            # Check slug (e.g. "summit") and display name (e.g. "Summit College")
            # Use word-boundary awareness: "summit" should not match "summiting"
            slug_hit = bool(
                slug
                and len(slug) > 2
                and re.search(r"\b" + re.escape(slug.lower()) + r"\b", msg_lower)
            )
            name_hit = bool(name and len(name) > 2 and name.lower() in msg_lower)
            if slug_hit or name_hit:
                matched = slug if slug_hit else name
                _log.warning(
                    "guardrail.cross_tenant_name_refused",
                    matched=matched,
                    tenant_id=tenant_id,
                    message_snippet=message[:80],
                )
                return GuardrailDecision(safe=False, reason="cross_tenant_probe")

    return GuardrailDecision(safe=True)


# ---------------------------------------------------------------------------
# Output rail — PII redaction
# ---------------------------------------------------------------------------

# Ordered: most specific first.
_REDACT_RULES: list[tuple[re.Pattern[str], str]] = [
    # Email addresses
    (re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+", re.I), "[EMAIL]"),
    # Common vendor API key prefixes + long opaque tokens
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "[REDACTED_KEY]"),  # OpenAI-style
    (re.compile(r"\bpk-[A-Za-z0-9]{20,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{30,}\b"), "[REDACTED_KEY]"),  # Google API key
    (re.compile(r"\bcohere-[A-Za-z0-9_\-]{20,}\b", re.I), "[REDACTED_KEY]"),
    # Generic secret-shaped strings: 32+ hex chars or 40+ base64-alnum
    (re.compile(r"\b[0-9a-f]{32,}\b", re.I), "[REDACTED_KEY]"),
    (re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"), "[REDACTED_KEY]"),
    # SSN (with or without dashes)
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_ID]"),
    (re.compile(r"\b\d{9}\b"), "[REDACTED_ID]"),
]


def redact(text: str) -> str:
    """Apply PII redaction to any outbound string (response, log field, trace attribute).

    Replaces emails, API-key-shaped strings, and national IDs with opaque placeholders.
    Never raises — on any error, returns the original text and logs a warning.
    """
    try:
        out = text
        for pattern, placeholder in _REDACT_RULES:
            out = pattern.sub(placeholder, out)
        return out
    except Exception as exc:
        _log.warning("guardrail.redact_failed", error=str(exc))
        return text
