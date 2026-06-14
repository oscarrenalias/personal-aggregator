"""Tests for aggregator_clusterer.scoring — compute_surfaced."""
from __future__ import annotations

from aggregator_clusterer.scoring import compute_surfaced

_MIN_GRADE = 66
_MIN_SOURCES = 2
_MIN_MEMBERS = 3


class TestComputeSurfaced:
    def _call(self, top_grade, distinct_sources, member_count):
        return compute_surfaced(
            top_grade,
            distinct_sources,
            member_count,
            min_grade=_MIN_GRADE,
            min_sources=_MIN_SOURCES,
            min_members=_MIN_MEMBERS,
        )

    # --- top_grade condition alone ---
    def test_top_grade_at_min_surfaces(self):
        surfaced, tg = self._call(66, 0, 0)
        assert surfaced is True
        assert tg == 66

    def test_top_grade_above_min_surfaces(self):
        surfaced, tg = self._call(80, 0, 0)
        assert surfaced is True
        assert tg == 80

    def test_top_grade_one_below_min_does_not_surface(self):
        surfaced, tg = self._call(65, 0, 0)
        assert surfaced is False
        assert tg == 65

    def test_top_grade_none_does_not_trigger_grade_condition(self):
        surfaced, tg = self._call(None, 0, 0)
        assert surfaced is False
        assert tg is None

    # --- distinct_sources condition alone ---
    def test_sources_at_min_surfaces(self):
        surfaced, _ = self._call(None, 2, 0)
        assert surfaced is True

    def test_sources_above_min_surfaces(self):
        surfaced, _ = self._call(None, 5, 0)
        assert surfaced is True

    def test_sources_one_below_min_does_not_surface(self):
        surfaced, _ = self._call(None, 1, 0)
        assert surfaced is False

    # --- member_count condition alone ---
    def test_members_at_min_surfaces(self):
        surfaced, _ = self._call(None, 0, 3)
        assert surfaced is True

    def test_members_above_min_surfaces(self):
        surfaced, _ = self._call(None, 0, 10)
        assert surfaced is True

    def test_members_one_below_min_does_not_surface(self):
        surfaced, _ = self._call(None, 0, 2)
        assert surfaced is False

    # --- combined conditions ---
    def test_all_three_conditions_met_surfaces(self):
        surfaced, tg = self._call(90, 3, 5)
        assert surfaced is True
        assert tg == 90

    def test_all_conditions_below_threshold_does_not_surface(self):
        surfaced, _ = self._call(None, 0, 0)
        assert surfaced is False

    # --- lone on-topic article does not surface ---
    def test_lone_on_topic_article_does_not_surface(self):
        # score=33 (on-topic, below min_grade=66), sources=1, members=1
        surfaced, tg = self._call(33, 1, 1)
        assert surfaced is False
        assert tg == 33

    def test_single_source_high_grade_does_not_surface_on_grade_alone_when_below_min(self):
        # grade=60 < 66 and only 1 source, 1 member — none of the three conditions met
        surfaced, _ = self._call(60, 1, 1)
        assert surfaced is False

    # --- return value correctness ---
    def test_top_grade_preserved_in_return_when_surfaced(self):
        _, tg = self._call(75, 0, 0)
        assert tg == 75

    def test_top_grade_preserved_in_return_when_not_surfaced(self):
        _, tg = self._call(50, 1, 1)
        assert tg == 50

    def test_none_top_grade_preserved_when_sources_trigger_surface(self):
        _, tg = self._call(None, 3, 0)
        assert tg is None
