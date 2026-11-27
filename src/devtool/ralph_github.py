"""The Ralph loop's GitHub, through `gh`: the board, a milestone's issues and PRs."""

import json
import subprocess
from collections.abc import Callable

from devtool.gates import approved_by
from devtool.merge_decision import GREEN, RETRO_HEADING
from devtool.merge_decision import LABEL as MERGE_WHEN_GREEN
from devtool.ralph import GitHubError, Issue, PullRequest

Run = Callable[[list[str], str | None], str]
DRAFT, PLAN_APPROVED = "draft", "plan-approved"
AGENT_MARKER = "> 🤖 Agent comment — written by Claude Code"
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
              contexts(first: 100) { nodes { ... on CheckRun { name conclusion startedAt } } }
            }
          }
        }
      }
    }
  }
}
"""


STATUS_LOOKUP_QUERY = """
query($board_owner: String!, $owner: String!, $repo: String!, $number: Int!, $project: Int!) {
  repositoryOwner(login: $board_owner) {
    ... on ProjectV2Owner {
      projectV2(number: $project) {
        id
        field(name: "Status") { ... on ProjectV2SingleSelectField { id options { id name } } }
      }
    }
  }
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      id
      projectItems(first: 20) {
        nodes {
          id
          project { number owner { ... on User { login } ... on Organization { login } } }
        }
      }
    }
  }
}
"""

SET_STATUS_MUTATION = """
mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $project, itemId: $item, fieldId: $field,
    value: { singleSelectOptionId: $option }
  }) { projectV2Item { id } }
}
"""

ADD_ITEM_MUTATION = """
mutation($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: { projectId: $project, contentId: $content }) { item { id } }
}
"""

MAIN_AUDIT_QUERY = """
query($owner: String!, $repo: String!) {
  repository(owner: $owner, name: $repo) {
    defaultBranchRef { target { ... on Commit { history(first: 20) { nodes {
      statusCheckRollup { contexts(first: 100) { nodes {
        ... on CheckRun { name conclusion startedAt detailsUrl }
      } } }
    } } } } }
  }
}
"""

OPEN_PRS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $branch: String!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      closedByPullRequestsReferences(first: 20, includeClosedPrs: false) { nodes { state } }
    }
    pullRequests(headRefName: $branch, states: OPEN) { totalCount }
  }
}
"""


class GitHubAdapter:
    """`repo` is `owner/name`; the board is the project `board_number` of `board_owner`, a user
    or an organization that need not own the repo."""

    def __init__(
        self,
        repo: str,
        board_owner: str,
        board_number: int,
        review_check: str,
        run: Run,
        *,
        branch_prefix: str,
        approver: str,
        merge_mode: str,
        audit_check: str,
    ):
        self.owner, self.repo = repo.split("/")
        self.board_owner = board_owner
        self.board_number = board_number
        self.review_check = review_check
        self.run = run
        self.branch_prefix = branch_prefix
        self.approver = approver
        self.merge_mode = merge_mode
        self.audit_check = audit_check

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
            has_retro_notes=any(
                line.strip() == RETRO_HEADING
                for comment in pr["comments"]["nodes"]
                for line in comment["body"].splitlines()
            ),
            behind=pr["mergeStateStatus"] == "BEHIND",
            branch=pr["headRefName"],
            unresolved_threads=sum(
                not thread["isResolved"] for thread in pr["reviewThreads"]["nodes"]
            ),
            blocked_with_checks_passing=pr["mergeStateStatus"] == "BLOCKED"
            and all_passed(commits[-1]),
        )

    def has_open_pr(self, issue: int) -> bool:
        """`includeClosedPrs: false` still lists merged PRs, so their state is checked here."""
        data = self._graphql(
            OPEN_PRS_QUERY,
            owner=self.owner,
            repo=self.repo,
            number=issue,
            branch=f"{self.branch_prefix}/{issue}-ralph",
        )["repository"]
        closing = data["issue"]["closedByPullRequestsReferences"]["nodes"]
        return data["pullRequests"]["totalCount"] > 0 or any(
            pr["state"] == "OPEN" for pr in closing
        )

    def is_open(self, issue: int) -> bool:
        args = [
            "issue",
            "view",
            str(issue),
            "-R",
            self._repo(),
            "--json",
            "state",
            "--jq",
            ".state",
        ]
        return self.run(args, None).strip() == "OPEN"

    def set_status(self, issue: int, status: str) -> None:
        data = self._graphql(
            STATUS_LOOKUP_QUERY,
            board_owner=self.board_owner,
            owner=self.owner,
            repo=self.repo,
            number=issue,
            project=self.board_number,
        )
        board = data["repositoryOwner"]["projectV2"]
        field = board["field"]
        option = next((o["id"] for o in field["options"] if o["name"] == status), None)
        if option is None:
            raise GitHubError(
                f"project {self.board_number} of {self.board_owner} has no Status option "
                f"{status!r}. Add it to the board's Status field."
            )
        content = data["repository"]["issue"]
        items = content["projectItems"]["nodes"]
        item = next((i["id"] for i in items if self._on_board(i["project"])), None)
        if item is None:
            added = self._graphql(ADD_ITEM_MUTATION, project=board["id"], content=content["id"])
            item = added["addProjectV2ItemById"]["item"]["id"]
        self._graphql(
            SET_STATUS_MUTATION, project=board["id"], item=item, field=field["id"], option=option
        )

    def comment(self, issue: int, body: str) -> str:
        args = ["issue", "comment", str(issue), "-R", self._repo(), "--body-file", "-"]
        return self.run(args, f"{AGENT_MARKER}\n\n{body}").strip()

    def plan_approved(self, parent: int) -> bool:
        """Only the login running the loop approves: a bot or a collaborator adding it doesn't."""
        path = f"repos/{self._repo()}/issues/{parent}/events"
        lines = self.run(["api", "--paginate", path, "--jq", ".[]"], None).splitlines()
        return approved_by([json.loads(line) for line in lines], PLAN_APPROVED, self.approver)

    def undraft(self, issue: int) -> None:
        self.run(["issue", "edit", str(issue), "-R", self._repo(), "--remove-label", DRAFT], None)

    def arrange_merge(self, pr: int) -> None:
        if self.merge_mode == "label":
            args = ["pr", "edit", str(pr), "-R", self._repo(), "--add-label", MERGE_WHEN_GREEN]
        else:
            args = ["pr", "merge", str(pr), "-R", self._repo(), "--auto", "--squash"]
        self.run(args, None)

    def failed_audit(self) -> str | None:
        """The newest completed audit run on main's latest commits, by its URL if it failed. A
        repo without one (its main is protected instead) has nothing to fail."""
        data = self._graphql(MAIN_AUDIT_QUERY, owner=self.owner, repo=self.repo)
        for commit in data["repository"]["defaultBranchRef"]["target"]["history"]["nodes"]:
            rollup = commit["statusCheckRollup"]
            nodes = rollup["contexts"]["nodes"] if rollup else []
            done = [n for n in nodes if n.get("name") == self.audit_check and n["conclusion"]]
            if done:
                newest = max(done, key=lambda run: run["startedAt"])
                return None if newest["conclusion"] in GREEN else newest["detailsUrl"]
        return None

    def policy_violations(self) -> list[int]:
        args = ["issue", "list", "-R", self._repo(), "--label", "policy-violation"]
        args += ["--state", "open", "--json", "number", "--jq", ".[].number"]
        return [int(n) for n in self.run(args, None).split()]

    def _repo(self) -> str:
        return f"{self.owner}/{self.repo}"

    def _graphql(self, query: str, **variables: object) -> dict:
        args = ["api", "graphql", "-f", f"query={query}"]
        for name, value in variables.items():
            # -F would send a login such as 2048 as a number; only ints go typed.
            args += ["-F" if isinstance(value, int) else "-f", f"{name}={value}"]
        return json.loads(self.run(args, None))["data"]


def check_runs(commit: dict) -> dict[str, str]:
    """Each check's newest conclusion; a commit pushed with others may have no runs (a null
    rollup). GitHub lists a re-run check's runs in no time order, and a queued run has no
    start yet, which makes it the newest."""
    rollup = commit["statusCheckRollup"]
    nodes = rollup["contexts"]["nodes"] if rollup else []
    runs = sorted((run for run in nodes if "name" in run), key=lambda run: run["startedAt"] or "~")
    return {run["name"]: run["conclusion"] for run in runs}


def all_passed(runs: dict[str, str]) -> bool:
    """Whether a commit has check runs and each one's newest run passed (or was skipped)."""
    return bool(runs) and all(conclusion in GREEN for conclusion in runs.values())


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
