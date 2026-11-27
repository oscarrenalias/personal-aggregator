#!/usr/bin/env python3
"""Entity merge effect simulation — §5.5 informational check.

For active threads updated in the last 48 h, reads entities from shadow arm
outputs in llm_shadow_results (not articles.entities), then computes the
composite merge-pair score using consolidate.py's weights and normalize_entity
plus existing topics and ts_rank FTS similarity. Prints how many pairs reach
score floors 0.15, 0.25, and 0.35.

Read-only: makes no DB writes.

Usage:
    uv run python scripts/entity_merge_effect.py
    uv run python scripts/entity_merge_effect.py --experiment rank-entities-v3-2026-12 --arm luna-low-v3
    uv run python scripts/entity_merge_effect.py --hours 48
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "packages" / "aggregator-common" / "src"))
sys.path.insert(0, str(_ROOT / "packages" / "aggregator-clusterer" / "src"))

from aggregator_common.env import load_env
load_env()

from sqlalchemy import func, select

from aggregator_common.db import get_session
from aggregator_common.models import Article, LlmShadowResult, Thread, ThreadMembership
from aggregator_common.text_norm import normalize_entity

# Weights mirror consolidate.py exactly
_ENTITY_W = 0.40
_TOPIC_W = 0.30
_FTS_W = 0.30

SCORE_FLOORS = [0.15, 0.25, 0.35]


def _to_set(value: object) -> frozenset:
    if value is None:
        return frozenset()
    if isinstance(value, list):
        return frozenset(str(v) for v in value if v is not None)
    if isinstance(value, dict):
        return frozenset(str(k) for k in value)
    return frozenset()


def _to_entity_set(value: object) -> frozenset:
    if value is None:
        return frozenset()
    if isinstance(value, list):
        result: set[str] = set()
        for v in value:
            if v is not None:
                normed = normalize_entity(str(v))
                if normed:
                    result.add(normed)
        return frozenset(result)
    return frozenset()


def _jaccard(a: frozenset, b: frozenset) -> float:
    union = len(a | b)
    return len(a & b) / union if union > 0 else 0.0


def run(experiment: str, arm: str, hours: int) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    with get_session() as session:
        # Load active threads updated within the window
        threads: list[Thread] = list(
            session.execute(
                select(Thread).where(
                    Thread.status == "active",
                    Thread.last_updated >= cutoff,
                )
            ).scalars().all()
        )

        if len(threads) < 2:
            print(f"Only {len(threads)} active thread(s) updated in the last {hours} h — not enough to compare pairs.")
            return

        thread_ids = [t.id for t in threads]
        print(f"Active threads updated in last {hours} h: {len(threads)}")

        # Get the 3 most recent members per thread
        rn_col = func.row_number().over(
            partition_by=ThreadMembership.thread_id,
            order_by=[ThreadMembership.assigned_at.desc(), Article.id.desc()],
        ).label("rn")

        ranked_sub = (
            select(
                ThreadMembership.thread_id,
                Article.id.label("article_id"),
                Article.topics,
                rn_col,
            )
            .join(Article, ThreadMembership.article_id == Article.id)
            .where(ThreadMembership.thread_id.in_(thread_ids))
            .subquery()
        )

        member_rows = session.execute(
            select(
                ranked_sub.c.thread_id,
                ranked_sub.c.article_id,
                ranked_sub.c.topics,
                ranked_sub.c.rn,
            ).where(ranked_sub.c.rn <= 3)
        ).all()

        # Map thread_id → list of article_ids (up to 3 most recent)
        thread_article_ids: dict[int, list[int]] = {}
        thread_topics: dict[int, frozenset] = {}
        for row in member_rows:
            thread_article_ids.setdefault(row.thread_id, []).append(row.article_id)
            if row.rn == 1:
                thread_topics[row.thread_id] = _to_set(row.topics)

        # Collect all article_ids we need shadow entities for
        all_article_ids: set[int] = set()
        for aids in thread_article_ids.values():
            all_article_ids.update(aids)

        # Fetch shadow arm entities from llm_shadow_results
        # ref_id is stored as text (str(article_id))
        ref_ids = [str(aid) for aid in all_article_ids]
        shadow_rows = session.execute(
            select(
                LlmShadowResult.ref_id,
                LlmShadowResult.output,
            ).where(
                LlmShadowResult.experiment == experiment,
                LlmShadowResult.arm == arm,
                LlmShadowResult.operation == "rank",
                LlmShadowResult.status == "ok",
                LlmShadowResult.ref_id.in_(ref_ids),
            )
        ).all()

        # article_id (int) → entity list from shadow output
        shadow_entities: dict[int, list] = {}
        for row in shadow_rows:
            art_id = int(row.ref_id)
            output = row.output or {}
            entities = output.get("entities") if isinstance(output, dict) else None
            shadow_entities[art_id] = entities or []

        total_shadowed = len(shadow_entities)
        total_articles = len(all_article_ids)
        print(f"Articles needed: {total_articles}, shadow results found: {total_shadowed}")
        if total_shadowed == 0:
            print(
                f"No shadow results for experiment={experiment!r} arm={arm!r} operation='rank'. "
                "Check that the experiment has run and the arm/experiment names are correct."
            )
            return

        # Build per-thread entity sets from shadow results (union of up to 3 recent members)
        thread_entities: dict[int, frozenset] = {}
        for tid, article_ids in thread_article_ids.items():
            entity_set: set[str] = set()
            for aid in article_ids:
                entity_set |= set(_to_entity_set(shadow_entities.get(aid, [])))
            thread_entities[tid] = frozenset(entity_set)

        # Batch FTS similarity for all active-thread pairs via Postgres self-join
        ta = Thread.__table__.alias("ta")
        tb = Thread.__table__.alias("tb")

        fts_rows = session.execute(
            select(
                ta.c.id.label("tid_a"),
                tb.c.id.label("tid_b"),
                func.ts_rank(
                    func.to_tsvector(
                        "english",
                        func.concat_ws(" ", ta.c.representative_title, ta.c.rolling_summary),
                    ),
                    func.plainto_tsquery(
                        "english",
                        func.concat_ws(" ", tb.c.representative_title, tb.c.rolling_summary),
                    ),
                ).label("fts_score"),
            ).where(
                ta.c.id < tb.c.id,
                ta.c.id.in_(thread_ids),
                tb.c.id.in_(thread_ids),
            )
        ).all()

        fts_scores: dict[tuple[int, int], float] = {
            (row.tid_a, row.tid_b): float(row.fts_score) for row in fts_rows
        }

        # Score all pairs
        total_pairs = 0
        counts_at_floor: dict[float, int] = {f: 0 for f in SCORE_FLOORS}

        for i, ta_t in enumerate(threads):
            for tb_t in threads[i + 1:]:
                total_pairs += 1
                lo, hi = (ta_t.id, tb_t.id) if ta_t.id < tb_t.id else (tb_t.id, ta_t.id)

                entity_overlap = _jaccard(
                    thread_entities.get(ta_t.id, frozenset()),
                    thread_entities.get(tb_t.id, frozenset()),
                )
                topic_overlap = _jaccard(
                    thread_topics.get(ta_t.id, frozenset()),
                    thread_topics.get(tb_t.id, frozenset()),
                )
                fts = min(fts_scores.get((lo, hi), 0.0), 1.0)

                composite = (
                    _ENTITY_W * entity_overlap
                    + _TOPIC_W * topic_overlap
                    + _FTS_W * fts
                )

                for floor in SCORE_FLOORS:
                    if composite >= floor:
                        counts_at_floor[floor] += 1

    # Print results
    print()
    print(f"Experiment: {experiment}  Arm: {arm}")
    print(f"Window: last {hours} h   Threads: {len(threads)}   Pairs: {total_pairs}")
    print()
    print(f"{'Floor':<10} {'Pairs ≥ floor':<18} {'% of all pairs'}")
    print("-" * 44)
    for floor in SCORE_FLOORS:
        n = counts_at_floor[floor]
        pct = 100.0 * n / total_pairs if total_pairs > 0 else 0.0
        print(f"{floor:<10.2f} {n:<18} {pct:.1f}%")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--experiment",
        default="rank-entities-v3-2026-12",
        help="Experiment name in llm_shadow_results (default: rank-entities-v3-2026-12)",
    )
    parser.add_argument(
        "--arm",
        default="luna-low-v3",
        help="Shadow arm name to read entities from (default: luna-low-v3)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=48,
        help="Window in hours: threads updated within this many hours (default: 48)",
    )
    args = parser.parse_args()
    run(args.experiment, args.arm, args.hours)


if __name__ == "__main__":
    main()
