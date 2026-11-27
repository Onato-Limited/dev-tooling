import pytest
from ralph_fakes import Clock, FakeGitHub, FakeSession

from devtool.ralph import (
    DONE,
    IN_PROGRESS,
    IN_REVIEW,
    TODO,
    Issue,
    Outcome,
    PullRequest,
    run,
)


def loop(github, session, notes=None, clock=None, max_iterations=10):
    notes = [] if notes is None else notes
    clock = clock or Clock()
    return run(2, github, session, notes.append, clock, clock.sleep, max_iterations)


def merged_run(pr):
    return Outcome(pr=pr), PullRequest(number=pr), False


def test_takes_the_lowest_ready_todo_issue_and_marks_it_in_progress_first():
    github = FakeGitHub(
        [
            Issue(7, TODO),
            Issue(8, TODO),
            Issue(9, IN_PROGRESS),
            Issue(10, TODO),
        ],
        blocked_by={8: (7,), 10: (9,)},
    )
    session = FakeSession(github, {7: merged_run(40), 8: merged_run(41)})

    loop(github, session)

    assert session.worked == [7, 8]
    assert session.status_at_start == [IN_PROGRESS, IN_PROGRESS]
    assert github.moves[:2] == [(7, IN_PROGRESS), (8, IN_PROGRESS)]


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
    ],
    ids=["changes requested 3 times", "CI failed 3 runs in a row", "NEEDS_HUMAN", "no PR"],
)
def test_a_stuck_issue_goes_to_in_review_with_its_reason_and_the_loop_goes_on(outcome, pr, reason):
    github = FakeGitHub([Issue(7, TODO), Issue(8, TODO)])
    session = FakeSession(github, {7: (outcome, pr, True), 8: merged_run(41)})

    loop(github, session)

    assert session.worked == [7, 8]
    assert github.moves[:2] == [(7, IN_PROGRESS), (7, IN_REVIEW)]
    assert github.comments == [(7, f"Moved to In review by the Ralph loop: {reason}.")]


def test_the_owner_is_messaged_as_soon_as_an_issue_needs_them():
    github = FakeGitHub([Issue(7, TODO), Issue(8, TODO)])
    stuck = (Outcome(pr=None, needs_human="choose a database"), None, True)
    session = FakeSession(github, {7: stuck, 8: merged_run(41)})
    notes = []

    stop = loop(github, session, notes)

    assert notes == [
        "Ralph loop: #7 needs you (moved to In review): needs a human: choose a database.\n"
        "https://github.com/owner/repo/issues/7#issuecomment-1",
        stop,
    ]


def test_stops_with_milestone_complete_when_no_issues_are_open():
    github = FakeGitHub([Issue(7, TODO)])
    session = FakeSession(github, {7: merged_run(40)})
    notes = []

    stop = loop(github, session, notes)

    assert stop == "Ralph loop stopped: milestone complete."
    assert notes == [stop]


def test_stops_at_the_iteration_cap():
    github = FakeGitHub([Issue(7, TODO), Issue(8, TODO)])
    session = FakeSession(github, {7: merged_run(40), 8: merged_run(41)})

    stop = loop(github, session, max_iterations=1)

    assert session.worked == [7]
    assert stop.startswith("Ralph loop stopped: iteration cap (1) reached.\n")


def test_the_stop_message_lists_stuck_issues_and_prs_without_retro_notes():
    github = FakeGitHub([Issue(7, TODO), Issue(8, TODO), Issue(9, TODO)])
    session = FakeSession(
        github,
        {
            7: (Outcome(pr=40), PullRequest(number=40, ci_failure_streak=3), True),
            8: (Outcome(pr=None, needs_human="a secret: the API key"), None, False),
            9: (Outcome(pr=42), PullRequest(number=42, has_retro_notes=False), False),
        },
    )
    notes = []

    stop = loop(github, session, notes)

    assert notes[-1] == stop
    assert stop == (
        "Ralph loop stopped: nothing left to take.\n"
        "Open:\n"
        "- #7 In review\n"
        "- #8 In review\n"
        "Moved to In review:\n"
        "- #7: PR #40: CI failed on 3 runs in a row\n"
        "- #8: needs a human: a secret: the API key\n"
        "Missing retro notes: PR #42"
    )


def test_stops_before_any_session_when_a_required_board_workflow_is_off():
    github = FakeGitHub([Issue(7, TODO)])
    github.workflows["Item closed"] = False
    del github.workflows["Pull request merged"]
    session = FakeSession(github, {7: merged_run(40)})
    notes = []

    stop = loop(github, session, notes)

    assert session.worked == []
    assert github.moves == []
    assert notes == [stop]
    assert stop == (
        "Ralph loop stopped: board workflows are off: Item closed, Pull request merged. "
        "Turn them on in the project's Workflows settings."
    )


def test_closed_issues_left_off_done_are_moved_to_done_and_named_in_the_stop_message():
    github = FakeGitHub([Issue(6, IN_PROGRESS, is_open=False), Issue(7, TODO)])
    session = FakeSession(github, {7: merged_run(40)})

    stop = loop(github, session)

    assert github.moves[:2] == [(6, DONE), (7, IN_PROGRESS)]
    assert stop == (
        "Ralph loop stopped: milestone complete.\n"
        "Moved to Done (closed, but the board had not moved them): #6"
    )
