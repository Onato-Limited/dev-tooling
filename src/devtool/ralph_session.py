"""One Ralph loop iteration: a fresh `claude -p` on an issue, in that issue's own worktree."""

import contextlib
import json
import re
import shlex
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from devtool.ralph import POLL_SECONDS, Issue, Outcome
from devtool.ralph_token import TokenError, TokenFile

STREAM = ["--permission-mode", "auto", "--output-format", "stream-json", "--verbose"]


# What a Claude Code parent sets in its children's environment, which would make `claude` one of
# them. Only these go: the rest of CLAUDE* (config dir, provider, login) is the user's.
PARENT_MARKERS = frozenset(
    [
        "CLAUDECODE",
        "CLAUDE_CODE_CHILD_NOTES_DIR",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_EXECPATH",
        "CLAUDE_CODE_MESSAGING_SOCKET",
        "CLAUDE_CODE_MESSAGING_TOKEN",
        "CLAUDE_CODE_SESSION_ATTENDED",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_EFFORT",
        "CLAUDE_PID",
    ]
)


class SessionTokens(Protocol):
    def session_env(self, environ: dict[str, str]) -> dict[str, str]: ...

    def current(self) -> str: ...


@dataclass(frozen=True)
class Bot:
    """The GitHub App's bot user, from the config's `app_slug` and the bot's user ID."""

    app_slug: str
    user_id: int

    @property
    def login(self) -> str:
        return f"{self.app_slug}[bot]"

    def git_env(self) -> dict[str, str]:
        """Commits as the bot, pushed over HTTPS with the token file's token: an SSH remote would
        push with the owner's key, and the helper list is emptied first
        so the keychain's owner credentials aren't offered."""
        email = f"{self.user_id}+{self.login}@users.noreply.github.com"
        return {
            "GIT_AUTHOR_NAME": self.login,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": self.login,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "url.https://github.com/.insteadOf",
            "GIT_CONFIG_VALUE_0": "git@github.com:",
            "GIT_CONFIG_KEY_1": "credential.https://github.com.helper",
            "GIT_CONFIG_VALUE_1": "",
            "GIT_CONFIG_KEY_2": "credential.https://github.com.helper",
            "GIT_CONFIG_VALUE_2": "ralph",  # git-credential-ralph
        }


@dataclass
class ClaudeSession:
    repo: Path
    branch_prefix: str
    bot: Bot
    tokens: SessionTokens
    session_path: str | None
    run: Callable[..., subprocess.CompletedProcess]
    start: Callable[..., subprocess.Popen]
    environ: dict[str, str]
    now: Callable[[], datetime]

    def work(self, issue: Issue, while_running: Callable[[], None]) -> Outcome:
        branch, worktree = self._branch(issue.number), self._worktree(issue.number)
        resuming, failed = self._open_worktree(worktree, branch)
        if failed:
            return Outcome(pr=None, needs_human=failed)
        prompt = (self.repo / ".ralph" / "PROMPT.md").read_text()
        prompt = prompt.replace("{issue}", str(issue.number)).replace("{branch}", branch)
        if resuming:
            note, failed = self._resuming(branch)
            if failed:
                return Outcome(pr=None, needs_human=failed)
            prompt += note
        text, log = self._claude(prompt, worktree, str(issue.number), while_running)
        if text is None:
            return Outcome(pr=None, needs_human=f"the session ended without a result; see {log}")
        return outcome(text)

    def _open_worktree(self, worktree: Path, branch: str) -> tuple[bool, str | None]:
        """Whether an earlier session's worktree or branch is still there, and what failed, if the
        worktree couldn't be had. A worktree still there is reused; else one is added on the
        branch, locally or on `origin` (git checks the remote one out as a tracking branch), else
        on a new branch cut from `origin/main`."""
        if failed := self._git(["fetch", "origin"]):
            return False, failed
        if worktree.exists():
            return True, None
        resuming = self._has_branch(branch)
        start = [branch] if resuming else ["-b", branch, "origin/main"]
        return resuming, self._git(["worktree", "add", str(worktree), *start])

    def _has_branch(self, branch: str) -> bool:
        refs = [f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"]
        found = self.run(
            ["git", "-C", str(self.repo), "for-each-ref", "--format=%(refname)", *refs],
            capture_output=True,
            text=True,
        ).stdout
        return bool(found.strip())

    def _resuming(self, branch: str) -> tuple[str, str | None]:
        """The prompt's note that the session resumes, or what failed in looking for its PR."""
        try:
            env = self.tokens.session_env(own_environ(self.environ))
        except TokenError as error:
            return "", f"the bot's token: {error}"
        done = self.run(
            ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"]
            + ["--jq", ".[].number"],
            capture_output=True,
            text=True,
            cwd=self.repo,
            env=env,
        )
        if done.returncode != 0:
            return "", f"gh pr list failed: {done.stderr.strip()}"
        found = done.stdout.split()
        pr = f"open PR #{found[0]}" if found else "no open PR"
        return (
            f"\nResuming: an earlier session left branch `{branch}` with {pr}. Before anything"
            " else, read the latest comments on the issue and on the PR: the owner's ruling is"
            " there.\n"
        ), None

    def _branch(self, issue: int) -> str:
        return f"{self.branch_prefix}/{issue}-ralph"

    def _worktree(self, issue: int) -> Path:
        return self.repo.parent / f"{self.repo.name}-{issue}"

    def _git(self, step: list[str]) -> str | None:
        """What failed, or None if it ran."""
        done = self.run(["git", "-C", str(self.repo), *step], capture_output=True, text=True)
        if done.returncode != 0:
            return f"git {' '.join(step[:2])} failed: {done.stderr.strip()}"
        return None

    def _claude(
        self, prompt: str, worktree: Path, name: str, while_running: Callable[[], None]
    ) -> tuple[str | None, Path]:
        """The session's final result, if it reached one, and its log."""
        logs = self.repo / ".ralph" / "logs"
        logs.mkdir(exist_ok=True)
        log = logs / f"{self.now():%Y%m%d-%H%M%S}-{name}.log"
        with tempfile.TemporaryDirectory() as tmp, log.open("w") as out:
            private = Path(tmp)
            token = TokenFile(private / "token", self.tokens.current, self.now)
            environ = with_session_path(self.session_path, without_token(own_environ(self.environ)))
            process = self.start(
                ["claude", "-p", prompt, *STREAM],
                cwd=worktree,
                env={
                    **with_token_file(private / "bin", environ),
                    **self.bot.git_env(),
                    "RALPH_TOKEN_FILE": str(token.path),
                },
                stdout=out,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                while not exited(process):
                    with contextlib.suppress(TokenError):  # the old token has a while left
                        token.refresh()
                    while_running()
            finally:
                process.kill()  # before its token and tools go with the directory
                process.wait()
        return final_result(log), log


def exited(process: subprocess.Popen) -> bool:
    try:
        process.wait(timeout=POLL_SECONDS)
    except subprocess.TimeoutExpired:
        return False
    return True


def own_environ(environ: dict[str, str]) -> dict[str, str]:
    """Without the CLAUDE* variables a Claude Code parent sets, which make `claude` a child."""
    return {k: v for k, v in environ.items() if k not in PARENT_MARKERS}


def without_token(environ: dict[str, str]) -> dict[str, str]:
    """Without the owner's GH_TOKEN: the session's `gh` reads the bot's from its token file."""
    return {k: v for k, v in environ.items() if k != "GH_TOKEN"}


def with_session_path(session_path: str | None, environ: dict[str, str]) -> dict[str, str]:
    if session_path is None:
        return environ
    return {**environ, "PATH": f"{session_path}:{environ['PATH']}"}


def with_token_file(bin: Path, environ: dict[str, str]) -> dict[str, str]:
    """With a `gh` first on the PATH that runs the real one with the token file's token, and
    the git credential helper that answers with it."""
    bin.mkdir()
    path = shlex.quote(environ["PATH"])
    script(bin / "gh", f'GH_TOKEN=$(cat "$RALPH_TOKEN_FILE") PATH={path} exec gh "$@"')
    script(
        bin / "git-credential-ralph",
        'test "$1" = get || exit 0\n'
        "echo username=x-access-token\n"
        'echo "password=$(cat "$RALPH_TOKEN_FILE")"',
    )
    return {**environ, "PATH": f"{bin}:{environ['PATH']}"}


def script(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o700)


def final_result(log: Path) -> str | None:
    """The last result's text; stderr shares the log, and a killed session cuts its last line."""
    for line in reversed([line for line in log.read_text().splitlines() if line[:1] == "{"]):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            return event.get("result")
    return None


def outcome(text: str) -> Outcome:
    prs = numbers("PR", text)
    human = re.search(r"^NEEDS_HUMAN: (.+?)\s*$", text, re.MULTILINE)
    return Outcome(
        pr=prs[0] if prs else None,
        needs_human=human.group(1) if human else None,
        also=prs[1:],
    )


def numbers(marker: str, text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(rf"^{marker}: #(\d+)\s*$", text, re.MULTILINE))
