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


def test_stops_listing_what_is_left_when_open_issues_remain_but_none_is_ready():
    github = FakeGitHub(
        [
            Issue(9, IN_PROGRESS),
            Issue(10, TODO),
            Issue(11, TODO),
            Issue(12, IN_REVIEW),
        ],
        blocked_by={10: (9,), 11: (9, 10)},
    )
    github.open_prs = {12}
    session = FakeSession(github, {})

    stop = loop(github, session)

    assert session.worked == []
    assert stop == (
        "Ralph loop stopped: nothing left to take.\n"
        "Open:\n"
        "- #9 In progress\n"
        "- #10 Todo, blocked by #9\n"
        "- #11 Todo, blocked by #9, #10\n"
        "- #12 In review"
    )


def test_stops_at_the_iteration_cap():
    github = FakeGitHub([Issue(7, TODO), Issue(8, TODO)])
    session = FakeSession(github, {7: merged_run(40), 8: merged_run(41)})

    stop = loop(github, session, max_iterations=1)

    assert session.worked == [7]
    assert stop.startswith("Ralph loop stopped: iteration cap (1) reached.\n")


def test_waits_for_a_pending_auto_merge_then_takes_the_issue_it_unblocks():
    github = FakeGitHub([Issue(7, TODO), Issue(8, TODO)], blocked_by={8: (7,)})
    session = FakeSession(
        github, {7: (Outcome(pr=40), PullRequest(number=40), True), 8: merged_run(41)}
    )
    clock = Clock()
    clock.on_sleep = lambda: github.merge(7, 40)

    stop = loop(github, session, clock=clock)

    assert session.worked == [7, 8]
    assert clock.sleeps == [60]
    assert stop == "Ralph loop stopped: milestone complete."


def pending_run(pr, behind=False):
    return Outcome(pr=pr), PullRequest(number=pr, behind=behind), True


def test_after_a_session_whose_pr_merged_its_worktree_and_branch_are_removed():
    github = FakeGitHub([Issue(7, TODO)])
    session = FakeSession(github, {7: merged_run(40)})

    loop(github, session)

    assert session.removed == [7]


def test_a_pending_prs_worktree_is_removed_once_it_merges():
    github = FakeGitHub([Issue(7, TODO)])
    session = FakeSession(github, {7: pending_run(40)})
    clock = Clock()
    clock.on_sleep = lambda: github.merge(7, 40)

    loop(github, session, clock=clock)

    assert session.removed == [7]


def test_a_stuck_issues_worktree_is_kept_for_the_owner_even_if_its_pr_merged():
    github = FakeGitHub([Issue(7, TODO), Issue(8, TODO)])
    stuck = (Outcome(pr=40, needs_human="pick a licence"), PullRequest(number=40), False)
    session = FakeSession(github, {7: stuck, 8: (Outcome(pr=None), None, False)})

    loop(github, session)

    assert session.removed == []


def test_a_failed_removal_is_listed_in_the_stop_message_and_the_loop_goes_on():
    github = FakeGitHub([Issue(7, TODO), Issue(8, TODO)])
    session = FakeSession(github, {7: merged_run(40), 8: merged_run(41)})
    session.removal_fails = {7: "git worktree remove failed: fatal: contains modified files"}

    stop = loop(github, session)

    assert session.worked == [7, 8]
    assert stop.endswith(
        "Worktrees left in place:\n- #7: git worktree remove failed: fatal: contains modified files"
    )


def test_an_in_review_issue_nothing_holds_goes_back_to_todo_and_is_taken_in_the_same_run():
    github = FakeGitHub([Issue(7, IN_REVIEW)])
    session = FakeSession(github, {7: merged_run(40)})

    loop(github, session)

    assert session.worked == [7]
    assert github.moves[:2] == [(7, TODO), (7, IN_PROGRESS)]
    assert github.comments == [
        (7, "Moved back to Todo by the Ralph loop: nothing holds it any more.")
    ]


def test_an_in_review_issue_whose_pr_is_still_open_stays_in_review():
    github = FakeGitHub([Issue(7, IN_REVIEW)])
    github.open_prs = {7}
    session = FakeSession(github, {})

    loop(github, session)

    assert (session.worked, github.moves, github.comments) == ([], [], [])


def test_an_in_review_issue_with_an_open_blocker_stays_in_review():
    github = FakeGitHub([Issue(7, IN_REVIEW), Issue(8, IN_PROGRESS)], blocked_by={7: (8,)})
    session = FakeSession(github, {})

    loop(github, session)

    assert (session.worked, github.moves, github.comments) == ([], [], [])


def test_a_released_issues_leftover_worktree_and_branch_are_removed_before_its_session():
    github = FakeGitHub([Issue(7, IN_REVIEW)])
    session = FakeSession(github, {7: merged_run(40)})
    removed_by_start = []
    session.while_running[7] = lambda: removed_by_start.extend(session.removed)

    loop(github, session)

    assert removed_by_start == [7]


def test_a_leftover_that_cannot_be_removed_is_reported_and_its_issue_stays_in_review():
    github = FakeGitHub([Issue(7, IN_REVIEW)])
    session = FakeSession(github, {})
    session.removal_fails = {7: "git worktree remove failed: fatal: contains modified files"}

    stop = loop(github, session)

    assert (session.worked, github.moves) == ([], [])
    assert stop.endswith(
        "Worktrees left in place:\n- #7: git worktree remove failed: fatal: contains modified files"
    )


def test_gives_up_waiting_for_auto_merge_after_an_hour():
    github = FakeGitHub([Issue(7, TODO), Issue(8, TODO)], blocked_by={8: (7,)})
    session = FakeSession(github, {7: (Outcome(pr=40), PullRequest(number=40), True)})
    clock = Clock()

    stop = loop(github, session, clock=clock)

    assert session.worked == [7]
    assert sum(clock.sleeps) == 3600
    assert stop.startswith(
        "Ralph loop stopped: auto-merge still pending after 60 minutes: PR #40.\n"
    )


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


def test_at_the_iteration_cap_the_loop_still_waits_for_its_open_prs():
    github = FakeGitHub([Issue(7, TODO), Issue(8, TODO)])
    session = FakeSession(github, {7: pending_run(40), 8: merged_run(41)})
    clock = Clock()
    clock.on_sleep = lambda: github.merge(7, 40)

    stop = loop(github, session, clock=clock, max_iterations=1)

    assert (session.worked, clock.sleeps) == ([7], [60])
    assert stop.startswith("Ralph loop stopped: iteration cap (1) reached.\n")


def test_an_issue_whose_pr_merges_just_after_the_board_is_read_is_not_worked_again():
    github = FakeGitHub([Issue(7, TODO)])
    session = FakeSession(github, {7: pending_run(40)})
    session.while_running[7] = lambda: github.set_status(7, IN_REVIEW)
    clock = Clock()
    read = github.milestone_issues

    def merges_after_the_read(milestone):
        issues = read(milestone)
        if session.worked:
            github.merge(7, 40)
        return issues

    github.milestone_issues = merges_after_the_read

    stop = loop(github, session, clock=clock)

    assert (session.worked, clock.sleeps, github.comments) == ([7], [], [])
    assert stop == "Ralph loop stopped: milestone complete."


def test_a_closed_issue_the_board_left_in_review_is_moved_to_done_not_released():
    github = FakeGitHub([Issue(6, IN_REVIEW, is_open=False)])
    session = FakeSession(github, {})

    loop(github, session)

    assert (github.moves, github.comments, session.removed) == ([(6, DONE)], [], [])


def test_the_hour_to_merge_starts_again_after_a_session():
    github = FakeGitHub([Issue(7, TODO), Issue(8, TODO)], blocked_by={8: (7,)})
    session = FakeSession(github, {7: pending_run(40), 8: pending_run(41)})
    clock = Clock()
    session.while_running[8] = lambda: setattr(clock, "now", clock.now + 3600)
    merges = iter([(7, 40), (8, 41)])
    clock.on_sleep = lambda: github.merge(*next(merges))

    stop = loop(github, session, clock=clock)

    assert (session.worked, clock.sleeps) == ([7, 8], [60, 60])
    assert stop == "Ralph loop stopped: milestone complete."
