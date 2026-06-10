"""Tests for the branded help screen and friendly first-run welcome."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from adso import branding, cli

GOODREADS_HEADER = "Book Id,Title,Author,Exclusive Shelf\n"
SAMPLE_ROW = "123,The Name of the Rose,Umberto Eco,read\n"


class CliWelcomeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = str(self.root / "cat.sqlite")
        # Isolate config: empty environment with a throwaway XDG home and cwd so
        # no real user profile or stray CSVs leak in.
        self._env = mock.patch.dict(
            "os.environ", {"XDG_CONFIG_HOME": str(self.root / "xdg")}, clear=True
        )
        self._env.start()
        self._prev_cwd = os.getcwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self._prev_cwd)
        self._env.stop()
        self._tmp.cleanup()

    def _run(self, argv: list[str]) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--db", self.db, *argv])
        return code, out.getvalue()

    def test_bare_adso_shows_welcome_on_first_run(self):
        code, out = self._run([])
        self.assertEqual(code, 0)
        self.assertIn("Welcome to Adso", out)
        self.assertIn("adso init", out)
        self.assertIn("adso import goodreads <csv>", out)
        self.assertIn("adso list", out)

    def test_welcome_points_at_nearby_csv(self):
        csv_path = self.root / "goodreads_library_export.csv"
        csv_path.write_text(GOODREADS_HEADER + SAMPLE_ROW, encoding="utf-8")
        code, out = self._run([])
        self.assertEqual(code, 0)
        self.assertIn("Found a Goodreads export nearby", out)
        self.assertIn("import goodreads goodreads_library_export.csv", out)

    def test_bare_adso_shows_help_after_init(self):
        self._run(["init"])
        code, out = self._run([])
        self.assertEqual(code, 0)
        self.assertNotIn("Welcome to Adso", out)
        self.assertIn("Your library".upper(), out)

    def test_help_flag_renders_branded_screen(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_version_flag(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as ctx:
            cli.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("adso 0.1.0", out.getvalue())


class BrandingRenderTests(unittest.TestCase):
    def test_help_contains_logo_usage_and_all_groups(self):
        rendered = branding.render_help("adso")
        self.assertIn("USAGE", rendered)
        self.assertIn(branding.LOGO, rendered)
        for heading, _rows in branding.COMMAND_GROUPS:
            self.assertIn(heading.upper(), rendered)

    def test_command_groups_match_parser_subcommands(self):
        parser = cli._build_parser()
        subparsers_action = next(
            action
            for action in parser._actions
            if isinstance(action, __import__("argparse")._SubParsersAction)
        )
        parser_commands = set(subparsers_action.choices)
        self.assertEqual(parser_commands, branding.all_grouped_commands())


if __name__ == "__main__":
    unittest.main()
