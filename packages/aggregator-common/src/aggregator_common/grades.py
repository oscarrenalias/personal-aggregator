"""Band boundary constants and label helper for the 5-level importance grade."""

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
