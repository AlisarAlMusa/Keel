"""[GATE] PII redaction — fake secrets in chat must never appear unredacted in output.

Spec §5 gate: "PII redaction: a fake key pasted into chat must never appear
unredacted in logs or traces."

Tests verify that redact() eliminates all of: email addresses, API-key-shaped
strings (vendor-prefixed and generic hex/base64), and national ID numbers (SSN).
"""

from __future__ import annotations

import pytest

from keel.infra.guardrails import redact

# ---------------------------------------------------------------------------
# Fixtures: strings that must be redacted
# ---------------------------------------------------------------------------

EMAIL_CASES: list[tuple[str, str]] = [
    ("Contact me at alice@university.edu please", "alice@university.edu"),
    ("admin+team@keel.io should not appear", "admin+team@keel.io"),
    ("Send to john.doe@corp.co.uk for details", "john.doe@corp.co.uk"),
]

API_KEY_CASES: list[tuple[str, str]] = [
    # OpenAI-style
    ("My key is sk-abcdefghijklmnopqrstuvwxyz123456", "sk-abcdefghijklmnopqrstuvwxyz123456"),
    # Google API key
    ("AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q7R", "AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q7R"),
    # Generic 32-hex (e.g. a Cohere key fragment)
    ("token: abcdef1234567890abcdef1234567890", "abcdef1234567890abcdef1234567890"),
    # Generic 40+ base64-alnum (JWT fragment style)
    (
        "Bearer ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefgh",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefgh",
    ),
    # pk- prefix style
    ("pk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "pk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
]

NATIONAL_ID_CASES: list[tuple[str, str]] = [
    # SSN with dashes
    ("My SSN is 123-45-6789", "123-45-6789"),
    # SSN without dashes
    ("SSN: 987654321 for verification", "987654321"),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,pii", EMAIL_CASES, ids=[f"email_{i}" for i in range(len(EMAIL_CASES))]
)
def test_email_redacted(text: str, pii: str) -> None:
    result = redact(text)
    assert pii not in result, f"Email not redacted.\nInput: {text!r}\nOutput: {result!r}"
    assert "[EMAIL]" in result


@pytest.mark.parametrize(
    "text,pii", API_KEY_CASES, ids=[f"key_{i}" for i in range(len(API_KEY_CASES))]
)
def test_api_key_redacted(text: str, pii: str) -> None:
    result = redact(text)
    assert pii not in result, f"API key not redacted.\nInput: {text!r}\nOutput: {result!r}"
    assert "[REDACTED_KEY]" in result


@pytest.mark.parametrize(
    "text,pii", NATIONAL_ID_CASES, ids=[f"id_{i}" for i in range(len(NATIONAL_ID_CASES))]
)
def test_national_id_redacted(text: str, pii: str) -> None:
    result = redact(text)
    assert pii not in result, f"National ID not redacted.\nInput: {text!r}\nOutput: {result!r}"
    assert "[REDACTED_ID]" in result


def test_clean_text_unchanged() -> None:
    """Text with no PII should pass through unmodified."""
    clean = "What are the prerequisites for CS301 next semester?"
    assert redact(clean) == clean


def test_redact_never_raises() -> None:
    """redact() must never raise — it's called at every egress point."""
    # Pass weird types coerced to str
    result = redact("")
    assert result == ""
    result = redact("normal text")
    assert result == "normal text"


def test_multiple_pii_in_one_string() -> None:
    """Multiple PII items in one string must all be redacted."""
    text = (
        "Email alice@test.edu, key sk-abc123def456ghi789jkl012mno345, "
        "SSN 111-22-3333"
    )
    result = redact(text)
    assert "alice@test.edu" not in result
    assert "sk-abc123def456ghi789jkl012mno345" not in result
    assert "111-22-3333" not in result
    assert "[EMAIL]" in result
    assert "[REDACTED_KEY]" in result
    assert "[REDACTED_ID]" in result
