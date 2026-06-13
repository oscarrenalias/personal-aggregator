"""Tests for aggregator_common.grades — band_label boundary and mid-band values."""
from __future__ import annotations

from aggregator_common.grades import band_label


class TestBandLabel:
    # --- noise band [0, 20] ---
    def test_score_zero_is_noise(self):
        assert band_label(0) == "noise"

    def test_score_twenty_is_noise(self):
        assert band_label(20) == "noise"

    def test_score_ten_is_noise(self):
        assert band_label(10) == "noise"

    # --- on-topic band [21, 45] ---
    def test_score_21_is_on_topic(self):
        assert band_label(21) == "on-topic"

    def test_score_45_is_on_topic(self):
        assert band_label(45) == "on-topic"

    def test_score_33_is_on_topic(self):
        assert band_label(33) == "on-topic"

    # --- good-to-know band [46, 65] ---
    def test_score_46_is_good_to_know(self):
        assert band_label(46) == "good-to-know"

    def test_score_65_is_good_to_know(self):
        assert band_label(65) == "good-to-know"

    def test_score_55_is_good_to_know(self):
        assert band_label(55) == "good-to-know"

    # --- important band [66, 85] ---
    def test_score_66_is_important(self):
        assert band_label(66) == "important"

    def test_score_85_is_important(self):
        assert band_label(85) == "important"

    def test_score_75_is_important(self):
        assert band_label(75) == "important"

    # --- must-know band [86, 100] ---
    def test_score_86_is_must_know(self):
        assert band_label(86) == "must-know"

    def test_score_100_is_must_know(self):
        assert band_label(100) == "must-know"

    def test_score_93_is_must_know(self):
        assert band_label(93) == "must-know"
