from devtool.gates import matches_any


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
