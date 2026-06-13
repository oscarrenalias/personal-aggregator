"""Band boundary constants and label helper for the 5-level importance grade.

Articles are scored 0–100 by the summarize-rank service.  The scale is divided
into five named bands that give the LLM concrete anchors for calibration:

  noise        0–20   — off-topic or very low-value content
  on-topic    21–45   — relevant but not compelling enough to act on
  good-to-know 46–65  — useful background; worth reading if time permits
  important   66–85   — clear signal; user will likely want to see this
  must-know   86–100  — high-priority; user should not miss this

The bands are intentionally wide so that small scoring variations do not cause
articles to flip between labels.  Thread surfacing uses the top-grade of member
articles (the max importance_score across all members) against the
``CLUSTERER_SURFACE_MIN_GRADE`` threshold, which defaults to 66 (the bottom
of the ``important`` band).
"""

NOISE_MAX = 20
ON_TOPIC_MAX = 45
GOOD_TO_KNOW_MAX = 65
IMPORTANT_MAX = 85
MUST_KNOW_MAX = 100


def band_label(score: int) -> str:
    """Return the grade label for a 0–100 importance_score."""
    if score <= NOISE_MAX:
        return "noise"
    if score <= ON_TOPIC_MAX:
        return "on-topic"
    if score <= GOOD_TO_KNOW_MAX:
        return "good-to-know"
    if score <= IMPORTANT_MAX:
        return "important"
    return "must-know"
