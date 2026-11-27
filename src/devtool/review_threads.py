"""Fail while a PR has an unresolved review thread, listing each one."""

import argparse
import json
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
          comments(first: 1) { nodes { author { login } body url } }
        }
      }
    }
  }
}
"""

Query = Callable[[str, dict], dict]


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


def describe(thread: dict) -> str:
    where = f"{thread['path']}:{thread['line']}" if thread["line"] else f"{thread['path']}"
    if thread["isOutdated"]:
        where += " (outdated)"
    first = thread["comments"]["nodes"][0]
    line = (first["body"].splitlines() or [""])[0]
    if len(line) > 80:
        line = line[:79] + "…"
    # A deleted account's comments have no author; GitHub shows them as "ghost".
    author = (first["author"] or {"login": "ghost"})["login"]
    return f"  {where}  {author}: {line}  {first['url']}"


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
    unresolved = [t for t in found if not t["isResolved"]]
    if not unresolved:
        print(f"Review threads: none unresolved on PR #{args.pr}.")
        return 0
    print(f"Review threads: {len(unresolved)} unresolved on PR #{args.pr}.")
    for thread in unresolved:
        print(describe(thread))
    print(
        "  Every review thread must be resolved before the PR merges. Fix the code or reply, "
        "resolve the thread, then re-run this job: resolving a thread starts no run of its own."
    )
    return 1
