import io
import json
import subprocess
from pathlib import Path

import pytest
import yaml

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


def git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_a_moved_or_deleted_matching_file_counts(tmp_path, capsys):
    repo = tmp_path / "repo"
    (repo / "db").mkdir(parents=True)
    (repo / "db" / "moved.sql").write_text("select 1;\n")
    (repo / "db" / "gone.sql").write_text("select 2;\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "mv", "db/moved.sql", "moved.sql")
    git(repo, "rm", "-q", "db/gone.sql")
    git(repo, "commit", "-q", "-m", "pr")
    changed = git(repo, "diff", "--name-only", "--no-renames", "HEAD~1...HEAD")

    code = run(tmp_path, changed, "db/")

    assert code == 1
    out = capsys.readouterr().out
    assert "  db/moved.sql\n" in out and "  db/gone.sql\n" in out


def test_the_label_events_are_required():
    with pytest.raises(SystemExit) as exit_:
        main(["--patterns", "db/"], stdin=io.StringIO("db/m.sql\n"))

    assert exit_.value.code == 2


def test_pr_policy_passes_the_patterns_and_the_approver_to_the_check():
    workflow = yaml.safe_load(Path(".github/workflows/pr-policy.yml").read_text())
    step = next(
        s
        for s in workflow["jobs"]["review-paths"]["steps"]
        if s.get("name") == "Check review paths"
    )
    command = " ".join(step["run"].split())

    assert step["env"]["PATTERNS"] == "${{ inputs.review-paths }}"
    assert step["env"]["APPROVER"] == "${{ inputs.approver }}"
    assert 'git diff --name-only --no-renames "origin/$BASE_REF...HEAD"' in command
    assert '--patterns "$PATTERNS"' in command
    assert '--label-events "$RUNNER_TEMP/label-events.json"' in command
    assert '${APPROVER:+--approver "$APPROVER"}' in command
