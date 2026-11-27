"""Ralph loop: works through a milestone's issues, one fresh session per issue."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Protocol

TODO, IN_PROGRESS, IN_REVIEW, DONE = "Todo", "In progress", "In review", "Done"
# The owner works these issues themselves; the loop never takes one.
ATTENDED = "Attended"
STUCK_AFTER = 3
POLL_SECONDS = 60
MERGE_TIMEOUT = 3600
# The board only stays true while these GitHub Projects built-in workflows move the cards.
REQUIRED_WORKFLOWS = (
    "Item closed",
    "Pull request merged",
    "Pull request linked to issue",
    "Auto-add to project",
)


@dataclass(frozen=True)
class Issue:
    number: int
    status: str
    is_open: bool = True
    open_blockers: tuple[int, ...] = ()
    draft: bool = False  # proposed for the milestone; waits for the owner's plan-approved


@dataclass(frozen=True)
class PullRequest:
    number: int
    merged: bool = False
    changes_requested: int = 0
    ci_failure_streak: int = 0
    has_retro_notes: bool = True
    behind: bool = False
    branch: str = ""
    unresolved_threads: int = 0
    blocked_with_checks_passing: bool = False  # every check passed, yet GitHub won't merge it


@dataclass(frozen=True)
class Outcome:
    pr: int | None
    needs_human: str | None = None
    also: tuple[int, ...] = ()  # other PRs the session opened, such as a checkpoint's fixes


@dataclass(frozen=True)
class Improvement:
    """What an improver session did: fix PRs it opened, and what it left to the owner."""

    prs: tuple[int, ...] = ()
    proposals: tuple[int, ...] = ()
    needs_human: str | None = None
    left_in_place: str | None = None  # why its worktree could not be removed


class GitHubError(Exception):
    """A GitHub call failed; the message names the command and its error."""


class ReadsBoard(Protocol):
    def board_workflows(self) -> dict[str, bool]: ...
    def milestone_issues(self, milestone: int) -> list[Issue]: ...


class GitHub(ReadsBoard, Protocol):
    def pull_request(self, number: int) -> PullRequest: ...
    def has_open_pr(self, issue: int) -> bool: ...  # one closes it or is on its branch
    def is_open(self, issue: int) -> bool: ...
    def set_status(self, issue: int, status: str) -> None: ...
    def comment(self, issue: int, body: str) -> str: ...  # the comment's URL
    def plan_approved(self, parent: int) -> bool: ...  # the owner added plan-approved last
    def undraft(self, issue: int) -> None: ...


class Session(Protocol):
    # `while_running` is called every POLL_SECONDS until the session ends.
    def work(self, issue: Issue, while_running: Callable[[], None]) -> Outcome: ...
    def remove(self, issue: int) -> str | None: ...  # what failed, if the worktree stays
    def update(self, pr: PullRequest) -> str | None: ...  # merges main in; what failed
    def improve(self, prs: tuple[int, ...], while_running: Callable[[], None]) -> Improvement: ...


class Devices(Protocol):
    def issues(self) -> list[int]: ...  # the issues with a device of their own
    def prune(self, issue: int) -> str | None: ...  # why it stays, if it does


def next_issue(issues: list[Issue]) -> Issue | None:
    """The lowest-numbered open Todo issue with no open blocker that isn't a draft. Only Todo is
    taken, so an Attended issue never is."""
    ready = [
        i for i in issues if i.is_open and i.status == TODO and not i.open_blockers and not i.draft
    ]
    return min(ready, key=lambda i: i.number, default=None)


def stuck_reason(outcome: Outcome, pr: PullRequest | None) -> str | None:
    if outcome.needs_human:
        return f"needs a human: {outcome.needs_human}"
    if pr is None:
        return "no PR opened"
    if pr.changes_requested >= STUCK_AFTER:
        return f"PR #{pr.number}: the review requested changes {pr.changes_requested} times"
    if pr.ci_failure_streak >= STUCK_AFTER:
        return f"PR #{pr.number}: CI failed on {pr.ci_failure_streak} runs in a row"
    return None


def describe(issue: Issue) -> str:
    blocked = ", ".join(f"#{b}" for b in issue.open_blockers)
    draft = ", draft" if issue.draft else ""
    return f"- #{issue.number} {issue.status}{draft}" + (
        f", blocked by {blocked}" if blocked else ""
    )


def workflows_off(github: ReadsBoard) -> list[str]:
    workflows = github.board_workflows()
    return [name for name in REQUIRED_WORKFLOWS if not workflows.get(name)]


def dry_run(milestone: int, github: ReadsBoard) -> str:
    """What the loop would take next, read from GitHub without changing anything."""
    lines = []
    if off := workflows_off(github):
        lines.append(
            f"Board workflows are off: {', '.join(off)}. "
            "Turn them on in the project's Workflows settings."
        )
    issues = github.milestone_issues(milestone)
    issue = next_issue(issues)
    if issue is None:
        lines += ["Nothing to take. Open:", *map(describe, [i for i in issues if i.is_open])]
    else:
        lines.append(f"Next: #{issue.number}")
    return "\n".join(lines)


def run(
    milestone: int,
    github: GitHub,
    session: Session,
    notify: Callable[[str], None],
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    max_iterations: int,
) -> str:
    message = _Loop(milestone, github, session, notify, clock, sleep, max_iterations).run()
    notify(message)
    return message


@dataclass(frozen=True)
class Stop:
    """Why the loop stops; no headline means it ran out of issues it can take."""

    headline: str | None = None


@dataclass
class _Loop:
    milestone: int
    github: GitHub
    session: Session
    notify: Callable[[str], None]
    clock: Callable[[], float]
    sleep: Callable[[float], None]
    max_iterations: int
    iterations: int = 0
    opened: list[int] = field(default_factory=list)
    pending: list[int] = field(default_factory=list)
    stuck: dict[int, str] = field(default_factory=dict)
    fixed: list[int] = field(default_factory=list)
    worktrees: dict[int, int] = field(default_factory=dict)  # a pending PR → its issue
    left_in_place: dict[int, str] = field(default_factory=dict)  # issue → why its worktree stayed
    waiting_since: float | None = None

    def run(self) -> str:
        if off := workflows_off(self.github):
            return (
                f"Ralph loop stopped: board workflows are off: {', '.join(off)}. "
                "Turn them on in the project's Workflows settings."
            )
        while True:
            issues = self.github.milestone_issues(self.milestone)
            self.move_closed_to_done(issues)
            issues = self.release_in_review(issues)
            if (stop := self.step(next_issue(issues))) is not None:
                return self.stop_message(stop.headline)

    def step(self, issue: Issue | None) -> Stop | None:
        self.refresh_pending()
        if issue is None:
            return self.wait_for_merges(Stop())
        if halt := self.halt():
            # No more sessions, but the open PRs still need merging before the loop stops.
            return self.wait_for_merges(Stop(halt))
        self.work(issue)
        return None

    def halt(self) -> str | None:
        if self.iterations == self.max_iterations:
            return f"iteration cap ({self.max_iterations}) reached"
        return None

    def refresh_pending(self) -> None:
        prs = [self.github.pull_request(n) for n in self.pending]
        self.pending = [pr.number for pr in prs if not pr.merged]
        for pr in prs:
            if pr.merged and pr.number in self.worktrees:
                self.remove_worktree(self.worktrees.pop(pr.number))

    def remove_worktree(self, issue: int) -> None:
        if failed := self.session.remove(issue):
            self.left_in_place[issue] = failed

    def move_closed_to_done(self, issues: list[Issue]) -> None:
        for closed in [i for i in issues if not i.is_open and i.status != DONE]:
            self.github.set_status(closed.number, DONE)
            self.fixed.append(closed.number)

    def release_in_review(self, issues: list[Issue]) -> list[Issue]:
        """Moves back to Todo each In-review issue nothing holds; the ones stuck this run stay."""
        released = []
        for issue in issues:
            if self.nothing_holds(issue) and self.cleared(issue.number):
                self.github.set_status(issue.number, TODO)
                self.github.comment(
                    issue.number, "Moved back to Todo by the Ralph loop: nothing holds it any more."
                )
                issue = replace(issue, status=TODO)
            released.append(issue)
        return released

    def cleared(self, issue: int) -> bool:
        """Whether the issue's leftover worktree and branch are gone, so a session can start."""
        if failed := self.session.remove(issue):
            self.left_in_place[issue] = failed
            return False
        return True

    def nothing_holds(self, issue: Issue) -> bool:
        return (
            issue.is_open
            and issue.status == IN_REVIEW
            and not issue.open_blockers
            and issue.number not in self.stuck
            and not self.github.has_open_pr(issue.number)
        )

    def wait_for_merges(self, stop: Stop) -> Stop | None:
        """`stop` once this run's PRs have merged; until then wait, as one may unblock an issue."""
        if not self.pending:
            return stop
        if self.waiting_since is None:
            self.waiting_since = self.clock()
        if self.clock() - self.waiting_since >= MERGE_TIMEOUT:
            prs = ", ".join(f"PR #{n}" for n in self.pending)
            return Stop(f"auto-merge still pending after {MERGE_TIMEOUT // 60} minutes: {prs}")
        self.sleep(POLL_SECONDS)
        return None

    def work(self, issue: Issue) -> None:
        self.waiting_since = None
        self.iterations += 1
        self.github.set_status(issue.number, IN_PROGRESS)
        outcome = self.session.work(issue, lambda: None)
        pr = self.github.pull_request(outcome.pr) if outcome.pr is not None else None
        if pr is not None:
            self.opened.append(pr.number)
        if reason := stuck_reason(outcome, pr):
            self.stuck[issue.number] = reason
            self.github.set_status(issue.number, IN_REVIEW)
            url = self.github.comment(
                issue.number, f"Moved to In review by the Ralph loop: {reason}."
            )
            self.notify(
                f"Ralph loop: #{issue.number} needs you (moved to In review): {reason}.\n{url}"
            )
        elif pr.merged:  # without a PR, stuck_reason gave a reason
            self.remove_worktree(issue.number)
        else:
            self.pending.append(pr.number)
            self.worktrees[pr.number] = issue.number

    def stop_message(self, headline: str | None) -> str:
        still_open = [i for i in self.github.milestone_issues(self.milestone) if i.is_open]
        if headline is None:
            headline = "nothing left to take" if still_open else "milestone complete"
        lines = [f"Ralph loop stopped: {headline}."]
        if still_open:
            lines += ["Open:", *map(describe, still_open)]
        if self.stuck:
            lines += ["Moved to In review:", *(f"- #{n}: {r}" for n, r in self.stuck.items())]
        no_notes = [n for n in self.opened if not self.github.pull_request(n).has_retro_notes]
        if no_notes:
            lines.append("Missing retro notes: " + ", ".join(f"PR #{n}" for n in no_notes))
        if self.fixed:
            moved = ", ".join(f"#{n}" for n in self.fixed)
            lines.append(f"Moved to Done (closed, but the board had not moved them): {moved}")
        if self.left_in_place:
            lines += [
                "Worktrees left in place:",
                *(f"- #{n}: {r}" for n, r in self.left_in_place.items()),
            ]
        return "\n".join(lines)
