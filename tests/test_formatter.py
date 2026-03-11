"""Tests for g3lobster.chat.formatter."""

from __future__ import annotations

import pytest

from g3lobster.chat.formatter import format_for_google_chat


class TestBoldConversion:
    def test_double_asterisk_to_single(self):
        assert format_for_google_chat("**bold**") == "*bold*"

    def test_multiple_bold_segments(self):
        assert format_for_google_chat("**a** and **b**") == "*a* and *b*"

    def test_bold_within_sentence(self):
        assert format_for_google_chat("this is **important** text") == "this is *important* text"


class TestStrikethroughConversion:
    def test_double_tilde_to_single(self):
        assert format_for_google_chat("~~strike~~") == "~strike~"

    def test_multiple_strikethroughs(self):
        assert format_for_google_chat("~~a~~ and ~~b~~") == "~a~ and ~b~"


class TestLinkConversion:
    def test_markdown_link_to_google_chat(self):
        assert format_for_google_chat("[click here](https://example.com)") == "<https://example.com|click here>"

    def test_multiple_links(self):
        result = format_for_google_chat("[a](https://a.com) and [b](https://b.com)")
        assert result == "<https://a.com|a> and <https://b.com|b>"


class TestHeaderConversion:
    def test_h1(self):
        assert format_for_google_chat("# Title") == "*Title*"

    def test_h2(self):
        assert format_for_google_chat("## Subtitle") == "*Subtitle*"

    def test_h3(self):
        assert format_for_google_chat("### Section") == "*Section*"

    def test_h6(self):
        assert format_for_google_chat("###### Deep") == "*Deep*"

    def test_multiline_headers(self):
        text = "# First\nsome text\n## Second"
        expected = "*First*\nsome text\n*Second*"
        assert format_for_google_chat(text) == expected


class TestCombinedFormatting:
    def test_bold_and_link(self):
        text = "**Check** [this](https://example.com)"
        expected = "*Check* <https://example.com|this>"
        assert format_for_google_chat(text) == expected

    def test_header_with_bold(self):
        # Header conversion runs after bold conversion
        text = "# **Important** Title"
        result = format_for_google_chat(text)
        assert "*Important*" in result

    def test_all_conversions(self):
        text = "# Heading\n**bold** ~~strike~~ [link](https://x.com)"
        result = format_for_google_chat(text)
        assert "*Heading*" in result
        assert "*bold*" in result
        assert "~strike~" in result
        assert "<https://x.com|link>" in result


class TestPassthrough:
    def test_empty_string(self):
        assert format_for_google_chat("") == ""

    def test_plain_text(self):
        assert format_for_google_chat("hello world") == "hello world"

    def test_single_asterisk_preserved(self):
        assert format_for_google_chat("*already italic*") == "*already italic*"


class TestCodeBlockPreservation:
    def test_fenced_code_not_converted(self):
        text = "```\n**bold** [link](url)\n```"
        assert format_for_google_chat(text) == text

    def test_mixed_code_and_text(self):
        text = "**bold** text\n```\n**not bold**\n```\n**also bold**"
        result = format_for_google_chat(text)
        assert result.startswith("*bold* text")
        assert "**not bold**" in result
        assert result.endswith("*also bold*")

    def test_code_block_with_language(self):
        text = "```python\nx = **y**\n```"
        assert format_for_google_chat(text) == text
