# dev-tooling

Shared CI policy checks, PR review and Ralph loop tooling for Onato Limited projects.

## Use

```sh
uvx --from git+https://github.com/Onato-Limited/dev-tooling@v2 devtool --version
```

Pin `@v2`: it moves to each `v2.x.y` release and never to a breaking one. Each release's notes
are on the [Releases](https://github.com/Onato-Limited/dev-tooling/releases) page; watch the repo
for releases to hear about new checks and inputs. `@v1` stays where it is and gets no more
releases; see [Moving from v1](#moving-from-v1).

### Moving from v1

v2 asks for approval with GitHub's own tools, reviews and `CODEOWNERS`, instead of labels.

- **`size`:** an oversized PR passes on an approving review from `approver`, not the
  `size-approved` label. GitHub doesn't let anyone approve their own PR.
- **Triggers:** the caller runs on `pull_request_review` (`submitted`, `dismissed`) instead of
  the `labeled` and `unlabeled` types, as in the [example](#pr-policy-checks).
- **Permissions:** the policy job needs `contents: read` and `pull-requests: read`; drop
  `issues: read`.
- **`review-paths`** is gone. Put its patterns in `.github/CODEOWNERS` with their owners: an
  approving review from one of a file's owners passes `codeowners`, not the `review-approved`
  label. Where `main` can be protected, turn on "Require review from Code Owners"; where it
  can't, list `policy / codeowners` in `.github/required-checks`. A `CODEOWNERS` line with no
  owners stands in for a `!` pattern.
- **The Ralph loop:** a session requests your review instead of asking for a label, and the loop
  takes the PR back when you approve it. `plan-approved` is still a label.
- **Your own `.ralph/PROMPT.md`**, if you have one, needs the same change. When `size` fails, it
  has the session request `{approver}`'s review and stop with `NEEDS_HUMAN: PR #<n> needs your
  approval: …`. The loop watches only that stop, so with a v1 prompt the issue stays in In review
  after you approve. See [Ralph loop](#ralph-loop), and the loop's own prompt for the wording.

## PR policy checks

Call the reusable workflow from a workflow on `pull_request`. Each check is its own job, shown on
the PR as `<your job> / <check>` (here `policy / size`); list those names as required checks.

```yaml
on:
  pull_request:
    # edited re-runs pr-shape when the title or body is fixed.
    types: [opened, synchronize, reopened, edited]
  # A review re-runs size and codeowners, which approving reviews pass.
  pull_request_review:
    types: [submitted, dismissed]
jobs:
  policy:
    uses: Onato-Limited/dev-tooling/.github/workflows/pr-policy.yml@v2
    permissions:
      contents: read
      pull-requests: read
    with:
      approver: <login>
```

| Check | What fails it | Inputs and files |
|---|---|---|
| `size` | More than `limit` changed lines, unless `approver`'s latest review approves the PR | `limit` (400), `approver`; `.github/pr-size-exclude` (gitignore-style paths not counted) |
| `protected-paths` | Changing, moving or deleting a file that matches a pattern | `protected-paths`: gitignore-style patterns, one per line (none by default, so it passes) |
| `codeowners` | Changing, moving or deleting a file that `CODEOWNERS` gives owners, until one of its owners approves the PR | `.github/CODEOWNERS`, `CODEOWNERS` or `docs/CODEOWNERS`, read from the base branch (none, so it passes) |
| `required-checks` | A name in `.github/required-checks` that no job reports on a PR, or a missing file | `.github/required-checks`: one check name per line, `#` comments |
| `secrets` | gitleaks finding a possible secret in the PR's commits (redacted; each finding with its fingerprint and the fix) | `.gitleaks.toml` (extra rules) and `.gitleaksignore` (fingerprints of false positives), read from your repo root |
| `pr-shape` | A title that isn't a conventional commit or `Structural: …`; a body that closes no issue (`Closes #12`, or any GitHub closing keyword); added lines matching `concurrency-pattern` without a `## Concurrency notes` section | `concurrency-pattern` (a regex, none by default, so that rule is off), `concurrency-paths` (gitignore-style, one per line, default all files) |

An oversized PR is a warning you can accept: approve the PR, as `approver`, in GitHub's review
UI. Where `main` is protected, turn on "Dismiss stale pull request approvals when new commits are
pushed", so a later push needs a fresh approval. GitHub doesn't let you approve your own PR, so
an oversized PR you opened yourself is split, or merged by an admin.

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

`protected-paths` is for files no PR may change. For files a PR may change once someone has
looked, use GitHub's `CODEOWNERS`, which also requests the owners' reviews. The last line that
matches a file decides its owners, and a line with no owners leaves its files unowned. So
`CODEOWNERS` can name what needs a review, or, starting from `*`, what may merge without one:

```
* @you
/src/features/
/tests/
```

Where `main` can be protected, turn on "Require review from Code Owners" and you needn't require
`policy / codeowners`. Where it can't, list `policy / codeowners` in `.github/required-checks`.
The check counts an approving review from any one of a file's owners, as GitHub does. It reads
`CODEOWNERS` from the base branch, so a PR can't drop its own owners. It can't tell who is in a
team or behind an email, so it fails on files only those own.

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
    uses: Onato-Limited/dev-tooling/.github/workflows/claude-review.yml@v2
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
the diff and PR body, and a Spec pass against the issue the PR closes and the spec. Every inline
finding starts with a label, `[Standards]` or `[Spec]` then `hard:` or `judgement:`. A hard
finding must be fixed. A judgement finding can be taken or left, with a reply saying which and
why. Either way the thread is then resolved. If your rules define a format for findings, the
review uses it after the label.
Each review is given its earlier findings on the PR and the replies to them. It doesn't raise
again a finding that was answered, taken or left with a reason, unless the diff as it stands still
has what either pass calls hard.

| Input | Default | |
|---|---|---|
| `model` | the action's | Model the review runs on; the summary comment names the model that ran |
| `effort` | `high` | `low`, `medium`, `high`, `xhigh` or `max` |
| `standards` | `CODING_STANDARDS.md` | The check fails, naming the file, when the base branch has none |
| `spec` | `SPEC.md` | Skipped when the base branch has none |
| `allowed-bots` | none | Comma-separated bot logins whose PRs are reviewed too, usually the Ralph loop's app slug (no `[bot]`): without it, the action fails every PR a bot opens |

Before the first review:
- Install the [Claude GitHub App](https://github.com/apps/claude) on the repo, and set the
  `CLAUDE_CODE_OAUTH_TOKEN` secret (`claude setup-token` makes one).
- Merge the calling workflow to the default branch on its own. The action runs only when the
  caller is identical to the default branch's copy, so a PR that adds or changes it gets no
  review and fails the check. Such a PR needs a person to merge it, past the failed check;
  merge-when-green never will. The same goes for a later change to the caller, such as adding
  `allowed-bots`.

Fork PRs are skipped, since they must never get the token, and a skipped job counts as passing,
so give them a person's review instead.

## Review threads

Fails while the PR has an unresolved review thread, outdated ones included, and lists each one.
Every thread blocks until it's resolved, a judgement finding that has been answered included. It
needs no secret and runs no Claude. Shown on the PR as `<your job> / threads`.

Call it from its own workflow, not the Claude review's: review events there would create skipped
review runs, and a skipped run counts as passing.

```yaml
on:
  pull_request:
  pull_request_review:
  pull_request_review_comment:
jobs:
  review-threads:
    uses: Onato-Limited/dev-tooling/.github/workflows/review-threads.yml@v2
    permissions:
      contents: read
      pull-requests: read
```

A push, a review or a reply re-runs it, but resolving a thread starts no run. So don't make it a
required check, in branch protection or in `.github/required-checks`. A run that failed while a
thread was open would keep blocking the merge after the thread is resolved, until someone re-runs
it. The check still runs, and its log lists what is left. Gate the merge on the threads
themselves instead:
- Where `main` can be protected, turn on "Require conversation resolution before merging".
  GitHub reads the threads as the PR merges, the same rule as this check's.
- Where it can't, merge-when-green reads the threads itself each time it runs.

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
    uses: Onato-Limited/dev-tooling/.github/workflows/merge-when-green.yml@v2
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
    uses: Onato-Limited/dev-tooling/.github/workflows/main-audit.yml@v2
    permissions:
      contents: read
      pull-requests: read
      checks: read
      statuses: read
      issues: write
    with:
      assignee: your-login
```

## Ralph loop

`devtool ralph` works through a milestone's issues unattended, one fresh Claude session per
issue, until the milestone is done or something needs you. Run it from the project's checkout:

```sh
uvx --from git+https://github.com/Onato-Limited/dev-tooling@v2 devtool ralph --milestone 12
```

- `--milestone N` is the parent issue; its sub-issues are the milestone.
- `--dry-run` prints the issue it would take next, and changes nothing.
- `--max-iterations N` caps the sessions it starts (3 by default).

Each pass takes the lowest-numbered open Todo issue with no open blocker, moves it to In
progress, and starts `claude` in a worktree of its own on `<prefix>/<issue>-ralph`. The session
opens a PR, and the loop arranges its merge, keeps it up to date with `main`, and waits for it.
An issue moves to In review, and you're told, when the session says `NEEDS_HUMAN:` or opens no
PR, or when its PR has had changes requested 3 times, has failed CI 3 runs in a row, can't be
brought up to date with `main` (a conflict, or `update_hook` failed), or is green but held by an
unresolved review thread. After 5 merged PRs, and before it
stops, an improver session reviews them if the project has `.ralph/IMPROVE.md`.

A session whose PR can only pass `size` or `codeowners` on your approval requests your review,
says why in a PR comment headed `## Needs your approval`, and stops with `NEEDS_HUMAN: PR #<n>
needs your approval: …`. It requests the review for `codeowners` too, since GitHub requests code
owners' reviews on private repos only on paid plans. The loop watches that PR while it runs. Once you approve it in GitHub's review UI, the loop moves the
issue back to Todo, and a resumed session finishes the PR, which may still need work. If the
loop has stopped before you approve, its stop message lists the PR; move the issue back to Todo
yourself.

The loop stops, and says why, when nothing is left to take, at the iteration cap, or when its
PRs still haven't merged, or haven't been approved, after it has waited an hour.
It won't start while `main`'s latest `audit_check` run is red or a `policy-violation` issue is
open (see [Main audit](#main-audit)); that stop reads `Ralph loop stopped: NEEDS_HUMAN: …`.

Every message to you starts `Ralph loop`. A `notify` command that should only page for what
needs you can match `needs you`, `NEEDS_HUMAN:`, `needs your decision` and `can't start`; every
stop starts `Ralph loop stopped:` or `Ralph loop can't`.

### What it runs as

- **You**, through `gh`: reading and moving the board, labels and comments. A GitHub App can't
  move cards on a user-owned project. Your login is the only one whose `plan-approved` label or
  approving review counts for the loop.
- **The project's GitHub App**, in each session: commits, pushes and PRs, authored as
  `<app_slug>[bot]`. The bot's user ID, for its commit email, isn't configured: each run, a dry
  run included, looks it up with `gh api users/<app_slug>[bot]`, so a wrong `app_slug` stops the
  loop there. Give the app read and write access to Contents, Pull requests and Issues, and
  read access to Checks. It also needs Workflows if a session may change `.github/workflows`:
  GitHub refuses the app's push of a commit that does. Pushing `main`'s merge to a PR's branch
  needs no Workflows, even when `main`'s new commits change a workflow. Install the app on the
  repo, and keep its private key in your login keychain under the app's slug:
  `security add-generic-password -s <app_slug> -a private-key -w "$(cat <key>.pem)"`.
  A real run mints a token before taking an issue, so a missing key stops it at once.
  Add `allowed-bots: <app_slug>` to your [Claude review](#claude-review) caller too: the
  review action refuses a PR a bot opened unless that bot is listed, so without it every loop
  PR's `review` check fails.

Branches use the repo's git identity as their prefix: `git config github.user`, or else your
email's name without GitHub's `NNN+`.

### The board

A GitHub project with a Status field whose options are Todo, In progress, In review, Done and
Attended. The loop never takes an Attended issue, so put the ones you work yourself there. Turn
on the project's built-in workflows Item closed, Pull request merged, Pull request linked to
issue and Auto-add to project; the loop won't start without them.

An issue labelled `draft` is proposed, not planned. Once you add `plan-approved` to the
milestone, the loop removes `draft` and moves each draft to Todo, leaving Attended ones where
they are.

### `.ralph/config`

One `name=value` per line. A line starting with `#` is a comment; a `#` later in a line is part
of the value.

| Key | |
|---|---|
| `app_id`, `installation_id`, `app_slug` | The GitHub App, and the installation on this repo. Required. |
| `board_owner`, `board_number` | The project: its owner (a user or an organization) and the number from its URL. Required. |
| `merge_mode` | `auto` turns on auto-merge for each PR; `label` adds `merge-when-green` (see [Merge when green](#merge-when-green)). Required. |
| `review_check` | The review's check name, `claude / review` by default. |
| `audit_check` | The main audit's check name, `main-audit / audit` by default. |
| `notify` | A command run with each message to you as its last argument. Every message is printed too. |
| `cleanup_list`, `cleanup_prune` | Set both or neither. The first prints the issue numbers that have a device of their own (a simulator, say), one per line; the second, given an issue, removes its device. The loop prunes each closed issue's device after every pass. |
| `update_hook` | A shell command run as the bot after `main` is merged into a PR's branch. It runs in a fresh worktree, `<checkout>-update-<pr>`, with a detached `HEAD` at `origin/<branch>`, so it holds nothing a session built or installed and has no current branch. The worktree is removed afterwards, and `session_path` isn't on its `PATH`. The hook may resolve conflicts and commit; it must commit whatever it changes, or stage it during a conflicted merge. The update fails if the hook exits non-zero, or leaves a conflict or anything `git status` lists, untracked files included. |
| `session_path` | Directories put first on the session's `PATH`, separated by `:`; relative ones are from the checkout. |

`notify` and the cleanup commands run in the checkout.

### Prompts

`.ralph/PROMPT.md` is what each session is told, with `{issue}`, `{branch}` and `{approver}`
(your login) filled in.
Without one the loop uses its own, which reads the project's `CLAUDE.md`. Either way the
session's last message must end with a `PR: #<number>` line once it has a PR, and a
`NEEDS_HUMAN: <reason>` line when it stops for you.

`.ralph/IMPROVE.md` is the improver's prompt, with `{prs}` filled in (`PRs #60, #61`). It
ends with a `PR: #<number>` line for each PR it opens and a `PROPOSED: #<number>` line for
each issue it proposes; you're told about each proposal.

Each session's output is in `.ralph/logs/`.

## Develop

```sh
make bootstrap
make test lint coverage
```

See `CLAUDE.md` for the workflow and `CODING_STANDARDS.md` for the rules.
