"""The Ralph loop's GitHub, through `gh`: the board, a milestone's issues and PRs."""

import json
import subprocess
from collections.abc import Callable

from devtool.ralph import GitHubError, Issue, PullRequest

Run = Callable[[list[str], str | None], str]
DRAFT = "draft"
FAILURE, SUCCESS = "FAILURE", "SUCCESS"


def gh_runner(subprocess_run: Callable[..., subprocess.CompletedProcess]) -> Run:
    def run(args: list[str], stdin: str | None) -> str:
        result = subprocess_run(["gh", *args], input=stdin, capture_output=True, text=True)
        if result.returncode != 0:
            command = " ".join(arg.partition("\n")[0] + "…" if "\n" in arg else arg for arg in args)
            raise GitHubError(f"gh {command}: {result.stderr.strip()}")
        return result.stdout

    return run


MILESTONE_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      subIssues(first: 100) {
        nodes {
          number
          state
          labels(first: 20) { nodes { name } }
          blockedBy(first: 20) { nodes { number state } }
          projectItems(first: 5) {
            nodes {
              project { number owner { ... on User { login } ... on Organization { login } } }
              fieldValueByName(name: "Status") {
                ... on ProjectV2ItemFieldSingleSelectValue { name }
              }
            }
          }
        }
      }
    }
  }
}
"""

# repositoryOwner takes a user or an organization; both are ProjectV2Owners.
WORKFLOWS_QUERY = """
query($owner: String!, $project: Int!) {
  repositoryOwner(login: $owner) {
    ... on ProjectV2Owner {
      projectV2(number: $project) { workflows(first: 20) { nodes { name enabled } } }
    }
  }
}
"""


PULL_REQUEST_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      merged
      mergeStateStatus
      headRefName
      reviewThreads(first: 100) { nodes { isResolved } }
      comments(last: 100) { nodes { body } }
      commits(last: 100) {
        nodes {
          commit {
            statusCheckRollup {
              contexts(first: 50) { nodes { ... on CheckRun { name conclusion } } }
            }
          }
        }
      }
    }
  }
}
"""


class GitHubAdapter:
    """`repo` is `owner/name`; the board is the project `board_number` of `board_owner`, a user
    or an organization that need not own the repo."""

    def __init__(self, repo: str, board_owner: str, board_number: int, review_check: str, run: Run):
        self.owner, self.repo = repo.split("/")
        self.board_owner = board_owner
        self.board_number = board_number
        self.review_check = review_check
        self.run = run

    def board_workflows(self) -> dict[str, bool]:
        data = self._graphql(WORKFLOWS_QUERY, owner=self.board_owner, project=self.board_number)
        if data["repositoryOwner"] is None:  # an unknown login is null, not an error
            raise GitHubError(
                f"GitHub has no user or organization {self.board_owner}. Set board_owner in "
                f".ralph/config to the login that owns project {self.board_number}."
            )
        nodes = data["repositoryOwner"]["projectV2"]["workflows"]["nodes"]
        return {node["name"]: node["enabled"] for node in nodes}

    def milestone_issues(self, milestone: int) -> list[Issue]:
        data = self._graphql(MILESTONE_QUERY, owner=self.owner, repo=self.repo, number=milestone)
        return [self._issue(node) for node in data["repository"]["issue"]["subIssues"]["nodes"]]

    def _issue(self, node: dict) -> Issue:
        return Issue(
            number=node["number"],
            status=self._status(node["projectItems"]["nodes"]),
            is_open=node["state"] == "OPEN",
            open_blockers=tuple(
                b["number"] for b in node["blockedBy"]["nodes"] if b["state"] == "OPEN"
            ),
            draft=any(label["name"] == DRAFT for label in node["labels"]["nodes"]),
        )

    def _status(self, items: list[dict]) -> str:
        for item in items:
            if self._on_board(item["project"]) and item["fieldValueByName"]:
                return item["fieldValueByName"]["name"]
        return "No status"

    def _on_board(self, project: dict) -> bool:
        """Project numbers are per owner, so both must match; logins match in any case."""
        return (
            project["number"] == self.board_number
            and project["owner"]["login"].casefold() == self.board_owner.casefold()
        )

    def pull_request(self, number: int) -> PullRequest:
        data = self._graphql(PULL_REQUEST_QUERY, owner=self.owner, repo=self.repo, number=number)
        pr = data["repository"]["pullRequest"]
        commits = [check_runs(node["commit"]) for node in pr["commits"]["nodes"]]
        review = self.review_check
        ci = [ci_conclusion(runs, review) for runs in commits if set(runs) - {review}]
        return PullRequest(
            number=number,
            merged=pr["merged"],
            changes_requested=sum(runs.get(review) == FAILURE for runs in commits),
            ci_failure_streak=trailing_failures(ci),
            has_retro_notes=any("## Retro notes" in c["body"] for c in pr["comments"]["nodes"]),
            behind=pr["mergeStateStatus"] == "BEHIND",
            branch=pr["headRefName"],
            unresolved_threads=sum(
                not thread["isResolved"] for thread in pr["reviewThreads"]["nodes"]
            ),
            blocked_with_checks_passing=pr["mergeStateStatus"] == "BLOCKED"
            and all_passed(commits[-1]),
        )

    def _graphql(self, query: str, **variables: object) -> dict:
        args = ["api", "graphql", "-f", f"query={query}"]
        for name, value in variables.items():
            # -F would send a login such as 2048 as a number; only ints go typed.
            args += ["-F" if isinstance(value, int) else "-f", f"{name}={value}"]
        return json.loads(self.run(args, None))["data"]


def check_runs(commit: dict) -> dict[str, str]:
    """Conclusion by check name; a commit pushed with others may have no runs (a null rollup)."""
    rollup = commit["statusCheckRollup"]
    nodes = rollup["contexts"]["nodes"] if rollup else []
    return {run["name"]: run["conclusion"] for run in nodes if "name" in run}


def all_passed(runs: dict[str, str]) -> bool:
    """Whether a commit has check runs and each one's latest run succeeded."""
    return bool(runs) and all(conclusion == SUCCESS for conclusion in runs.values())


def trailing_failures(conclusions: list[str]) -> int:
    streak = 0
    for conclusion in reversed(conclusions):
        if conclusion != FAILURE:
            break
        streak += 1
    return streak


def ci_conclusion(runs: dict[str, str], review: str) -> str:
    """A commit's CI: failed when any check but the review failed."""
    others = [conclusion for name, conclusion in runs.items() if name != review]
    return FAILURE if FAILURE in others else SUCCESS
