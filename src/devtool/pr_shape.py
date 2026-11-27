"""Fail when a PR's title, body or diff breaks the PR shape rules."""

import argparse
import re
import sys
from pathlib import Path
from typing import TextIO

from devtool.gates import load_patterns, matches_any

TYPES = [
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
]
TITLE = re.compile(rf"^(({'|'.join(TYPES)})(\([^)]+\))?!?: \S|Structural: \S)")


CLOSES = re.compile(r"\b(close[sd]?|fix(e[sd])?|resolve[sd]?):?\s+([\w.-]+/[\w.-]+)?#\d+", re.I)


def title_problem(title: str) -> str | None:
    if TITLE.match(title):
        return None
    return (
        f"Title: `{title}` isn't a conventional commit.\n"
        "  Start it with a type and a colon, like `feat: add pr-shape` or `fix(cli): …` "
        f"(types: {', '.join(TYPES)}), or with `Structural: ` for a change that only moves or "
        "renames code."
    )


def body_problem(body: str) -> str | None:
    if CLOSES.search(body):
        return None
    return (
        "Body: doesn't close an issue.\n"
        "  Add a line like `Closes #12` (or Fixes/Resolves, or `Closes owner/repo#12`), so the "
        "issue closes when the PR merges."
    )


HUNK = re.compile(r"^@@ -\S+ \+(\d+)")
NOTES = re.compile(r"^## Concurrency notes\s*$", re.M)


def added_lines(diff: str):
    """(path, line number, text) for each added line of a `git diff -U0`."""
    path, number = None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
        elif hunk := HUNK.match(line):
            number = int(hunk[1])
        elif line.startswith("+"):
            yield path, number, line[1:]
            number += 1


def concurrency_problem(body: str, diff: str, pattern: str, paths: list[str]) -> str | None:
    if not pattern or NOTES.search(body):
        return None
    found = [
        f"  {path}:{number}: {text.strip()}"
        for path, number, text in added_lines(diff)
        if (not paths or matches_any(path, paths)) and re.search(pattern, text)
    ]
    if not found:
        return None
    return "\n".join(
        [
            "Concurrency: added lines match the concurrency pattern, and the body has no "
            "`## Concurrency notes` section.",
            *found,
            "  Add a `## Concurrency notes` section saying what runs concurrently and why it is "
            "safe.",
        ]
    )


def main(argv: list[str], stdin: TextIO = sys.stdin) -> int:
    parser = argparse.ArgumentParser(prog="devtool pr-shape", description=__doc__)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument(
        "--concurrency-pattern",
        default="",
        help="a regex; added lines matching it need a `## Concurrency notes` section",
    )
    parser.add_argument(
        "--concurrency-paths",
        default="",
        help="gitignore-style patterns, one per line, limiting where the pattern applies",
    )
    args = parser.parse_args(argv)

    body = args.body_file.read_text()
    paths = load_patterns(args.concurrency_paths)
    problems = [
        problem
        for problem in (
            title_problem(args.title),
            body_problem(body),
            concurrency_problem(body, stdin.read(), args.concurrency_pattern, paths),
        )
        if problem is not None
    ]
    if not problems:
        print("PR shape OK.")
        return 0
    for problem in problems:
        print(problem)
    return 1
