import contextlib
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from devtool.ralph import POLL_SECONDS, Issue, Outcome
from devtool.ralph_session import Bot, ClaudeSession
from devtool.ralph_token import TokenError


class FakeTokens:
    """Mints ghs_1, ghs_2, … in turn; the mints numbered in `fails` raise instead."""

    def __init__(self):
        self.minted = 0
        self.fails = set()
        self.env_fails = None  # what session_env raises, if it does

    def session_env(self, environ):
        if self.env_fails:
            raise TokenError(self.env_fails)
        return {**environ, "GH_TOKEN": "ghs_bot"}

    def current(self):
        self.minted += 1
        if self.minted in self.fails:
            raise TokenError("GitHub refused the token request: HTTP 502")
        return f"ghs_{self.minted}"


class FakeProcesses:
    """Records each command; `claude` streams `events` (a string as a raw line) to its stdout
    and exits with `exit` after `polls` waits have timed out."""

    def __init__(self, *events, exit=0, polls=0):
        self.events = events
        self.exit = exit
        self.polls = polls
        self.waits = []  # each wait's timeout
        self.calls = []
        self.fails = {}  # git subcommand, one word or two → the CompletedProcess it fails with
        self.outputs = {}  # git subcommand, or another command's first two words → its stdout
        self.on_wait = nothing  # called with claude's start kwargs at each wait
        self.killed_with_token = []  # at each kill, whether the token file was still there

    def start(self, args, **kwargs):
        self.calls.append((args, kwargs))
        for event in self.events:
            kwargs["stdout"].write((event if isinstance(event, str) else json.dumps(event)) + "\n")
        return FakeProcess(self, kwargs)

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        keys = (" ".join(args[3:5]), args[3]) if args[0] == "git" else (" ".join(args[:2]),)
        for key in keys:
            if key in self.fails:
                return self.fails[key]
        stdout = next((self.outputs[key] for key in keys if key in self.outputs), "")
        return subprocess.CompletedProcess(args, 0, stdout, "")

    def claude(self):
        return next((args, kwargs) for args, kwargs in self.calls if args[0] == "claude")


class FakeProcess:
    def __init__(self, processes, kwargs):
        self.processes = processes
        self.kwargs = kwargs

    def kill(self):
        self.processes.killed_with_token.append(token_file(self.kwargs).exists())
        self.killed = True

    def wait(self, timeout=None):
        if getattr(self, "killed", False):
            return -9
        self.processes.on_wait(self.kwargs)
        self.processes.waits.append(timeout)
        if len(self.processes.waits) <= self.processes.polls:
            raise subprocess.TimeoutExpired("claude", timeout)
        return self.processes.exit


def nothing(*_):
    pass


def token_file(kwargs):
    return Path(kwargs["env"]["RALPH_TOKEN_FILE"])


def result(text):
    return {"type": "result", "result": text}


def session(tmp_path, processes):
    repo = tmp_path / "checkout"
    (repo / ".ralph").mkdir(parents=True)
    (repo / ".ralph" / "PROMPT.md").write_text("Work on issue #{issue} on branch {branch}.\n")
    return repo, ClaudeSession(
        repo=repo,
        branch_prefix="someone",
        bot=Bot("some-app", 123),
        tokens=FakeTokens(),
        session_path=None,
        run=processes,
        start=processes.start,
        environ={"PATH": "/usr/bin"},
        now=lambda: datetime(2026, 11, 23, 1, 2, 3),
    )


def test_a_session_runs_claude_on_the_issue_in_its_own_worktree_and_logs_the_stream(tmp_path):
    processes = FakeProcesses({"type": "system"}, result("Opened it.\nPR: #52"))
    repo, claude = session(tmp_path, processes)

    outcome = claude.work(Issue(7, "In progress"), nothing)

    worktree = tmp_path / "checkout-7"
    assert outcome == Outcome(pr=52)
    assert [" ".join(args) for args, _ in processes.calls[:3]] == [
        f"git -C {repo} fetch origin",
        f"git -C {repo} for-each-ref --format=%(refname) refs/heads/someone/7-ralph"
        " refs/remotes/origin/someone/7-ralph",
        f"git -C {repo} worktree add {worktree} -b someone/7-ralph origin/main",
    ]
    args, kwargs = processes.claude()
    assert args[:3] == ["claude", "-p", "Work on issue #7 on branch someone/7-ralph.\n"]
    assert " ".join(args[3:]) == "--permission-mode auto --output-format stream-json --verbose"
    assert kwargs["cwd"] == worktree
    log = repo / ".ralph" / "logs" / "20261123-010203-7.log"
    assert log.read_text().splitlines()[-1] == json.dumps(result("Opened it.\nPR: #52"))


def test_an_issue_whose_worktree_is_still_there_is_resumed_in_it_without_adding_one(tmp_path):
    processes = FakeProcesses(result("PR: #52"))
    _, claude = session(tmp_path, processes)
    worktree = tmp_path / "checkout-7"
    worktree.mkdir()

    outcome = claude.work(Issue(7, "In progress"), nothing)

    assert outcome == Outcome(pr=52)
    adds = [args for args, _ in processes.calls if args[3:5] == ["worktree", "add"]]
    assert adds == []
    args, kwargs = processes.claude()
    assert kwargs["cwd"] == worktree
    assert "\nResuming: an earlier session left branch `someone/7-ralph`" in args[2]


def test_a_session_on_a_branch_still_there_is_told_it_resumes_and_where_the_ruling_is(tmp_path):
    processes = FakeProcesses(result("PR: #52"))
    processes.outputs = {"for-each-ref": "refs/heads/someone/7-ralph\n", "gh pr": "52\n"}
    _, claude = session(tmp_path, processes)

    claude.work(Issue(7, "In progress"), nothing)

    gh = next(kwargs for args, kwargs in processes.calls if args[0] == "gh")
    assert gh["env"]["GH_TOKEN"] == "ghs_bot"
    assert processes.claude()[0][2] == (
        "Work on issue #7 on branch someone/7-ralph.\n"
        "\nResuming: an earlier session left branch `someone/7-ralph` with open PR #52. Before"
        " anything else, read the latest comments on the issue and on the PR: the owner's"
        " ruling is there.\n"
    )


def test_a_session_resuming_a_branch_whose_pr_was_never_opened_is_told_there_is_none(tmp_path):
    processes = FakeProcesses(result("PR: #52"))
    processes.outputs = {"for-each-ref": "refs/heads/someone/7-ralph\n"}
    _, claude = session(tmp_path, processes)

    claude.work(Issue(7, "In progress"), nothing)

    assert processes.claude()[0][2].splitlines()[2] == (
        "Resuming: an earlier session left branch `someone/7-ralph` with no open PR. Before"
        " anything else, read the latest comments on the issue and on the PR: the owner's"
        " ruling is there."
    )


def test_an_issue_whose_branch_is_still_there_gets_a_worktree_on_that_branch(tmp_path):
    processes = FakeProcesses(result("PR: #52"))
    processes.outputs = {"for-each-ref": "refs/remotes/origin/someone/7-ralph\n"}
    repo, claude = session(tmp_path, processes)

    claude.work(Issue(7, "In progress"), nothing)

    worktree = tmp_path / "checkout-7"
    adds = [args for args, _ in processes.calls if args[3:5] == ["worktree", "add"]]
    assert adds == [["git", "-C", str(repo), "worktree", "add", str(worktree), "someone/7-ralph"]]


def test_while_claude_runs_the_session_calls_while_running_every_poll(tmp_path):
    processes = FakeProcesses(result("PR: #52"), polls=2)
    _, claude = session(tmp_path, processes)
    calls = []

    outcome = claude.work(Issue(7, "In progress"), lambda: calls.append(len(processes.waits)))

    assert calls == [1, 2]
    assert processes.waits == [POLL_SECONDS] * 3
    assert outcome == Outcome(pr=52)


def test_each_session_starts_fresh_even_when_the_loop_runs_inside_claude_code(tmp_path):
    processes = FakeProcesses(result("PR: #52"))
    _, claude = session(tmp_path, processes)
    claude.environ = {
        "PATH": "/usr/bin",
        "CLAUDECODE": "1",
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "CLAUDE_CODE_SESSION_ID": "parent",
        "CLAUDE_CODE_CHILD_SESSION": "1",
        "CLAUDE_PID": "4242",
    }

    claude.work(Issue(7, "In progress"), nothing)

    env = processes.claude()[1]["env"]
    assert [k for k in env if k.startswith("CLAUDE")] == []
    assert behind_session_bin(env["PATH"]) == "/usr/bin"


@pytest.mark.parametrize(
    "name", ["CLAUDE_CONFIG_DIR", "CLAUDE_CODE_SKIP_BEDROCK_AUTH", "CLAUDE_CODE_MAX_OUTPUT_TOKENS"]
)
def test_the_users_own_claude_settings_reach_the_session(tmp_path, name):
    processes = FakeProcesses(result("PR: #52"))
    _, claude = session(tmp_path, processes)
    claude.environ = {"PATH": "/usr/bin", "CLAUDECODE": "1", name: "set"}

    claude.work(Issue(7, "In progress"), nothing)

    env = processes.claude()[1]["env"]
    assert [(k, v) for k, v in env.items() if k.startswith("CLAUDE")] == [(name, "set")]


def test_the_configured_session_path_goes_first_on_the_sessions_path(tmp_path):
    processes = FakeProcesses(result("PR: #52"))
    _, claude = session(tmp_path, processes)
    claude.session_path = "/opt/tools/bin"

    claude.work(Issue(7, "In progress"), nothing)

    env = processes.claude()[1]["env"]
    assert behind_session_bin(env["PATH"]) == "/opt/tools/bin:/usr/bin"


@pytest.mark.parametrize(
    ("token_fails", "commands", "reason"),
    [
        (None, ["git", "git", "git", "gh"], "gh pr list failed: HTTP 401: Bad credentials"),
        ("HTTP 502", ["git", "git", "git"], "the bot's token: HTTP 502"),
    ],
    ids=["the lookup", "the bot's token"],
)
def test_a_failed_pr_lookup_when_resuming_needs_a_human_and_starts_no_claude(
    tmp_path, token_fails, commands, reason
):
    processes = FakeProcesses(result("PR: #52"))
    processes.outputs = {"for-each-ref": "refs/heads/someone/7-ralph\n"}
    error = "HTTP 401: Bad credentials\n"
    processes.fails = {"gh pr": subprocess.CompletedProcess([], 1, "", error)}
    _, claude = session(tmp_path, processes)
    claude.tokens.env_fails = token_fails

    outcome = claude.work(Issue(7, "In progress"), nothing)

    assert outcome == Outcome(pr=None, needs_human=reason)
    assert [args[0] for args, _ in processes.calls] == commands


def test_a_worktree_that_cannot_be_made_needs_a_human_and_starts_no_claude(tmp_path):
    processes = FakeProcesses(result("PR: #52"))
    error = "fatal: invalid reference: origin/main\n"
    processes.fails = {"worktree": subprocess.CompletedProcess([], 128, "", error)}
    _, claude = session(tmp_path, processes)

    outcome = claude.work(Issue(7, "In progress"), nothing)

    assert outcome == Outcome(
        pr=None,
        needs_human="git worktree add failed: fatal: invalid reference: origin/main",
    )
    assert [args[0] for args, _ in processes.calls] == ["git", "git", "git"]


def test_a_needs_human_marker_is_reported_to_the_loop_as_its_reason(tmp_path):
    text = "Stopped.\nPR: #53\nNEEDS_HUMAN: the spec doesn't say which SQLite version to pin\n"
    _, claude = session(tmp_path, FakeProcesses(result(text)))

    outcome = claude.work(Issue(8, "In progress"), nothing)

    assert outcome == Outcome(pr=53, needs_human="the spec doesn't say which SQLite version to pin")


def test_the_first_pr_line_is_the_issues_pr_and_the_rest_are_other_prs_it_opened(tmp_path):
    text = "Done.\nPR: #130\nPR: #126\nPR: #127\n"
    _, claude = session(tmp_path, FakeProcesses(result(text)))

    outcome = claude.work(Issue(18, "In progress"), nothing)

    assert outcome == Outcome(pr=130, also=(126, 127))


def test_a_session_that_ends_without_a_result_needs_a_human_and_points_at_its_log(tmp_path):
    repo, claude = session(tmp_path, FakeProcesses({"type": "system"}, exit=1))

    outcome = claude.work(Issue(9, "In progress"), nothing)

    log = repo / ".ralph" / "logs" / "20261123-010203-9.log"
    assert outcome == Outcome(pr=None, needs_human=f"the session ended without a result; see {log}")


def test_a_fetch_that_fails_needs_a_human_and_starts_no_claude(tmp_path):
    processes = FakeProcesses(result("PR: #52"))
    error = "fatal: unable to access 'https://github.com/': Could not resolve host\n"
    processes.fails = {"fetch": subprocess.CompletedProcess([], 128, "", error)}
    _, claude = session(tmp_path, processes)

    outcome = claude.work(Issue(7, "In progress"), nothing)

    assert outcome == Outcome(
        pr=None,
        needs_human="git fetch origin failed: fatal: unable to access 'https://github.com/':"
        " Could not resolve host",
    )
    assert [args[3] for args, _ in processes.calls] == ["fetch"]


@pytest.mark.parametrize(
    "last", [{"type": "result", "subtype": "error_max_turns"}, '{"type": "assistant", "mess']
)
def test_a_result_without_text_or_a_cut_off_line_is_a_session_without_a_result(tmp_path, last):
    repo, claude = session(tmp_path, FakeProcesses(last))

    outcome = claude.work(Issue(9, "In progress"), nothing)

    log = repo / ".ralph" / "logs" / "20261123-010203-9.log"
    assert outcome == Outcome(pr=None, needs_human=f"the session ended without a result; see {log}")


def test_a_session_starts_with_a_freshly_minted_token_in_a_file_only_it_can_read(tmp_path):
    processes = FakeProcesses(result("PR: #52"))
    _, claude = session(tmp_path, processes)
    seen = {}

    def start(args, **kwargs):
        seen.update(token=token_file(kwargs).read_text(), mode=token_file(kwargs).stat().st_mode)
        return processes.start(args, **kwargs)

    claude.start = start
    claude.work(Issue(7, "In progress"), nothing)

    assert (seen["token"], oct(seen["mode"] & 0o777)) == ("ghs_1", "0o600")


@pytest.mark.parametrize(
    ("fails", "tokens"),
    [(set(), ["ghs_1", "ghs_1", "ghs_2", "ghs_2"]), ({2}, ["ghs_1", "ghs_1", "ghs_1", "ghs_3"])],
    ids=["renewed", "a failed renewal keeps the old token and is tried again"],
)
def test_the_token_file_gets_a_new_token_once_45_minutes_pass_and_not_before(
    tmp_path, fails, tokens
):
    processes = FakeProcesses(result("PR: #52"), polls=3)
    _, claude = session(tmp_path, processes)
    claude.tokens.fails = fails
    clock = [datetime(2026, 11, 23, 1, 0, 0)]
    claude.now = lambda: clock[0]
    seen = []

    def on_wait(kwargs):
        seen.append(token_file(kwargs).read_text())
        clock[0] += timedelta(minutes=30) if len(seen) == 1 else timedelta(minutes=15)

    processes.on_wait = on_wait
    outcome = claude.work(Issue(7, "In progress"), nothing)

    # Waits at 0, 30, 45 and 60 minutes: renewed at 45 (or, failing that, at 60).
    assert (seen, outcome) == (tokens, Outcome(pr=52))


def test_the_session_is_told_where_its_token_file_is_and_given_no_token_itself(tmp_path):
    processes = FakeProcesses(result("PR: #52"))
    _, claude = session(tmp_path, processes)
    claude.environ = {"PATH": "/usr/bin", "GH_TOKEN": "owner-token", "GITHUB_TOKEN": "owner-token"}

    claude.work(Issue(7, "In progress"), nothing)

    env = processes.claude()[1]["env"]
    assert [k for k in env if not k.startswith("GIT_")] == ["PATH", "RALPH_TOKEN_FILE"]
    assert [v for v in env.values() if "ghs_" in v or "owner-token" in v] == []


def behind_session_bin(path):
    """The PATH the session would have had without its own `bin` in front."""
    first, rest = path.split(":", 1)
    assert first.endswith("/bin")
    return rest


def in_session(claude, processes, command):
    """What `command` prints when run in the claude session's environment, as it starts."""
    printed = []

    def start(args, **kwargs):
        shell = ["/bin/sh", "-c", command]
        printed.append(subprocess.run(shell, env=kwargs["env"], capture_output=True, text=True))
        return processes.start(args, **kwargs)

    claude.start = start
    claude.work(Issue(7, "In progress"), nothing)
    return printed[0].stdout


def test_gh_in_the_session_runs_the_real_gh_with_the_token_from_the_file(tmp_path):
    processes = FakeProcesses(result("PR: #52"))
    _, claude = session(tmp_path, processes)
    real = tmp_path / "real-bin"
    real.mkdir()
    (real / "gh").write_text('#!/bin/sh\necho "$GH_TOKEN $*"\n')
    (real / "gh").chmod(0o755)
    claude.environ = {"PATH": f"{real}:/usr/bin:/bin"}

    assert in_session(claude, processes, "gh pr view 52") == "ghs_1 pr view 52\n"


def test_git_in_the_session_pushes_to_github_with_the_token_from_the_file(tmp_path):
    processes = FakeProcesses(result("PR: #52"))
    _, claude = session(tmp_path, processes)
    # The owner's own git config stays out of it.
    claude.environ = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1"}

    printed = in_session(
        claude, processes, "printf 'protocol=https\\nhost=github.com\\n\\n' | git credential fill"
    )

    assert printed == "protocol=https\nhost=github.com\nusername=x-access-token\npassword=ghs_1\n"


def interrupted():
    raise KeyboardInterrupt


@pytest.mark.parametrize("while_running", [nothing, interrupted])
def test_the_token_file_is_deleted_when_the_session_ends_however_it_ends(tmp_path, while_running):
    processes = FakeProcesses(result("PR: #52"), polls=1)
    _, claude = session(tmp_path, processes)

    with contextlib.suppress(KeyboardInterrupt):
        claude.work(Issue(7, "In progress"), while_running)

    private = token_file(processes.claude()[1]).parent
    assert [path for path in [private] if path.exists()] == []
    assert processes.killed_with_token == [True]  # claude stopped before its token went


def test_the_session_commits_and_pushes_as_the_bot_not_the_owner(tmp_path):
    processes = FakeProcesses(result("PR: #52"))
    _, claude = session(tmp_path, processes)

    claude.work(Issue(7, "In progress"), nothing)

    env = processes.claude()[1]["env"]
    bot = "some-app[bot]"
    email = "123+some-app[bot]@users.noreply.github.com"
    assert {k: env[k] for k in env if k.startswith("GIT_")} == {
        "GIT_AUTHOR_NAME": bot,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": bot,
        "GIT_COMMITTER_EMAIL": email,
        # The remote is SSH, which would push with the owner's key: go over HTTPS with the
        # token file's token, through git-credential-ralph.
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "url.https://github.com/.insteadOf",
        "GIT_CONFIG_VALUE_0": "git@github.com:",
        "GIT_CONFIG_KEY_1": "credential.https://github.com.helper",
        "GIT_CONFIG_VALUE_1": "",
        "GIT_CONFIG_KEY_2": "credential.https://github.com.helper",
        "GIT_CONFIG_VALUE_2": "ralph",
    }
    assert behind_session_bin(env["PATH"]) == "/usr/bin"
