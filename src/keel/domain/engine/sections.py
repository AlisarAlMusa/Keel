"""Section search — open, conflict-free section combinations.

Given a set of courses to schedule and all available sections, returns every
combination where every section is open and no two sections have a time
conflict.

Reuses the section-scope verifier checks for correctness parity — the same
overlap logic used during plan validation.

Source of truth: specs/004-phase-1-engine/spec.md §4.5, §5; plan.md §3.
"""

from __future__ import annotations

from itertools import product

from keel.domain.models import Section


def _slots_conflict(a: Section, b: Section) -> bool:
    """True if any meeting slot of section a overlaps any meeting slot of section b."""
    for slot_a in a.slots:
        for slot_b in b.slots:
            if slot_a.overlaps(slot_b):
                return True
    return False


def find_conflict_free_combinations(
    course_sections: dict[str, list[Section]],
) -> list[dict[str, Section]]:
    """Return all open, conflict-free section combinations for a set of courses.

    Parameters
    ----------
    course_sections:
        Map of course_code → list of candidate sections for that course.
        Only open sections (enrolled < capacity) should be passed in; this
        function also filters for openness as a safety net.

    Returns
    -------
    list[dict[str, Section]]
        Each element is a full combination: {course_code: chosen_section}.
        Empty list if no valid combination exists.
    """
    if not course_sections:
        return [{}]

    codes = sorted(course_sections.keys())  # stable ordering

    # Filter to open sections only
    open_sections: list[list[Section]] = []
    for code in codes:
        available = [s for s in course_sections[code] if s.is_open]
        if not available:
            return []  # no open section for this course → no valid combination
        open_sections.append(available)

    results: list[dict[str, Section]] = []

    for combo in product(*open_sections):
        # combo[i] is the chosen section for codes[i]
        conflict = False
        sections = list(combo)
        for i in range(len(sections)):
            for j in range(i + 1, len(sections)):
                if _slots_conflict(sections[i], sections[j]):
                    conflict = True
                    break
            if conflict:
                break
        if not conflict:
            results.append(dict(zip(codes, sections, strict=True)))

    return results
