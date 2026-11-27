import io
import textwrap

from devtool.required_checks import main, parse_required

CI = """
on:
  pull_request:
  push:
    branches: [main]
jobs:
  lint:
    runs-on: ubuntu-latest
  coverage:
    name: Coverage gate
    runs-on: ubuntu-latest
"""


def repo(tmp_path, required, **workflows):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "required-checks").write_text(required)
    for name, text in workflows.items():
        (tmp_path / ".github" / "workflows" / f"{name}.yml").write_text(textwrap.dedent(text))
    return ["--root", str(tmp_path)]


def run(argv):
    return main(argv, stdin=io.StringIO())


def test_the_file_parses_into_names_one_per_line_ignoring_comments_and_blank_lines():
    text = "# what a PR must pass\nlint\n\n  policy / size  \n# coverage\ncoverage\n"

    assert parse_required(text) == ["lint", "policy / size", "coverage"]


def test_a_name_that_matches_no_job_fails_naming_it_the_checks_that_exist_and_the_fix(
    tmp_path, capsys
):
    argv = repo(tmp_path, "lint\nlnt\n", ci=CI)

    code = run(argv)

    assert code == 1
    assert capsys.readouterr().out == (
        "lnt: matches no job that runs on pull requests.\n"
        "Checks that exist: Coverage gate, lint\n"
        "Every name in .github/required-checks must be a check a PR reports, or merges wait for it "
        "forever. Fix the name, or remove it.\n"
    )


def test_names_matching_a_job_by_its_name_or_its_id_pass(tmp_path, capsys):
    argv = repo(tmp_path, "lint\nCoverage gate\n", ci=CI)

    code = run(argv)

    assert code == 0
    assert capsys.readouterr().out == "All 2 required checks match a job that runs on PRs.\n"


RELEASE = """
on:
  push:
    branches: [main]
jobs:
  tag:
    runs-on: ubuntu-latest
"""


def test_a_job_in_a_workflow_that_does_not_run_on_pull_requests_fails_saying_so(tmp_path, capsys):
    argv = repo(tmp_path, "lint\ntag\n", ci=CI, release=RELEASE)

    code = run(argv)

    assert code == 1
    assert capsys.readouterr().out.splitlines()[:2] == [
        "tag: a job in release.yml, which doesn't run on pull requests, "
        "so it never reports on a PR.",
        "Checks that exist: Coverage gate, lint",
    ]


def test_the_string_and_list_forms_of_on_count_as_running_on_pull_requests(tmp_path, capsys):
    argv = repo(
        tmp_path,
        "a\nb\n",
        one="on: pull_request\njobs:\n  a:\n    runs-on: ubuntu-latest\n",
        many="on: [push, pull_request]\njobs:\n  b:\n    runs-on: ubuntu-latest\n",
    )

    assert run(argv) == 0


POLICY = """
on: workflow_call
jobs:
  size:
    runs-on: ubuntu-latest
  protected-paths:
    name: Protected paths
    runs-on: ubuntu-latest
"""

CALLS_LOCAL = """
on: pull_request
jobs:
  policy:
    uses: ./.github/workflows/policy.yml
"""


def test_a_job_calling_a_local_workflow_reports_caller_job_slash_called_job(tmp_path, capsys):
    argv = repo(
        tmp_path,
        "policy / size\npolicy / Protected paths\npolicy / sise\n",
        pr=CALLS_LOCAL,
        policy=POLICY,
    )

    code = run(argv)

    assert code == 1
    assert capsys.readouterr().out.splitlines()[:2] == [
        "policy / sise: matches no job that runs on pull requests.",
        "Checks that exist: policy / Protected paths, policy / size",
    ]


CALLS_REMOTE = """
on: pull_request
jobs:
  policy:
    uses: some-org/tools/.github/workflows/pr-policy.yml@v1
"""


def test_a_job_calling_a_workflow_in_another_repo_matches_any_called_job_loosely(tmp_path, capsys):
    argv = repo(tmp_path, "policy / size\n", pr=CALLS_REMOTE)

    code = run(argv)

    assert code == 0
    assert capsys.readouterr().out.splitlines()[-1] == "  policy / size ~ policy / …"


def test_a_missing_required_checks_file_fails_with_the_fix(tmp_path, capsys):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)

    code = run(["--root", str(tmp_path)])

    assert code == 1
    assert capsys.readouterr().out == (
        "No .github/required-checks. Add one listing the checks a PR must pass, one per line, "
        "as they appear on the PR (a called workflow's jobs as `<caller job> / <called job>`).\n"
    )


MATRIX = """
on: pull_request
jobs:
  test:
    strategy:
      matrix:
        python: ["3.12", "3.13"]
    runs-on: ubuntu-latest
  build:
    name: Build ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu, macos]
    runs-on: ${{ matrix.os }}-latest
"""


def test_names_only_known_when_a_job_runs_match_loosely_and_say_so(tmp_path, capsys):
    argv = repo(tmp_path, "test (3.12)\nBuild ubuntu\n", ci=MATRIX)

    code = run(argv)

    assert code == 0
    assert capsys.readouterr().out == (
        "All 2 required checks match a job that runs on PRs.\n"
        "Matched loosely, since these names are only known when the job runs, "
        "or come from a workflow in another repo:\n"
        "  test (3.12) ~ test (…)\n"
        "  Build ubuntu ~ Build …\n"
    )
