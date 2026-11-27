import io
import json

from devtool.main_audit import main
from devtool.merge_when_green import GitHubError

RUN_URL = "https://github.com/o/r/actions/runs/99"


class FakeGitHub:
    """GitHub as main-audit reads it: a comparison, each commit's PRs, head checks and issues."""

    def __init__(self, commits, pulls=None, runs=None, statuses=None, open_issues=(), fail=None):
        self.fail = fail or {}
        self.listed = 250
        self.commits = commits
        self.pulls = pulls or {}
        self.runs = runs or {}
        self.statuses = statuses or {}
        self.open_issues = list(open_issues)
        self.calls = []

    def rest(self, method, path, fields=None):
        self.calls.append((method, path, fields))
        if path in self.fail:
            raise GitHubError(self.fail[path])
        if method == "POST":
            return {"number": 31}
        if "/compare/" in path:
            page = int(path.rsplit("page=", 1)[1])
            # GitHub lists at most 250 of a comparison's commits, whatever total_commits says.
            listed = self.commits[: self.listed]
            found = listed[(page - 1) * 100 : page * 100]
            return {"total_commits": len(self.commits), "commits": found}
        sha = path.split("/commits/")[-1].split("/")[0]
        if path.endswith("/pulls"):
            return self.pulls.get(sha, [])
        if "/check-runs?" in path:
            runs = self.runs.get(sha, [])
            return {"total_count": len(runs), "check_runs": runs}
        if path.endswith("/status"):
            return {"statuses": self.statuses.get(sha, [])}
        return self.open_issues


def commit(sha, parents=1):
    return {"sha": sha, "parents": [{"sha": f"p{n}"} for n in range(parents)]}


def pull(number, merged_as, head, merged=True):
    return {
        "number": number,
        "merge_commit_sha": merged_as,
        "merged_at": "2026-11-27T05:34:17Z" if merged else None,
        "head": {"sha": head},
    }


def check_run(name, conclusion="success", started="2026-11-27T01:00:00Z"):
    return {"name": name, "status": "completed", "conclusion": conclusion, "started_at": started}


def squash(sha, number, head, checks=("lint",)):
    """A commit, its squash-merged PR and that PR's head checks, as the fake's arguments."""
    return commit(sha), {sha: [pull(number, sha, head)]}, {head: [check_run(c) for c in checks]}


def run(github, tmp_path, forced=False, after="bbb2222", required="# comment\nlint\n"):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"forced": forced, "before": "aaa1111", "after": after}))
    checks = tmp_path / "required-checks"
    checks.write_text(required)
    argv = ["--repo", "o/r", "--event", str(event), "--required-checks", str(checks)]
    argv += ["--assignee", "owner", "--run-url", RUN_URL]
    return main(argv, stdin=io.StringIO(), rest=github.rest)


def test_a_push_that_kept_to_the_policy_opens_nothing_and_passes(capsys, tmp_path):
    sha, pulls, runs = squash("bbb2222", 7, "head7")
    github = FakeGitHub([sha], pulls, runs)

    code = run(github, tmp_path)

    assert code == 0
    assert capsys.readouterr().out == "main-audit: aaa1111...bbb2222 kept to the policy.\n"
    assert [call for call in github.calls if call[0] != "GET"] == []


ALARM = (
    "- **every commit on main comes from a merged PR** (ccc3333): ccc3333 was pushed to main "
    "without a PR. Revert it on main and open the change as a PR.\n"
    "\n"
    "Run: https://github.com/o/r/actions/runs/99\n"
)


def test_a_violation_opens_an_issue_assigned_to_the_given_login_and_fails(capsys, tmp_path):
    github = FakeGitHub([commit("ccc3333")])

    code = run(github, tmp_path, after="ccc3333")

    assert code == 1
    issue = {
        "title": "main-audit: a push to main bypassed the policy",
        "body": f"A push to main broke the policy:\n\n{ALARM}",
        "labels[]": "policy-violation",
        "assignees[]": "owner",
    }
    assert github.calls[-1] == ("POST", "repos/o/r/issues", issue)
    assert capsys.readouterr().out == f"{ALARM}\nmain-audit: opened #31 for owner.\n"


def test_each_pushed_commit_past_the_first_hundred_is_audited_with_its_pr_and_head_checks(
    capsys, tmp_path
):
    commits, pulls, runs = [], {}, {}
    for n in range(100):
        found, its_pulls, its_runs = squash(f"s{n}", n, f"h{n}", checks=("lint", "ci/legacy"))
        commits.append(found)
        pulls |= its_pulls
        runs |= its_runs
    commits += [commit("zzz9999"), commit("rrr8888"), commit("ooo7777")]
    pulls["zzz9999"] = [pull(41, "x", "hx41"), pull(42, "zzz9999", "hx42")]
    pulls["rrr8888"] = [pull(43, "zzz9999", "hx43")]
    # An open PR's merge_commit_sha is its test merge, not a commit on main.
    pulls["ooo7777"] = [pull(44, "test-merge", "hx44", merged=False)]
    runs |= {"hx42": [check_run("lint")], "hx43": [check_run("lint"), check_run("ci/legacy")]}
    statuses = {"hx42": [{"context": "ci/legacy", "state": "error"}]}
    github = FakeGitHub(commits, pulls, runs, statuses)

    run(github, tmp_path, after="ooo7777", required="lint\nci/legacy\n")

    assert capsys.readouterr().out.splitlines()[:3] == [
        "- **PRs merge only with every required check green** (zzz9999): #42 merged with "
        "ci/legacy (error). Fix what those checks found in a new PR, and wait for green before "
        "merging.",
        "- **PRs merge by squash** (rrr8888): #43 was rebase-merged: it merged as zzz9999, not as "
        "this commit. Merge PRs by squash only: turn off merge commits and rebase merging in the "
        "repo's settings, under Pull Requests.",
        "- **every commit on main comes from a merged PR** (ooo7777): ooo7777 was pushed to main "
        "without a PR. Revert it on main and open the change as a PR.",
    ]


def test_a_push_past_what_github_lists_is_a_violation_and_the_listed_commits_are_audited(
    capsys, tmp_path
):
    pushed = [squash(f"s{n}", n, f"h{n}") for n in range(3)]
    commits = [found for found, _, _ in pushed] + [commit("ccc3333")]
    pulls = {sha: prs for _, its, _ in pushed for sha, prs in its.items()}
    runs = {head: checks for _, _, its in pushed for head, checks in its.items()}
    github = FakeGitHub(commits, pulls, runs)
    github.listed = 3

    code = run(github, tmp_path, after="ccc3333")

    assert code == 1
    assert capsys.readouterr().out.splitlines()[:2] == [
        "- **every pushed commit is audited** (ccc3333): the push added 4 commits and GitHub "
        "lists only the first 3. Audit the rest by hand with `git log --first-parent` on main, "
        "then close this issue.",
        "",
    ]


def test_github_refusing_a_call_prints_what_it_said_and_the_fix_and_fails(capsys, tmp_path):
    refused = {"repos/o/r/issues": "HTTP 403: Resource not accessible by integration"}
    github = FakeGitHub([commit("ccc3333")], fail=refused)

    code = run(github, tmp_path, after="ccc3333")

    assert code == 1
    assert capsys.readouterr().out.splitlines()[-2:] == [
        "main-audit: GitHub refused: HTTP 403: Resource not accessible by integration",
        "  The workflow's token needs `issues: write`, and read access to contents, pull "
        "requests, checks and statuses.",
    ]


def test_a_violation_while_one_is_open_comments_on_it_instead(capsys, tmp_path):
    github = FakeGitHub([commit("ccc3333")], open_issues=[{"number": 30}])

    code = run(github, tmp_path, after="ccc3333")

    assert code == 1
    assert github.calls[-2:] == [
        ("GET", "repos/o/r/issues?labels=policy-violation&state=open", None),
        ("POST", "repos/o/r/issues/30/comments", {"body": f"Another push:\n\n{ALARM}"}),
    ]
    assert capsys.readouterr().out == f"{ALARM}\nmain-audit: commented on #30.\n"
