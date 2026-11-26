# dev-tooling

Shared CI policy checks, PR review and Ralph loop tooling for Onato Limited projects.

## Use

```sh
uvx --from git+https://github.com/Onato-Limited/dev-tooling@v1 devtool --version
```

Pin `@v1`: it moves to each `v1.x.y` release and never to a breaking one.

## PR policy checks

Call the reusable workflow from a workflow on `pull_request`. Each check is its own job, shown on
the PR as `<your job> / <check>` (here `policy / size`); list those names as required checks.

```yaml
on:
  pull_request:
    # labeled/unlabeled re-run the size check when the size-approved label changes.
    types: [opened, synchronize, reopened, labeled, unlabeled]
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

A multi-line input is a YAML block scalar:

```yaml
    with:
      protected-paths: |
        legacy/
        /config/production.yml
```

## Develop

```sh
make bootstrap
make test lint coverage
```

See `CLAUDE.md` for the workflow and `CODING_STANDARDS.md` for the rules.
