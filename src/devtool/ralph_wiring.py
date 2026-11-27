"""The pieces `devtool ralph` builds from a project's config: its devices, `notify`, and the
logins read from GitHub."""

import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

from devtool.ralph_github import Run
from devtool.ralph_session import Bot

Runner = Callable[..., subprocess.CompletedProcess]


class DevicesError(Exception):
    """The project's device list command failed; the message names it and its error."""


class CommandDevices:
    """Issue devices behind the project's commands: `list_command` prints one issue number per
    line, and `prune_command <issue>` removes that issue's device. Both run in the checkout."""

    def __init__(self, list_command: str, prune_command: str, repo: Path, run: Runner):
        self.list_command = list_command
        self.prune_command = prune_command
        self.repo = repo
        self.run = run

    def issues(self) -> list[int]:
        done = self._run(shlex.split(self.list_command))
        if failed := failure(self.list_command, done):
            raise DevicesError(failed)
        if odd := [line for line in done.stdout.split() if not (line.isascii() and line.isdigit())]:
            raise DevicesError(
                f"{self.list_command} printed {odd[0]!r}: it must print one issue number per line"
            )
        return [int(line) for line in done.stdout.split()]

    def prune(self, issue: int) -> str | None:
        done = self._run([*shlex.split(self.prune_command), str(issue)])
        return failure(f"{self.prune_command} {issue}", done)

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return self.run(args, cwd=self.repo, capture_output=True, text=True, check=False)


def failure(named: str, done: subprocess.CompletedProcess) -> str | None:
    if done.returncode != 0:
        return f"{named} failed (exit {done.returncode}): {done.stderr.strip()}"
    return None


def notifier(command: str | None, repo: Path, run: Runner) -> Callable[[str], None]:
    """Prints each message, and passes it to the project's command as its last argument. Like
    the device commands, it runs in the checkout, wherever the loop was started from."""

    def notify(message: str) -> None:
        print(message)
        if command:
            run([*shlex.split(command), message], cwd=repo, check=False)

    return notify


def approver(gh: Run) -> str:
    """The login `gh` is signed in as: the owner, whose `plan-approved` label counts."""
    return gh(["api", "user", "--jq", ".login"], None).strip()


def bot(app_slug: str, gh: Run) -> Bot:
    user_id = gh(["api", f"users/{app_slug}[bot]", "--jq", ".id"], None)
    return Bot(app_slug, int(user_id))
