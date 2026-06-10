from __future__ import annotations

import unittest

from adso.metadata import _parse_description

BLURB = (
    "Tuchman's sweeping history follows the calamitous 14th century "
    "through the life of the French nobleman Enguerrand de Coucy."
)


class ParseDescriptionTests(unittest.TestCase):
    def test_strips_header_before_blank_line(self) -> None:
        raw = f"Amazon.com Review\n\n{BLURB}"
        self.assertEqual(_parse_description(raw), BLURB)

    def test_strips_header_directly_before_paragraph(self) -> None:
        raw = f"From Publishers Weekly\n{BLURB}"
        self.assertEqual(_parse_description(raw), BLURB)

    def test_internal_dot_does_not_protect_header(self) -> None:
        # "Amazon.com" contains a dot, but not a sentence boundary.
        raw = f"Amazon.com Review\n\n{BLURB}"
        self.assertEqual(_parse_description(raw), BLURB)

    def test_keeps_single_paragraph_description(self) -> None:
        self.assertEqual(_parse_description(BLURB), BLURB)

    def test_keeps_short_single_line_description(self) -> None:
        self.assertEqual(_parse_description("A medieval history"), "A medieval history")

    def test_keeps_first_line_ending_in_sentence_punctuation(self) -> None:
        raw = f"A vivid portrait.\n\n{BLURB}"
        self.assertEqual(_parse_description(raw), raw)

    def test_keeps_first_line_with_sentence_break(self) -> None:
        raw = f"Bold. Unforgettable\n\n{BLURB}"
        self.assertEqual(_parse_description(raw), raw)

    def test_keeps_long_first_line(self) -> None:
        first = "A chronicle of plague and war across fourteenth-century Europe"
        raw = f"{first}\n\n{BLURB}"
        self.assertEqual(_parse_description(raw), raw)

    def test_keeps_header_with_nothing_after_it(self) -> None:
        # Stripping would leave an empty description; keep it verbatim.
        self.assertEqual(_parse_description("Amazon.com Review\n\n"), "Amazon.com Review")

    def test_strips_only_the_first_header(self) -> None:
        raw = f"Amazon.com Review\n\n{BLURB}\n\nFrom Publishers Weekly\n\nMore praise."
        self.assertEqual(
            _parse_description(raw),
            f"{BLURB}\n\nFrom Publishers Weekly\n\nMore praise.",
        )

    def test_handles_type_text_object(self) -> None:
        raw = {"type": "/type/text", "value": f"Amazon.com Review\n\n{BLURB}"}
        self.assertEqual(_parse_description(raw), BLURB)

    def test_handles_windows_line_endings(self) -> None:
        raw = f"Amazon.com Review\r\n\r\n{BLURB}"
        self.assertEqual(_parse_description(raw), BLURB)

    def test_none_returns_none(self) -> None:
        self.assertIsNone(_parse_description(None))

    def test_empty_and_whitespace_return_none(self) -> None:
        self.assertIsNone(_parse_description(""))
        self.assertIsNone(_parse_description("   \n  "))

    def test_object_without_value_returns_none(self) -> None:
        self.assertIsNone(_parse_description({"type": "/type/text"}))

    def test_non_string_returns_none(self) -> None:
        self.assertIsNone(_parse_description(42))


if __name__ == "__main__":
    unittest.main()
