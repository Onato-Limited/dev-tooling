import io
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from devtool.codeowners import main, owners_of, rules


def run(tmp_path, changed, codeowners, *reviews, author="onato-agent[bot]"):
    owners = tmp_path / "CODEOWNERS"
    if codeowners is not None:
        owners.write_text(codeowners)
    given = tmp_path / "reviews.json"
    given.write_text(json.dumps([{"user": {"login": who}, "state": st} for who, st in reviews]))
    argv = ["--codeowners", str(owners), "--reviews", str(given), "--author", author]
    return main(argv, stdin=io.StringIO(changed))


def test_an_owned_file_without_an_owners_approval_fails_listing_it_and_its_owners(tmp_path, capsys):
    code = run(tmp_path, "db/m.sql\nsrc/a.py\n", "# schema\n/db/ @onato @alice\n")

    assert code == 1
    assert capsys.readouterr().out == (
        "Owned files without an approving review from one of their owners:\n"
        "  db/m.sql  (@onato, @alice)\n"
        "Ask one of each file's owners to approve the PR.\n"
    )


def test_it_passes_once_any_one_of_a_files_owners_approves(tmp_path, capsys):
    code = run(tmp_path, "db/m.sql\n", "/db/ @onato @alice\n", ("Alice", "APPROVED"))

    assert code == 0
    assert capsys.readouterr().out == (
        "Owned files, each approved by one of its owners:\n  db/m.sql  (@onato, @alice)\n"
    )


def test_the_last_matching_line_wins_and_a_line_with_no_owners_leaves_files_unowned(
    tmp_path, capsys
):
    owners = "* @onato\n/docs/\n/docs/security/ @alice\n"

    assert run(tmp_path, "docs/a.md\n", owners) == 0
    assert capsys.readouterr().out == "No owned files changed.\n"
    assert run(tmp_path, "docs/security/b.md\nREADME.md\n", owners) == 1
    out = capsys.readouterr().out
    assert "  docs/security/b.md  (@alice)\n" in out and "  README.md  (@onato)\n" in out


def test_a_moved_or_deleted_owned_file_counts(tmp_path, capsys):
    repo = tmp_path / "repo"
    (repo / "db").mkdir(parents=True)
    (repo / "db" / "moved.sql").write_text("select 1;\n")
    (repo / "db" / "gone.sql").write_text("select 2;\n")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["commit", "-q", "-m", "base"],
        ["mv", "db/moved.sql", "moved.sql"],
        ["rm", "-q", "db/gone.sql"],
        ["commit", "-q", "-m", "pr"],
    ):
        subprocess.run([*git, *args], cwd=repo, check=True, capture_output=True)
    changed = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "HEAD~1...HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert run(tmp_path, changed, "/db/ @onato\n") == 1
    out = capsys.readouterr().out
    assert "  db/moved.sql  (@onato)\n" in out and "  db/gone.sql  (@onato)\n" in out


def test_a_team_or_email_owner_is_named_as_one_this_check_cant_verify(tmp_path, capsys):
    owners = "/db/ @org/dba\n/ops/ ops@example.com\n"

    code = run(tmp_path, "db/m.sql\nops/run.sh\n", owners, ("onato", "APPROVED"))

    assert code == 1
    assert capsys.readouterr().out == (
        "Owned files without an approving review from one of their owners:\n"
        "  db/m.sql  (@org/dba)\n"
        "  ops/run.sh  (ops@example.com)\n"
        "Ask one of each file's owners to approve the PR.\n"
        "This check can't tell who is in a team or behind an email: @org/dba, ops@example.com. "
        'Name people instead, or protect the branch with "Require review from Code Owners".\n'
    )


def test_the_author_cannot_be_the_owner_who_approves(tmp_path, capsys):
    code = run(tmp_path, "db/m.sql\n", "/db/ @onato\n", ("onato", "APPROVED"), author="onato")

    assert code == 1
    assert capsys.readouterr().out.endswith(
        "GitHub doesn't let onato approve their own PR, so another owner approves it, or an "
        "admin merges it.\n"
    )


def test_with_no_codeowners_file_it_passes(tmp_path, capsys):
    code = run(tmp_path, "db/m.sql\n", None)

    assert code == 0
    assert capsys.readouterr().out == "No CODEOWNERS file on the base branch.\n"


def test_pr_policy_reads_codeowners_from_the_base_branch_and_passes_it_with_the_reviews():
    workflow = yaml.safe_load(Path(".github/workflows/pr-policy.yml").read_text())
    job = workflow["jobs"]["codeowners"]
    steps = {s.get("name"): s for s in job["steps"]}
    read = steps["Read CODEOWNERS from the base branch"]
    check = steps["Check code owners' approvals"]
    command = " ".join(check["run"].split())

    assert 'git show "origin/$BASE_REF:$path"' in read["run"]
    assert all(p in read["run"] for p in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"))
    assert (
        'gh api --paginate "repos/$REPO/pulls/$PR/reviews"' in steps["Read the PR's reviews"]["run"]
    )
    assert 'git diff --name-only --no-renames "origin/$BASE_REF...HEAD"' in command
    assert '--codeowners "$RUNNER_TEMP/CODEOWNERS"' in command
    assert '--reviews "$RUNNER_TEMP/reviews.json"' in command
    assert '--author "$AUTHOR"' in command
    assert check["env"]["AUTHOR"] == "${{ github.event.pull_request.user.login }}"
    assert job["permissions"] == {"contents": "read", "pull-requests": "read"}


def test_review_paths_is_gone_from_the_workflow():
    workflow = yaml.safe_load(Path(".github/workflows/pr-policy.yml").read_text())

    assert "review-paths" not in workflow["jobs"]
    assert "review-paths" not in workflow[True]["workflow_call"]["inputs"]


def test_a_review_from_a_deleted_account_is_read_without_counting(tmp_path):
    owners = tmp_path / "CODEOWNERS"
    owners.write_text("/db/ @onato\n")
    given = tmp_path / "reviews.json"
    given.write_text(json.dumps([{"user": None, "state": "APPROVED"}]))

    code = main(
        ["--codeowners", str(owners), "--reviews", str(given)], stdin=io.StringIO("db/m.sql\n")
    )

    assert code == 1


@pytest.mark.parametrize(
    ("codeowners", "path", "owners"),
    [
        ("/db @a\n", "db/m.sql", ["@a"]),
        ("src/db @a\n", "src/db/m.sql", ["@a"]),
        ("src/db @a\n", "lib/src/db/m.sql", []),
        ("/apps/ @a\n/apps/github @b\n", "apps/github/x.py", ["@b"]),
        ("/apps/ @a\n/apps/github\n", "apps/github/x.py", []),
        ("/apps/ @a\n/apps/github\n", "apps/web/x.py", ["@a"]),
        ("**/logs @a\n", "build/logs/x.txt", ["@a"]),
        ("logs @a\n", "build/logs/x.txt", ["@a"]),
        ("docs/* @a\n", "docs/b.md", ["@a"]),
        ("docs/* @a\n", "docs/sub/b.md", []),
        ("/packages/app-* @a\n", "packages/app-web/x.py", ["@a"]),
        ("/packages/app-* @a\n", "packages/app-web", ["@a"]),
        ("*.d @a\n", "conf.d/x.conf", ["@a"]),
        ("/docs/ @a\ndocs/* @b\n", "docs/sub/b.md", ["@a"]),
        ("*.js @a\n", "src/deep/x.js", ["@a"]),
        ("*.js @a\n", "src/x.jsx", []),
        ("/build/logs/ @a\n", "build/logs/x/y.log", ["@a"]),
        ("/build/logs/ @a\n", "src/build/logs/y.log", []),
        ("apps/**/test @a\n", "apps/web/unit/test/x.py", ["@a"]),
        ("apps/**/test @a\n", "apps/test/x.py", ["@a"]),
        ("docs/**/*.md @a\n", "docs/a.md", ["@a"]),
        ("/src/**/migrations/ @a\n", "src/migrations/001.sql", ["@a"]),
        ("/my\\ docs/ @a\n", "my docs/x.md", ["@a"]),
        ("* @a\n", "deep/er/x.py", ["@a"]),
    ],
)
def test_patterns_match_as_github_reads_codeowners(codeowners, path, owners):
    assert owners_of(path, rules(codeowners)) == owners
