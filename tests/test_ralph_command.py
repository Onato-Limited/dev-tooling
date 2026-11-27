import subprocess
from dataclasses import replace

import pytest
from ralph_fakes import FakeGitHub

from devtool import cli, ralph_command
from devtool.ralph import TODO, GitHubError, Issue
from devtool.ralph_command import NoDevices, Parts, assemble, branch_prefix, main
from devtool.ralph_config import Config, ConfigError
from devtool.ralph_session import Bot
from devtool.ralph_token import TokenError
from devtool.ralph_wiring import CommandDevices, DevicesError

CONFIG = Config(
    app_id="1",
    installation_id="2",
    app_slug="some-app",
    board_owner="someone",
    board_number=3,
    merge_mode="label",
    audit_check="audit / main",
    update_hook="scripts/after-merge",
)
REQUIRED = (
    "app_id=1\ninstallation_id=2\napp_slug=some-app\nboard_owner=someone\nboard_number=3\n"
    "merge_mode=auto\n"
)
GH = {
    ("repo", "view"): "owner/repo\n",
    ("api", "user"): "The-Owner\n",
    ("api", "users/some-app[bot]"): "123\n",
}


def gh(args, stdin):
    return GH[tuple(args[:2])]


def git_identity(user="", email="someone@example.com"):
    def run(args, **kwargs):
        out = user if args[-1] == "github.user" else email
        return subprocess.CompletedProcess(args, 0 if out else 1, out, "")

    return run


def parts(tmp_path, config=CONFIG):
    return assemble(config, tmp_path, gh, git_identity(), subprocess.Popen, {}, tokens=None)


def test_the_adapter_and_session_are_built_from_the_config_and_what_github_says(tmp_path):
    built = parts(tmp_path)

    github, session = built.github, built.session
    assert f"{github.owner}/{github.repo} {github.board_owner}/{github.board_number}" == (
        "owner/repo someone/3"
    )
    assert [github.audit_check, github.merge_mode, github.approver] == [
        "audit / main", "label", "The-Owner"
    ]  # fmt: skip
    assert [session.bot, session.update_hook, session.repo] == [
        Bot("some-app", 123), "scripts/after-merge", tmp_path
    ]  # fmt: skip
    assert github.branch_prefix == session.branch_prefix == "someone"


def test_a_relative_session_path_is_resolved_against_the_checkout(tmp_path):
    built = parts(tmp_path, replace(CONFIG, session_path="tools/bin:/opt/bin"))

    assert built.session.session_path == f"{tmp_path}/tools/bin:/opt/bin"


def test_devices_come_from_the_cleanup_commands_and_without_them_there_are_none(tmp_path):
    config = replace(
        CONFIG, cleanup_list="scripts/devices list", cleanup_prune="scripts/devices prune"
    )

    devices = parts(tmp_path, config).devices

    assert isinstance(devices, CommandDevices)
    assert (devices.list_command, devices.prune_command) == (
        "scripts/devices list",
        "scripts/devices prune",
    )
    assert isinstance(parts(tmp_path).devices, NoDevices)
    assert (NoDevices().issues(), NoDevices().prune(7)) == ([], None)


def test_one_cleanup_command_without_the_other_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="set both cleanup_list and cleanup_prune, or neither"):
        parts(tmp_path, replace(CONFIG, cleanup_list="scripts/devices list"))


def test_the_improver_runs_only_when_the_project_has_its_prompt(tmp_path):
    assert parts(tmp_path).improves is False
    (tmp_path / ".ralph").mkdir()
    (tmp_path / ".ralph" / "IMPROVE.md").write_text("Review {prs}.\n")
    assert parts(tmp_path).improves is True


@pytest.mark.parametrize(
    ("user", "email", "prefix"),
    [
        ("someone", "other@example.com", "someone"),
        ("", "123+someone@users.noreply.github.com", "someone"),
        ("", "someone@example.com", "someone"),
    ],
)
def test_the_branch_prefix_is_the_github_user_or_else_the_emails_name(
    tmp_path, user, email, prefix
):
    assert branch_prefix(git_identity(user, email), tmp_path) == prefix


def config_file(tmp_path, text=REQUIRED):
    (tmp_path / ".ralph").mkdir(exist_ok=True)
    (tmp_path / ".ralph" / "config").write_text(text)


class Connect:
    """Hands `main` the parts it would build, and records whether it was asked."""

    def __init__(self, built=None, fails=None):
        self.built, self.fails, self.calls = built, fails, 0

    def __call__(self, config, repo):
        self.calls += 1
        if self.fails:
            raise self.fails
        return self.built


def test_a_bad_config_prints_its_error_and_exits_1_before_touching_github(tmp_path, capsys):
    config_file(tmp_path, "app_id=1\n")
    connect = Connect()

    assert main(["--milestone", "2"], repo=tmp_path, connect=connect) == 1
    assert "is missing installation_id" in capsys.readouterr().out
    assert connect.calls == 0


def test_a_dry_run_prints_the_next_issue_and_starts_nothing(tmp_path, capsys):
    config_file(tmp_path)
    built = Parts(
        FakeGitHub([Issue(7, TODO)]), session=None, devices=NoDevices(), notify=None, improves=False
    )

    assert main(["--milestone", "2", "--dry-run"], repo=tmp_path, connect=Connect(built)) == 0
    assert capsys.readouterr().out == "Next: #7\n"


def test_a_lookup_that_fails_stops_with_its_error_before_any_session(tmp_path, capsys):
    config_file(tmp_path)
    failed = GitHubError("gh api users/some-app[bot] failed: HTTP 404")

    assert main(["--milestone", "2"], repo=tmp_path, connect=Connect(fails=failed)) == 1
    assert capsys.readouterr().out == (
        "Ralph loop stopped: gh api users/some-app[bot] failed: HTTP 404\n"
    )


class Failing:
    """A session, or devices, whose first call raises `error`."""

    def __init__(self, error):
        self.error = error

    def work(self, issue, while_running):
        raise self.error

    def issues(self):
        raise self.error


@pytest.mark.parametrize(
    "error",
    [
        TokenError("GitHub refused the token request: HTTP 502"),
        DevicesError("scripts/devices list failed (exit 1): no pool"),
    ],
    ids=["a token that can't be minted", "a device list that fails"],
)
def test_an_error_from_the_running_loop_is_the_stop_message_and_exits_1(tmp_path, error):
    config_file(tmp_path)
    notes = []
    github = FakeGitHub([] if isinstance(error, DevicesError) else [Issue(7, TODO)])
    built = Parts(github, Failing(error), Failing(error), notes.append, improves=False)

    assert main(["--milestone", "2"], repo=tmp_path, connect=Connect(built)) == 1
    assert notes == [f"Ralph loop stopped: {error}"]


def test_devtool_runs_the_ralph_command():
    assert cli.COMMANDS["ralph"].main is main


def test_without_a_git_identity_the_branch_prefix_is_a_config_error_naming_the_fix(tmp_path):
    with pytest.raises(ConfigError, match="git config github.user <login>"):
        branch_prefix(git_identity(user="", email=""), tmp_path)


def test_a_run_that_ends_tells_the_owner_and_exits_0(tmp_path):
    config_file(tmp_path)
    notes = []
    built = Parts(FakeGitHub([]), None, NoDevices(), notes.append, improves=False)

    assert main(["--milestone", "2"], repo=tmp_path, connect=Connect(built)) == 0
    assert notes == ["Ralph loop stopped: milestone complete."]


def test_a_live_run_mints_as_the_configured_app_and_reads_no_key_until_it_mints(
    tmp_path, monkeypatch
):
    built = {}
    ran = []
    monkeypatch.setattr(ralph_command, "assemble", lambda *args, **kwargs: built.update(kwargs))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: ran.append(args))

    ralph_command.live(CONFIG, tmp_path)

    assert (built["tokens"].app_id, built["tokens"].installation_id, ran) == ("1", "2", [])


def test_a_live_runs_signer_reads_the_apps_key_from_the_keychain_when_it_signs(
    tmp_path, monkeypatch
):
    built = {}
    monkeypatch.setattr(ralph_command, "assemble", lambda *args, **kwargs: built.update(kwargs))
    monkeypatch.setattr(ralph_command, "keychain_key", lambda slug, run: f"key of {slug}")
    signer = lambda key, run: lambda data: f"{key} signed {data.decode()}".encode()  # noqa: E731
    monkeypatch.setattr(ralph_command, "openssl_signer", signer)

    ralph_command.live(CONFIG, tmp_path)

    assert built["tokens"].sign(b"claims") == b"key of some-app signed claims"
