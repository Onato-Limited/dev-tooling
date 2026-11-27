"""`ClaudeSession.update` against real repos: an origin, and the checkout the loop runs from."""

import subprocess
from datetime import datetime

import pytest

from devtool.ralph import PullRequest
from devtool.ralph_session import Bot, ClaudeSession
from devtool.ralph_token import TokenError

IDENTITY = ["-c", "user.name=Owner", "-c", "user.email=owner@example.com"]


class FakeTokens:
    fails = None  # what minting raises, if it does

    def session_env(self, environ):
        if self.fails:
            raise TokenError(self.fails)
        return {**environ, "GH_TOKEN": "ghs_bot"}


def git(where, *args):
    done = subprocess.run(
        ["git", "-C", str(where), *IDENTITY, *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def commit(where, message, **files):
    for name, text in files.items():
        (where / name).write_text(text)
    git(where, "add", "-A")
    git(where, "commit", "-q", "-m", message)


def repos(tmp_path, update_hook=None):
    """An origin whose `main` has a PR branch `pr` cut from it, and a clone as the checkout."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(origin), str(seed))
    commit(seed, "Start", **{"a.txt": "a\n"})
    git(seed, "push", "-q", "origin", "HEAD:main")
    git(seed, "checkout", "-q", "-b", "pr")
    commit(seed, "The PR's work", **{"b.txt": "b\n"})
    git(seed, "push", "-q", "origin", "pr")
    git(seed, "checkout", "-q", "main")
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", "-q", str(origin), str(checkout))
    return seed, ClaudeSession(
        repo=checkout,
        branch_prefix="someone",
        bot=Bot("some-app", 123),
        tokens=FakeTokens(),
        session_path=None,
        update_hook=update_hook,
        run=subprocess.run,
        start=subprocess.Popen,
        environ={"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "GIT_CONFIG_NOSYSTEM": "1"},
        now=lambda: datetime(2026, 11, 25, 1, 2, 3),
    )


def on_origin(seed, path, branch="pr"):
    git(seed, "fetch", "-q", "origin")
    return git(seed, "show", f"origin/{branch}:{path}")


def main_moves_on(seed):
    commit(seed, "Main moves on", **{"a.txt": "a, later\n"})
    git(seed, "push", "-q", "origin", "HEAD:main")


def main_changes_b(seed):
    """The PR and `main` both change b.txt, so merging `main` in conflicts; the PR's head."""
    git(seed, "checkout", "-q", "pr")
    commit(seed, "The PR changes b", **{"b.txt": "the PR's b\n"})
    git(seed, "push", "-q", "origin", "pr")
    before = git(seed, "rev-parse", "HEAD")
    git(seed, "checkout", "-q", "main")
    commit(seed, "Main changes b", **{"b.txt": "main's b\n"})
    git(seed, "push", "-q", "origin", "HEAD:main")
    return before


def test_update_merges_main_into_the_prs_branch_and_pushes_it_as_the_bot(tmp_path):
    seed, session = repos(tmp_path)
    main_moves_on(seed)

    failed = session.update(PullRequest(40, behind=True, branch="pr"))

    assert failed is None
    assert (on_origin(seed, "a.txt"), on_origin(seed, "b.txt")) == ("a, later\n", "b\n")
    author = git(seed, "log", "-1", "--format=%an <%ae>", "origin/pr")
    assert author == "some-app[bot] <123+some-app[bot]@users.noreply.github.com>\n"


def test_the_update_hook_runs_after_the_merge_in_its_worktree_and_may_commit(tmp_path):
    hook = "echo hooked > c.txt && git add c.txt && git commit -qm 'The hook adds c'"
    seed, session = repos(tmp_path, update_hook=hook)
    main_moves_on(seed)

    failed = session.update(PullRequest(40, behind=True, branch="pr"))

    assert failed is None
    assert (on_origin(seed, "a.txt"), on_origin(seed, "c.txt")) == ("a, later\n", "hooked\n")


@pytest.mark.parametrize(
    "hook",
    [
        "git checkout -q --theirs b.txt && git add b.txt",
        "git checkout -q --theirs b.txt && git add b.txt && git commit -q --no-edit",
    ],
    ids=["resolved", "resolved and committed"],
)
def test_a_conflict_the_update_hook_resolves_is_committed_and_pushed(tmp_path, hook):
    seed, session = repos(tmp_path, update_hook=hook)
    main_changes_b(seed)

    failed = session.update(PullRequest(40, behind=True, branch="pr"))

    assert failed is None
    assert on_origin(seed, "b.txt") == "main's b\n"
    parents = git(seed, "log", "-1", "--format=%p", "origin/pr").split()
    assert len(parents) == 2


def test_an_update_hook_that_fails_fails_the_update_and_pushes_nothing(tmp_path):
    seed, session = repos(tmp_path, update_hook="echo no entries file >&2; exit 3")
    before = git(seed, "rev-parse", "origin/pr")
    main_moves_on(seed)

    failed = session.update(PullRequest(40, behind=True, branch="pr"))

    assert failed == "update_hook failed (exit 3): no entries file"
    git(seed, "fetch", "-q", "origin")
    assert git(seed, "rev-parse", "origin/pr") == before


@pytest.mark.parametrize("hook", [None, "true"], ids=["no hook", "a hook that leaves it"])
def test_a_conflict_left_unresolved_fails_and_pushes_nothing_or_leaves_a_worktree(tmp_path, hook):
    seed, session = repos(tmp_path, update_hook=hook)
    before = main_changes_b(seed)

    failed = session.update(PullRequest(40, behind=True, branch="pr"))

    assert failed == "merging main conflicts in b.txt"
    git(seed, "fetch", "-q", "origin")
    assert git(seed, "rev-parse", "origin/pr") == before
    assert not (tmp_path / "checkout-update-40").exists()


def test_a_pr_whose_branch_is_gone_reports_the_worktree_that_could_not_be_made(tmp_path):
    _, session = repos(tmp_path)

    failed = session.update(PullRequest(40, behind=True, branch="deleted"))

    assert failed.startswith("git worktree add failed: fatal: invalid reference: origin/deleted")


def test_the_update_pushes_with_the_bots_token_through_gh_not_the_owners_keychain(tmp_path):
    seed, session = repos(tmp_path)
    main_moves_on(seed)
    calls = []
    session.run = lambda args, **kwargs: (
        calls.append((args, kwargs)) or subprocess.run(args, **kwargs)
    )

    session.update(PullRequest(40, behind=True, branch="pr"))

    env = next(kwargs["env"] for args, kwargs in calls if "push" in args)
    helpers = (env["GIT_CONFIG_VALUE_1"], env["GIT_CONFIG_VALUE_2"])
    assert (env["GH_TOKEN"], helpers) == ("ghs_bot", ("", "!gh auth git-credential"))


def test_a_token_that_cannot_be_minted_fails_the_update_before_any_worktree(tmp_path):
    _, session = repos(tmp_path)
    session.tokens.fails = "GitHub refused the token request: HTTP 502"

    failed = session.update(PullRequest(40, behind=True, branch="pr"))

    assert failed == "the bot's token: GitHub refused the token request: HTTP 502"
    assert not (tmp_path / "checkout-update-40").exists()


def test_a_merge_that_fails_without_a_conflict_fails_the_update_with_gits_error(tmp_path):
    seed, session = repos(tmp_path)
    git(seed, "checkout", "-q", "--orphan", "unrelated")
    commit(seed, "Unrelated", **{"u.txt": "u\n"})
    git(seed, "push", "-q", "origin", "unrelated")

    failed = session.update(PullRequest(41, behind=True, branch="unrelated"))

    assert failed == "git merge --no-edit failed: fatal: refusing to merge unrelated histories"
