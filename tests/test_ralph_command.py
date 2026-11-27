import subprocess
from dataclasses import replace

import pytest
from ralph_fakes import FakeGitHub, FakeSession

from devtool import cli, ralph_command
from devtool.ralph import TODO, GitHubError, Issue, Outcome, PullRequest
from devtool.ralph_command import NoDevices, Parts, assemble, branch_prefix, main
from devtool.ralph_config import Config, ConfigError
from devtool.ralph_session import Bot
from devtool.ralph_token import TokenError
from devtool.ralph_wiring import CommandDevices, DevicesError

CONFIG = Config(
    app_id="1", installation_id="2", app_slug="some-app", board_owner="someone", board_number=3,
    merge_mode="label", review_check="review / claude", audit_check="audit / main",
    update_hook="scripts/after-merge",
)  # fmt: skip
REQUIRED = (
    "app_id=1\ninstallation_id=2\napp_slug=some-app\nboard_owner=someone\nboard_number=3\n"
    "merge_mode=auto\n"
)
GH = {("repo", "view"): "owner/repo\n", ("api", "user"): "The-Owner\n"}
GH[("api", "users/some-app[bot]")] = "123\n"


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
    assert [github.review_check, github.audit_check, github.merge_mode, github.approver] == [
        "review / claude", "audit / main", "label", "The-Owner"
    ]  # fmt: skip
    assert [session.bot, session.update_hook, session.repo] == [
        Bot("some-app", 123), "scripts/after-merge", tmp_path
    ]  # fmt: skip
    assert github.branch_prefix == session.branch_prefix == "someone"


def test_the_check_before_the_loop_mints_a_token(tmp_path):
    minted = []
    tokens = type("Tokens", (), {"current": lambda self: minted.append(1) or "ghs_bot"})()

    assemble(CONFIG, tmp_path, gh, git_identity(), subprocess.Popen, {}, tokens=tokens).preflight()

    assert minted == [1]


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


def test_the_branch_prefix_is_the_github_user_or_else_the_emails_name_or_says_to_set_one(tmp_path):
    named = [("someone", "x@example.com"), ("", "123+someone@users.noreply.github.com"),
             ("", "someone@example.com")]  # fmt: skip
    assert {branch_prefix(git_identity(*identity), tmp_path) for identity in named} == {"someone"}
    with pytest.raises(ConfigError, match="git config github.user <login>"):
        branch_prefix(git_identity(user="", email=""), tmp_path)


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
    built = Parts(FakeGitHub([Issue(7, TODO)]), None, NoDevices(), None, improves=False)

    assert main(["--milestone", "2", "--dry-run"], repo=tmp_path, connect=Connect(built)) == 0
    assert capsys.readouterr().out == "Next: #7\n"


def test_a_lookup_that_fails_stops_with_its_error_before_any_session(tmp_path, capsys):
    config_file(tmp_path)
    failed = GitHubError("gh api users/some-app[bot] failed: HTTP 404")

    assert main(["--milestone", "2"], repo=tmp_path, connect=Connect(fails=failed)) == 1
    assert capsys.readouterr().out == f"Ralph loop stopped: {failed}\n"


class Failing:
    """A session, devices, or the check before the loop, whose first call raises `error`."""

    def __init__(self, error):
        self.error = error

    def __call__(self):
        raise self.error

    def work(self, issue, while_running):
        raise self.error

    def issues(self):
        raise self.error


NO_KEY = "Ralph loop can't start: no private key for the some-app GitHub App in the login keychain"
NO_MINT = "Ralph loop can't get a GitHub token: POST /app/installations/2/access_tokens: HTTP 502"
NO_LIST = "scripts/devices list failed (exit 1): no pool"


@pytest.mark.parametrize(
    ("where", "error", "stop"),
    [
        ("start", TokenError(NO_KEY), NO_KEY),
        ("session", TokenError(NO_MINT), NO_MINT),
        ("devices", DevicesError(NO_LIST), f"Ralph loop stopped: {NO_LIST}"),
    ],
    ids=["a key missing at the start", "a mint that fails in a session", "a device list"],
)
def test_an_error_stops_the_loop_through_notify_only_and_exits_1(
    tmp_path, capsys, where, error, stop
):
    config_file(tmp_path)
    notes = []
    github = FakeGitHub([] if where == "devices" else [Issue(7, TODO)])
    fail = Failing(error)
    built = Parts(github, fail, fail, notes.append, False, fail if where == "start" else nothing)

    assert main(["--milestone", "2"], repo=tmp_path, connect=Connect(built)) == 1
    assert notes == [stop]  # a TokenError already says "Ralph loop can't …"
    assert capsys.readouterr().out == ""
    if where == "start":  # nothing was taken, so no issue is left in In progress
        assert github.moves == []


def nothing():
    pass


def test_devtool_runs_the_ralph_command():
    assert cli.COMMANDS["ralph"].main is main


def test_a_run_gets_the_iteration_cap_and_the_improver_and_tells_only_the_owner(tmp_path, capsys):
    config_file(tmp_path)
    notes = []
    github = FakeGitHub([Issue(7, TODO), Issue(8, TODO)])
    session = FakeSession(github, {7: (Outcome(pr=40), PullRequest(40), False)})
    built = Parts(github, session, NoDevices(), notes.append, improves=True)

    assert (
        main(["--milestone", "2", "--max-iterations", "1"], repo=tmp_path, connect=Connect(built))
        == 0
    )
    assert notes[-1].startswith("Ralph loop stopped: iteration cap (1) reached.")
    assert (session.improved, capsys.readouterr().out) == ([((40,), 1)], "")


def test_a_live_run_mints_as_the_configured_app_reading_its_key_only_to_sign(tmp_path, monkeypatch):
    built, read = {}, []
    monkeypatch.setattr(ralph_command, "assemble", lambda *args, **kwargs: built.update(kwargs))
    monkeypatch.setattr(ralph_command, "keychain_key", lambda slug, run: read.append(slug) or "key")
    signer = lambda key, run: lambda data: f"{key} signed {data.decode()}".encode()  # noqa: E731
    monkeypatch.setattr(ralph_command, "openssl_signer", signer)

    ralph_command.live(CONFIG, tmp_path)

    tokens = built["tokens"]
    assert (tokens.app_id, tokens.installation_id, read) == ("1", "2", [])
    assert (tokens.sign(b"claims"), read) == (b"key signed claims", ["some-app"])
