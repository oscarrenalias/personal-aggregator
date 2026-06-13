from __future__ import annotations


def compute_surfaced(
    top_grade: int | None,
    distinct_sources: int,
    member_count: int,
    *,
    min_grade: int,
    min_sources: int,
    min_members: int,
) -> tuple[bool, int | None]:
    """Return (surfaced, top_grade) for a thread based on member grades and cluster shape.

    A thread surfaces if ANY of the three conditions holds:
    - top_grade meets the importance threshold (min_grade)
    - distinct source count meets the critical-mass floor (min_sources)
    - total member count meets the critical-mass floor (min_members)
    """
    surfaced = (
        (top_grade is not None and top_grade >= min_grade)
        or distinct_sources >= min_sources
        or member_count >= min_members
    )
    return surfaced, top_grade
