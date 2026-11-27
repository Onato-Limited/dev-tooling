from devtool.main_audit import Commit, MergedPR, Push, Violation, audit

REQUIRED = ["lint", "policy / size"]
GREEN = {"lint": "SUCCESS", "policy / size": "SUCCESS"}


def squashed(sha="aaa1111", number=7, checks=None):
    pr = MergedPR(number=number, merge_commit_sha=sha, checks=GREEN if checks is None else checks)
    return Commit(sha=sha, parents=1, pr=pr)


def test_squash_merged_prs_with_every_required_check_green_pass():
    checks = {"lint": "SUCCESS", "policy / size": "NEUTRAL"}
    push = Push(forced=False, after="bbb2222", commits=[squashed(), squashed("bbb2222", 8, checks)])

    assert audit(push, REQUIRED) == []


def test_a_forced_push_is_a_violation():
    push = Push(forced=True, after="bbb2222", commits=[squashed()])

    assert audit(push, REQUIRED) == [
        Violation(
            rule="no forced pushes",
            sha="bbb2222",
            found="main was force-pushed to bbb2222",
            fix="Check what the push dropped from main and bring it back through a PR. "
            "Never force-push main.",
        )
    ]


def test_a_commit_with_no_merged_pr_is_a_violation():
    push = Push(forced=False, after="ccc3333", commits=[Commit("ccc3333", parents=1, pr=None)])

    assert audit(push, REQUIRED) == [
        Violation(
            rule="every commit on main comes from a merged PR",
            sha="ccc3333",
            found="This commit was pushed to main without a PR",
            fix="Revert it on main and open the change as a PR.",
        )
    ]


def test_a_pr_merged_with_a_required_check_red_pending_or_missing_is_a_violation():
    checks = {"lint": "FAILURE", "build": "PENDING", "docs": "NEUTRAL", "e2e": "SKIPPED"}
    push = Push(forced=False, after="ddd4444", commits=[squashed("ddd4444", 9, checks)])

    assert audit(push, ["lint", "build", "docs", "e2e", "policy / size"]) == [
        Violation(
            rule="PRs merge only with every required check green",
            sha="ddd4444",
            found="#9 merged with lint (failure), build (pending), policy / size (missing)",
            fix="Fix what those checks found in a new PR, and wait for green before merging.",
        )
    ]


SQUASH_FIX = (
    "Merge PRs by squash only: turn off merge commits and rebase merging in the repo's "
    "settings, under Pull Requests."
)


def test_a_merge_commit_is_a_violation_of_the_squash_rule():
    merged = Commit("eee5555", parents=2, pr=MergedPR(11, "eee5555", GREEN))
    push = Push(forced=False, after="eee5555", commits=[merged])

    assert audit(push, REQUIRED) == [
        Violation(
            rule="PRs merge by squash",
            sha="eee5555",
            found="#11 was merged with a merge commit",
            fix=SQUASH_FIX,
        )
    ]


def test_a_commit_whose_pr_merged_as_another_commit_is_a_rebase_merge():
    rebased = Commit("fff6666", parents=1, pr=MergedPR(12, "fff7777", GREEN))
    push = Push(forced=False, after="fff7777", commits=[rebased, squashed("fff7777", 12)])

    assert audit(push, REQUIRED) == [
        Violation(
            rule="PRs merge by squash",
            sha="fff6666",
            found="#12 was rebase-merged: it merged as fff7777, not as this commit",
            fix=SQUASH_FIX,
        )
    ]
