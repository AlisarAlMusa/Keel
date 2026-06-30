"""Repository layer — tenant-scoped DB access (defense-in-depth layer 2).

Concrete repositories live in ``core.py``; the base class (tenant binding +
post-fetch tenant assertion) lives in ``base.py``.
"""

from keel.repositories.base import TenantScopedRepository
from keel.repositories.core import (
    ActionsRepository,
    LedgerRepository,
    actions,
    ledger,
)

__all__ = [
    "TenantScopedRepository",
    "LedgerRepository",
    "ActionsRepository",
    "ledger",
    "actions",
]
