"""Fail when a PR changes more lines than the limit."""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from devtool.gates import approved_by_review, load_patterns, matches_any


@dataclass
class Result:
    passed: bool
    total: int
    largest: list[tuple[str, int]]


def entries(numstat: str):
    """(added, deleted, path) for each file of `git diff -z --numstat`, a renamed one under its
    new path. Newline-separated input is read too."""
    if "\0" not in numstat:
        yield from (tuple(line.split("\t", 2)) for line in numstat.splitlines())
        return
    fields = iter(numstat.split("\0"))
    for field in fields:
        if not field:
            continue
        added, deleted, path = field.split("\t", 2)
        if not path:  # a rename: its old path, then its new one, follow as fields of their own
            next(fields)
            path = next(fields)
        yield added, deleted, path


def check(numstat: str, excludes: list[str], limit: int) -> Result:
    sizes: dict[str, int] = {}
    for added, deleted, path in entries(numstat):
        binary = added == "-"
        if not binary and not matches_any(path, excludes):
            sizes[path] = int(added) + int(deleted)
    total = sum(sizes.values())
    largest = sorted(sizes.items(), key=lambda item: item[1], reverse=True)
    return Result(passed=total <= limit, total=total, largest=largest)


def main(argv: list[str], stdin: TextIO = sys.stdin) -> int:
    parser = argparse.ArgumentParser(
        prog="devtool pr-size", description="Fail when a PR changes too many lines."
    )
    parser.add_argument("--limit", type=int, default=400)
    parser.add_argument("--exclude-file", type=Path, default=Path(".github/pr-size-exclude"))
    parser.add_argument("--approver", help="the only login whose approving review counts")
    parser.add_argument("--reviews", type=Path, help="the PR's reviews, as JSON")
    parser.add_argument("--author", help="the PR's author, who can't approve their own PR")
    args = parser.parse_args(argv)

    excludes = load_patterns(args.exclude_file.read_text()) if args.exclude_file.exists() else []
    result = check(stdin.read(), excludes=excludes, limit=args.limit)
    reviews = json.loads(args.reviews.read_text()) if args.reviews else []
    approved = not result.passed and approved_by_review(reviews, args.approver)

    approval = f": approved by {args.approver}'s review" if approved else ""
    print(f"{result.total} changed lines, limit {args.limit}{approval}")
    for path, size in result.largest[:10]:
        print(f"  {size:6d}  {path}")
    if result.passed or approved:
        return 0
    print(fix(args.limit, args.approver, args.author))
    return 1


def fix(limit: int, approver: str | None, author: str | None = None) -> str:
    split = (
        f"PRs change at most {limit} lines. "
        "Split this PR into smaller ones that each merge on their own"
    )
    if approver is None:
        return split + "."
    if approver == author:
        return (
            f"{split}. GitHub doesn't let {approver} approve their own PR, so to keep it as one, "
            "an admin merges it."
        )
    return f"{split}, or ask {approver} to approve it as one."
