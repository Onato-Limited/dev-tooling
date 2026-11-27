"""Copy a review's rule files from the base branch and print the Claude review prompt."""

import argparse
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path, PurePosixPath
from string import Template
from typing import TextIO

from devtool.review_threads import Query, QueryError, by_reviewer, gh_graphql, login, threads

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


def earlier_threads(repo: str, pr: int, query: Query) -> str:
    """The reviewer's earlier threads on the PR, each with its replies, as a Markdown list."""
    found = [t for t in threads(repo, pr, query) if by_reviewer(t["comments"]["nodes"][0])]
    if not found:
        return "No earlier review threads."
    lines = []
    for thread in found:
        where = f"{thread['path']}:{thread['line']}" if thread["line"] else thread["path"]
        state = "resolved" if thread["isResolved"] else "open"
        lines.append(f"- `{where}`, {state}")
        for comment in thread["comments"]["nodes"]:
            first, *rest = comment["body"].splitlines() or [""]
            lines.append(f"  - {login(comment)}: {first}")
            lines += [f"    {line}" for line in rest]
    return "\n".join(lines)


def main(argv: list[str], stdin: TextIO = sys.stdin, query: Query = gh_graphql) -> int:
    parser = argparse.ArgumentParser(prog="devtool review-prompt", description=__doc__)
    parser.add_argument("--base", required=True, help="the PR's base ref, e.g. origin/main")
    parser.add_argument("--repo", required=True, help="owner/name of the PR's repository")
    parser.add_argument("--pr", type=int, required=True, help="the PR number")
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
    try:
        earlier = earlier_threads(args.repo, args.pr, query)
    except QueryError as error:
        print(f"Review: can't read PR #{args.pr}'s review threads: {error}", file=sys.stderr)
        print(
            "  The job needs `pull-requests: read` and GH_TOKEN set. Grant it in the calling "
            "workflow, then re-run this job.",
            file=sys.stderr,
        )
        return 1
    rules = "\n".join(f"- `{RULES_DIR / path}`" for path in copied)
    template = Template(files("devtool").joinpath("review_prompt.md").read_text())
    prompt = template.substitute(repo=args.repo, pr=args.pr, rules=rules, earlier=earlier)
    print(prompt, end="")
    return 0
