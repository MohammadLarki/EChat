import unittest
from types import SimpleNamespace
from unittest.mock import patch

import cli_display
import main


class DialogDisplayTests(unittest.TestCase):
    def format_one(self, title, width):
        dialog = SimpleNamespace(name=title, entity=None)
        with patch.object(cli_display, "_chat_kind", return_value="Channel"):
            lines = cli_display.dialog_lines([dialog], width=width)
        return dialog, lines

    def test_narrow_terminal_places_type_near_index_and_title_below(self):
        _, lines = self.format_one("A very long chat title", width=40)
        self.assertEqual("[1] Channel", lines[0])
        self.assertTrue(lines[1].startswith("    A very long"))
        self.assertTrue(all(cli_display._cell_width(line) <= 40 for line in lines))

    def test_persian_title_is_left_for_terminal_to_render(self):
        title = "گفتگوی فارسی"
        dialog, lines = self.format_one(title, width=40)
        self.assertEqual(title, dialog.name)
        self.assertEqual("    " + title, lines[1])

    def test_mixed_persian_english_numbers_and_punctuation(self):
        title = "فروشگاه Geo Store 2026 (VIP)"
        dialog, lines = self.format_one(title, width=48)
        self.assertEqual(title, dialog.name)
        self.assertEqual("    " + title, lines[1])
        self.assertTrue(lines[1].startswith("    "))
        pipe_title = "Geo Store || روستا و ..."
        _, pipe_lines = self.format_one(pipe_title, width=60)
        self.assertEqual("    " + pipe_title, pipe_lines[1])

    def test_emoji_title_keeps_source_string_unchanged(self):
        title = "😀 اخبار فارسی 📣"
        dialog, lines = self.format_one(title, width=48)
        self.assertEqual(title, dialog.name)
        self.assertEqual("    " + title, lines[1])

    def test_punctuation_digits_and_emoji_title_is_unchanged(self):
        title = "سلام (VIP) 123! 😀"
        dialog, lines = self.format_one(title, width=36)
        self.assertEqual(title, dialog.name)
        self.assertTrue(lines[1].startswith("    "))
        self.assertTrue(all(cli_display._cell_width(line) <= 36 for line in lines))

    def test_wide_terminal_uses_one_line_without_far_right_type_column(self):
        _, lines = self.format_one("English chat", width=100)
        self.assertEqual("[  1] Channel | English chat", lines[0])
        self.assertEqual(1, len(lines))

    def test_cli_uses_detected_terminal_width(self):
        dialog = SimpleNamespace(name="A narrow title", entity=None)
        with patch("main.shutil.get_terminal_size", return_value=SimpleNamespace(columns=40, lines=24)):
            with patch.object(main.cli_display, "dialog_lines", return_value=["rendered"]) as render:
                with patch("builtins.print"):
                    main.print_dialogs([dialog])
        render.assert_called_once_with([dialog], 1, 40)


if __name__ == "__main__":
    unittest.main()
