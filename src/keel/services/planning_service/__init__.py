"""Planning-service use cases, split by concept (course vs. graduation planning).

Re-exports every use-case function so callers keep using ``planning_service.<fn>``
regardless of which submodule a use case lives in.
"""

from keel.services.planning_service.course_planning import (
    propose_plan,
    propose_sections,
    simulate_whatif,
)
from keel.services.planning_service.grad_planning import (
    delete_grad_plan,
    load_grad_plan,
    plan_graduation,
    swap_grad_plan_course,
)

__all__ = [
    "propose_plan",
    "propose_sections",
    "simulate_whatif",
    "plan_graduation",
    "load_grad_plan",
    "delete_grad_plan",
    "swap_grad_plan_course",
]
