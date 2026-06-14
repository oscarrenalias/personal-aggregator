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

    top_grade is the maximum importance_score across all member articles (as
    supplied by the caller).  It is returned unchanged so callers can persist
    both fields together in a single assignment.

    A thread is surfaced (``surfaced=True``) if ANY of the three OR-conditions
    holds — high individual grade, multi-source breadth, or volume of coverage:
    - top_grade meets the importance threshold (min_grade)
    - distinct source count meets the critical-mass floor (min_sources)
    - total member count meets the critical-mass floor (min_members)

    Passing ``top_grade=None`` (thread has no ranked members yet) forces
    ``surfaced=False`` unless the cluster-shape conditions are met.
    """
    surfaced = (
        (top_grade is not None and top_grade >= min_grade)
        or distinct_sources >= min_sources
        or member_count >= min_members
    )
    return surfaced, top_grade
