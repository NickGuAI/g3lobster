from __future__ import annotations

import pytest

from g3lobster.chat.formatter import format_for_google_chat


class TestBoldConversion:
    def test_bold_double_asterisks_to_single(self):
        assert format_for_google_chat("**bold**") == "*bold*"

    def test_bold_within_sentence(self):
        assert format_for_google_chat("This is **bold** text") == "This is *bold* text"

    def test_multiple_bold_segments(self):
        result = format_for_google_chat("**one** and **two**")
        assert result == "*one* and *two*"


class TestStrikethroughConversion:
    def test_strikethrough_double_tilde_to_single(self):
        assert format_for_google_chat("~~strike~~") == "~strike~"

    def test_strikethrough_within_sentence(self):
        assert format_for_google_chat("This is ~~removed~~ text") == "This is ~removed~ text"


class TestLinkConversion:
    def test_markdown_link_to_google_chat_format(self):
        assert format_for_google_chat("[text](https://example.com)") == "<https://example.com|text>"

    def test_link_within_sentence(self):
        result = format_for_google_chat("Visit [Google](https://google.com) now")
        assert result == "Visit <https://google.com|Google> now"


class TestHeaderConversion:
    @pytest.mark.parametrize("level", range(1, 7))
    def test_header_levels_1_through_6(self, level):
        hashes = "#" * level
        result = format_for_google_chat(f"{hashes} Header")
        assert result == "*Header*"

    def test_header_preserves_content(self):
        assert format_for_google_chat("# My Title") == "*My Title*"

    def test_header_level_2(self):
        assert format_for_google_chat("## Subtitle") == "*Subtitle*"


class TestCombinedFormatting:
    def test_bold_and_strikethrough_in_one_string(self):
        result = format_for_google_chat("**bold** and ~~strike~~")
        assert result == "*bold* and ~strike~"

    def test_link_and_bold_in_one_string(self):
        result = format_for_google_chat("**Click** [here](https://example.com)")
        assert result == "*Click* <https://example.com|here>"


class TestPassthrough:
    def test_empty_string(self):
        assert format_for_google_chat("") == ""

    def test_plain_text_unchanged(self):
        assert format_for_google_chat("Hello world") == "Hello world"

    def test_plain_text_with_punctuation(self):
        text = "No markdown here, just plain text!"
        assert format_for_google_chat(text) == text


class TestCodeBlockPreservation:
    def test_bold_inside_code_block_not_converted(self):
        text = "```\n**bold**\n```"
        result = format_for_google_chat(text)
        assert "**bold**" in result

    def test_link_inside_code_block_not_converted(self):
        text = "```\n[text](url)\n```"
        result = format_for_google_chat(text)
        assert "[text](url)" in result

    def test_formatting_outside_code_block_still_converted(self):
        text = "**bold** before\n```\n**not bold**\n```\n**bold** after"
        result = format_for_google_chat(text)
        assert "*bold* before" in result
        assert "*bold* after" in result
        assert "**not bold**" in result


class TestMultiLineFormatting:
    def test_multiple_lines_with_mixed_formatting(self):
        text = "# Title\n\nSome **bold** text\n\n~~removed~~ item\n\n[link](https://example.com)"
        result = format_for_google_chat(text)
        assert "*Title*" in result
        assert "*bold*" in result
        assert "~removed~" in result
        assert "<https://example.com|link>" in result

    def test_multiline_preserves_line_structure(self):
        text = "Line one\nLine two\nLine three"
        result = format_for_google_chat(text)
        assert "Line one" in result
        assert "Line two" in result
        assert "Line three" in result


class TestNestedBoldInHeaders:
    def test_bold_inside_header(self):
        result = format_for_google_chat("# **Important**")
        assert "*Important*" in result

    def test_header_with_bold_word(self):
        result = format_for_google_chat("## A **key** point")
        assert "*A " in result or "A " in result
        assert "point*" in result
