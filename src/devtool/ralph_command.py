"""Run the Ralph loop on a milestone of the project in the current directory: one fresh Claude
session per issue, as the project's GitHub App, until the milestone is done or needs you."""

import argparse
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO

from devtool import ralph
from devtool.ralph import Devices, GitHub, GitHubError, Session, dry_run
from devtool.ralph_config import Config, ConfigError, load
from devtool.ralph_github import GitHubAdapter, Run, gh_runner
from devtool.ralph_session import ClaudeSession, SessionTokens
from devtool.ralph_token import TokenError, Tokens, github_post, keychain_key, openssl_signer
from devtool.ralph_wiring import CommandDevices, DevicesError, Runner, approver, bot, notifier


@dataclass(frozen=True)
class Parts:
    github: GitHub
    session: Session
    devices: Devices
    notify: Callable[[str], None]
    improves: bool  # the project has an improver prompt


class NoDevices:
    """A project without cleanup commands has no devices to prune."""

    def issues(self) -> list[int]:
        return []

    def prune(self, issue: int) -> str | None:
        return None


def assemble(
    config: Config,
    repo: Path,
    gh: Run,
    run: Runner,
    start: Callable[..., subprocess.Popen],
    environ: dict[str, str],
    *,
    tokens: SessionTokens,
) -> Parts:
    """The loop's parts for the project checked out at `repo`; `gh` is the owner's login."""
    if bool(config.cleanup_list) != bool(config.cleanup_prune):
        raise ConfigError(
            "Ralph loop can't start: set both cleanup_list and cleanup_prune, or neither."
        )
    prefix = branch_prefix(run, repo)
    name = gh(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], None)
    github = GitHubAdapter(
        name.strip(),
        config.board_owner,
        config.board_number,
        config.review_check,
        gh,
        branch_prefix=prefix,
        approver=approver(gh),
        merge_mode=config.merge_mode,
        audit_check=config.audit_check,
    )
    session_path = config.session_path and ":".join(
        str(repo / entry) for entry in config.session_path.split(":")
    )
    session = ClaudeSession(
        repo=repo,
        branch_prefix=prefix,
        bot=bot(config.app_slug, gh),
        tokens=tokens,
        session_path=session_path,
        update_hook=config.update_hook,
        run=run,
        start=start,
        environ=environ,
        now=datetime.now,
    )
    devices: Devices = NoDevices()
    if config.cleanup_list and config.cleanup_prune:
        devices = CommandDevices(config.cleanup_list, config.cleanup_prune, repo, run)
    improves = (repo / ".ralph" / "IMPROVE.md").exists()
    return Parts(github, session, devices, notifier(config.notify, repo, run), improves)


def branch_prefix(run: Runner, repo: Path) -> str:
    """The repo's git identity: `github.user`, else the email's name without GitHub's `NNN+`."""
    for key in ("github.user", "user.email"):
        done = run(["git", "-C", str(repo), "config", key], capture_output=True, text=True)
        if done.returncode == 0 and done.stdout.strip():
            return re.sub(r"^\d+\+", "", done.stdout.strip().split("@")[0])
    raise ConfigError(
        "Ralph loop can't start: git has no github.user or user.email for this repo. Set one: "
        "git config github.user <login>"
    )


def live(config: Config, repo: Path) -> Parts:
    def sign(data: bytes) -> bytes:
        # The key is read from the keychain only when a token is minted, so a dry run needs none.
        return openssl_signer(keychain_key(config.app_slug, subprocess.run), subprocess.run)(data)

    post = github_post(urllib.request.urlopen)
    tokens = Tokens(config.app_id, config.installation_id, sign, post, time.time)
    gh = gh_runner(subprocess.run)
    return assemble(
        config, repo, gh, subprocess.run, subprocess.Popen, dict(os.environ), tokens=tokens
    )


def main(
    argv: list[str],
    stdin: TextIO = sys.stdin,
    *,
    repo: Path | None = None,
    connect: Callable[[Config, Path], Parts] = live,
) -> int:
    parser = argparse.ArgumentParser(prog="devtool ralph", description=__doc__)
    parser.add_argument("--milestone", type=int, required=True, help="the parent issue")
    parser.add_argument("--dry-run", action="store_true", help="print the next issue and stop")
    parser.add_argument("--max-iterations", type=int, default=3, help="sessions to start at most")
    args = parser.parse_args(argv)
    repo = repo or Path.cwd()
    parts = None
    try:
        config = load(repo / ".ralph" / "config")
        parts = connect(config, repo)
        if args.dry_run:
            print(dry_run(args.milestone, parts.github))
            return 0
        ralph.run(
            args.milestone,
            parts.github,
            parts.session,
            parts.notify,
            time.time,
            time.sleep,
            args.max_iterations,
            improves=parts.improves,
            devices=parts.devices,
        )
    except ConfigError as error:
        print(error)
        return 1
    except (GitHubError, TokenError, DevicesError) as error:
        (parts.notify if parts else print)(f"Ralph loop stopped: {error}")
        return 1
    return 0
