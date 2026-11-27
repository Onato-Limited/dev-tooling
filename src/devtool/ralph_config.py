"""A project's Ralph loop settings, from its `.ralph/config`."""

from dataclasses import dataclass
from pathlib import Path

REQUIRED = ("app_id", "installation_id", "app_slug", "board_owner", "board_number", "merge_mode")
MERGE_MODES = ("auto", "label")


class ConfigError(Exception):
    """The config is missing or wrong; the message names the key and the fix."""


@dataclass(frozen=True)
class Config:
    app_id: str
    installation_id: str
    app_slug: str  # also the bot's login (`<slug>[bot]`) and the key's keychain service
    board_owner: str  # a user or an organization, not necessarily the repo's owner
    board_number: int
    merge_mode: str
    review_check: str = "claude / review"
    audit_check: str = "main-audit / audit"
    notify: str | None = None
    cleanup_list: str | None = None
    cleanup_prune: str | None = None
    update_hook: str | None = None
    session_path: str | None = None


def load(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(
            f"Ralph loop can't start: {path} is missing. Add it with these keys, one name=value "
            f"per line: {', '.join(REQUIRED)}."
        )
    settings = read_settings(path)
    known = list(Config.__dataclass_fields__)
    if unknown := [name for name in settings if name not in known]:
        raise ConfigError(
            f"Ralph loop can't start: {path} has unknown keys: {', '.join(unknown)}. "
            f"The keys are {', '.join(known)}. Rename each to one of these, or remove it."
        )
    if missing := [name for name in REQUIRED if name not in settings]:
        raise ConfigError(
            f"Ralph loop can't start: {path} is missing {', '.join(missing)}. "
            "Add each as a name=value line."
        )
    if settings["merge_mode"] not in MERGE_MODES:
        raise ConfigError(
            f"Ralph loop can't start: merge_mode in {path} is {settings['merge_mode']!r}. Set it "
            "to auto (enable auto-merge on each PR) or label (add merge-when-green to each PR)."
        )
    if not (settings["board_number"].isascii() and settings["board_number"].isdigit()):
        raise ConfigError(
            f"Ralph loop can't start: board_number in {path} is {settings['board_number']!r}. "
            "Set it to the project's number, from its URL (.../projects/<number>)."
        )
    return Config(**{**settings, "board_number": int(settings["board_number"])})


def read_settings(path: Path) -> dict[str, str]:
    """Each `name=value` line's value, trimmed; blank values count as absent."""
    settings = {}
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, equals, value = (part.strip() for part in line.partition("="))
        if not (name and equals):
            raise ConfigError(
                f"Ralph loop can't start: line {number} of {path} is {line!r}. Write each "
                "setting as name=value, or start the line with # to make it a comment."
            )
        if value:
            settings[name] = value
    return settings
