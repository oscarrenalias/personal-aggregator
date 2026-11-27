-- Read-only export query: one JSON object per article for summarize_rank evaluation.
-- Run via: .claude/skills/prod-db/prod-db.sh psql -qAt -f - < evals/summarize_rank/export.sql
-- exported_at is added by build_dataset.py; all other dataset fields are produced here.
-- Selects N (default 150) recent status='ready' articles with importance_score not null,
-- stratified so ~1/3 have importance_score >= 60 (threshold band 56-76 well represented).
-- Includes the interest_profile text (singleton row) and enabled categories (name, description).
WITH profile AS (
    SELECT text AS interest_profile_text
    FROM interest_profile
    LIMIT 1
),
categories_agg AS (
    SELECT json_agg(
        json_build_object('name', name, 'description', description)
        ORDER BY name
    ) AS categories
    FROM categories
    WHERE enabled = true
),
high_score AS (
    SELECT
        a.id,
        COALESCE(a.clean_title, a.feed_title)   AS title,
        s.name                                   AS source_name,
        a.clean_title,
        a.feed_title,
        COALESCE(a.clean_text, a.excerpt, a.feed_summary, '') AS content_text,
        a.excerpt,
        a.feed_summary,
        a.importance_score
    FROM articles a
    JOIN sources s ON s.id = a.source_id
    WHERE a.status = 'ready'
      AND a.importance_score IS NOT NULL
      AND a.importance_score >= 60
    ORDER BY a.published_at DESC NULLS LAST, a.id DESC
    LIMIT 50
),
low_score AS (
    SELECT
        a.id,
        COALESCE(a.clean_title, a.feed_title)   AS title,
        s.name                                   AS source_name,
        a.clean_title,
        a.feed_title,
        COALESCE(a.clean_text, a.excerpt, a.feed_summary, '') AS content_text,
        a.excerpt,
        a.feed_summary,
        a.importance_score
    FROM articles a
    JOIN sources s ON s.id = a.source_id
    WHERE a.status = 'ready'
      AND a.importance_score IS NOT NULL
      AND a.importance_score < 60
    ORDER BY a.published_at DESC NULLS LAST, a.id DESC
    LIMIT 100
),
combined AS (
    SELECT * FROM high_score
    UNION ALL
    SELECT * FROM low_score
)
SELECT json_build_object(
    'article_id',          c.id,
    'source_name',         c.source_name,
    'clean_title',         c.clean_title,
    'feed_title',          c.feed_title,
    'title',               c.title,
    'content_text',        c.content_text,
    'excerpt',             c.excerpt,
    'feed_summary',        c.feed_summary,
    'production_score',    c.importance_score,
    'interest_profile_text', COALESCE(p.interest_profile_text, ''),
    'categories',          COALESCE(ca.categories, '[]'::json)
)
FROM combined c
CROSS JOIN profile p
CROSS JOIN categories_agg ca
ORDER BY c.id;
