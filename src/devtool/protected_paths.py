"""Fail when a PR changes a file under a protected path."""

import argparse
import sys
from typing import TextIO

from devtool.gates import load_patterns, matches_any

FIX = (
    "Files matching the protected-paths input may not change in a PR. Revert the changes to these "
    "files. If one must change, remove its pattern from protected-paths in a PR of its own first."
)


def main(argv: list[str], stdin: TextIO = sys.stdin) -> int:
    parser = argparse.ArgumentParser(prog="devtool protected-paths", description=__doc__)
    parser.add_argument(
        "--patterns",
        default="",
        help="gitignore-style patterns, one per line; `#` starts a comment",
    )
    args = parser.parse_args(argv)

    patterns = load_patterns(args.patterns)
    touched = [path for path in stdin.read().splitlines() if matches_any(path, patterns)]
    if not touched:
        print("No protected paths changed.")
        return 0
    print("Changed under protected paths:")
    for path in touched:
        print(f"  {path}")
    print(FIX)
    return 1
