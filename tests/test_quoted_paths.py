import io
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from devtool import codeowners, pr_shape, pr_size, protected_paths
from devtool.pr_shape import header_path

WORKFLOW = Path(".github/workflows/pr-policy.yml").resolve()


def steps():
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    return {s["name"]: s["run"] for job in jobs.values() for s in job["steps"] if "run" in s}


def test_every_git_diff_in_pr_policy_prints_paths_unquoted():
    diffs = [run for run in steps().values() if "git diff" in run or "quotePath" in run]

    assert len(diffs) == 4
    assert all("git -c core.quotePath=false diff" in " ".join(run.split()) for run in diffs)


def test_every_path_listing_in_pr_policy_is_nul_separated():
    listings = [run for run in steps().values() if "--name-only" in run or "--numstat" in run]

    assert len(listings) == 3
    assert all("git -c core.quotePath=false diff -z " in " ".join(r.split()) for r in listings)


def changed_by(step: str, repo: Path) -> str:
    """The changed files as the step's own git command lists them, in `repo`."""
    command = steps()[step].split("|")[0]
    env = {**os.environ, "BASE_REF": "main", "RUNNER_TEMP": str(repo.parent)}
    done = subprocess.run(
        ["bash", "-c", command], cwd=repo, env=env, check=True, capture_output=True, text=True
    )
    return done.stdout


def scratch_repo(tmp_path: Path, *names: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "db").mkdir(parents=True)
    (repo / "README.md").write_text("x\n")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
        subprocess.run([*git, *args], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "main"], cwd=repo, check=True)
    for name in names or ("é.sql",):
        (repo / "db" / name).write_text("Mutex lock;\n")
    for args in (["add", "-A"], ["commit", "-q", "-m", "pr"]):
        subprocess.run([*git, *args], cwd=repo, check=True, capture_output=True)
    return repo


def test_codeowners_sees_a_non_ascii_path_as_written(tmp_path, capsys):
    repo = scratch_repo(tmp_path)
    (tmp_path / "CODEOWNERS").write_text("/db/ @dba\n")
    (tmp_path / "reviews.json").write_text(json.dumps([]))
    argv = [
        "--codeowners",
        str(tmp_path / "CODEOWNERS"),
        "--reviews",
        str(tmp_path / "reviews.json"),
    ]

    code = codeowners.main(
        argv, stdin=io.StringIO(changed_by("Check code owners' approvals", repo))
    )

    assert code == 1
    assert "  db/é.sql  (@dba)\n" in capsys.readouterr().out


def test_protected_paths_sees_a_non_ascii_path_as_written(tmp_path, capsys):
    repo = scratch_repo(tmp_path)
    changed = changed_by("Check protected paths", repo)

    code = protected_paths.main(["--patterns", "db/"], stdin=io.StringIO(changed))

    assert code == 1
    assert re.search(r"db/é\.sql", capsys.readouterr().out)


AWKWARD = (
    'a"b.sql',
    "a\\b.sql",
    "a\tb.sql",
    "a b.sql",
    "a\x01b.sql",
    "é b.sql",
    'a"b c.sql',
    "a\u2028b.sql",
)


def test_codeowners_sees_paths_git_quotes_as_written(tmp_path, capsys):
    repo = scratch_repo(tmp_path, *AWKWARD)
    (tmp_path / "CODEOWNERS").write_text("/db/ @dba\n")
    (tmp_path / "reviews.json").write_text(json.dumps([]))
    argv = [
        "--codeowners",
        str(tmp_path / "CODEOWNERS"),
        "--reviews",
        str(tmp_path / "reviews.json"),
    ]

    code = codeowners.main(
        argv, stdin=io.StringIO(changed_by("Check code owners' approvals", repo))
    )

    out = capsys.readouterr().out
    assert code == 1
    assert all(f"  db/{name}  (@dba)\n" in out for name in AWKWARD)


def test_protected_paths_sees_paths_git_quotes_as_written(tmp_path, capsys):
    repo = scratch_repo(tmp_path, *AWKWARD)
    changed = changed_by("Check protected paths", repo)

    code = protected_paths.main(["--patterns", "/db/*.sql"], stdin=io.StringIO(changed))

    out = capsys.readouterr().out
    assert code == 1
    assert all(f"db/{name}" in out for name in AWKWARD)


def test_size_excludes_paths_git_quotes_as_written(tmp_path, capsys):
    repo = scratch_repo(tmp_path, *AWKWARD)
    (tmp_path / "exclude").write_text("/db/\n")
    numstat = changed_by("Count changed lines", repo)

    code = pr_size.main(
        ["--limit", "0", "--exclude-file", str(tmp_path / "exclude")], stdin=io.StringIO(numstat)
    )

    assert (code, capsys.readouterr().out.splitlines()[0]) == (0, "0 changed lines, limit 0")


def test_size_counts_a_renamed_file_once_under_its_new_path(tmp_path, capsys):
    repo = scratch_repo(tmp_path)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    (repo / "big.txt").write_text("".join(f"line {i}\n" for i in range(50)))
    for args in (["add", "-A"], ["commit", "-q", "-m", "big"]):
        subprocess.run([*git, *args], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=repo, check=True)
    subprocess.run([*git, "mv", "big.txt", "moved.txt"], cwd=repo, check=True)
    (repo / "moved.txt").write_text((repo / "moved.txt").read_text() + "one more\n")
    for args in (["add", "-A"], ["commit", "-q", "-m", "move"]):
        subprocess.run([*git, *args], cwd=repo, check=True, capture_output=True)
    (tmp_path / "exclude").write_text("")
    numstat = changed_by("Count changed lines", repo)

    pr_size.main(["--exclude-file", str(tmp_path / "exclude")], stdin=io.StringIO(numstat))

    out = capsys.readouterr().out
    assert out.splitlines()[0] == "1 changed lines, limit 400"
    assert "moved.txt" in out


def test_pr_shape_limits_the_concurrency_rule_to_paths_git_quotes(tmp_path, capsys):
    repo = scratch_repo(tmp_path, *AWKWARD)
    diff = changed_by("Check the PR's title, body and diff", repo)
    (tmp_path / "body.md").write_text("Closes #1\n")
    argv = ["--title", "fix: x", "--body-file", str(tmp_path / "body.md")]
    argv += ["--concurrency-pattern", "Mutex", "--concurrency-paths", "/db/*.sql"]

    code = pr_shape.main(argv, stdin=io.StringIO(diff))

    out = capsys.readouterr().out
    assert code == 1
    assert all(f"  db/{name}:1: Mutex lock;" in out for name in AWKWARD)
    assert "Body:" not in out


@pytest.mark.parametrize(
    "text",
    ['"\\x"', '"a\\"', '"unclosed', '"\\9"'],
    ids=["unknown escape", "trailing backslash", "unclosed", "not octal"],
)
def test_text_that_isnt_valid_c_quoting_is_left_as_written(text):
    assert header_path(text) == text


def test_an_added_crlf_line_matches_a_pattern_anchored_at_its_end(tmp_path, capsys):
    (tmp_path / "body.md").write_text("Closes #1\n")
    diff = "+++ b/src/a.py\r\n@@ -0,0 +1 @@\r\n+foo lock()\r\n"
    argv = ["--title", "fix: x", "--body-file", str(tmp_path / "body.md")]
    argv += ["--concurrency-pattern", r"lock\(\)$"]

    code = pr_shape.main(argv, stdin=io.StringIO(diff, newline=""))

    assert (code, "  src/a.py:1: foo lock()" in capsys.readouterr().out) == (1, True)


def test_a_quoted_name_holding_a_byte_that_isnt_utf8_is_read_as_written():
    # Python reads such a byte as a lone surrogate under a C or C.UTF-8 locale.
    assert header_path('"a\udce9\\"b"') == 'a\udce9"b'
