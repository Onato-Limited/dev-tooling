import pytest

from devtool.gates import approved_by_review, matches_any


def test_a_negated_pattern_after_a_match_excludes_the_path():
    patterns = ["*", "!src/**"]

    assert not matches_any("src/app/main.py", patterns)
    assert matches_any("README.md", patterns)


def test_the_last_matching_pattern_wins():
    patterns = ["*", "!src/**", "src/payments/**"]

    assert matches_any("src/payments/charge.py", patterns)
    assert not matches_any("src/app/main.py", patterns)


def test_a_negated_pattern_alone_matches_nothing():
    assert not matches_any("src/app/main.py", ["!src/**"])


def review(login, state):
    return {"user": {"login": login}, "state": state}


@pytest.mark.parametrize(
    ("reviews", "approved"),
    [
        ([review("onato", "APPROVED")], True),
        ([review("onato", "APPROVED"), review("onato", "CHANGES_REQUESTED")], False),
        ([review("onato", "APPROVED"), review("onato", "DISMISSED")], False),
        ([review("onato", "APPROVED"), review("onato", "COMMENTED")], True),
        ([review("someone", "APPROVED")], False),
        ([], False),
        ([{"user": None, "state": "APPROVED"}], False),
    ],
    ids=[
        "approved",
        "changes requested later",
        "dismissed later",
        "a comment later doesn't undo it",
        "another login's approval",
        "no reviews",
        "a deleted account's review",
    ],
)
def test_the_approvers_latest_deciding_review_decides(reviews, approved):
    assert approved_by_review(reviews, "onato") is approved
