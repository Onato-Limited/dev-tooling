# dev-tooling

Shared CI checks, PR review and the Ralph loop, as one Python CLI (`devtool`) and reusable GitHub
workflows that other repos call pinned at `@v1`.

- `CODING_STANDARDS.md`: what every change here must meet.
- GitHub issues on `Onato-Limited/dev-tooling`: the work queue. Each issue's "Tests to write first"
  is the test list.

This repo is public. Project-specific rules belong in each caller's own files, passed in as inputs
or read from the caller's repo, never written here.

# Workflow

- One issue = one branch (`<prefix>/<issue#>-<slug>` from `origin/main`) in its own worktree = one
  PR whose body says `Closes #<n>`. The prefix is the repo's git identity: `git config github.user`,
  or else the local part of `git config user.email` without GitHub's `NNN+`.
- PR titles are conventional commits (`feat: …`, `fix: …`) or start `Structural:`. PRs are
  squash-merged, so the title and body become the commit on `main`.
- `main` is protected: PR only, linear history, and the checks in `.github/required-checks` must
  pass. When a check is added there, add it to `main`'s required status checks too.
- Tests first: write one failing test, see it fail for the expected reason, write only enough code
  to pass, refactor when green. Say in the PR that each test was seen failing.

# Releases

`pyproject.toml`'s version is the release. On a merge to `main`, `release.yml` tags `v<version>`
and moves `v<major>` to it if that tag is new, then creates a GitHub Release for `v<version>`
with notes generated from the merged PR titles. A merge that leaves the version alone releases
nothing. `v*.*.*` tags are immutable (a tag ruleset); only the major tag moves. A breaking change
to a command or a workflow's inputs is a new major.

# Project commands

All from the repo root.

| Command | What it does |
|---|---|
| `make bootstrap` | `uv sync`: install the package and dev tools |
| `make test` | All tests |
| `make lint` | ruff lint and format check |
| `make coverage` | Tests with the 100% branch coverage gate |
| `uv run devtool <command>` | Run the CLI from the checkout |
