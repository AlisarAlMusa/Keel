"""DAG loader for the prerequisite graph.

Builds a directed graph from prerequisite edges, detects cycles at load time
via Kahn's algorithm, and exposes a topological sort and reachability helpers
used by the degree audit and verifier.

Source of truth: specs/004-phase-1-engine/spec.md §5, plan.md §3.
"""

from __future__ import annotations

from collections import defaultdict, deque

from keel.domain.exceptions import CyclicCatalogError
from keel.domain.models import Prerequisite


class PrereqGraph:
    """Immutable prerequisite DAG for one tenant's catalog.

    Build once at startup (or per-request if catalog fits in memory).
    All methods are pure — no mutation after construction.
    """

    def __init__(
        self,
        prerequisites: list[Prerequisite],
        all_course_codes: frozenset[str],
    ) -> None:
        # adjacency: course_code -> set of its direct prerequisites
        self._prereqs: dict[str, set[str]] = defaultdict(set)
        # reverse: prereq_code -> set of courses that require it
        self._dependents: dict[str, set[str]] = defaultdict(set)

        for edge in prerequisites:
            self._prereqs[edge.course_code].add(edge.requires_code)
            self._dependents[edge.requires_code].add(edge.course_code)

        self._all_codes: frozenset[str] = all_course_codes
        self._topo_order: list[str] = self._kahn_sort()

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def topo_order(self) -> list[str]:
        """All course codes in topological order (no deps before dependents)."""
        return list(self._topo_order)

    def direct_prereqs(self, course_code: str) -> frozenset[str]:
        """Direct prerequisites for a single course."""
        return frozenset(self._prereqs.get(course_code, set()))

    def all_prereqs(self, course_code: str) -> frozenset[str]:
        """Transitive closure of prerequisites (all ancestors)."""
        visited: set[str] = set()
        stack = list(self._prereqs.get(course_code, set()))
        while stack:
            code = stack.pop()
            if code in visited:
                continue
            visited.add(code)
            stack.extend(self._prereqs.get(code, set()))
        return frozenset(visited)

    def prereqs_satisfied(
        self,
        course_code: str,
        completed_codes: frozenset[str],
    ) -> bool:
        """True iff every direct prerequisite of course_code is in completed_codes."""
        return self.direct_prereqs(course_code) <= completed_codes

    def unlocks_count(self, course_code: str) -> int:
        """How many courses become directly reachable after completing this one.

        Used by the greedy planner to prioritise courses that unlock the most
        downstream work.
        """
        return len(self._dependents.get(course_code, set()))

    # ── internal ──────────────────────────────────────────────────────────────

    def _kahn_sort(self) -> list[str]:
        """Kahn's algorithm: topological sort + cycle detection.

        Raises CyclicCatalogError if any cycle exists. All known course codes
        (including those with no edges) are included in the output.
        """
        in_degree: dict[str, int] = {code: 0 for code in self._all_codes}
        for course, prereqs in self._prereqs.items():
            # ensure every node appears even if it has no in-edges
            if course not in in_degree:
                in_degree[course] = 0
            for p in prereqs:
                if p not in in_degree:
                    in_degree[p] = 0
                in_degree[course] += 1  # course depends on p → course has one more in-edge

        queue: deque[str] = deque(
            sorted(code for code, deg in in_degree.items() if deg == 0)
        )
        order: list[str] = []

        while queue:
            node = queue.popleft()
            order.append(node)
            for dependent in sorted(self._dependents.get(node, set())):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        remaining = [c for c in in_degree if in_degree[c] > 0]
        if remaining:
            raise CyclicCatalogError(
                f"Prerequisite cycle detected among courses: {sorted(remaining)}"
            )

        return order
