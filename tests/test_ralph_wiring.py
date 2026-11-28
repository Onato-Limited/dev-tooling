import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

from devtool.ralph import GitHubError, branch_name
from devtool.ralph_session import Bot
from devtool.ralph_wiring import CommandDevices, DevicesError, approver, bot, notifier


def test_the_default_prompt_names_no_project_and_has_the_placeholders_and_the_lines_read_back():
    prompt = files("devtool").joinpath("ralph_prompt.md").read_text()

    assert all(
        part in prompt for part in ("{issue}", "{branch}", "`PR: #<number>`", "`NEEDS_HUMAN: ")
    )
    assert not [name for name in ("onato", "bench") if name in prompt.lower()]
    assert "GH_TOKEN" not in prompt  # the session's gh reads a token file; there's none to read


def test_the_branch_name_is_the_prefix_then_the_issue():
    assert branch_name("someone", 7) == "someone/7-ralph"


class FakeRun:
    """Answers each command with the next (exit, stdout, stderr) and records what ran."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        code, out, err = self.replies.pop(0)
        return subprocess.CompletedProcess(args, code, out, err)


def test_the_devices_are_the_issues_the_list_command_prints(tmp_path):
    run = FakeRun((0, "7\n12\n", ""))

    assert CommandDevices(
        "scripts/devices list", "scripts/devices prune", tmp_path, run
    ).issues() == [7, 12]
    args, kwargs = run.calls[0]
    assert (args, kwargs["cwd"]) == (["scripts/devices", "list"], tmp_path)


def test_a_list_command_that_fails_raises_naming_it_and_its_error(tmp_path):
    run = FakeRun((1, "", "no pool\n"))
    devices = CommandDevices("scripts/devices list", "scripts/devices prune", tmp_path, run)

    with pytest.raises(DevicesError, match=r"^scripts/devices list failed \(exit 1\): no pool$"):
        devices.issues()


def test_a_list_command_that_prints_something_else_raises_saying_what_it_must_print(tmp_path):
    run = FakeRun((0, "issue-7\n", ""))
    devices = CommandDevices("scripts/devices list", "scripts/devices prune", tmp_path, run)

    with pytest.raises(DevicesError) as error:
        devices.issues()

    assert str(error.value) == (
        "scripts/devices list printed 'issue-7': it must print one issue number per line"
    )


def test_a_device_is_pruned_with_the_prune_command_and_its_issue(tmp_path):
    run = FakeRun((0, "", ""))

    failed = CommandDevices("scripts/devices list", "scripts/devices prune", tmp_path, run).prune(7)

    assert failed is None
    assert run.calls[0][0] == ["scripts/devices", "prune", "7"]


def test_a_prune_that_fails_says_what_ran_and_its_error(tmp_path):
    run = FakeRun((2, "", "leased to 'issue-7 bench'\n"))

    failed = CommandDevices("scripts/devices list", "scripts/devices prune", tmp_path, run).prune(7)

    assert failed == "scripts/devices prune 7 failed (exit 2): leased to 'issue-7 bench'"


def test_notify_prints_the_message_and_runs_the_project_command_in_the_checkout(capsys, tmp_path):
    run = FakeRun((0, "", ""))

    notifier("scripts/notify --quiet", tmp_path, run)("Ralph loop stopped: milestone complete.")

    assert run.calls[0][1]["cwd"] == tmp_path

    assert capsys.readouterr().out == "Ralph loop stopped: milestone complete.\n"
    assert run.calls[0][0] == [
        "scripts/notify",
        "--quiet",
        "Ralph loop stopped: milestone complete.",
    ]


def test_without_a_notify_command_the_message_is_only_printed(capsys):
    run = FakeRun()

    notifier(None, Path("."), run)("Ralph loop stopped: milestone complete.")

    assert (capsys.readouterr().out, run.calls) == ("Ralph loop stopped: milestone complete.\n", [])


def test_the_approver_is_the_login_gh_is_signed_in_as_exactly_as_github_returns_it():
    calls = []

    login = approver(lambda args, stdin: calls.append(args) or "The-Owner\n")

    assert (login, calls) == ("The-Owner", [["api", "user", "--jq", ".login"]])


def test_the_bot_is_the_app_slug_and_its_bot_users_id():
    calls = []

    found = bot("some-app", lambda args, stdin: calls.append(args) or "123\n")

    assert (found, calls) == (Bot("some-app", 123), [["api", "users/some-app[bot]", "--jq", ".id"]])


def test_a_bot_lookup_that_fails_raises_the_github_error():
    def refused(args, stdin):
        raise GitHubError("gh api users/some-app[bot] failed: HTTP 404")

    with pytest.raises(GitHubError, match=r"users/some-app\[bot\] failed: HTTP 404"):
        bot("some-app", refused)


def prompt_text():
    return " ".join(files("devtool").joinpath("ralph_prompt.md").read_text().split())


def test_a_check_only_the_owners_label_passes_is_not_fixed_with_commits():
    prompt = prompt_text()

    assert "`size` (`size-approved`) or `review-paths` (`review-approved`)" in prompt
    assert "don't try to make it pass with more commits" in prompt


def test_the_approval_request_is_a_pr_comment_not_the_pr_body():
    prompt = prompt_text()

    assert "post a PR comment headed `## Needs your approval`" in prompt
    assert "naming the check, the label, and why" in prompt
    assert "say why in its body" not in prompt


def test_needs_human_for_an_approval_names_the_pr_and_the_label():
    assert "`NEEDS_HUMAN: PR #<number> needs <label>: <one-line reason>`" in prompt_text()


def test_the_prompt_says_the_loop_resumes_the_issue_once_the_label_is_added():
    assert (
        "Once the owner adds the label, the loop moves the issue back to Todo, and a resumed "
        "session finishes the PR." in prompt_text()
    )
