"""Copy a review's rule files from the base branch and print the Claude review prompt."""

import argparse
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path, PurePosixPath
from string import Template
from typing import TextIO

RULES_DIR = Path(".review-rules")


def from_base(base: str, path: str) -> str | None:
    # cat-file blob, not show: `git show base:docs` prints a directory listing and succeeds.
    shown = subprocess.run(
        ["git", "cat-file", "blob", f"{base}:{path}"], capture_output=True, text=True
    )
    return shown.stdout if shown.returncode == 0 else None


def includes(review: str) -> list[str]:
    """The paths of `REVIEW.md` lines that are only `@<path>`, if they stay inside the repo."""
    paths = [line.strip()[1:] for line in review.splitlines() if line.strip().startswith("@")]
    return [p for p in paths if ".." not in PurePosixPath(p).parts]


def main(argv: list[str], stdin: TextIO = sys.stdin) -> int:
    parser = argparse.ArgumentParser(prog="devtool review-prompt", description=__doc__)
    parser.add_argument("--base", required=True, help="the PR's base ref, e.g. origin/main")
    parser.add_argument("--repo", required=True, help="owner/name of the PR's repository")
    parser.add_argument("--pr", required=True, help="the PR number")
    parser.add_argument("--standards", default="CODING_STANDARDS.md", help="required")
    parser.add_argument("--spec", default="SPEC.md", help="used if the base branch has it")
    args = parser.parse_args(argv)

    if from_base(args.base, args.standards) is None:
        print(
            f"Review: no {args.standards} on {args.base}. The review judges a PR against the "
            f"repository's coding standards; add {args.standards} to the base branch, or set "
            "the workflow's `standards` input to the file that holds them.",
            file=sys.stderr,
        )
        return 1
    shutil.rmtree(RULES_DIR, ignore_errors=True)
    copied: list[str] = []
    pending = [args.standards, "CLAUDE.md", args.spec, "REVIEW.md"]
    while pending:
        path = pending.pop(0)
        text = None if path in copied else from_base(args.base, path)
        if text is None:
            continue
        (RULES_DIR / path).parent.mkdir(parents=True, exist_ok=True)
        (RULES_DIR / path).write_text(text)
        copied.append(path)
        if path == "REVIEW.md":
            pending += includes(text)
    rules = "\n".join(f"- `{RULES_DIR / path}`" for path in copied)
    template = Template(files("devtool").joinpath("review_prompt.md").read_text())
    print(template.substitute(repo=args.repo, pr=args.pr, rules=rules), end="")
    return 0
