"""Configuration profiles for Adso.

Adso reads named *profiles* from an INI file so the database path and Notion
connector settings can be switched as a unit. Switching profiles switches the
Notion target, which is how a throwaway *sandbox* database is kept separate from
*production* — you can't accidentally write to the real Notion database while a
sandbox profile is active.

Two files are consulted, project-local first:

    ./adso.ini                              (next to where you run Adso)
    $XDG_CONFIG_HOME/adso/config.ini        (computer-wide; ~/.config/adso/...)

Both are loaded into one parser with the project file read *last*, so its values
win — settings travel with a portable library folder, with the user-level file
as a fallback default.

Resolution precedence for every setting is:

    explicit CLI flag  >  environment variable  >  active profile  >  built-in

so environment variables stay authoritative for automation (CI, scripts).
"""

from __future__ import annotations

import os
from configparser import ConfigParser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


DEFAULT_DB = "adso.sqlite"
PROJECT_CONFIG_BASENAME = "adso.ini"

# Per-profile keys, exposed to `adso config set` in their CLI (dashed) form. The
# mapping is dash -> INI key so the stored file stays readable.
PROFILE_KEYS = {
    "db": "db",
    "notion-api-key": "notion_api_key",
    "notion-database-id": "notion_database_id",
    "notion-target": "notion_target",
}
SECRET_KEYS = frozenset({"notion_api_key"})


def user_config_path(env: Mapping[str, str] | None = None) -> Path:
    """Computer-wide config file, honouring XDG_CONFIG_HOME."""
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "adso" / "config.ini"


def project_config_path(cwd: Path | None = None) -> Path:
    """Project-local config file beside the working directory."""
    return (cwd or Path.cwd()) / PROJECT_CONFIG_BASENAME


def config_paths(env: Mapping[str, str] | None = None, cwd: Path | None = None) -> list[Path]:
    """Config files in load order (user first, project last so project wins)."""
    return [user_config_path(env), project_config_path(cwd)]


def _read_parser(env: Mapping[str, str] | None = None, cwd: Path | None = None) -> ConfigParser:
    parser = ConfigParser()
    # ConfigParser.read silently skips files that don't exist, and reads them in
    # order, so later (project-local) files override earlier (user-level) ones.
    parser.read([str(path) for path in config_paths(env, cwd)], encoding="utf-8")
    return parser


def _profile_section(profile: str) -> str:
    return f"profile.{profile}"


@dataclass
class ResolvedConfig:
    """The settings Adso should actually use for this invocation."""

    db_path: str
    profile: str | None
    notion_api_key: str | None
    notion_database_id: str | None
    notion_target: str | None
    # Where each resolved value came from, for `config show` / doctor display.
    sources: dict[str, str] = field(default_factory=dict)


def load(
    *,
    db_arg: str | None = None,
    profile_arg: str | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> ResolvedConfig:
    """Resolve effective settings from CLI args, environment, and config files."""
    env = os.environ if env is None else env
    parser = _read_parser(env, cwd)

    profile = profile_arg or env.get("ADSO_PROFILE") or _default_profile(parser)
    section = _profile_section(profile) if profile else None
    have_section = bool(section and parser.has_section(section))

    def from_profile(key: str) -> str | None:
        if have_section and parser.has_option(section, key):
            value = parser.get(section, key).strip()
            return value or None
        return None

    sources: dict[str, str] = {}

    db_path, sources["db"] = _resolve(
        ("flag", db_arg),
        ("env:ADSO_DB", env.get("ADSO_DB")),
        ("profile", _expand(from_profile("db"))),
        ("default", DEFAULT_DB),
    )
    notion_api_key, sources["notion_api_key"] = _resolve(
        ("env:NOTION_API_KEY", env.get("NOTION_API_KEY")),
        ("profile", from_profile("notion_api_key")),
    )
    notion_database_id, sources["notion_database_id"] = _resolve(
        ("env:NOTION_DB_ID", env.get("NOTION_DB_ID")),
        ("profile", from_profile("notion_database_id")),
    )
    notion_target, sources["notion_target"] = _resolve(
        ("profile", from_profile("notion_target")),
    )

    return ResolvedConfig(
        db_path=db_path or DEFAULT_DB,
        profile=profile,
        notion_api_key=notion_api_key,
        notion_database_id=notion_database_id,
        notion_target=notion_target,
        sources=sources,
    )


def _resolve(*candidates: tuple[str, str | None]) -> tuple[str | None, str]:
    """Return the first non-empty candidate value and the source label it came from."""
    for label, value in candidates:
        if value:
            return value, label
    return None, "unset"


def _expand(value: str | None) -> str | None:
    return str(Path(value).expanduser()) if value else None


def _default_profile(parser: ConfigParser) -> str | None:
    if parser.has_option("adso", "default_profile"):
        value = parser.get("adso", "default_profile").strip()
        return value or None
    return None


# --- Introspection used by `adso config` and doctor --------------------------


def list_profiles(env: Mapping[str, str] | None = None, cwd: Path | None = None) -> list[str]:
    parser = _read_parser(env, cwd)
    names = [
        section[len("profile.") :]
        for section in parser.sections()
        if section.startswith("profile.")
    ]
    return sorted(names)


def default_profile(env: Mapping[str, str] | None = None, cwd: Path | None = None) -> str | None:
    return _default_profile(_read_parser(env, cwd))


def profile_settings(
    profile: str,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> dict[str, str]:
    """Raw stored settings for one profile (no env/flag overlay)."""
    parser = _read_parser(env, cwd)
    section = _profile_section(profile)
    if not parser.has_section(section):
        return {}
    return {key: parser.get(section, key) for key in parser.options(section)}


def mask_secret(value: str | None) -> str:
    if not value:
        return "(unset)"
    if len(value) <= 4:
        return "****"
    return f"****{value[-4:]}"


# --- Writers (used by `adso config set` / `use` / `init`) --------------------


def _scope_path(local: bool, env: Mapping[str, str] | None = None, cwd: Path | None = None) -> Path:
    return project_config_path(cwd) if local else user_config_path(env)


def _load_writable(path: Path) -> ConfigParser:
    parser = ConfigParser()
    if path.exists():
        parser.read(str(path), encoding="utf-8")
    return parser


def _write(parser: ConfigParser, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        parser.write(handle)


def set_value(
    profile: str,
    key: str,
    value: str,
    *,
    local: bool = False,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> Path:
    """Set one profile setting. *key* is the dashed CLI form (see PROFILE_KEYS)."""
    if key not in PROFILE_KEYS:
        raise ValueError(
            f"Unknown setting '{key}'. Choose from: {', '.join(sorted(PROFILE_KEYS))}."
        )
    path = _scope_path(local, env, cwd)
    parser = _load_writable(path)
    section = _profile_section(profile)
    if not parser.has_section(section):
        parser.add_section(section)
    parser.set(section, PROFILE_KEYS[key], value)
    _write(parser, path)
    return path


def set_default_profile(
    profile: str,
    *,
    local: bool = False,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> Path:
    path = _scope_path(local, env, cwd)
    parser = _load_writable(path)
    if not parser.has_section("adso"):
        parser.add_section("adso")
    parser.set("adso", "default_profile", profile)
    _write(parser, path)
    return path


STARTER_CONFIG = """\
# Adso configuration. Profiles bundle a database path with Notion connector
# settings so you can switch them as a unit. Keep secrets out of this file when
# you can — Adso reads NOTION_API_KEY from the environment, which takes priority.
#
# Precedence per setting: CLI flag > environment variable > profile > default.

[adso]
default_profile = personal

[profile.personal]
db = ~/Books/library.sqlite
# notion_database_id = your-production-notion-database-id
notion_target = production

[profile.sandbox]
db = ~/Books/sandbox.sqlite
# notion_database_id = your-throwaway-notion-database-id
notion_target = sandbox
"""


def init_config(
    *,
    local: bool = False,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> tuple[Path, bool]:
    """Write a commented starter config. Returns (path, created)."""
    path = _scope_path(local, env, cwd)
    if path.exists():
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STARTER_CONFIG, encoding="utf-8")
    return path, True
