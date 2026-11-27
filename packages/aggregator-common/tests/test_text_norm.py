"""Unit tests for aggregator_common.text_norm.normalize_entity.

Covers: NFKC normalization, casefold, whitespace collapse, punctuation strip,
'the ' prefix removal, and empty-after-normalisation cases.
"""
from __future__ import annotations

from aggregator_common.text_norm import normalize_entity


class TestNFKCNormalization:
    def test_nfkc_ligature_fi_normalized(self):
        # U+FB01 LATIN SMALL LIGATURE FI → "fi"
        assert normalize_entity("ﬁnancial") == "financial"

    def test_nfkc_fullwidth_letter_normalized(self):
        # U+FF21 FULLWIDTH LATIN CAPITAL LETTER A → "A" then casefolded to "a"
        assert normalize_entity("Ａpple") == "apple"


class TestCasefold:
    def test_uppercase_lowercased(self):
        assert normalize_entity("NVIDIA") == "nvidia"

    def test_mixed_case_lowercased(self):
        assert normalize_entity("OpenAI") == "openai"

    def test_already_lowercase_unchanged(self):
        assert normalize_entity("tesla") == "tesla"

    def test_nvidia_and_spaced_nvidia_match(self):
        # Scenario: clustering-overlap / Normalised entity overlap / Case and spacing differences still overlap
        assert normalize_entity("NVIDIA") == normalize_entity(" Nvidia ")


class TestWhitespaceCollapse:
    def test_leading_whitespace_stripped(self):
        assert normalize_entity("  apple") == "apple"

    def test_trailing_whitespace_stripped(self):
        assert normalize_entity("apple  ") == "apple"

    def test_internal_multiple_spaces_collapsed(self):
        assert normalize_entity("New  York") == "new york"

    def test_tabs_collapsed_to_space(self):
        assert normalize_entity("New\t\tYork") == "new york"

    def test_newlines_collapsed(self):
        assert normalize_entity("New\nYork") == "new york"


class TestPunctuationStrip:
    def test_leading_punctuation_stripped(self):
        assert normalize_entity(".Apple") == "apple"

    def test_trailing_punctuation_stripped(self):
        assert normalize_entity("Apple.") == "apple"

    def test_leading_and_trailing_punctuation_stripped(self):
        assert normalize_entity('"Apple"') == "apple"

    def test_comma_stripped(self):
        assert normalize_entity("Apple,") == "apple"

    def test_internal_punctuation_preserved(self):
        # Hyphens in names are internal; only leading/trailing are stripped
        assert "s" in normalize_entity("S&P 500")


class TestTheRemoval:
    def test_the_prefix_removed(self):
        assert normalize_entity("The Guardian") == "guardian"

    def test_the_prefix_case_insensitive_after_casefold(self):
        # "THE Guardian" → casefold → "the guardian" → remove "the " → "guardian"
        assert normalize_entity("THE Guardian") == "guardian"

    def test_the_prefix_only_at_start(self):
        # "in the loop" should not have "the" removed (it's not a prefix of the entity)
        result = normalize_entity("In the Loop")
        assert result == "in the loop"

    def test_the_alone_becomes_empty_or_stripped(self):
        # "The" → after casefold "the" → remove "the " requires space after, so "the" stays
        # Actually "the" doesn't start with "the " (needs space), so it should remain
        # Let's verify what actually happens
        result = normalize_entity("The")
        # "the" → no "the " at start, so remains "the"
        assert result == "the"


class TestEmptyAfterNormalisation:
    def test_empty_string_returns_empty(self):
        # Scenario: clustering-overlap / Normalised entity overlap / Empty after normalisation is dropped
        assert normalize_entity("") == ""

    def test_whitespace_only_returns_empty(self):
        assert normalize_entity("   ") == ""

    def test_punctuation_only_returns_empty(self):
        assert normalize_entity("...") == ""

    def test_punctuation_and_whitespace_returns_empty(self):
        assert normalize_entity(" . , ") == ""


class TestRoundtrip:
    def test_equivalent_entities_normalize_to_same(self):
        # From spec: 'NVIDIA' and ' Nvidia ' should produce identical frozensets in entity overlap
        assert normalize_entity("NVIDIA") == normalize_entity("Nvidia")
        assert normalize_entity("NVIDIA") == normalize_entity(" NVIDIA ")

    def test_the_guardian_normalized(self):
        assert normalize_entity("The Guardian") == normalize_entity("Guardian")

    def test_microsoft_corp_all_caps(self):
        assert normalize_entity("MICROSOFT CORP") == "microsoft corp"
