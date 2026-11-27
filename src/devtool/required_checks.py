"""Fail when .github/required-checks names a check that no job produces on a PR."""

import argparse
import re
import sys
from pathlib import Path
from typing import TextIO

import yaml

from devtool.gates import load_patterns

FIX = (
    "Every name in .github/required-checks must be a check a PR reports, or merges wait for it "
    "forever. Fix the name, or remove it."
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


def job_names(workflow: dict, root: Path) -> list[str]:
    """The checks a workflow's jobs report; a job calling a workflow reports one per called job."""
    names = []
    for job_id, job in workflow.get("jobs", {}).items():
        name = job_label(job_id, job)
        uses = job.get("uses")
        if uses is None:
            names.append(name)
        elif uses.startswith("./"):
            called = yaml.safe_load((root / uses).read_text())
            names += [f"{name} / {called_name}" for called_name in job_names(called, root)]
        else:
            # A workflow in another repo isn't read, so any of its job names matches.
            names.append(f"{name} / {UNKNOWN}")
    return names


def check_names(root: Path) -> tuple[set[str], dict[str, str]]:
    """The checks jobs report on PRs, and the jobs that never do, with their workflow file."""
    on_prs, elsewhere = set(), {}
    for path in sorted((root / ".github" / "workflows").glob("*.y*ml")):
        workflow = yaml.safe_load(path.read_text())
        for name in job_names(workflow, root):
            if runs_on_pull_requests(workflow):
                on_prs.add(name)
            else:
                elsewhere[name] = path.name
    return on_prs, elsewhere


def main(argv: list[str], stdin: TextIO = sys.stdin) -> int:
    parser = argparse.ArgumentParser(prog="devtool required-checks", description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="the caller's checkout")
    args = parser.parse_args(argv)

    required_file = args.root / ".github" / "required-checks"
    if not required_file.exists():
        print(MISSING)
        return 1
    required = parse_required(required_file.read_text())
    existing, elsewhere = check_names(args.root)
    matches = {name: find(name, existing) for name in required}
    unmatched = [name for name, label in matches.items() if label is None]
    if not unmatched:
        print(f"All {len(required)} required checks match a job that runs on PRs.")
        loose = [(name, label) for name, label in matches.items() if label != name]
        if loose:
            print(
                "Matched loosely, since these names are only known when the job runs, "
                "or come from a workflow in another repo:"
            )
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
