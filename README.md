# dev-tooling

Shared CI policy checks, PR review and Ralph loop tooling for Onato Limited projects.

## Use

```sh
uvx --from git+https://github.com/Onato-Limited/dev-tooling@v1 devtool --version
```

Pin `@v1`: it moves to each `v1.x.y` release and never to a breaking one. Each release's notes
are on the [Releases](https://github.com/Onato-Limited/dev-tooling/releases) page; watch the repo
for releases to hear about new checks and inputs.

## PR policy checks

Call the reusable workflow from a workflow on `pull_request`. Each check is its own job, shown on
the PR as `<your job> / <check>` (here `policy / size`); list those names as required checks.

```yaml
on:
  pull_request:
    # labeled/unlabeled re-run the size check when the size-approved label changes;
    # edited re-runs pr-shape when the title or body is fixed.
    types: [opened, synchronize, reopened, labeled, unlabeled, edited]
jobs:
  policy:
    uses: Onato-Limited/dev-tooling/.github/workflows/pr-policy.yml@v1
    permissions:
      contents: read
      issues: read
      pull-requests: read
    with:
      approver: <login>
```

| Check | What fails it | Inputs and files |
|---|---|---|
| `size` | More than `limit` changed lines, unless `approver` added the `size-approved` label | `limit` (400), `approver`; `.github/pr-size-exclude` (gitignore-style paths not counted) |
| `protected-paths` | Changing, moving or deleting a file that matches a pattern | `protected-paths`: gitignore-style patterns, one per line (none by default, so it passes) |
| `required-checks` | A name in `.github/required-checks` that no job reports on a PR, or a missing file | `.github/required-checks`: one check name per line, `#` comments |
| `secrets` | gitleaks finding a possible secret in the PR's commits (redacted; each finding with its fingerprint and the fix) | `.gitleaks.toml` (extra rules) and `.gitleaksignore` (fingerprints of false positives), read from your repo root |
| `pr-shape` | A title that isn't a conventional commit or `Structural: …`; a body that closes no issue (`Closes #12`, or any GitHub closing keyword); added lines matching `concurrency-pattern` without a `## Concurrency notes` section | `concurrency-pattern` (a regex, none by default, so that rule is off), `concurrency-paths` (gitignore-style, one per line, default all files) |

`required-checks` reads your workflows the way GitHub names checks: a job's `name:`, or its id; a
job that calls a workflow reports `<job> / <called job>` for each called job, fetching the called
workflow at its ref when it is in another repo. Jobs in workflows that don't run on `pull_request`
don't count. Matrix jobs and names with `${{ }}` are matched loosely, and the output lists them.

A multi-line input is a YAML block scalar:

```yaml
    with:
      protected-paths: |
        legacy/
        /config/production.yml
```

## Claude review

Reviews a PR with Claude Code against your repo's rules, leaves inline comments and a summary
comment, and fails unless Claude approves. Shown on the PR as `<your job> / review`.

```yaml
on:
  pull_request:
    # edited: the review reads the PR body, so a body written after the push needs a fresh review.
    types: [opened, synchronize, reopened, edited]
jobs:
  claude:
    uses: Onato-Limited/dev-tooling/.github/workflows/claude-review.yml@v1
    permissions:
      contents: read
      pull-requests: write
      issues: read
      id-token: write
    secrets:
      CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
    with:
      effort: high
```

The rules come from the PR's **base branch**, so a PR can't loosen the rules it is reviewed
against: `standards` (required), `CLAUDE.md`, `spec` and `REVIEW.md` when present, and any file
`REVIEW.md` includes with a line that is only `@<path>`. The review makes a Standards pass over
the diff and PR body, and a Spec pass against the issue the PR closes and the spec. If your rules
define a format for findings, the review uses it.

| Input | Default | |
|---|---|---|
| `model` | the action's | Model the review runs on; the summary comment names the model that ran |
| `effort` | `high` | `low`, `medium`, `high`, `xhigh` or `max` |
| `standards` | `CODING_STANDARDS.md` | The check fails, naming the file, when the base branch has none |
| `spec` | `SPEC.md` | Skipped when the base branch has none |
| `allowed-bots` | none | Comma-separated bot logins whose PRs are reviewed too |

Before the first review:
- Install the [Claude GitHub App](https://github.com/apps/claude) on the repo, and set the
  `CLAUDE_CODE_OAUTH_TOKEN` secret (`claude setup-token` makes one).
- Merge the calling workflow to the default branch on its own. The action runs only when the
  caller is identical to the default branch's copy, so a PR that adds or changes it gets no
  review and fails the check.

Fork PRs are skipped, since they must never get the token, and a skipped job counts as passing,
so give them a person's review instead.

## Review threads

Fails while the PR has a review thread that blocks it, outdated ones included, and lists each
one. It needs no secret and runs no Claude. Shown on the PR as `<your job> / threads`.

- A hard finding, and every person's thread, blocks until it's resolved.
- A judgement finding from the Claude review (its first line starts `[Standards] judgement:` or
  `[Spec] judgement:`) can be taken or left. It blocks only until someone other than the reviewer
  replies saying which, and why. It can stay open after that, and the log lists it as answered.

Call it from its own workflow, not the Claude review's: review events there would create skipped
review runs, and a skipped run counts as passing.

```yaml
on:
  pull_request:
  pull_request_review:
  pull_request_review_comment:
jobs:
  review-threads:
    uses: Onato-Limited/dev-tooling/.github/workflows/review-threads.yml@v1
    permissions:
      contents: read
      pull-requests: read
```

A push, a review or a reply re-runs it. Resolving a thread starts no run, so after resolving the
last one, re-run the failed job.

## Merge when green

For repos without auto-merge (private repos on GitHub Free). It squash-merges a PR labelled
`merge-when-green` once all of these hold:
- every check in the default branch's `.github/required-checks` is green on the head;
- no review thread blocks it, as review-threads decides;
- the PR has a `## Retro notes` comment, when `require-retro-notes` is on;
- the branch is up to date with its base. If it's behind, the workflow updates the branch and
  merges on a later run, once the checks pass again.

The commit's title is `<PR title> (#n)` and its message is the PR body.

The merge is made by a GitHub App you own, not `GITHUB_TOKEN`, so it starts your `main`
workflows. Give the app read and write access to Contents, Pull requests and Workflows, and read
access to Checks and Commit statuses. It needs Workflows to merge a PR that changes
`.github/workflows`. Install the app on the repo, and keep its private key in an Actions secret.

Every run checks all labelled PRs, so it can be triggered by anything that might make a PR ready:

```yaml
on:
  pull_request:
    types: [labeled, synchronize, ready_for_review]
  pull_request_review:
  issue_comment:
    types: [created, edited]
  workflow_run:
    # Your workflows that report the required checks.
    workflows: [ci, policy]
    types: [completed]
  # Resolving a thread starts no run, so this catches it, and anything else that was missed.
  schedule:
    - cron: "*/15 * * * *"
jobs:
  merge-when-green:
    uses: Onato-Limited/dev-tooling/.github/workflows/merge-when-green.yml@v1
    permissions:
      contents: read
    secrets:
      app-private-key: ${{ secrets.MY_APP_PRIVATE_KEY }}
    with:
      app-id: "123456"
      require-retro-notes: true
```

A PR that isn't ready is left alone. The log gives one line per PR saying what it is waiting
for, and a waiting PR doesn't fail the run. A run fails only when GitHub refuses a call, and the
log says which PR and why.

## Main audit

For repos where `main` can't be protected. On every push to the default branch (`main` below) it
checks each pushed commit against the policy. Pushes to any other branch or tag are skipped, so
the default branch is the one to name in `branches:`. The checks:
- the push wasn't forced;
- the commit came from a merged PR;
- that PR was squash-merged (no merge commit, no rebase);
- every check in `.github/required-checks` was green on the PR's head. The list is read as it
  was before the push, so a push can't drop the check it fails.

GitHub lists at most 250 of a push's commits. A longer push is itself reported, and the listed
commits are still checked. A push that creates `main` has nothing before it to check against,
so its run fails, saying so, and opens no issue.

A failure opens a `policy-violation` issue assigned to `assignee`, and GitHub emails them. That
matters when the push came from a bot. While that issue is open, later failures are added to it
as comments. Each entry gives the rule, the commit, what was found, the fix and the run link. The
run fails too, so the push shows a red X on `main`. Close the issue once it's dealt with.

This only checks how commits got onto `main`. Run your full check set on `main` as well, on push
and nightly.

```yaml
on:
  push:
    branches: [main]
jobs:
  main-audit:
    uses: Onato-Limited/dev-tooling/.github/workflows/main-audit.yml@v1
    permissions:
      contents: read
      pull-requests: read
      checks: read
      statuses: read
      issues: write
    with:
      assignee: your-login
```

## Develop

```sh
make bootstrap
make test lint coverage
```

See `CLAUDE.md` for the workflow and `CODING_STANDARDS.md` for the rules.
