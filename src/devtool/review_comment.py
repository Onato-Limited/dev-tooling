"""Print a Claude review's top-level comment, with a footer naming the model and effort."""

import argparse
import sys
from pathlib import Path
from typing import TextIO

from devtool.review_verdict import read_json


def model_that_ran(execution: Path) -> str | None:
    messages = read_json(execution)
    for message in messages if isinstance(messages, list) else []:
        if isinstance(message, dict) and message.get("subtype") == "init":
            return message.get("model")
    return None


def main(argv: list[str], stdin: TextIO = sys.stdin) -> int:
    parser = argparse.ArgumentParser(prog="devtool review-comment", description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="the review's structured output")
    parser.add_argument("--execution", type=Path, required=True, help="the run's execution file")
    parser.add_argument("--effort", required=True, help="the effort the review ran at")
    args = parser.parse_args(argv)

    output = read_json(args.output)
    comment = str(output.get("comment") or "").strip() if isinstance(output, dict) else ""
    if not comment:
        print(
            f"Review: no comment to post in {args.output}. The review's structured output needs "
            "a non-empty `comment`; re-run the review job.",
            file=sys.stderr,
        )
        return 1
    model = model_that_ran(args.execution)
    by = f"`{model}`" if model else "an unknown model (the run has no init message naming one)"
    print(f"{comment}\n\n---\nReviewed by {by} at effort `{args.effort}`.")
    return 0
