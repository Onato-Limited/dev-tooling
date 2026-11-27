"""Whether a PR labelled merge-when-green should merge, have its branch updated, or wait."""

from dataclasses import dataclass

LABEL = "merge-when-green"
RETRO_HEADING = "## Retro notes"
GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    body: str
    head_sha: str
    labels: list[str]
    draft: bool
    mergeable: str
    behind: bool
    checks: dict[str, str]
    blocking_threads: int
    comments: list[str]


@dataclass(frozen=True)
class Wait:
    reason: str


@dataclass(frozen=True)
class UpdateBranch:
    sha: str


@dataclass(frozen=True)
class Merge:
    title: str
    body: str
    sha: str


def merge_decision(
    pr: PullRequest, required: list[str], retro_required: bool
) -> Wait | UpdateBranch | Merge:
    if LABEL not in pr.labels:
        return Wait(f"no `{LABEL}` label")
    if pr.draft:
        return Wait("a draft; mark it ready for review")
    not_green = [
        f"{name} ({pr.checks.get(name, 'missing').lower()})"
        for name in required
        if pr.checks.get(name) not in GREEN
    ]
    if not_green:
        return Wait(f"required checks not green: {', '.join(not_green)}")
    if pr.blocking_threads:
        return Wait(f"{pr.blocking_threads} blocking review threads")
    if retro_required and not any(
        line.strip() == RETRO_HEADING for comment in pr.comments for line in comment.splitlines()
    ):
        return Wait(f"no `{RETRO_HEADING}` comment")
    if pr.mergeable == "CONFLICTING":
        return Wait("conflicts with the base branch; resolve them and push")
    if pr.mergeable != "MERGEABLE":
        return Wait("GitHub hasn't worked out yet whether it can merge")
    if pr.behind:
        return UpdateBranch(pr.head_sha)
    return Merge(f"{pr.title} (#{pr.number})", pr.body, pr.head_sha)
