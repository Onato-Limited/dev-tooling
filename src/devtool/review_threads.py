"""Fail while a PR has a review thread that blocks it, listing each one."""

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from typing import TextIO

QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          isOutdated
          path
          line
          comments(first: 100) { nodes { author { __typename login } body url } }
        }
      }
    }
  }
}
"""

Query = Callable[[str, dict], dict]

# The Claude app. A user account can have the same login, so the author must also be a Bot.
REVIEWER = "claude"
JUDGEMENT = re.compile(r"\[[^\]]+\]\s+judgement\b", re.IGNORECASE)
FIX = (
    "  Resolve each hard finding and each person's thread before the PR merges. A judgement "
    "finding from the reviewer needs a reply saying why it was taken or left."
)


class QueryError(Exception):
    """GitHub's GraphQL API couldn't be read; the message is what `gh` said."""


def gh_graphql(query: str, variables: dict, run=subprocess.run) -> dict:
    """The `data` of a GraphQL query, through `gh` and its GH_TOKEN."""
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is not None:
            # -F sends a number as a number; -f keeps a string, such as a cursor, a string.
            flag = "-F" if isinstance(value, int) else "-f"
            args += [flag, f"{key}={value}"]
    result = run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise QueryError(result.stderr.strip())
    return json.loads(result.stdout)["data"]


def threads(repo: str, pr: int, query: Query) -> list[dict]:
    owner, name = repo.split("/", 1)
    found, after = [], None
    while True:
        variables = {"owner": owner, "name": name, "number": pr, "after": after}
        page = query(QUERY, variables)["repository"]["pullRequest"]["reviewThreads"]
        found += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return found
        after = page["pageInfo"]["endCursor"]


def login(comment: dict) -> str:
    # A deleted account's comments have no author; GitHub shows them as "ghost".
    return (comment["author"] or {"login": "ghost"})["login"]


def by_reviewer(comment: dict) -> bool:
    author = comment["author"] or {}
    return author.get("__typename") == "Bot" and author.get("login") == REVIEWER


def answered_judgement(thread: dict) -> bool:
    """A judgement finding from the reviewer that someone else has replied to."""
    first, *replies = thread["comments"]["nodes"]
    return (
        by_reviewer(first)
        and JUDGEMENT.match(first["body"]) is not None
        and any(not by_reviewer(reply) for reply in replies)
    )


def blocking(thread: dict) -> bool:
    """Whether the thread holds up the merge: unresolved, and not an answered judgement."""
    return not thread["isResolved"] and not answered_judgement(thread)


def describe(thread: dict) -> str:
    where = f"{thread['path']}:{thread['line']}" if thread["line"] else f"{thread['path']}"
    if thread["isOutdated"]:
        where += " (outdated)"
    first = thread["comments"]["nodes"][0]
    line = (first["body"].splitlines() or [""])[0]
    if len(line) > 80:
        line = line[:79] + "…"
    return f"  {where}  {login(first)}: {line}  {first['url']}"


def main(argv: list[str], stdin: TextIO = sys.stdin, query: Query = gh_graphql) -> int:
    parser = argparse.ArgumentParser(prog="devtool review-threads", description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/name of the PR's repository")
    parser.add_argument("--pr", type=int, required=True, help="the PR number")
    args = parser.parse_args(argv)

    try:
        found = threads(args.repo, args.pr, query)
    except QueryError as error:
        print(f"Review threads: can't read PR #{args.pr}'s threads: {error}")
        print(
            "  The job needs `pull-requests: read`. Grant it in the calling workflow, then re-run "
            "this job."
        )
        return 1
    blocked = [t for t in found if blocking(t)]
    answered = [t for t in found if not t["isResolved"] and not blocking(t)]
    if blocked:
        print(f"Review threads: {len(blocked)} blocking on PR #{args.pr}.")
        for thread in blocked:
            print(describe(thread))
        print(FIX)
        if answered:
            print("Judgement findings answered and left open:")
    elif answered:
        print(
            f"Review threads: none blocking on PR #{args.pr}. "
            "Judgement findings answered and left open:"
        )
    else:
        print(f"Review threads: none unresolved on PR #{args.pr}.")
    for thread in answered:
        print(describe(thread))
    return 1 if blocked else 0
