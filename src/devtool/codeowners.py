"""Fail when a PR changes a file that CODEOWNERS gives owners, and none of them has approved it."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TextIO

from devtool.gates import approved_by_review

FIX = "Ask one of each file's owners to approve the PR."


def rules(text: str) -> list[tuple[str, list[str]]]:
    """Each line's pattern and owners, in file order; a line may name no owners."""
    found = []
    for line in text.splitlines():
        # A space in a path is written `\ `, so split only at unescaped whitespace.
        words = re.split(r"(?<!\\)\s+", line.split("#", 1)[0].strip())
        if words != [""]:
            found.append((words[0].replace("\\ ", " "), words[1:]))
    return found


def matcher(pattern: str) -> re.Pattern[str]:
    """GitHub's reading of a CODEOWNERS pattern, which differs from gitignore's.

    A leading or inner `/` anchors it to the root. `*` stops at `/` and `**` doesn't. A pattern
    names a file or a directory, and covers the directory's contents with or without a trailing
    `/`, except that `docs/*` covers only files directly in `docs/`.
    """
    body = pattern.strip("/")
    anchored = pattern.startswith("/") or "/" in body
    regex = ""
    for token in re.split(r"(/\*\*/|\*\*/|/\*\*|\*\*|\*|\?)", body):
        regex += {
            "/**/": "/(?:.*/)?",
            "**/": "(?:.*/)?",
            "/**": "/.*",
            "**": ".*",
            "*": "[^/]*",
            "?": "[^/]",
        }.get(token, re.escape(token))
    last = body.rsplit("/", 1)[-1]
    if pattern.endswith("/"):
        regex += "/.*"
    elif last != "*":
        regex += "(?:/.*)?"
    return re.compile(("" if anchored else "(?:.*/)?") + regex)


def owners_of(path: str, rules: list[tuple[str, list[str]]]) -> list[str]:
    """The owners the last matching line names, as GitHub reads CODEOWNERS."""
    owners: list[str] = []
    for pattern, named in rules:
        if matcher(pattern).fullmatch(path):
            owners = named
    return owners


def person(owner: str) -> str | None:
    """The login of an `@user` owner; a team (`@org/team`) or an email has none."""
    return owner[1:].lower() if owner.startswith("@") and "/" not in owner else None


def main(argv: list[str], stdin: TextIO = sys.stdin) -> int:
    parser = argparse.ArgumentParser(prog="devtool codeowners", description=__doc__)
    parser.add_argument("--codeowners", type=Path, required=True, help="the base branch's file")
    parser.add_argument("--reviews", type=Path, required=True, help="the PR's reviews, as JSON")
    parser.add_argument("--author", help="the PR's author, who can't approve their own PR")
    args = parser.parse_args(argv)

    if not args.codeowners.exists():
        print("No CODEOWNERS file on the base branch.")
        return 0
    table = rules(args.codeowners.read_text())
    reviews = [
        {**r, "user": {"login": ((r["user"] or {}).get("login") or "").lower()}}
        for r in json.loads(args.reviews.read_text())
    ]
    author = (args.author or "").lower()
    owned = [(p, o) for p in stdin.read().splitlines() if (o := owners_of(p, table))]
    if not owned:
        print("No owned files changed.")
        return 0

    def approved(owners: list[str]) -> bool:
        people = [login for o in owners if (login := person(o)) and login != author]
        return any(approved_by_review(reviews, login) for login in people)

    waiting = [(path, owners) for path, owners in owned if not approved(owners)]
    if not waiting:
        print("Owned files, each approved by one of its owners:")
        print("\n".join(f"  {path}  ({', '.join(owners)})" for path, owners in owned))
        return 0
    print("Owned files without an approving review from one of their owners:")
    print("\n".join(f"  {path}  ({', '.join(owners)})" for path, owners in waiting))
    print(FIX)
    unknown = dict.fromkeys(o for _, owners in waiting for o in owners if person(o) is None)
    if unknown:
        print(
            f"This check can't tell who is in a team or behind an email: {', '.join(unknown)}. "
            'Name people instead, or protect the branch with "Require review from Code Owners".'
        )
    if any(person(o) == author for _, owners in waiting for o in owners):
        print(
            f"GitHub doesn't let {args.author} approve their own PR, so another owner approves "
            "it, or an admin merges it."
        )
    return 1
