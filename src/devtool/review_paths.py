"""Fail when a PR changes a file under a review path, unless the approver has approved it."""

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from devtool.gates import approved_by, load_patterns, matches_any

APPROVAL_LABEL = "review-approved"


def main(argv: list[str], stdin: TextIO = sys.stdin) -> int:
    parser = argparse.ArgumentParser(prog="devtool review-paths", description=__doc__)
    parser.add_argument(
        "--patterns",
        default="",
        help="gitignore-style patterns, one per line; `!` excludes, `#` starts a comment",
    )
    parser.add_argument("--approver", help=f"the only login whose {APPROVAL_LABEL} label counts")
    parser.add_argument(
        "--label-events", type=Path, required=True, help="the PR's issue events, as JSON"
    )
    args = parser.parse_args(argv)

    patterns = load_patterns(args.patterns)
    touched = [path for path in stdin.read().splitlines() if matches_any(path, patterns)]
    if not touched:
        print("No review paths changed.")
        return 0
    events = json.loads(args.label_events.read_text())
    approved = approved_by(events, APPROVAL_LABEL, args.approver)
    approval = f", approved by {args.approver} ({APPROVAL_LABEL})" if approved else ""
    print(f"Changed under review paths{approval}:")
    for path in touched:
        print(f"  {path}")
    if approved:
        return 0
    print(fix(args.approver))
    return 1


def fix(approver: str | None) -> str:
    if approver is None:
        return (
            "Files matching the review-paths input need the owner's review, but no approver is "
            "set, so no label can pass this check. Set the approver input, or revert the changes "
            "to these files."
        )
    return (
        f"Files matching the review-paths input need {approver}'s review. Ask {approver} to add "
        f"the {APPROVAL_LABEL} label."
    )
