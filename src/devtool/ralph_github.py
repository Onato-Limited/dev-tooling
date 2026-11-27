"""The Ralph loop's GitHub, through `gh`: the board and a milestone's issues."""

import json
import subprocess
from collections.abc import Callable

from devtool.ralph import GitHubError, Issue

Run = Callable[[list[str], str | None], str]
DRAFT = "draft"


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
        """Project numbers are per owner, so both must match."""
        return (
            project["number"] == self.board_number and project["owner"]["login"] == self.board_owner
        )

    def _graphql(self, query: str, **variables: object) -> dict:
        args = ["api", "graphql", "-f", f"query={query}"]
        for name, value in variables.items():
            args += ["-F", f"{name}={value}"]
        return json.loads(self.run(args, None))["data"]
