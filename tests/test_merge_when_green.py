import io
import subprocess

import pytest

from devtool.merge_when_green import GitHubError, gh_rest, main
from devtool.review_threads import QueryError


class FakeGitHub:
    """GitHub as merge-when-green reads it: labelled PRs, their threads, comparisons and calls."""

    def __init__(self, prs=(), unresolved=None, behind_by=None, fail=None, threads=None):
        self.prs = list(prs)
        self.unresolved = unresolved or {}
        self.threads = threads or {}
        self.behind_by = behind_by or {}
        self.fail = fail or {}
        self.calls = []

    def query(self, text, variables):
        if "reviewThreads" in text:
            count = self.unresolved.get(variables["number"], 0)
            nodes = [review_thread("onato", "Why?")] * count
            nodes += self.threads.get(variables["number"], [])
            page = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}
            return {"repository": {"pullRequest": {"reviewThreads": page}}}
        self.calls.append(("query", variables))
        return {"repository": {"pullRequests": {"nodes": self.prs}}}

    def rest(self, method, path, fields=None):
        self.calls.append((method, path, fields))
        if path in self.fail:
            raise GitHubError(self.fail[path])
        if "/check-runs?" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            page = int(path.rsplit("page=", 1)[1])
            runs = next(pr["_check_runs"] for pr in self.prs if pr["headRefOid"] == sha)
            return {"total_count": len(runs), "check_runs": runs[(page - 1) * 100 : page * 100]}
        if method == "GET":
            return {"behind_by": self.behind_by.get(path, 0)}
        return {}


def person(login):
    return {"__typename": "Bot" if login == "claude" else "User", "login": login}


def review_thread(author, body, *replies):
    """An unresolved review thread: its first comment and any replies, as (login, body) pairs."""
    comments = [(author, body), *replies]
    nodes = [{"author": person(who), "body": text, "url": "u"} for who, text in comments]
    return {"isResolved": False, "comments": {"nodes": nodes}}


def check_run(name, conclusion="success", status="completed", started="2026-11-27T01:00:00Z"):
    """A check run as GET /commits/{sha}/check-runs returns it."""
    return {"name": name, "status": status, "conclusion": conclusion, "started_at": started}


def rollup_run(name, conclusion, started):
    """A check run as the GraphQL statusCheckRollup returns it, which leaves out re-runs."""
    return {
        "__typename": "CheckRun",
        "name": name,
        "status": "COMPLETED",
        "conclusion": conclusion,
        "startedAt": started,
    }


def status(context, state):
    return {"__typename": "StatusContext", "context": context, "state": state}


def pr_node(number=12, checks=None, rollup=(), comments=(), mergeable="MERGEABLE", sha="abc123"):
    runs = [check_run("lint")] if checks is None else checks
    return {
        # Not part of the GraphQL node: the fake serves these from the check-runs endpoint.
        "_check_runs": runs,
        "number": number,
        "title": "feat: a thing",
        "body": "Closes #4",
        "isDraft": False,
        "mergeable": mergeable,
        "headRefOid": sha,
        "baseRefName": "main",
        "labels": {"nodes": [{"name": "merge-when-green"}]},
        "comments": {"nodes": [{"body": body} for body in comments]},
        "commits": {
            "nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": list(rollup)}}}}]
        },
    }


def run(github, *extra, tmp_path, required_checks="# comment\nlint\n"):
    required = tmp_path / "required-checks"
    required.write_text(required_checks)
    argv = ["--repo", "o/r", "--required-checks", str(required), *extra]
    return main(argv, stdin=io.StringIO(), query=github.query, rest=github.rest)


def test_no_labelled_prs_says_so_and_passes(capsys, tmp_path):
    github = FakeGitHub()

    code = run(github, tmp_path=tmp_path)

    assert code == 0
    assert capsys.readouterr().out == "merge-when-green: no open PRs labelled merge-when-green.\n"
    assert github.calls == [("query", {"owner": "o", "name": "r", "label": "merge-when-green"})]


def test_a_pr_that_waits_prints_the_reason_and_passes(capsys, tmp_path):
    github = FakeGitHub([pr_node(checks=[check_run("lint", "failure")])])

    code = run(github, tmp_path=tmp_path)

    assert code == 0
    assert capsys.readouterr().out == "#12 waits: required checks not green: lint (failure)\n"
    assert github.calls[1:] == [
        ("GET", "repos/o/r/compare/main...abc123", None),
        ("GET", "repos/o/r/commits/abc123/check-runs?filter=latest&per_page=100&page=1", None),
    ]


def test_unfinished_runs_are_pending_and_status_contexts_count(capsys, tmp_path):
    github = FakeGitHub(
        [
            pr_node(
                checks=[check_run("lint", None, status="in_progress")],
                rollup=[status("ci/legacy", "ERROR")],
            )
        ]
    )

    run(github, tmp_path=tmp_path, required_checks="lint\nci/legacy\n")

    assert capsys.readouterr().out == (
        "#12 waits: required checks not green: lint (pending), ci/legacy (error)\n"
    )


def test_of_two_runs_with_one_name_the_newest_counts(capsys, tmp_path):
    newest_red = [
        check_run("lint", "failure", started="2026-11-27T02:00:00Z"),
        check_run("lint", "success", started="2026-11-27T01:00:00Z"),
    ]
    queued = [
        check_run("lint", "success", started="2026-11-27T01:00:00Z"),
        check_run("lint", None, status="queued", started=None),
    ]
    github = FakeGitHub(
        [pr_node(checks=newest_red), pr_node(number=13, checks=queued, sha="def456")]
    )

    run(github, tmp_path=tmp_path)

    assert capsys.readouterr().out == (
        "#12 waits: required checks not green: lint (failure)\n"
        "#13 waits: required checks not green: lint (pending)\n"
    )


def test_a_passing_re_run_counts_though_the_rollup_holds_only_cancelled_duplicates(
    capsys, tmp_path
):
    # Onato-Limited/doc-snark-simulator#55: review events start duplicate runs that concurrency
    # cancels, and the rollup never shows the re-run that passed.
    name = "review-threads / threads"
    rollup = [
        rollup_run(name, "SUCCESS", "2026-11-27T05:15:48Z"),
        rollup_run(name, "CANCELLED", "2026-11-27T05:21:05Z"),
    ]
    latest = [
        check_run(name, "success", started="2026-11-27T05:15:48Z"),
        check_run(name, "cancelled", started="2026-11-27T05:21:05Z"),
        check_run(name, "success", started="2026-11-27T05:22:25Z"),
    ]
    github = FakeGitHub([pr_node(checks=latest, rollup=rollup)])

    run(github, tmp_path=tmp_path, required_checks=f"{name}\n")

    assert capsys.readouterr().out == "#12 merged: feat: a thing (#12)\n"


def test_check_runs_past_the_first_hundred_are_read(capsys, tmp_path):
    runs = [check_run(f"job {n}") for n in range(100)] + [check_run("lint", "failure")]
    github = FakeGitHub([pr_node(checks=runs)])

    run(github, tmp_path=tmp_path)

    assert capsys.readouterr().out == "#12 waits: required checks not green: lint (failure)\n"


def test_unresolved_review_threads_are_counted(capsys, tmp_path):
    github = FakeGitHub([pr_node()], unresolved={12: 2})

    run(github, tmp_path=tmp_path)

    assert capsys.readouterr().out == "#12 waits: 2 blocking review threads\n"


def test_an_answered_judgement_thread_does_not_hold_up_the_merge(capsys, tmp_path):
    answered = review_thread("claude", "[Spec] judgement: maybe", ("onato", "Leaving it: …"))
    unanswered = review_thread("claude", "[Spec] judgement: maybe")
    github = FakeGitHub(
        [pr_node(), pr_node(number=13, sha="def456")], threads={12: [answered], 13: [unanswered]}
    )

    run(github, tmp_path=tmp_path)

    assert capsys.readouterr().out == (
        "#12 merged: feat: a thing (#12)\n#13 waits: 1 blocking review threads\n"
    )


def test_a_head_with_no_checks_at_all_waits_for_them(capsys, tmp_path):
    node = pr_node(checks=[])
    node["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = None

    run(FakeGitHub([node]), tmp_path=tmp_path)

    assert capsys.readouterr().out == "#12 waits: required checks not green: lint (missing)\n"


def test_behind_the_base_updates_the_branch_at_the_head_it_checked(capsys, tmp_path):
    github = FakeGitHub([pr_node()], behind_by={"repos/o/r/compare/main...abc123": 3})

    code = run(github, tmp_path=tmp_path)

    assert code == 0
    assert capsys.readouterr().out == (
        "#12 is behind main: updated the branch, so its checks run again\n"
    )
    assert github.calls[-1] == (
        "PUT",
        "repos/o/r/pulls/12/update-branch",
        {"expected_head_sha": "abc123"},
    )


def test_all_green_squash_merges_the_head_it_checked_linking_the_pr(capsys, tmp_path):
    github = FakeGitHub([pr_node(comments=["## Retro notes\nfine"])])

    code = run(github, "--require-retro-notes", tmp_path=tmp_path)

    assert code == 0
    assert capsys.readouterr().out == "#12 merged: feat: a thing (#12)\n"
    assert github.calls[-1] == (
        "PUT",
        "repos/o/r/pulls/12/merge",
        {
            "merge_method": "squash",
            "commit_title": "feat: a thing (#12)",
            "commit_message": "Closes #4",
            "sha": "abc123",
        },
    )


REFUSED_FIX = (
    "  The app's token needs read and write access to Contents and Pull requests here. A push "
    "after this run read the PR is refused too; the next run tries again.\n"
)


def test_a_refused_call_names_the_pr_and_the_fix_and_the_others_still_run(capsys, tmp_path):
    github = FakeGitHub(
        [pr_node(), pr_node(number=13, sha="def456")],
        fail={"repos/o/r/pulls/12/merge": "gh: Head branch was modified (HTTP 409)"},
    )

    code = run(github, tmp_path=tmp_path)

    assert code == 1
    assert capsys.readouterr().out == (
        "#12: GitHub refused: gh: Head branch was modified (HTTP 409)\n"
        + REFUSED_FIX
        + "#13 merged: feat: a thing (#13)\n"
    )


def test_gh_rest_sends_the_method_path_and_fields_and_returns_the_json():
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout='{"merged": true}')

    result = gh_rest("PUT", "repos/o/r/pulls/1/merge", {"sha": "abc", "n": "2"}, run=fake_run)

    assert result == {"merged": True}
    assert calls == [
        (
            ["gh", "api", "-X", "PUT", "repos/o/r/pulls/1/merge", "-f", "sha=abc", "-f", "n=2"],
            {"capture_output": True, "text": True},
        )
    ]


def test_gh_rest_raises_with_what_gh_said_when_it_fails():
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="gh: Not Found (HTTP 404)\n")

    with pytest.raises(GitHubError) as error:
        gh_rest("GET", "repos/o/r/compare/a...b", run=fake_run)

    assert str(error.value) == "gh: Not Found (HTTP 404)"


def test_prs_that_cannot_be_listed_fail_naming_the_error_and_the_fix(capsys, tmp_path):
    def query(text, variables):
        raise QueryError("gh: Resource not accessible by integration")

    code = main(
        ["--repo", "o/r", "--required-checks", str(tmp_path / "x")],
        stdin=io.StringIO(),
        query=query,
        rest=FakeGitHub().rest,
    )

    assert code == 1
    assert capsys.readouterr().out == (
        "merge-when-green: can't list the open PRs: gh: Resource not accessible by integration\n"
        + REFUSED_FIX
    )
