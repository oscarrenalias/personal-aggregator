-- Read-only export query: one JSON object per merge pair.
-- Run via: .claude/skills/prod-db/prod-db.sh psql -qAt -f - < evals/clusterer_merge/export.sql
-- exported_at is added by build_dataset.py; all other dataset fields are produced here.
-- Source: llm_calls WHERE service='clusterer' AND operation='merge'.
-- Retention: JANITOR_LLM_TELEMETRY_RETENTION_DAYS (default 30 days), so an export covers
--   approximately the last month of merge decisions.
-- shadow_verdicts is always '{}' from the gpt-4.1 cutover onward; the shadow experiment
--   table is no longer used as the pair source.
WITH pairs AS (
    SELECT
        ref_id,
        MIN(created_at)                    AS first_seen,
        split_part(ref_id, ':', 1)::bigint AS id_a,
        split_part(ref_id, ':', 2)::bigint AS id_b
    FROM llm_calls
    WHERE service   = 'clusterer'
      AND operation = 'merge'
      AND ref_id IS NOT NULL
    GROUP BY ref_id
),
valid_pairs AS (
    SELECT
        p.ref_id,
        p.first_seen,
        p.id_a,
        p.id_b,
        ta.representative_title AS title_a,
        ta.rolling_summary      AS summary_a,
        tb.representative_title AS title_b,
        tb.rolling_summary      AS summary_b
    FROM pairs p
    JOIN threads ta ON ta.id = p.id_a
    JOIN threads tb ON tb.id = p.id_b
),
member_counts AS (
    SELECT thread_id, COUNT(*) AS member_count
    FROM thread_memberships
    GROUP BY thread_id
),
ranked_members AS (
    SELECT
        tm.thread_id,
        a.id                                      AS article_id,
        COALESCE(a.clean_title, a.feed_title)     AS title,
        s.name                                    AS source,
        tm.assigned_at,
        a.summary,
        ROW_NUMBER() OVER (PARTITION BY tm.thread_id ORDER BY tm.assigned_at ASC)  AS rn_asc,
        ROW_NUMBER() OVER (PARTITION BY tm.thread_id ORDER BY tm.assigned_at DESC) AS rn_desc
    FROM thread_memberships tm
    JOIN articles a ON a.id = tm.article_id
    JOIN sources  s ON s.id = a.source_id
),
selected_members AS (
    SELECT DISTINCT
        thread_id, article_id, title, source, assigned_at, summary
    FROM ranked_members
    WHERE rn_asc = 1 OR rn_desc <= 2
),
members_agg AS (
    SELECT
        sm.thread_id,
        json_agg(
            json_build_object(
                'title',       sm.title,
                'source',      sm.source,
                'assigned_at', sm.assigned_at,
                'summary',     sm.summary
            )
            ORDER BY sm.assigned_at
        ) AS recent_articles
    FROM selected_members sm
    GROUP BY sm.thread_id
)
SELECT
    json_build_object(
        'pair_id',    vp.ref_id,
        'first_seen', vp.first_seen,
        'thread_a', json_build_object(
            'id',              vp.id_a,
            'title',           vp.title_a,
            'summary',         vp.summary_a,
            'member_count',    COALESCE(mca.member_count, 0),
            'recent_articles', COALESCE(maa.recent_articles, '[]'::json)
        ),
        'thread_b', json_build_object(
            'id',              vp.id_b,
            'title',           vp.title_b,
            'summary',         vp.summary_b,
            'member_count',    COALESCE(mcb.member_count, 0),
            'recent_articles', COALESCE(mab.recent_articles, '[]'::json)
        ),
        'shadow_verdicts', '{}'::jsonb
    )
FROM valid_pairs vp
LEFT JOIN member_counts       mca ON mca.thread_id = vp.id_a
LEFT JOIN member_counts       mcb ON mcb.thread_id = vp.id_b
LEFT JOIN members_agg         maa ON maa.thread_id = vp.id_a
LEFT JOIN members_agg         mab ON mab.thread_id = vp.id_b
ORDER BY vp.ref_id;
