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

Fork PRs are skipped, since they must never get the token, and a skipped job counts as passing,
so give them a person's review instead.

## Develop

```sh
make bootstrap
make test lint coverage
```

See `CLAUDE.md` for the workflow and `CODING_STANDARDS.md` for the rules.
