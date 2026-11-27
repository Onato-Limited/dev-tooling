"""A fake GitHub for the Ralph loop's tests, held in memory."""

from dataclasses import replace

from devtool.ralph import REQUIRED_WORKFLOWS


class FakeGitHub:
    """Issues by number; each issue's open blockers are worked out from `blocked_by` on read."""

    def __init__(self, issues, blocked_by=None):
        self.issues = {issue.number: issue for issue in issues}
        self.blocked_by = blocked_by or {}
        self.moves = []
        self.comments = []
        self.workflows = {name: True for name in REQUIRED_WORKFLOWS}

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
