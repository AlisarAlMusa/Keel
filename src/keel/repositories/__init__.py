"""Repository layer — tenant-scoped DB access (defense-in-depth layer 2).

Concrete repositories live in ``core.py``; the base class (tenant binding +
post-fetch tenant assertion) lives in ``base.py``.
"""

from keel.repositories.base import TenantScopedRepository
from keel.repositories.core import (
    ActionsRepository,
    LedgerRepository,
)
from keel.repositories.programs import ProgramRepository
from keel.repositories.sections import SectionRepository
from keel.repositories.students import StudentRepository
from keel.repositories.waitlist import WaitlistRepository

__all__ = [
    "TenantScopedRepository",
    "LedgerRepository",
    "ActionsRepository",
    "StudentRepository",
    "ProgramRepository",
    "SectionRepository",
    "WaitlistRepository",
]
