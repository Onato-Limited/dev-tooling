"""Fakes for the Ralph loop's tests: GitHub, a session and a clock, held in memory."""

from dataclasses import replace

from devtool.ralph import REQUIRED_WORKFLOWS, GitHubError, Improvement, PullRequest


class FakeGitHub:
    """Issues by number; each issue's open blockers are worked out from `blocked_by` on read."""

    def __init__(self, issues, blocked_by=None):
        self.issues = {issue.number: issue for issue in issues}
        self.blocked_by = blocked_by or {}
        self.moves = []
        self.comments = []
        self.workflows = {name: True for name in REQUIRED_WORKFLOWS}
        self.prs = {}
        self.open_prs = set()  # issues an open PR closes or works on
        self.updates = []  # PRs brought up to date with main, in order
        self.pr_fails = []  # errors the next PR reads raise, one each
        self.arranged = []  # PRs whose merge the loop arranged, in order
        self.approved = False  # the owner's plan-approved label on the milestone's parent
        self.undrafted = []
        self.plan_asked = 0
        self.audit_failed = None  # the URL of main's latest audit run, if it is red
        self.violations = []  # open policy-violation issues

    def board_workflows(self):
        return self.workflows

    def milestone_issues(self, milestone):
        assert milestone == 2
        is_open = [n for n, issue in self.issues.items() if issue.is_open]
        return [
            replace(
                issue, open_blockers=tuple(b for b in self.blocked_by.get(n, ()) if b in is_open)
            )
            for n, issue in sorted(self.issues.items())
        ]

    def pull_request(self, number):
        if self.pr_fails:
            raise GitHubError(self.pr_fails.pop(0))
        return self.prs[number]

    def arrange_merge(self, pr):
        self.arranged.append(pr)

    def has_open_pr(self, issue):
        return issue in self.open_prs

    def is_open(self, issue):
        return self.issues[issue].is_open

    def failed_audit(self):
        return self.audit_failed

    def policy_violations(self):
        return self.violations

    def plan_approved(self, parent):
        assert parent == 2
        self.plan_asked += 1
        return self.approved

    def undraft(self, issue):
        self.undrafted.append(issue)
        self.issues[issue] = replace(self.issues[issue], draft=False)

    def set_status(self, issue, status):
        self.moves.append((issue, status))
        self.issues[issue] = replace(self.issues[issue], status=status)

    def comment(self, issue, body):
        self.comments.append((issue, body))
        return f"https://github.com/owner/repo/issues/{issue}#issuecomment-1"

    def merge(self, issue, pr):
        self.issues[issue] = replace(self.issues[issue], is_open=False, status="Done")
        self.prs[pr] = replace(self.prs[pr], merged=True)


class FakeSession:
    """Each issue's session opens `pr` and, unless `pending`, has it merged at once."""

    def __init__(self, github, runs):
        self.github = github
        self.runs = runs
        self.worked = []
        self.status_at_start = []
        self.removed = []
        self.removal_fails = {}  # issue → what failed
        self.while_running = {}  # issue → what happens on GitHub while its session runs
        self.update_fails = {}  # PR → why it couldn't be brought up to date
        # issue → the PRs updated by the time its session ended; "improve" → one list per run
        self.updates_at_end = {}
        self.improved = []  # (the PRs it was given, how many issues had been worked by then)
        self.improvement = Improvement()
        self.improver_merges = True  # whether the improver's PRs merge at once

    def remove(self, issue):
        self.removed.append(issue)
        return self.removal_fails.get(issue)

    def update(self, pr):
        if failed := self.update_fails.get(pr.number):
            return failed
        self.github.updates.append(pr.number)
        self.github.prs[pr.number] = replace(pr, behind=False)
        return None

    def improve(self, prs, while_running):
        self.improved.append((prs, len(self.worked)))
        self.while_running.pop("improve", lambda: None)()
        while_running()
        self.updates_at_end.setdefault("improve", []).append(list(self.github.updates))
        for number in self.improvement.prs:
            self.github.prs[number] = PullRequest(number=number, merged=self.improver_merges)
        return self.improvement

    def work(self, issue, while_running):
        self.worked.append(issue.number)
        self.status_at_start.append(self.github.issues[issue.number].status)
        self.while_running.get(issue.number, lambda: None)()
        while_running()
        self.updates_at_end[issue.number] = list(self.github.updates)
        outcome, pr, pending = self.runs[issue.number]
        if pr is not None:
            self.github.prs[pr.number] = pr
            if not pending:
                self.github.merge(issue.number, pr.number)
        return outcome


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
        self.on_sleep = lambda: None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        self.on_sleep()


class FakeDevices:
    """Issues with a device of their own; a prune in `fails` leaves it and says why."""

    def __init__(self, issues=(), fails=None):
        self.held = list(issues)
        self.fails = fails or {}
        self.fails_once = {}  # issue → why its first prune fails; a later one works
        self.pruned = []

    def issues(self):
        return list(self.held)

    def prune(self, issue):
        if failed := self.fails.get(issue) or self.fails_once.pop(issue, None):
            return failed
        self.pruned.append(issue)
        self.held.remove(issue)
        return None
