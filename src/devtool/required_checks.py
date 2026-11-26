"""Fail when .github/required-checks names a check that no job produces on a PR."""

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import yaml

from devtool.gates import load_patterns

FIX = (
    "Every name in .github/required-checks must be a check a PR reports, or merges wait for it "
    "forever. Fix the name, or remove it."
)
UNREADABLE_FIX = (
    "Check the job's `uses:` path and ref. A workflow in a private repo needs a token that can "
    "read it."
)
MISSING = (
    "No .github/required-checks. Add one listing the checks a PR must pass, one per line, "
    "as they appear on the PR (a called workflow's jobs as `<caller job> / <called job>`)."
)


def parse_required(text: str) -> list[str]:
    return load_patterns(text)


PR_EVENTS = {"pull_request", "pull_request_target"}


def runs_on_pull_requests(workflow: dict) -> bool:
    # YAML 1.1 reads the bare key `on` as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    events = {triggers} if isinstance(triggers, str) else set(triggers)
    return bool(events & PR_EVENTS)


Fetch = Callable[[str, str, str], str]


class FetchError(Exception):
    """A called workflow couldn't be read; the message is what `gh` said."""


class Unreadable(Exception):
    def __init__(self, target: str, reason: str):
        super().__init__(target, reason)
        self.target, self.reason, self.caller = target, reason, ""


def gh_fetch(repository: str, path: str, ref: str, run=subprocess.run) -> str:
    """A file from another repo at a ref, through `gh` and its GH_TOKEN."""
    result = run(
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github.raw",
            f"repos/{repository}/contents/{path}?ref={ref}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise FetchError(result.stderr.strip())
    return result.stdout


@dataclass(frozen=True)
class Source:
    """Where a workflow lives: the caller's checkout, or another repo at a ref."""

    root: Path
    fetch: Fetch
    repository: str | None = None
    ref: str | None = None

    def load(self, uses: str) -> tuple[dict, "Source"]:
        # A `./` path is in the same repo and commit as the workflow that names it.
        if uses.startswith("./"):
            if self.repository is None:
                return yaml.safe_load((self.root / uses).read_text()), self
            return self._fetch(self.repository, uses[2:], self.ref), self
        location, ref = uses.split("@", 1)
        owner, repo, path = location.split("/", 2)
        repository = f"{owner}/{repo}"
        return self._fetch(repository, path, ref), Source(self.root, self.fetch, repository, ref)

    def _fetch(self, repository: str, path: str, ref: str) -> dict:
        try:
            return yaml.safe_load(self.fetch(repository, path, ref))
        except FetchError as error:
            raise Unreadable(f"{repository}/{path}@{ref}", str(error)) from error


UNKNOWN = "…"
EXPRESSION = re.compile(r"\$\{\{.*?\}\}")


def job_label(job_id: str, job: dict) -> str:
    """The job's check name, with … for the parts only known when it runs."""
    # A matrix job's name gets its matrix values appended, unless the name already uses them.
    has_matrix = "matrix" in job.get("strategy", {})
    name = job.get("name", job_id)
    if EXPRESSION.search(name):
        return EXPRESSION.sub(UNKNOWN, name)
    return f"{name} ({UNKNOWN})" if has_matrix else name


def find(name: str, labels) -> str | None:
    for label in labels:
        parts = (re.escape(part) for part in label.split(UNKNOWN))
        if re.fullmatch(".+".join(parts), name):
            return label
    return None


def job_names(workflow: dict, source: Source) -> list[str]:
    """The checks a workflow's jobs report; a job calling a workflow reports one per called job."""
    names = []
    for job_id, job in workflow.get("jobs", {}).items():
        name = job_label(job_id, job)
        if "uses" in job:
            called, called_from = source.load(job["uses"])
            names += [f"{name} / {called_name}" for called_name in job_names(called, called_from)]
        else:
            names.append(name)
    return names


def check_names(root: Path, fetch: Fetch) -> tuple[set[str], dict[str, str]]:
    """The checks jobs report on PRs, and the jobs that never do, with their workflow file."""
    on_prs, elsewhere = set(), {}
    for path in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        workflow = yaml.safe_load(path.read_text())
        try:
            names = job_names(workflow, Source(root, fetch))
        except Unreadable as error:
            error.caller = path.name
            raise
        for name in names:
            if runs_on_pull_requests(workflow):
                on_prs.add(name)
            else:
                elsewhere[name] = path.name
    return on_prs, elsewhere


def main(argv: list[str], stdin: TextIO = sys.stdin, fetch: Fetch = gh_fetch) -> int:
    parser = argparse.ArgumentParser(prog="devtool required-checks", description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="the caller's checkout")
    args = parser.parse_args(argv)

    required_file = args.root / ".github" / "required-checks"
    if not required_file.exists():
        print(MISSING)
        return 1
    required = parse_required(required_file.read_text())
    try:
        existing, elsewhere = check_names(args.root, fetch)
    except Unreadable as error:
        print(f"Can't read {error.target}, called by a job in {error.caller}: {error.reason}")
        print(UNREADABLE_FIX)
        return 1
    matches = {name: find(name, existing) for name in required}
    unmatched = [name for name, label in matches.items() if label is None]
    if not unmatched:
        print(f"All {len(required)} required checks match a job that runs on PRs.")
        loose = [(name, label) for name, label in matches.items() if label != name]
        if loose:
            print("Matched loosely, since these names are only known when the job runs:")
        for name, label in loose:
            print(f"  {name} ~ {label}")
        return 0
    for name in unmatched:
        label = find(name, elsewhere)
        if label is not None:
            print(
                f"{name}: a job in {elsewhere[label]}, which doesn't run on pull requests, "
                "so it never reports on a PR."
            )
        else:
            print(f"{name}: matches no job that runs on pull requests.")
    print(f"Checks that exist: {', '.join(sorted(existing))}")
    print(FIX)
    return 1
