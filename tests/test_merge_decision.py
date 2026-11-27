from dataclasses import replace

from devtool.merge_decision import LABEL, Merge, PullRequest, UpdateBranch, Wait, merge_decision

REQUIRED = ["lint", "policy / size"]


def green_pr(**changes):
    pr = PullRequest(
        number=12,
        title="feat: a thing",
        body="Closes #4",
        head_sha="abc123",
        labels=[LABEL],
        draft=False,
        mergeable="MERGEABLE",
        behind=False,
        checks={"lint": "SUCCESS", "policy / size": "SUCCESS"},
        unresolved_threads=0,
        comments=[],
    )
    return replace(pr, **changes)


def test_without_the_label_it_waits():
    decision = merge_decision(green_pr(labels=["bug"]), REQUIRED, retro_required=False)

    assert decision == Wait("no `merge-when-green` label")


def test_all_green_it_squash_merges_the_head_with_the_pr_title_and_body():
    decision = merge_decision(green_pr(), REQUIRED, retro_required=False)

    assert decision == Merge(title="feat: a thing (#12)", body="Closes #4", sha="abc123")


def test_a_required_check_missing_pending_or_red_waits_naming_each_one():
    pr = green_pr(checks={"lint": "FAILURE", "build": "PENDING", "other": "FAILURE"})
    required = ["lint", "build", "policy / size"]

    decision = merge_decision(pr, required, retro_required=False)

    assert decision == Wait(
        "required checks not green: lint (failure), build (pending), policy / size (missing)"
    )


def test_neutral_and_skipped_checks_pass_as_branch_protection_counts_them():
    pr = green_pr(checks={"lint": "NEUTRAL", "policy / size": "SKIPPED"})

    decision = merge_decision(pr, REQUIRED, retro_required=False)

    assert isinstance(decision, Merge)


def test_an_unresolved_review_thread_waits_with_the_count():
    decision = merge_decision(green_pr(unresolved_threads=2), REQUIRED, retro_required=False)

    assert decision == Wait("2 unresolved review threads")


def test_required_retro_notes_missing_waits():
    pr = green_pr(comments=["Looks good", "Notes:\n## Retro notes later"])

    decision = merge_decision(pr, REQUIRED, retro_required=True)

    assert decision == Wait("no `## Retro notes` comment")


def test_a_retro_notes_heading_on_any_line_of_a_comment_counts():
    pr = green_pr(comments=["Looks good", "Done.\n\n## Retro notes\n- went fine"])

    assert isinstance(merge_decision(pr, REQUIRED, retro_required=True), Merge)
    assert isinstance(merge_decision(green_pr(), REQUIRED, retro_required=False), Merge)


def test_a_draft_waits():
    decision = merge_decision(green_pr(draft=True), REQUIRED, retro_required=False)

    assert decision == Wait("a draft; mark it ready for review")


def test_a_conflict_with_the_base_waits_rather_than_updating_the_branch():
    pr = green_pr(mergeable="CONFLICTING", behind=True)

    decision = merge_decision(pr, REQUIRED, retro_required=False)

    assert decision == Wait("conflicts with the base branch; resolve them and push")


def test_mergeability_not_yet_known_waits():
    decision = merge_decision(green_pr(mergeable="UNKNOWN"), REQUIRED, retro_required=False)

    assert decision == Wait("GitHub hasn't worked out yet whether it can merge")


def test_behind_the_base_updates_the_branch_at_the_head_it_checked():
    decision = merge_decision(green_pr(behind=True), REQUIRED, retro_required=False)

    assert decision == UpdateBranch(sha="abc123")
