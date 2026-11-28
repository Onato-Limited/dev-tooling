"""Ralph loop: works through a milestone's issues, one fresh session per issue."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Protocol

TODO, IN_PROGRESS, IN_REVIEW, DONE = "Todo", "In progress", "In review", "Done"
# The owner works these issues themselves; the loop never takes one.
ATTENDED = "Attended"
STUCK_AFTER = 3
IMPROVE_AFTER = 5  # merged PRs between improver passes
POLL_SECONDS = 60
MERGE_TIMEOUT = 3600
APPROVAL_LABELS = ("size-approved", "review-approved")
# The shape the session prompt gives a stop that waits on one of the owner's approval labels.
APPROVAL_STOP = re.compile(rf"PR #(\d+) needs ({'|'.join(APPROVAL_LABELS)}):")
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
    def label_approved(self, pr: int, label: str) -> bool: ...  # the owner added it last
    def undraft(self, issue: int) -> None: ...
    def arrange_merge(self, pr: int) -> None: ...  # per the project's merge mode
    def failed_audit(self) -> str | None: ...  # main's latest audit run's URL, if it is red
    def policy_violations(self) -> list[int]: ...  # the open policy-violation issues


class Session(Protocol):
    # `while_running` is called every POLL_SECONDS until the session ends.
    def work(self, issue: Issue, while_running: Callable[[], None]) -> Outcome: ...
    def remove(self, issue: int) -> str | None: ...  # what failed, if the worktree stays
    def update(self, pr: PullRequest) -> str | None: ...  # merges main in; what failed
    def improve(self, prs: tuple[int, ...], while_running: Callable[[], None]) -> Improvement: ...


class Devices(Protocol):
    def issues(self) -> list[int]: ...  # the issues with a device of their own
    def prune(self, issue: int) -> str | None: ...  # why it stays, if it does


def branch_name(prefix: str, issue: int) -> str:
    """The branch an issue's sessions work on, and the loop looks for its PR on."""
    return f"{prefix}/{issue}-ralph"


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
    *,
    improves: bool,
    devices: Devices,
) -> str:
    loop = _Loop(
        milestone, github, session, notify, clock, sleep, max_iterations, improves, devices
    )
    message = loop.run()
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
    improves: bool  # the project has an improver prompt
    devices: Devices
    left_running: dict[int, str] = field(default_factory=dict)  # issue → why its device stayed
    iterations: int = 0
    merged: list[int] = field(default_factory=list)  # since the last improver pass
    improvements: list[Improvement] = field(default_factory=list)
    opened: list[int] = field(default_factory=list)
    # This run's open PRs → each one's issue, or None for a session's other PRs.
    pending: dict[int, int | None] = field(default_factory=dict)
    awaiting: dict[int, tuple[int, str]] = field(default_factory=dict)  # PR → its issue, label
    unlabelled: list[str] = field(default_factory=list)  # label waits that ran out
    taken_back: set[tuple[int, str]] = field(default_factory=set)  # (PR, label), once each
    stuck: dict[int, str] = field(default_factory=dict)
    fixed: list[int] = field(default_factory=list)
    left_in_place: dict[int, str] = field(default_factory=dict)  # issue → why its worktree stayed
    errors_while_running: list[str] = field(default_factory=list)
    not_brought_up_to_date: list[str] = field(default_factory=list)  # PRs no issue holds
    held_by_threads: list[str] = field(default_factory=list)  # PRs no issue holds, too
    waiting_since: float | None = None

    def run(self) -> str:
        if off := workflows_off(self.github):
            return (
                f"Ralph loop stopped: board workflows are off: {', '.join(off)}. "
                "Turn them on in the project's Workflows settings."
            )
        if hold := self.audit_hold():
            return f"Ralph loop stopped: NEEDS_HUMAN: {hold}."
        self.approve_drafts()
        while True:
            issues = self.github.milestone_issues(self.milestone)
            self.move_closed_to_done(issues)
            issues = self.release_in_review(issues)
            stop = self.step(next_issue(issues))
            self.prune_devices()
            if stop is not None:
                return self.stop_message(stop.headline)

    def audit_hold(self) -> str | None:
        """Why `main` can't be trusted: it took a push that bypassed the policy."""
        holds = []
        if url := self.github.failed_audit():
            # Most violations fail the same way on a re-run; a newer green run is what lifts it.
            holds.append(
                f"main's latest audit failed: {url}. The loop starts again once main's newest "
                "audit run is green (the next merged PR's, or a re-run of this one)"
            )
        if violations := self.github.policy_violations():
            named = ", ".join(f"#{n}" for n in violations)
            holds.append(f"policy-violation issue {named} is open. Close it once it's dealt with")
        return "; ".join(holds) or None

    def prune_devices(self) -> None:
        """Each issue's device goes once the issue is closed, here or by hand."""
        for issue in self.devices.issues():
            if self.github.is_open(issue):
                continue
            if failed := self.devices.prune(issue):
                self.left_running[issue] = failed  # tried again next pass
            else:
                self.left_running.pop(issue, None)

    def approve_drafts(self) -> None:
        """Once the plan is approved, drafts go to Todo; one the owner holds in Attended stays."""
        drafts = [i for i in self.github.milestone_issues(self.milestone) if i.draft and i.is_open]
        if not drafts or not self.github.plan_approved(self.milestone):
            return
        for draft in drafts:
            self.github.undraft(draft.number)
            if draft.status != ATTENDED:
                self.github.set_status(draft.number, TODO)

    def step(self, issue: Issue | None) -> Stop | None:
        if self.take_back_approved():
            return None  # the issue is Todo again; the next pass reads the board afresh
        self.refresh_pending()
        halt = None if issue is None else self.halt()
        if issue is None or halt:
            # No more sessions, but the open PRs still need merging before the loop stops.
            stop = self.wait_for_merges(Stop(halt))
            if stop == Stop(halt) and self.merged and self.improves:  # merged, not timed out
                self.improve()
                return None  # its PRs need merging before the loop stops
            return stop
        if self.improves and len(self.merged) >= IMPROVE_AFTER:
            self.improve()
            return None
        self.work(issue)
        return None

    def improve(self) -> None:
        self.waiting_since = None  # its PRs get their own hour, as a session's do
        prs, self.merged = tuple(self.merged), []
        improvement = self.session.improve(prs, self.update_behind_pending)
        self.improvements.append(improvement)
        for number in improvement.prs:
            if not self.github.pull_request(number).merged:
                self.wait_on(number, None)
        for proposal in improvement.proposals:
            self.notify(f"Ralph loop: the improver proposes #{proposal}; it needs your decision.")
        if improvement.needs_human:
            self.notify(f"Ralph loop: the improver needs you: {improvement.needs_human}.")

    def halt(self) -> str | None:
        if self.iterations == self.max_iterations:
            return f"iteration cap ({self.max_iterations}) reached"
        return None

    def refresh_pending(self) -> None:
        prs = [self.github.pull_request(n) for n in self.pending]
        for pr in prs:
            if not pr.merged:
                continue
            if pr.number in self.opened:  # the improver's own PRs aren't reviewed again
                self.merged.append(pr.number)
            if (issue := self.pending.pop(pr.number)) is not None:
                self.remove_worktree(issue)
        self.update_behind(prs)
        self.drop_held(prs)

    def take_back_approved(self) -> bool:
        """An issue whose PR got the approval label it stopped for goes back to Todo, so a resumed
        session finishes the PR: it may still need work once the label's check passes."""
        taken = False
        for pr, (issue, label) in list(self.awaiting.items()):
            if not self.github.is_open(issue):  # merged or closed by the owner meanwhile
                del self.awaiting[pr]
                continue
            if not self.github.label_approved(pr, label):
                continue
            del self.awaiting[pr]
            self.taken_back.add((pr, label))
            self.stuck.pop(issue, None)
            self.github.set_status(issue, TODO)
            self.github.comment(
                issue, f"Moved back to Todo by the Ralph loop: {label} was added to PR #{pr}."
            )
            taken = True
        return taken

    def drop_held(self, prs: list[PullRequest]) -> None:
        """Stop waiting for a green PR an unresolved review thread blocks: it never merges."""
        for pr in prs:
            held = pr.blocked_with_checks_passing and pr.unresolved_threads
            if held and pr.number in self.pending:  # not merged, nor dropped for another reason
                count = pr.unresolved_threads
                threads = f"{count} unresolved review thread" + ("" if count == 1 else "s")
                reason = f"PR #{pr.number} is held by {threads}"
                self.stop_waiting(pr.number, reason, self.held_by_threads)

    def update_behind(self, prs: list[PullRequest]) -> None:
        for pr in prs:
            if not pr.merged and pr.behind and (failed := self.session.update(pr)):
                reason = f"PR #{pr.number} can't be brought up to date with main: {failed}"
                self.stop_waiting(pr.number, reason, self.not_brought_up_to_date)

    def stop_waiting(self, pr: int, reason: str, unowned: list[str]) -> None:
        """Drops a pending PR; its issue goes to In review, or `unowned` lists a PR without one."""
        issue = self.pending.pop(pr)
        if issue is None:
            unowned.append(reason)
            self.notify(f"Ralph loop: {reason}.")
            return
        self.needs_owner(issue, reason)

    def needs_owner(self, issue: int, reason: str) -> None:
        self.stuck[issue] = reason
        self.github.set_status(issue, IN_REVIEW)
        url = self.github.comment(issue, f"Moved to In review by the Ralph loop: {reason}.")
        self.notify(f"Ralph loop: #{issue} needs you (moved to In review): {reason}.\n{url}")

    def update_behind_pending(self) -> None:
        """While a session runs: a GitHub error is reported at the end, not raised into it."""
        try:
            self.update_behind([self.github.pull_request(n) for n in self.pending])
        except GitHubError as error:
            self.errors_while_running.append(str(error))

    def wait_on(self, pr: int, issue: int | None) -> None:
        self.github.arrange_merge(pr)
        self.pending[pr] = issue

    def remove_worktree(self, issue: int) -> bool:
        """Whether the issue's worktree and branch are gone; what kept them is reported."""
        if failed := self.session.remove(issue):
            self.left_in_place[issue] = failed
            return False
        return True

    def move_closed_to_done(self, issues: list[Issue]) -> None:
        for closed in [i for i in issues if not i.is_open and i.status != DONE]:
            self.github.set_status(closed.number, DONE)
            self.fixed.append(closed.number)

    def release_in_review(self, issues: list[Issue]) -> list[Issue]:
        """Moves back to Todo each In-review issue nothing holds; the ones stuck this run stay."""
        released = []
        for issue in issues:
            if self.nothing_holds(issue) and self.remove_worktree(issue.number):
                self.github.set_status(issue.number, TODO)
                self.github.comment(
                    issue.number, "Moved back to Todo by the Ralph loop: nothing holds it any more."
                )
                issue = replace(issue, status=TODO)
            released.append(issue)
        return released

    def nothing_holds(self, issue: Issue) -> bool:
        return (
            issue.is_open
            and issue.status == IN_REVIEW
            and not issue.open_blockers
            and issue.number not in self.stuck
            and issue.number not in self.pending.values()
            and not self.github.has_open_pr(issue.number)
        )

    def wait_for_merges(self, stop: Stop) -> Stop | None:
        """`stop` once this run's PRs have merged; until then wait, as one may unblock an issue."""
        if not self.pending and not self.awaiting:
            return stop
        if self.waiting_since is None:
            self.waiting_since = self.clock()
        if self.clock() - self.waiting_since >= MERGE_TIMEOUT:
            # A label wait that runs out leaves the PR with the owner, as before it was watched.
            self.unlabelled += [f"PR #{n} ({label})" for n, (_, label) in self.awaiting.items()]
            self.awaiting.clear()
            if not self.pending:
                return stop
            prs = ", ".join(f"PR #{n}" for n in self.pending)
            return Stop(f"auto-merge still pending after {MERGE_TIMEOUT // 60} minutes: {prs}")
        self.sleep(POLL_SECONDS)
        return None

    def work(self, issue: Issue) -> None:
        self.waiting_since = None
        # The owner may have answered a label wait another way and moved the issue back to Todo.
        self.awaiting = {pr: wait for pr, wait in self.awaiting.items() if wait[0] != issue.number}
        self.iterations += 1
        self.github.set_status(issue.number, IN_PROGRESS)
        outcome = self.session.work(issue, self.update_behind_pending)
        pr = self.github.pull_request(outcome.pr) if outcome.pr is not None else None
        if pr is not None:
            self.opened.append(pr.number)
        self.opened += outcome.also
        if reason := stuck_reason(outcome, pr):
            self.needs_owner(issue.number, reason)  # its PRs, and any others, are the owner's
            self.await_approval(issue.number, outcome)
            return
        merged_others = []
        for other in outcome.also:
            if self.github.pull_request(other).merged:
                merged_others.append(other)
            else:
                self.wait_on(other, None)
        if pr.merged:  # without a PR, stuck_reason gave a reason
            self.merged.append(pr.number)
            self.remove_worktree(issue.number)
        else:
            self.wait_on(pr.number, issue.number)
        self.merged += merged_others

    def await_approval(self, issue: int, outcome: Outcome) -> None:
        """A stop asking for an approval label on the session's own PR: the loop watches for it."""
        stop = APPROVAL_STOP.match(outcome.needs_human or "")
        # A second stop for a label already taken back means the loop and the check disagree.
        if stop and int(stop[1]) == outcome.pr and (outcome.pr, stop[2]) not in self.taken_back:
            self.awaiting[outcome.pr] = (issue, stop[2])

    def stop_message(self, headline: str | None) -> str:
        still_open = [i for i in self.github.milestone_issues(self.milestone) if i.is_open]
        if headline is None and still_open and all(i.draft for i in still_open):
            headline = f"drafts wait for the owner's plan-approved label on #{self.milestone}"
        elif headline is None:
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
        if self.left_running:
            lines += [
                "Devices left running:",
                *(f"- #{n}: {r}" for n, r in self.left_running.items()),
            ]
        if self.errors_while_running:
            lines += [
                "GitHub errors while a session ran:",
                *(f"- {e}" for e in self.errors_while_running),
            ]
        if self.not_brought_up_to_date:
            lines += ["Not brought up to date:", *(f"- {r}" for r in self.not_brought_up_to_date)]
        if self.held_by_threads:
            lines += ["Held by review threads:", *(f"- {r}" for r in self.held_by_threads)]
        if self.unlabelled:
            lines.append(
                "Still without your approval label: " + ", ".join(self.unlabelled) + ". Once "
                "you add it, move the issue back to Todo."
            )
        return "\n".join(lines + self.improver_lines())

    def improver_lines(self) -> list[str]:
        prs = [n for i in self.improvements for n in i.prs]
        proposals = [n for i in self.improvements for n in i.proposals]
        lines = ["Improver PRs: " + ", ".join(f"PR #{n}" for n in prs)] if prs else []
        if proposals:
            lines.append("Improver proposals: " + ", ".join(f"#{n}" for n in proposals))
        lines += [
            f"Improver needs you: {i.needs_human}" for i in self.improvements if i.needs_human
        ]
        lines += [
            f"Improver worktree left in place: {i.left_in_place}"
            for i in self.improvements
            if i.left_in_place
        ]
        return lines
