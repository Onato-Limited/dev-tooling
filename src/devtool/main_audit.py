"""Whether a push to main kept to the policy: squash-merged PRs, required checks green, no force."""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from devtool.merge_decision import GREEN
from devtool.merge_when_green import GitHubError, Rest, check_runs, gh_rest, newest_states
from devtool.required_checks import parse_required


@dataclass(frozen=True)
class MergedPR:
    number: int
    merge_commit_sha: str
    checks: dict[str, str]


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: int
    pr: MergedPR | None


@dataclass(frozen=True)
class Push:
    forced: bool
    after: str
    commits: list[Commit]
    # How many commits the push added; more than `commits` when GitHub didn't list them all.
    added: int | None = None


@dataclass(frozen=True)
class Violation:
    rule: str
    sha: str
    found: str
    fix: str


LABEL = "policy-violation"
REFUSED_FIX = (
    "  The workflow's token needs `issues: write`, and read access to contents, pull requests, "
    "checks and statuses."
)
FORCED_FIX = (
    "Check what the push dropped from main and bring it back through a PR. Never force-push main."
)
UNLISTED = "every pushed commit is audited"
UNLISTED_FIX = (
    "Audit the rest by hand with `git log --first-parent` on main, then close this issue."
)
NO_PR = "every commit on main comes from a merged PR"
NO_PR_FIX = "Revert it on main and open the change as a PR."
SQUASH = "PRs merge by squash"
SQUASH_FIX = (
    "Merge PRs by squash only: turn off merge commits and rebase merging in the repo's "
    "settings, under Pull Requests."
)
CHECKS = "PRs merge only with every required check green"
CHECKS_FIX = "Fix what those checks found in a new PR, and wait for green before merging."


def audit(push: Push, required: list[str]) -> list[Violation]:
    violations = []
    if push.forced:
        found = f"main was force-pushed to {push.after}"
        violations.append(Violation("no forced pushes", push.after, found, FORCED_FIX))
    if push.added is not None and push.added > len(push.commits):
        found = (
            f"the push added {push.added} commits and GitHub lists only the first "
            f"{len(push.commits)}"
        )
        violations.append(Violation(UNLISTED, push.after, found, UNLISTED_FIX))
    for commit in push.commits:
        if commit.pr is None:
            found = "This commit was pushed to main without a PR"
            violations.append(Violation(NO_PR, commit.sha, found, NO_PR_FIX))
            continue
        if commit.parents > 1:
            found = f"#{commit.pr.number} was merged with a merge commit"
            violations.append(Violation(SQUASH, commit.sha, found, SQUASH_FIX))
        elif commit.pr.merge_commit_sha != commit.sha:
            found = (
                f"#{commit.pr.number} was rebase-merged: it merged as "
                f"{commit.pr.merge_commit_sha}, not as this commit"
            )
            violations.append(Violation(SQUASH, commit.sha, found, SQUASH_FIX))
        not_green = [
            f"{name} ({commit.pr.checks.get(name, 'missing').lower()})"
            for name in required
            if commit.pr.checks.get(name) not in GREEN
        ]
        if not_green:
            found = f"#{commit.pr.number} merged with {', '.join(not_green)}"
            violations.append(Violation(CHECKS, commit.sha, found, CHECKS_FIX))
    return violations


def pushed_commits(repo: str, before: str, after: str, rest: Rest) -> tuple[list[dict], int]:
    """The commits the push added, past the 20 the push event lists, and how many there are."""
    commits, page = [], 1
    while True:
        found = rest("GET", f"repos/{repo}/compare/{before}...{after}?per_page=100&page={page}")
        commits += found["commits"]
        # GitHub lists at most 250 of a comparison's commits; past that, pages come back empty.
        if len(commits) >= found["total_commits"] or not found["commits"]:
            return commits, found["total_commits"]
        page += 1


def head_checks(repo: str, sha: str, rest: Rest) -> dict[str, str]:
    statuses = rest("GET", f"repos/{repo}/commits/{sha}/status")["statuses"]
    states = {status["context"]: status["state"].upper() for status in statuses}
    return states | newest_states(check_runs(repo, sha, rest))


def merged_pr(repo: str, sha: str, rest: Rest) -> MergedPR | None:
    merged = [pr for pr in rest("GET", f"repos/{repo}/commits/{sha}/pulls") if pr["merged_at"]]
    if not merged:
        return None
    # A commit can belong to several PRs; the one it was merged as is the one that counts.
    pr = next((pr for pr in merged if pr["merge_commit_sha"] == sha), merged[0])
    checks = head_checks(repo, pr["head"]["sha"], rest)
    return MergedPR(pr["number"], pr["merge_commit_sha"], checks)


def gather(repo: str, event: dict, rest: Rest) -> Push:
    listed, total = pushed_commits(repo, event["before"], event["after"], rest)
    commits = [
        Commit(found["sha"], len(found["parents"]), merged_pr(repo, found["sha"], rest))
        for found in listed
    ]
    return Push(event["forced"], event["after"], commits, total)


def main(argv: list[str], stdin: TextIO = sys.stdin, rest: Rest = gh_rest) -> int:
    parser = argparse.ArgumentParser(prog="devtool main-audit", description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/name of the repository")
    parser.add_argument("--event", required=True, help="the push event's JSON")
    parser.add_argument(
        "--required-checks", required=True, help="main's .github/required-checks before the push"
    )
    parser.add_argument("--assignee", required=True, help="who the policy-violation issue is for")
    parser.add_argument("--run-url", required=True, help="this run, for the issue to link")
    args = parser.parse_args(argv)

    event = json.loads(Path(args.event).read_text())
    required = parse_required(Path(args.required_checks).read_text())
    try:
        violations = audit(gather(args.repo, event, rest), required)
        if not violations:
            print(f"main-audit: {event['before']}...{event['after']} kept to the policy.")
            return 0
        alarm = report(violations, args.run_url)
        print(alarm)
        print(raise_alarm(args.repo, alarm, args.assignee, rest))
    except GitHubError as error:
        print(f"main-audit: GitHub refused: {error}")
        print(REFUSED_FIX)
    return 1


def raise_alarm(repo: str, alarm: str, assignee: str, rest: Rest) -> str:
    """Comment on the open policy-violation issue, or open one; say which."""
    already = rest("GET", f"repos/{repo}/issues?labels={LABEL}&state=open")
    if already:
        number = already[0]["number"]
        rest(
            "POST", f"repos/{repo}/issues/{number}/comments", {"body": f"Another push:\n\n{alarm}"}
        )
        return f"main-audit: commented on #{number}."
    issue = {
        "title": "main-audit: a push to main bypassed the policy",
        "body": f"A push to main broke the policy:\n\n{alarm}",
        "labels[]": LABEL,
        "assignees[]": assignee,
    }
    opened = rest("POST", f"repos/{repo}/issues", issue)
    return f"main-audit: opened #{opened['number']} for {assignee}."


def report(violations: list[Violation], run_url: str) -> str:
    lines = [f"- **{v.rule}** ({v.sha}): {v.found}. {v.fix}" for v in violations]
    return "\n".join(lines) + f"\n\nRun: {run_url}\n"
