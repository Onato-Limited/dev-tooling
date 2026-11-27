"""Whether a push to main kept to the policy: squash-merged PRs, required checks green, no force."""

from dataclasses import dataclass

from devtool.merge_decision import GREEN


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


@dataclass(frozen=True)
class Violation:
    rule: str
    sha: str
    found: str
    fix: str


FORCED_FIX = (
    "Check what the push dropped from main and bring it back through a PR. Never force-push main."
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
    for commit in push.commits:
        if commit.pr is None:
            found = f"{commit.sha} was pushed to main without a PR"
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
