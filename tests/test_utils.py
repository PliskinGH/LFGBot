"""Unit tests for the pure helper functions in ``common/utils.py``."""
import configparser
import re

import pytest

from common import utils


class TestSplitConfigList:
    def test_none_returns_empty_list(self):
        assert utils.split_config_list(None) == []

    def test_empty_string_returns_empty_string_item(self):
        assert utils.split_config_list("") == [""]

    def test_splits_and_strips_commas(self):
        assert utils.split_config_list("a, b ,c") == ["a", "b", "c"]

    def test_single_item(self):
        assert utils.split_config_list("root") == ["root"]

    def test_keeps_empty_spots_between_commas(self):
        assert utils.split_config_list(", ,") == ["", "", ""]


class TestGetGuildFromConfig:
    def _config(self):
        config = configparser.ConfigParser()
        config.read_string(
            "[DEFAULT]\nID = 1\n"
            "[GuildA]\nID = 90401\n"
            "[GuildB]\nID = 42424\n"
        )
        return config

    def test_matching_guild_returns_section_name(self):
        config = self._config()
        assert utils.get_guild_from_config(config, 90401) == "GuildA"
        assert utils.get_guild_from_config(config, 42424) == "GuildB"

    def test_unknown_guild_returns_default(self):
        config = self._config()
        assert utils.get_guild_from_config(config, 99999) == utils.CONFIG_DEFAULT

    def test_guild_without_id_section_is_ignored(self):
        config = configparser.ConfigParser()
        config.read_string("[DEFAULT]\nID = 1\n[NoId]\nmap = xyz\n")
        assert utils.get_guild_from_config(config, 123456) == utils.CONFIG_DEFAULT


class TestParseIntervals:
    @pytest.mark.parametrize(
        "text,cardinal,expected",
        [
            ("6", 10, [1, 2, 3, 4, 5, 6]),           # n -> first n items
            ("6", 3, [1, 2, 3]),                      # capped by cardinal
            ("2", 10, [1, 2]),                        # single n -> first n items
            ("2,5-9", 10, [2, 5, 6, 7, 8, 9]),       # mix of single + range
            ("9-5", 10, [5, 6, 7, 8, 9]),            # reversed bounds
            ("3-10", 5, [3, 4, 5]),                   # range capped by cardinal
            ("7", 3, [1, 2, 3]),                      # single n capped by cardinal
        ],
    )
    def test_valid_inputs(self, text, cardinal, expected):
        assert utils.parse_intervals(text, cardinal) == expected

    @pytest.mark.parametrize("text", ["abc", "2 5", "2..5", "2;5", "5*"])
    def test_invalid_characters_return_empty(self, text):
        assert utils.parse_intervals(text, 10) == []

    def test_empty_string_raises_value_error(self):
        # Empty inputs match the numeric regex but break on the int() conversion.
        with pytest.raises(ValueError):
            utils.parse_intervals("", 10)


class TestIndefiniteArticle:
    @pytest.mark.parametrize("word", ["Autumn", "Oath", "Elder", "Iberian", "umbrella", "orange"])
    def test_vowel_words_get_an(self, word):
        assert utils.indefinite_article(word) == "an"

    @pytest.mark.parametrize("word", ["Root", "Lake", "Map", "Tower", "Ferry", ""])
    def test_consonant_or_empty_get_a(self, word):
        assert utils.indefinite_article(word) == "a"


class TestGetDefaultEmojiUrl:
    def test_returns_twemoji_url_for_first_codepoint(self):
        # U+1F44D (thumbs up) -> 1f44d
        assert utils.get_default_emoji_url("👍") == (
            "https://twemoji.maxcdn.com/v/latest/72x72/1f44d.png"
        )

    def test_handles_multichar_emoji(self):
        assert utils.get_default_emoji_url("✅").endswith("/72x72/2705.png")


class TestCleanThreadTitle:
    def test_none_becomes_empty_string(self):
        assert utils.clean_thread_title(None, re.compile(r"")) == ""

    def test_strips_custom_emoji_patterns(self):
        custom = re.compile(r"<:[a-zA-Z0-9_]+:[0-9]+>")
        assert utils.clean_thread_title("Root <:root:123456> game", custom) == (
            "Root  game"
        )

    def test_strips_whitespace(self):
        custom = re.compile(r"<:[a-zA-Z0-9_]+:[0-9]+>")
        assert utils.clean_thread_title("  spoiler-free  ", custom) == "spoiler-free"

    def test_truncates_over_100_characters(self):
        title = "x" * 120
        assert utils.clean_thread_title(title, re.compile(r"")) == "x" * 100


class TestGetIdFromMention:
    @pytest.mark.parametrize(
        "mention",
        ["<@123>", "<@!123>", "<@&123>", "<#123>"],
    )
    def test_valid_mentions(self, mention):
        assert utils.get_id_from_mention(mention) == 123

    @pytest.mark.parametrize(
        "bad",
        ["@123", "123", "<@abc>", "<@123", "<@123> extra", "hello"],
    )
    def test_invalid_mentions_return_none(self, bad):
        assert utils.get_id_from_mention(bad) is None


class TestSafeListGet:
    def test_existing_index(self):
        assert utils.safe_list_get(["a", "b"], 1, "fallback") == "b"

    def test_missing_index_returns_default(self):
        assert utils.safe_list_get(["a", "b"], 5, "fallback") == "fallback"

    def test_negative_index_raises_indexerror_and_returns_default(self):
        assert utils.safe_list_get([], -1, None) is None