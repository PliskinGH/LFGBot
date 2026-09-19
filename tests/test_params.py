"""Tests for the game-parameter helpers in ``cogs/matchmaking/utils.py`` — config
entry parsing and value/display-name conversion."""

import random

from cogs.matchmaking.constants import RANDOM_DISPLAY, RANDOM_VALUE
from cogs.matchmaking.utils import (
    format_accepted_values,
    is_random_value,
    normalize_param_values,
    parse_param_entries,
    pending_roll,
    random_token_available,
    render_param_values,
    roll_param_values,
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


class TestRollSentinel:
    """The Random roll token."""

    MAPPING = {"autumn": "Autumn", "winter": "Winter"}

    @staticmethod
    def _shadowing():
        # A parameter whose own value displays the sentinel's display name.
        return {"false": "Fixed", "true": RANDOM_DISPLAY}

    def test_canonical_value_is_the_display_lowercase(self):
        assert RANDOM_VALUE == RANDOM_DISPLAY.lower()

    def test_is_random_value(self):
        assert is_random_value(RANDOM_VALUE) is True
        assert is_random_value(RANDOM_DISPLAY) is True
        assert is_random_value(f" {RANDOM_DISPLAY.swapcase()} ") is True
        assert is_random_value("autumn") is False

    def test_pending_roll(self):
        assert pending_roll(["autumn", RANDOM_VALUE], self.MAPPING) is True
        assert pending_roll(["autumn", "winter"], self.MAPPING) is False
        # A shadowing map never holds a roll, whatever the values say.
        assert pending_roll([RANDOM_VALUE], self._shadowing()) is False

    def test_random_token_is_available(self):
        assert random_token_available(self.MAPPING) is True

    def test_random_token_is_shadowed_by_a_value_or_display(self):
        assert random_token_available(self._shadowing()) is False
        assert random_token_available({RANDOM_VALUE: "Anything"}) is False

    def test_display_form_is_normalized_to_the_sentinel(self):
        # Display form round-trips to the sentinel.
        assert normalize_param_values(
            [RANDOM_DISPLAY], self.MAPPING) == [RANDOM_VALUE]

    def test_shadowing_display_name_keeps_its_value(self):
        assert normalize_param_values(
            [RANDOM_DISPLAY], self._shadowing()) == ["true"]

    def test_unknown_display_is_left_unchanged(self):
        assert normalize_param_values(["Summer"], self.MAPPING) == ["Summer"]

    def test_sentinel_is_rendered_as_its_display_form(self):
        assert render_param_values(
            [RANDOM_VALUE], self.MAPPING) == [RANDOM_DISPLAY]

    def test_shadowing_display_name_is_rendered_as_its_value(self):
        assert render_param_values(
            [RANDOM_DISPLAY], self._shadowing()) == [RANDOM_DISPLAY]


class TestRollParamValues:
    MAPPING = {
        "autumn": "Autumn",
        "winter": "Winter",
        "mountain": "Mountain",
        "lake": "Lake",
    }
    DECK = {"standard": "Standard", "e&p": "Exiles and Partisans"}

    def test_single_roll_draws_one_known_value(self):
        rolled = roll_param_values([RANDOM_VALUE], self.MAPPING)

        assert len(rolled) == 1
        assert rolled[0] in self.MAPPING

    def test_two_rolls_draw_different_values(self):
        rng = random.Random(0)
        rolled = roll_param_values(
            [RANDOM_VALUE, RANDOM_VALUE], self.MAPPING, rng)

        assert len(set(rolled)) == 2
        assert set(rolled) <= set(self.MAPPING)

    def test_every_seed_draws_distinct_values(self):
        for seed in range(20):
            rolled = roll_param_values(
                [RANDOM_VALUE, RANDOM_VALUE, RANDOM_VALUE], self.MAPPING,
                random.Random(seed))
            assert len(set(rolled)) == 3

    def test_explicit_values_are_kept_and_never_drawn(self):
        rng = random.Random(7)
        rolled = roll_param_values(
            ["autumn", RANDOM_VALUE], self.MAPPING, rng)

        assert rolled[0] == "autumn"
        assert rolled[1] in {"winter", "mountain", "lake"}

    def test_exhausted_pool_drops_the_extra_roll(self):
        rolled = roll_param_values(
            [RANDOM_VALUE, RANDOM_VALUE], self.DECK, random.Random(1))

        assert len(rolled) == 2
        assert len(set(rolled)) == 2
        assert set(rolled) == set(self.DECK)

    def test_no_value_left_to_draw_drops_the_sentinel(self):
        # Nothing left to draw: dropped, not duplicated.
        rolled = roll_param_values(
            ["standard", RANDOM_VALUE], {"standard": "Standard"},
            random.Random(1))

        assert rolled == ["standard"]

    def test_values_without_a_sentinel_are_untouched(self):
        values = ["autumn", "winter"]

        assert roll_param_values(values, self.MAPPING) == values

    def test_no_roll_when_the_token_is_a_display_name(self):
        # The parameter's own display name wins: the sentinel resolves to the
        # value behind it instead of drawing.
        shadowing = {"false": "Fixed", "true": RANDOM_DISPLAY}

        assert roll_param_values(
            [RANDOM_VALUE], shadowing, random.Random(0)) == ["true"]

    def test_no_roll_when_the_token_is_a_raw_value(self):
        # A raw value named like the token is a value, not a roll.
        shadowing = {RANDOM_VALUE: RANDOM_DISPLAY, "other": "Other"}

        assert roll_param_values(
            [RANDOM_VALUE], shadowing, random.Random(0)) == [RANDOM_VALUE]

    def test_completion_style_roll_draws_several_values(self):
        rolled = roll_param_values(
            [RANDOM_VALUE, RANDOM_VALUE], self.MAPPING, random.Random(3))

        assert len(set(rolled)) == 2

