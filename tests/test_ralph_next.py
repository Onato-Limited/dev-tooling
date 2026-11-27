import pytest
from ralph_fakes import FakeGitHub

from devtool.ralph import (
    ATTENDED,
    IN_PROGRESS,
    IN_REVIEW,
    TODO,
    Issue,
    Outcome,
    PullRequest,
    dry_run,
    next_issue,
    stuck_reason,
)


def test_the_next_issue_is_the_lowest_open_unblocked_todo_that_is_not_a_draft():
    issues = [
        Issue(5, TODO, is_open=False),
        Issue(6, TODO, open_blockers=(4,)),
        Issue(7, TODO, draft=True),
        Issue(8, IN_PROGRESS),
        Issue(10, TODO),
        Issue(9, TODO),
    ]

    assert next_issue(issues) == Issue(9, TODO)


def test_with_nothing_ready_there_is_no_next_issue():
    assert next_issue([Issue(7, IN_REVIEW), Issue(8, TODO, open_blockers=(7,))]) is None


def test_an_attended_issue_is_never_next_even_with_the_lowest_number():
    assert next_issue([Issue(3, ATTENDED), Issue(9, TODO)]) == Issue(9, TODO)
    assert next_issue([Issue(3, ATTENDED)]) is None


@pytest.mark.parametrize(
    ("outcome", "pr", "reason"),
    [
        (
            Outcome(pr=40),
            PullRequest(number=40, changes_requested=3),
            "PR #40: the review requested changes 3 times",
        ),
        (
            Outcome(pr=40),
            PullRequest(number=40, ci_failure_streak=3),
            "PR #40: CI failed on 3 runs in a row",
        ),
        (
            Outcome(pr=None, needs_human="needs splitting: over 400 lines"),
            None,
            "needs a human: needs splitting: over 400 lines",
        ),
        (Outcome(pr=None), None, "no PR opened"),
        (Outcome(pr=40), PullRequest(number=40, changes_requested=2, ci_failure_streak=2), None),
    ],
    ids=["changes requested 3 times", "CI failed 3 runs in a row", "NEEDS_HUMAN", "no PR", "ok"],
)
def test_a_session_is_stuck_after_three_review_or_ci_failures_or_without_a_pr(outcome, pr, reason):
    assert stuck_reason(outcome, pr) == reason


def test_dry_run_gives_the_next_issue_and_any_workflows_off_without_moving_a_card():
    github = FakeGitHub([Issue(6, IN_PROGRESS, is_open=False), Issue(7, TODO), Issue(8, TODO)])
    github.workflows["Item closed"] = False

    assert dry_run(2, github) == (
        "Board workflows are off: Item closed. Turn them on in the project's Workflows settings.\n"
        "Next: #7"
    )
    assert github.moves == []
    assert github.comments == []


def test_dry_run_with_nothing_ready_says_why_and_what_is_open():
    github = FakeGitHub(
        [Issue(7, IN_REVIEW), Issue(8, TODO), Issue(9, TODO, draft=True)], blocked_by={8: (7,)}
    )

    assert dry_run(2, github) == (
        "Nothing to take. Open:\n- #7 In review\n- #8 Todo, blocked by #7\n- #9 Todo, draft"
    )
