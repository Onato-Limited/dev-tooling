import json
import subprocess
from datetime import datetime

from devtool.ralph import POLL_SECONDS, Issue, Outcome
from devtool.ralph_session import ClaudeSession


class FakeProcesses:
    """Records each command; `claude` streams `events` to its stdout and exits with `exit`
    after `polls` waits have timed out."""

    def __init__(self, *events, exit=0, polls=0):
        self.events = events
        self.exit = exit
        self.polls = polls
        self.waits = []  # each wait's timeout
        self.calls = []
        self.fails = {}  # git subcommand, one word or two → the CompletedProcess it fails with
        self.outputs = {}  # git subcommand, or another command's first two words → its stdout
        self.on_wait = nothing  # called with claude's start kwargs at each wait

    def start(self, args, **kwargs):
        self.calls.append((args, kwargs))
        for event in self.events:
            kwargs["stdout"].write(json.dumps(event) + "\n")
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

    def wait(self, timeout):
        self.processes.on_wait(self.kwargs)
        self.processes.waits.append(timeout)
        if len(self.processes.waits) <= self.processes.polls:
            raise subprocess.TimeoutExpired("claude", timeout)
        return self.processes.exit


def nothing(*_):
    pass


def result(text):
    return {"type": "result", "result": text}


def session(tmp_path, processes):
    repo = tmp_path / "checkout"
    (repo / ".ralph").mkdir(parents=True)
    (repo / ".ralph" / "PROMPT.md").write_text("Work on issue #{issue} on branch {branch}.\n")
    return repo, ClaudeSession(
        repo=repo,
        branch_prefix="someone",
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
    assert [args for args, _ in processes.calls[:3]] == [
        ["git", "-C", str(repo), "fetch", "origin"],
        [
            "git",
            "-C",
            str(repo),
            "for-each-ref",
            "--format=%(refname)",
            "refs/heads/someone/7-ralph",
            "refs/remotes/origin/someone/7-ralph",
        ],
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            str(worktree),
            "-b",
            "someone/7-ralph",
            "origin/main",
        ],
    ]
    args, kwargs = processes.claude()
    assert args == [
        "claude",
        "-p",
        "Work on issue #7 on branch someone/7-ralph.\n",
        "--permission-mode",
        "auto",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
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
    assert processes.claude()[1]["cwd"] == worktree


def test_a_session_on_a_branch_still_there_is_told_it_resumes_and_where_the_ruling_is(tmp_path):
    processes = FakeProcesses(result("PR: #52"))
    processes.outputs = {"for-each-ref": "refs/heads/someone/7-ralph\n", "gh pr": "52\n"}
    _, claude = session(tmp_path, processes)

    claude.work(Issue(7, "In progress"), nothing)

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
    claude.environ = {"PATH": "/usr/bin", "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "parent"}

    claude.work(Issue(7, "In progress"), nothing)

    env = processes.claude()[1]["env"]
    assert [k for k in env if k.startswith("CLAUDE")] == []


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
