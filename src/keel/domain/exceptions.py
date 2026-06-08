"""Typed domain exceptions (ENGINEERING_RULES §17).

The domain raises these; services translate them to API errors at the boundary.
Never leak stack traces or secrets to clients. ``domain/`` stays free of
framework/IO imports — these are plain exceptions.
"""

from __future__ import annotations


class KeelError(Exception):
    """Base class for all Keel domain errors."""


class NotFoundError(KeelError):
    """A requested entity does not exist (or is not visible to this tenant)."""


class PermissionDeniedError(KeelError):
    """The caller is authenticated but not allowed to perform the action."""


class ToolFailureError(KeelError):
    """An agent tool failed; surfaced as a structured result, not a crash."""


class ExternalServiceError(KeelError):
    """A downstream dependency (DB, model-server, Vault, etc.) failed."""


class ConfigurationError(KeelError):
    """Required configuration or a required secret is missing/invalid."""
