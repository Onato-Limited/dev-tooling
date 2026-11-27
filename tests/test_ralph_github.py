import json
import socket
import subprocess

import pytest

from devtool.ralph import GitHubError, Issue, PullRequest
from devtool.ralph_github import GitHubAdapter, gh_runner


class FakeGh:
    """Answers each `gh` call with the next canned reply and records what was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, args, stdin=None):
        self.calls.append((args, stdin))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps({"data": reply})


def adapter(gh, board_owner="owner"):
    return GitHubAdapter("owner/repo", board_owner, 2, "claude-review", gh)


def sub_issue(
    number, state="OPEN", status="Todo", blockers=(), project=2, labels=(), board_owner="owner"
):
    return {
        "number": number,
        "state": state,
        "labels": {"nodes": [{"name": name} for name in labels]},
        "blockedBy": {"nodes": [{"number": n, "state": s} for n, s in blockers]},
        "projectItems": {
            "nodes": [
                {
                    "project": {"number": project, "owner": {"login": board_owner}},
                    "fieldValueByName": {"name": status},
                }
            ]
        },
    }


def test_milestone_issues_come_from_the_parents_sub_issues_with_status_and_open_blockers():
    gh = FakeGh(
        {
            "repository": {
                "issue": {
                    "subIssues": {
                        "nodes": [
                            sub_issue(6, state="CLOSED", status="Done"),
                            sub_issue(7, blockers=[(6, "CLOSED")]),
                            sub_issue(8, blockers=[(6, "CLOSED"), (7, "OPEN")]),
                            sub_issue(9, status="Todo", project=5),
                            sub_issue(10, labels=["bug", "draft"]),
                        ]
                    }
                }
            }
        }
    )

    issues = adapter(gh).milestone_issues(2)

    assert issues == [
        Issue(6, "Done", is_open=False),
        Issue(7, "Todo"),
        Issue(8, "Todo", open_blockers=(7,)),
        Issue(9, "No status"),
        Issue(10, "Todo", draft=True),
    ]
    args, _ = gh.calls[0]
    assert args[:2] == ["api", "graphql"]
    assert "number=2" in args


def test_board_workflows_maps_each_built_in_workflow_to_whether_it_is_enabled():
    gh = FakeGh(
        {
            "repositoryOwner": {
                "projectV2": {
                    "workflows": {
                        "nodes": [
                            {"name": "Item closed", "enabled": False},
                            {"name": "Auto-add sub-issues to project", "enabled": True},
                        ]
                    }
                }
            }
        }
    )

    workflows = adapter(gh).board_workflows()

    assert workflows == {"Item closed": False, "Auto-add sub-issues to project": True}
    args, _ = gh.calls[0]
    assert "owner=owner" in args
    assert "project=2" in args


def sent_query(call):
    args, _ = call
    return args[args.index("-f") + 1]


def sent_variables(call):
    args, _ = call
    return [args[i + 1] for i, arg in enumerate(args) if arg in ("-f", "-F")][1:]


def test_a_board_owned_by_an_organization_or_another_user_is_read_from_that_owner():
    reply = {"repositoryOwner": {"projectV2": {"workflows": {"nodes": []}}}}
    for owner in ("some-org", "another-user"):
        gh = FakeGh(reply)

        adapter(gh, board_owner=owner).board_workflows()

        assert "repositoryOwner(login: $owner)" in sent_query(gh.calls[0])
        assert f"owner={owner}" in sent_variables(gh.calls[0])


def test_a_status_counts_only_from_the_board_owner_s_project_of_that_number():
    gh = FakeGh(
        {
            "repository": {
                "issue": {
                    "subIssues": {
                        "nodes": [
                            sub_issue(7, status="In review", board_owner="owner"),
                            sub_issue(8, status="In review", board_owner="some-org"),
                        ]
                    }
                }
            }
        }
    )

    issues = adapter(gh, board_owner="some-org").milestone_issues(2)

    assert issues == [Issue(7, "No status"), Issue(8, "In review")]


class FakeSubprocess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


def test_the_board_owner_matches_whatever_its_case():
    gh = FakeGh(
        {
            "repository": {
                "issue": {
                    "subIssues": {"nodes": [sub_issue(7, status="Todo", board_owner="Some-Org")]}
                }
            }
        }
    )

    assert adapter(gh, board_owner="some-org").milestone_issues(2) == [Issue(7, "Todo")]


def test_a_board_owner_github_does_not_know_fails_naming_it_and_the_fix():
    gh = FakeGh({"repositoryOwner": None})

    with pytest.raises(GitHubError) as error:
        adapter(gh, board_owner="no-such-org").board_workflows()

    assert str(error.value) == (
        "GitHub has no user or organization no-such-org. Set board_owner in .ralph/config to "
        "the login that owns project 2."
    )


def test_text_is_sent_as_a_string_and_only_numbers_as_numbers():
    gh = FakeGh({"repositoryOwner": {"projectV2": {"workflows": {"nodes": []}}}})

    adapter(gh, board_owner="2048").board_workflows()

    args, _ = gh.calls[0]
    assert args[args.index("owner=2048") - 1] == "-f"
    assert args[args.index("project=2") - 1] == "-F"


def test_gh_runner_sends_stdin_to_gh_and_returns_its_output():
    process = FakeSubprocess(stdout="ok\n")

    output = gh_runner(process)(["issue", "comment", "7"], "body")

    assert output == "ok\n"
    args, kwargs = process.calls[0]
    assert args == ["gh", "issue", "comment", "7"]
    assert kwargs["input"] == "body"


def test_gh_runner_raises_the_command_and_its_error_when_gh_fails():
    process = FakeSubprocess(returncode=1, stderr="HTTP 401: Bad credentials\n")

    with pytest.raises(GitHubError) as error:
        gh_runner(process)(["api", "graphql"], None)

    assert str(error.value) == "gh api graphql: HTTP 401: Bad credentials"


def test_gh_runner_errors_shorten_a_multi_line_query_to_its_name():
    process = FakeSubprocess(returncode=1, stderr="HTTP 502\n")

    with pytest.raises(GitHubError) as error:
        gh_runner(process)(["api", "graphql", "-f", "query=\nquery {\n}\n", "-F", "n=1"], None)

    assert str(error.value) == "gh api graphql -f query=… -F n=1: HTTP 502"


def test_tests_cannot_reach_github():
    with pytest.raises(RuntimeError, match="tests must not touch the network"):
        gh_runner(subprocess.run)(["--version"], None)
    with pytest.raises(RuntimeError, match="tests must not touch the network"):
        socket.create_connection(("api.github.com", 443))


def commit(**checks):
    """A PR commit and its check runs; no checks means GitHub ran none (a null rollup)."""
    if not checks:
        return {"commit": {"statusCheckRollup": None}}
    runs = [{"name": name.replace("_", "-"), "conclusion": c} for name, c in checks.items()]
    return {"commit": {"statusCheckRollup": {"contexts": {"nodes": [*runs, {}]}}}}


def pr_reply(merged=False, comments=(), commits=(), merge_state="CLEAN", head="", threads=()):
    return {
        "repository": {
            "pullRequest": {
                "merged": merged,
                "mergeStateStatus": merge_state,
                "headRefName": head,
                "reviewThreads": {"nodes": [{"isResolved": resolved} for resolved in threads]},
                "comments": {"nodes": [{"body": body} for body in comments]},
                "commits": {"nodes": list(commits)},
            }
        }
    }


def test_pull_request_counts_review_change_requests_and_the_trailing_ci_failures():
    gh = FakeGh(
        pr_reply(
            commits=[
                commit(ci="FAILURE", claude_review="FAILURE"),
                commit(ci="SUCCESS", claude_review="FAILURE"),
                commit(ci="FAILURE", claude_review="SUCCESS"),
                commit(),
                commit(ci="FAILURE", claude_review="FAILURE"),
            ],
            comments=["## Standards\nNo hard findings."],
        )
    )

    pr = adapter(gh).pull_request(41)

    assert pr == PullRequest(41, changes_requested=3, ci_failure_streak=2, has_retro_notes=False)
    assert "number=41" in sent_variables(gh.calls[0])


def test_pull_request_reports_merged_and_a_retro_notes_comment():
    gh = FakeGh(
        pr_reply(
            merged=True, comments=["Done.\n\n## Retro notes\n- Went fine."], commits=[commit()]
        )
    )

    assert adapter(gh).pull_request(44) == PullRequest(44, merged=True, has_retro_notes=True)


@pytest.mark.parametrize(
    ("state", "behind"), [("BEHIND", True), ("CLEAN", False), ("BLOCKED", False)]
)
def test_pull_request_says_whether_it_is_behind_main(state, behind):
    gh = FakeGh(pr_reply(merge_state=state, commits=[commit()]))

    assert adapter(gh).pull_request(54).behind is behind
    assert "mergeStateStatus" in sent_query(gh.calls[0])


def test_pull_request_names_its_branch():
    gh = FakeGh(pr_reply(head="someone/54-ralph", commits=[commit()]))

    assert adapter(gh).pull_request(54).branch == "someone/54-ralph"
    assert "headRefName" in sent_query(gh.calls[0])


@pytest.mark.parametrize(
    ("state", "commits", "held"),
    [
        ("BLOCKED", [commit(ci="FAILURE"), commit(ci="SUCCESS", claude_review="SUCCESS")], True),
        ("BLOCKED", [commit(ci="SUCCESS", claude_review="FAILURE")], False),
        ("BLOCKED", [commit(ci="SUCCESS"), commit()], False),
        ("CLEAN", [commit(ci="SUCCESS", claude_review="SUCCESS")], False),
    ],
)
def test_a_pull_request_says_whether_github_blocks_it_once_every_check_passed(state, commits, held):
    gh = FakeGh(pr_reply(merge_state=state, commits=commits))

    assert adapter(gh).pull_request(201).blocked_with_checks_passing is held


def test_any_failing_check_but_the_review_counts_toward_the_ci_streak():
    gh = FakeGh(
        pr_reply(
            commits=[
                commit(lint="FAILURE", claude_review="SUCCESS"),
                commit(coverage="FAILURE", lint="SUCCESS"),
                commit(claude_review="FAILURE"),
                commit(lint="SUCCESS", coverage="FAILURE"),
            ],
            comments=["## Retro notes\n- fine"],
        )
    )

    pr = adapter(gh).pull_request(41)

    assert (pr.ci_failure_streak, pr.changes_requested) == (3, 1)


def test_a_pull_request_counts_its_unresolved_review_threads():
    gh = FakeGh(pr_reply(threads=[True, False, True, False, False], commits=[commit()]))

    assert adapter(gh).pull_request(201).unresolved_threads == 3
    assert "reviewThreads" in sent_query(gh.calls[0])


def test_the_review_check_is_the_one_the_config_names():
    commits = [
        commit(verdict="FAILURE"),
        commit(verdict="FAILURE"),
        commit(claude_review="FAILURE"),
    ]
    gh = FakeGh(pr_reply(commits=commits))

    pr = GitHubAdapter("owner/repo", "owner", 2, "verdict", gh).pull_request(41)

    assert (pr.changes_requested, pr.ci_failure_streak) == (2, 1)
