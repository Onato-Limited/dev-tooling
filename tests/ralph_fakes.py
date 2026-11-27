"""Fakes for the Ralph loop's tests: GitHub, sessions and devices, held in memory."""

from dataclasses import replace

from devtool.ralph import REQUIRED_WORKFLOWS, GitHubError


class FakeGitHub:
    """Issues by number; each issue's open blockers are worked out from `blocked_by` on read."""

    def __init__(self, issues, blocked_by=None):
        self.issues = {issue.number: issue for issue in issues}
        self.blocked_by = blocked_by or {}
        self.prs = {}
        self.moves = []
        self.comments = []
        self.updates = []  # PRs brought up to date with main, in order
        self.pr_fails = []  # errors the next PR reads raise, one each
        self.workflows = {name: True for name in REQUIRED_WORKFLOWS}
        self.approved = False  # the owner's plan-approved label on the milestone's parent
        self.undrafted = []
        self.open_prs = set()  # issues an open PR closes or works on

    def plan_approved(self, parent):
        assert parent == 2
        return self.approved

    def undraft(self, issue):
        self.undrafted.append(issue)
        self.issues[issue] = replace(self.issues[issue], draft=False)

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

    def has_open_pr(self, issue):
        return issue in self.open_prs

    def set_status(self, issue, status):
        self.moves.append((issue, status))
        self.issues[issue] = replace(self.issues[issue], status=status)

    def comment(self, issue, body):
        self.comments.append((issue, body))
        return f"https://github.com/owner/repo/issues/{issue}#issuecomment-1"

    def is_open(self, issue):
        return self.issues[issue].is_open

    def merge(self, issue, pr):
        self.issues[issue] = replace(self.issues[issue], is_open=False, status="Done")
        self.prs[pr] = replace(self.prs[pr], merged=True)
