"""One Ralph loop iteration: a fresh `claude -p` on an issue, in that issue's own worktree."""

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from devtool.ralph import POLL_SECONDS, Issue, Outcome

STREAM = ["--permission-mode", "auto", "--output-format", "stream-json", "--verbose"]


@dataclass
class ClaudeSession:
    repo: Path
    branch_prefix: str
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
            prompt += self._resuming(branch)
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

    def _resuming(self, branch: str) -> str:
        found = self.run(
            ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"]
            + ["--jq", ".[].number"],
            capture_output=True,
            text=True,
            cwd=self.repo,
            env=own_environ(self.environ),
        ).stdout.split()
        pr = f"open PR #{found[0]}" if found else "no open PR"
        return (
            f"\nResuming: an earlier session left branch `{branch}` with {pr}. Before anything"
            " else, read the latest comments on the issue and on the PR: the owner's ruling is"
            " there.\n"
        )

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
        with log.open("w") as out:
            process = self.start(
                ["claude", "-p", prompt, *STREAM],
                cwd=worktree,
                env=own_environ(self.environ),
                stdout=out,
                stderr=subprocess.STDOUT,
                text=True,
            )
            while not exited(process):
                while_running()
        return final_result(log), log


def exited(process: subprocess.Popen) -> bool:
    try:
        process.wait(timeout=POLL_SECONDS)
    except subprocess.TimeoutExpired:
        return False
    return True


def own_environ(environ: dict[str, str]) -> dict[str, str]:
    """Without the CLAUDE* variables a Claude Code parent sets, which make `claude` a child."""
    return {k: v for k, v in environ.items() if not k.startswith("CLAUDE")}


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
