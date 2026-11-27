import json
import socket
import subprocess

import pytest

from devtool.ralph import GitHubError, Issue
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
