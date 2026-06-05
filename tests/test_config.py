"""Tests for configuration profile resolution and the config writers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adso import config


class ConfigResolutionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.xdg = self.root / "xdg"
        self.cwd = self.root / "work"
        self.cwd.mkdir(parents=True)
        self.env = {"XDG_CONFIG_HOME": str(self.xdg)}

    def tearDown(self):
        self._tmp.cleanup()

    def _write_user(self, text: str) -> None:
        path = config.user_config_path(self.env)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _write_project(self, text: str) -> None:
        config.project_config_path(self.cwd).write_text(text, encoding="utf-8")

    def test_default_db_when_no_config(self):
        cfg = config.load(env=self.env, cwd=self.cwd)
        self.assertEqual(cfg.db_path, config.DEFAULT_DB)
        self.assertIsNone(cfg.profile)

    def test_profile_supplies_db_path(self):
        self._write_user(
            "[adso]\ndefault_profile = home\n\n[profile.home]\ndb = /data/home.sqlite\n"
        )
        cfg = config.load(env=self.env, cwd=self.cwd)
        self.assertEqual(cfg.profile, "home")
        self.assertEqual(cfg.db_path, "/data/home.sqlite")
        self.assertEqual(cfg.sources["db"], "profile")

    def test_precedence_flag_over_env_over_profile(self):
        self._write_user("[adso]\ndefault_profile = home\n\n[profile.home]\ndb = /from/profile.sqlite\n")
        env = {**self.env, "ADSO_DB": "/from/env.sqlite"}

        # env beats profile
        cfg = config.load(env=env, cwd=self.cwd)
        self.assertEqual(cfg.db_path, "/from/env.sqlite")
        self.assertEqual(cfg.sources["db"], "env:ADSO_DB")

        # explicit flag beats env
        cfg = config.load(db_arg="/from/flag.sqlite", env=env, cwd=self.cwd)
        self.assertEqual(cfg.db_path, "/from/flag.sqlite")
        self.assertEqual(cfg.sources["db"], "flag")

    def test_profile_arg_overrides_default_profile(self):
        self._write_user(
            "[adso]\ndefault_profile = home\n\n"
            "[profile.home]\ndb = /home.sqlite\n\n"
            "[profile.sandbox]\ndb = /sandbox.sqlite\n"
        )
        cfg = config.load(profile_arg="sandbox", env=self.env, cwd=self.cwd)
        self.assertEqual(cfg.profile, "sandbox")
        self.assertEqual(cfg.db_path, "/sandbox.sqlite")

    def test_project_layers_over_user(self):
        self._write_user("[adso]\ndefault_profile = home\n\n[profile.home]\ndb = /user.sqlite\n")
        self._write_project("[profile.home]\ndb = /project.sqlite\n")
        cfg = config.load(env=self.env, cwd=self.cwd)
        # default_profile still comes from the user file; db comes from project.
        self.assertEqual(cfg.profile, "home")
        self.assertEqual(cfg.db_path, "/project.sqlite")

    def test_env_notion_overrides_profile(self):
        self._write_user(
            "[profile.home]\nnotion_api_key = profilekey\nnotion_database_id = profiledb\n"
        )
        env = {**self.env, "ADSO_PROFILE": "home", "NOTION_DB_ID": "envdb"}
        cfg = config.load(env=env, cwd=self.cwd)
        self.assertEqual(cfg.notion_api_key, "profilekey")
        self.assertEqual(cfg.notion_database_id, "envdb")
        self.assertEqual(cfg.sources["notion_database_id"], "env:NOTION_DB_ID")

    def test_db_path_is_expanded(self):
        self._write_user("[adso]\ndefault_profile = home\n\n[profile.home]\ndb = ~/lib.sqlite\n")
        cfg = config.load(env=self.env, cwd=self.cwd)
        self.assertFalse(cfg.db_path.startswith("~"))
        self.assertTrue(cfg.db_path.endswith("lib.sqlite"))


class ConfigWriterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cwd = self.root / "work"
        self.cwd.mkdir(parents=True)
        self.env = {"XDG_CONFIG_HOME": str(self.root / "xdg")}

    def tearDown(self):
        self._tmp.cleanup()

    def test_set_and_use_round_trip(self):
        config.set_value("sandbox", "db", "/s.sqlite", env=self.env, cwd=self.cwd)
        config.set_value("sandbox", "notion-database-id", "abc", env=self.env, cwd=self.cwd)
        config.set_default_profile("sandbox", env=self.env, cwd=self.cwd)

        cfg = config.load(env=self.env, cwd=self.cwd)
        self.assertEqual(cfg.profile, "sandbox")
        self.assertEqual(cfg.db_path, "/s.sqlite")
        self.assertEqual(cfg.notion_database_id, "abc")
        self.assertIn("sandbox", config.list_profiles(env=self.env, cwd=self.cwd))

    def test_set_rejects_unknown_key(self):
        with self.assertRaises(ValueError):
            config.set_value("home", "nonsense", "x", env=self.env, cwd=self.cwd)

    def test_local_scope_writes_project_file(self):
        config.set_value("home", "db", "/p.sqlite", local=True, env=self.env, cwd=self.cwd)
        self.assertTrue(config.project_config_path(self.cwd).exists())
        self.assertFalse(config.user_config_path(self.env).exists())

    def test_init_writes_starter_once(self):
        path, created = config.init_config(env=self.env, cwd=self.cwd)
        self.assertTrue(created)
        self.assertTrue(path.exists())
        # Second call leaves it untouched.
        _, created_again = config.init_config(env=self.env, cwd=self.cwd)
        self.assertFalse(created_again)
        self.assertIn("personal", config.list_profiles(env=self.env, cwd=self.cwd))

    def test_mask_secret(self):
        self.assertEqual(config.mask_secret(None), "(unset)")
        self.assertEqual(config.mask_secret("abcd1234"), "****1234")
        self.assertEqual(config.mask_secret("ab"), "****")


if __name__ == "__main__":
    unittest.main()
