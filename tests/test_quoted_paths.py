import io
import json
import os
import re
import subprocess
from pathlib import Path

import yaml

from devtool import codeowners, protected_paths

WORKFLOW = Path(".github/workflows/pr-policy.yml").resolve()


def steps():
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    return {s["name"]: s["run"] for job in jobs.values() for s in job["steps"] if "run" in s}


def test_every_git_diff_in_pr_policy_prints_paths_unquoted():
    diffs = [run for run in steps().values() if "git diff" in run or "quotePath" in run]

    assert len(diffs) == 4
    assert all("git -c core.quotePath=false diff" in " ".join(run.split()) for run in diffs)


def changed_by(step: str, repo: Path) -> str:
    """The changed files as the step's own git command lists them, in `repo`."""
    command = steps()[step].split("|")[0]
    env = {**os.environ, "BASE_REF": "main"}
    done = subprocess.run(
        ["bash", "-c", command], cwd=repo, env=env, check=True, capture_output=True, text=True
    )
    return done.stdout


def scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "db").mkdir(parents=True)
    (repo / "README.md").write_text("x\n")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
        subprocess.run([*git, *args], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", "main"], cwd=repo, check=True)
    (repo / "db" / "é.sql").write_text("select 1;\n")
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
