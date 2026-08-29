"""Tests for the game-parameter helpers in ``common/utils.py`` — config entry
parsing and value/display-name conversion."""

from common.utils import (
    format_accepted_values,
    normalize_param_values,
    parse_param_entries,
    render_param_values,
)


class TestParseParamEntries:
    def test_bare_values_get_identity_display(self):
        assert parse_param_entries("adset, standard") == {
            "adset": "adset",
            "standard": "standard",
        }

    def test_pairs(self):
        assert parse_param_entries(
            "(adset, Advanced), (standard, Standard)"
        ) == {
            "adset": "Advanced",
            "standard": "Standard",
        }

    def test_pairs_with_commas_in_display_name(self):
        assert parse_param_entries("(lm_bm, Black, Market)") == {
            "lm_bm": "Black, Market",
        }

    def test_mixed_bare_and_pairs_with_whitespace(self):
        assert parse_param_entries(" a , (b, Bee),c ") == {
            "a": "a",
            "b": "Bee",
            "c": "c",
        }

    def test_empty_entries_are_ignored(self):
        assert parse_param_entries("") == {}
        assert parse_param_entries(" , , ") == {}


class TestNormalizeParamValues:
    MAPPING = {
        "adset": "Advanced",
        "standard": "Standard",
        "e&p": "Exiles and Partisans",
    }

    def test_raw_values_pass_through(self):
        assert normalize_param_values(["adset", "e&p"], self.MAPPING) == [
            "adset", "e&p",
        ]

    def test_display_names_resolve_to_values(self):
        assert normalize_param_values(
            ["Advanced", "Exiles and Partisans"], self.MAPPING
        ) == ["adset", "e&p"]

    def test_display_names_are_case_insensitive(self):
        assert normalize_param_values(
            ["advanced", "EXILES AND PARTISANS"], self.MAPPING
        ) == ["adset", "e&p"]

    def test_unknown_tokens_are_left_unchanged(self):
        assert normalize_param_values(["adset", "unknown"], self.MAPPING) == [
            "adset", "unknown",
        ]


class TestRenderParamValues:
    MAPPING = {
        "adset": "Advanced",
        "standard": "Standard",
    }

    def test_raw_values_map_to_display_names(self):
        assert render_param_values(["adset", "standard"], self.MAPPING) == [
            "Advanced", "Standard",
        ]

    def test_display_names_are_kept(self):
        assert render_param_values(["Advanced"], self.MAPPING) == ["Advanced"]

    def test_unknown_tokens_are_kept(self):
        assert render_param_values(["adset", "mystery"], self.MAPPING) == [
            "Advanced", "mystery",
        ]

    def test_empty_mapping_returns_values_unchanged(self):
        assert render_param_values(["adset"], {}) == ["adset"]


class TestFormatAcceptedValues:
    def test_pairs_show_value_and_display(self):
        assert format_accepted_values(
            {"adset": "Advanced", "standard": "Standard"}
        ) == "adset (Advanced), standard (Standard)"

    def test_bare_values_are_shown_bare(self):
        assert format_accepted_values({"yes": "yes", "no": "no"}) == "yes, no"

