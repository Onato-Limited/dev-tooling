"""Merge each open PR labelled merge-when-green once it is green, or update its branch."""

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from devtool.merge_decision import LABEL, PullRequest, UpdateBranch, Wait, merge_decision
from devtool.required_checks import parse_required
from devtool.review_threads import Query, QueryError, blocking, gh_graphql, threads

REFUSED_FIX = (
    "  The app's token needs read and write access to Contents and Pull requests here. A push "
    "after this run read the PR is refused too; the next run tries again."
)

QUERY = """
query($owner: String!, $name: String!, $label: String!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, labels: [$label], first: 100) {
      nodes {
        number title body isDraft mergeable headRefOid baseRefName
        labels(first: 100) { nodes { name } }
        comments(last: 100) { nodes { body } }
        commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) { nodes {
          __typename
          ... on StatusContext { context state }
        } } } } } }
      }
    }
  }
}
"""


class GitHubError(Exception):
    """A GitHub REST call failed; the message is what `gh` said."""


Rest = Callable[..., dict]


def gh_rest(method: str, path: str, fields: dict | None = None, run=subprocess.run) -> dict:
    """A REST call through `gh` and its GH_TOKEN."""
    args = ["gh", "api", "-X", method, path]
    for key, value in (fields or {}).items():
        # -f sends the value raw; -F would read a body starting with @ as a file name.
        args += ["-f", f"{key}={value}"]
    result = run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise GitHubError(result.stderr.strip())
    return json.loads(result.stdout)


def check_runs(repo: str, sha: str, rest: Rest) -> list[dict]:
    """The latest attempt of each check suite's runs on a commit."""
    runs, page = [], 1
    while True:
        path = f"repos/{repo}/commits/{sha}/check-runs?filter=latest&per_page=100&page={page}"
        found = rest("GET", path)
        runs += found["check_runs"]
        if len(runs) >= found["total_count"]:
            return runs
        page += 1


def checks(repo: str, node: dict, rest: Rest) -> dict[str, str]:
    """Each check's state on the head, the newest run winning when one name ran more than once."""
    # Check runs come from REST, not the rollup: the rollup leaves out re-runs, so a cancelled
    # duplicate there can hide the re-run that passed.
    # A commit nothing has reported on yet has no rollup at all.
    rollup = node["commits"]["nodes"][0]["commit"]["statusCheckRollup"]
    contexts = rollup["contexts"]["nodes"] if rollup else []
    states = {
        context["context"]: context["state"]
        for context in contexts
        if context["__typename"] == "StatusContext"
    }
    return states | newest_states(check_runs(repo, node["headRefOid"], rest))


def newest_states(runs: list[dict]) -> dict[str, str]:
    """Each check run's state by name, the newest run winning when one name ran more than once."""
    states = {}
    # A run that hasn't started yet has no started_at, and is the newest.
    for run in sorted(runs, key=lambda run: (run["started_at"] is None, run["started_at"] or "")):
        done = run["status"] == "completed"
        states[run["name"]] = run["conclusion"].upper() if done else "PENDING"
    return states


def snapshot(repo: str, node: dict, query: Query, rest: Rest) -> PullRequest:
    compare = rest("GET", f"repos/{repo}/compare/{node['baseRefName']}...{node['headRefOid']}")
    return PullRequest(
        number=node["number"],
        title=node["title"],
        body=node["body"],
        head_sha=node["headRefOid"],
        labels=[label["name"] for label in node["labels"]["nodes"]],
        draft=node["isDraft"],
        mergeable=node["mergeable"],
        behind=compare["behind_by"] > 0,
        checks=checks(repo, node, rest),
        unresolved_threads=sum(blocking(thread) for thread in threads(repo, node["number"], query)),
        comments=[comment["body"] for comment in node["comments"]["nodes"]],
    )


def main(
    argv: list[str], stdin: TextIO = sys.stdin, query: Query = gh_graphql, rest: Rest = gh_rest
) -> int:
    parser = argparse.ArgumentParser(prog="devtool merge-when-green", description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/name of the repository")
    parser.add_argument(
        "--required-checks", required=True, help="the default branch's .github/required-checks"
    )
    parser.add_argument(
        "--require-retro-notes", action="store_true", help="wait for a `## Retro notes` comment"
    )
    args = parser.parse_args(argv)

    owner, name = args.repo.split("/", 1)
    try:
        data = query(QUERY, {"owner": owner, "name": name, "label": LABEL})
    except QueryError as error:
        print(f"merge-when-green: can't list the open PRs: {error}")
        print(REFUSED_FIX)
        return 1
    prs = data["repository"]["pullRequests"]["nodes"]
    if not prs:
        print(f"merge-when-green: no open PRs labelled {LABEL}.")
        return 0
    required = parse_required(Path(args.required_checks).read_text())
    code = 0
    for node in prs:
        try:
            act(args, node, required, query, rest)
        except (GitHubError, QueryError) as error:
            print(f"#{node['number']}: GitHub refused: {error}")
            print(REFUSED_FIX)
            code = 1
    return code


def act(args, node: dict, required: list[str], query: Query, rest: Rest) -> None:
    pr = snapshot(args.repo, node, query, rest)
    decision = merge_decision(pr, required, args.require_retro_notes)
    if isinstance(decision, Wait):
        print(f"#{pr.number} waits: {decision.reason}")
    elif isinstance(decision, UpdateBranch):
        path = f"repos/{args.repo}/pulls/{pr.number}/update-branch"
        rest("PUT", path, {"expected_head_sha": decision.sha})
        print(
            f"#{pr.number} is behind {node['baseRefName']}: updated the branch, so its checks "
            "run again"
        )
    else:
        fields = {
            "merge_method": "squash",
            "commit_title": decision.title,
            "commit_message": decision.body,
            "sha": decision.sha,
        }
        rest("PUT", f"repos/{args.repo}/pulls/{pr.number}/merge", fields)
        print(f"#{pr.number} merged: {decision.title}")
