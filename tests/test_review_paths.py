import io
import json

from devtool.review_paths import main


def label_events(tmp_path, *events):
    path = tmp_path / "events.json"
    path.write_text(
        json.dumps(
            [{"event": e, "label": {"name": n}, "actor": {"login": a}} for e, n, a in events]
        )
    )
    return str(path)


def run(tmp_path, changed, patterns, *events, approver="onato"):
    argv = ["--patterns", patterns, "--label-events", label_events(tmp_path, *events)]
    if approver:
        argv += ["--approver", approver]
    return main(argv, stdin=io.StringIO(changed))


def test_a_change_under_a_review_path_fails_naming_the_files_the_label_and_the_approver(
    tmp_path, capsys
):
    code = run(tmp_path, "db/migrations/001.sql\nsrc/a.py\n", "# schema\ndb/migrations/\n")

    assert code == 1
    assert capsys.readouterr().out == (
        "Changed under review paths:\n"
        "  db/migrations/001.sql\n"
        "Files matching the review-paths input need onato's review. Ask onato to add the "
        "review-approved label.\n"
    )


def test_it_passes_once_the_approver_adds_the_review_approved_label(tmp_path, capsys):
    events = [("labeled", "bug", "someone"), ("labeled", "review-approved", "onato")]

    code = run(tmp_path, "db/migrations/001.sql\n", "db/migrations/", *events)

    assert code == 0
    assert capsys.readouterr().out == (
        "Changed under review paths, approved by onato (review-approved):\n"
        "  db/migrations/001.sql\n"
    )


def test_someone_elses_label_or_one_later_removed_does_not_approve(tmp_path):
    others = [("labeled", "review-approved", "onato-agent[bot]")]
    removed = [("labeled", "review-approved", "onato"), ("unlabeled", "review-approved", "x")]

    assert run(tmp_path, "db/m.sql\n", "db/", *others) == 1
    assert run(tmp_path, "db/m.sql\n", "db/", *removed) == 1


def test_without_an_approver_no_label_passes_it(tmp_path, capsys):
    events = [("labeled", "review-approved", "onato")]

    code = run(tmp_path, "db/m.sql\n", "db/", *events, approver=None)

    assert code == 1
    assert capsys.readouterr().out.endswith(
        "Files matching the review-paths input need the owner's review, but no approver is set, "
        "so no label can pass this check. Set the approver input, or revert the changes to these "
        "files.\n"
    )


def test_with_no_patterns_it_passes(tmp_path, capsys):
    code = run(tmp_path, "db/m.sql\n", "")

    assert code == 0
    assert capsys.readouterr().out == "No review paths changed.\n"


def test_an_allowlist_passes_a_pr_inside_it_and_fails_one_outside(tmp_path, capsys):
    allowlist = "*\n!src/**\n!tests/**\n"

    assert run(tmp_path, "src/a.py\ntests/test_a.py\n", allowlist) == 0
    capsys.readouterr()
    assert run(tmp_path, "src/a.py\nREADME.md\n", allowlist) == 1
    assert "  README.md\n" in capsys.readouterr().out
