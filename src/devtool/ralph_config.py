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
    lines = (line.strip() for line in path.read_text().splitlines())
    pairs = (line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))
    settings = {name.strip(): value.strip() for name, value in pairs if value.strip()}
    known = list(Config.__dataclass_fields__)
    if unknown := [name for name in settings if name not in known]:
        raise ConfigError(
            f"Ralph loop can't start: {path} has unknown keys: {', '.join(unknown)}. "
            f"The keys are {', '.join(known)}."
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
    if not settings["board_number"].isdigit():
        raise ConfigError(
            f"Ralph loop can't start: board_number in {path} is {settings['board_number']!r}. "
            "Set it to the project's number, from its URL (.../projects/<number>)."
        )
    return Config(**{**settings, "board_number": int(settings["board_number"])})
