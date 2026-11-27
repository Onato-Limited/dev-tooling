"""Fail unless a Claude review approved and was refused no tool it needs."""

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

VERDICTS = ("APPROVE", "REQUEST_CHANGES")
REQUIRED_TOOLS = {"mcp__github_inline_comment__create_inline_comment"}
REQUIRED_COMMANDS = ("gh pr view", "gh pr diff", "gh issue view")


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def run_result(execution: Path) -> dict | None:
    messages = read_json(execution)
    if not isinstance(messages, list):
        return None
    results = [m for m in messages if isinstance(m, dict) and m.get("type") == "result"]
    return results[-1] if results else None


def is_required(denial: dict) -> bool:
    if denial.get("tool_name") in REQUIRED_TOOLS:
        return True
    command = (denial.get("tool_input") or {}).get("command") or ""
    # Anywhere in the command: a chain that includes the diff still means the diff was refused.
    return any(required in command for required in REQUIRED_COMMANDS)


def describe(denial: dict) -> str:
    tool_input = denial.get("tool_input") or {}
    detail = tool_input.get("command") or tool_input.get("file_path") or json.dumps(tool_input)
    return f"{denial.get('tool_name', '?')}: {detail}"


def main(argv: list[str], stdin: TextIO = sys.stdin) -> int:
    parser = argparse.ArgumentParser(prog="devtool review-verdict", description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="the review's structured output")
    parser.add_argument("--execution", type=Path, required=True, help="the run's execution file")
    args = parser.parse_args(argv)

    result = run_result(args.execution)
    if result is None:
        print(
            f"Review: no run result in {args.execution}. A review counts only once its run "
            "finishes; re-run the review job, and check its Review step's log if it fails again."
        )
        return 1
    if result.get("is_error"):
        # claude-code-action hides Claude's output, so this is the only place the cause shows.
        error = str(result.get("result") or "").strip() or "(the run gave no error text)"
        print(f"Review: the Claude run ended in an error: {error}")
        print(
            "  Fix the cause it names (the model, effort or CLAUDE_CODE_OAUTH_TOKEN the workflow "
            "was given), then re-run the review job."
        )
        return 1
    denials = result.get("permission_denials") or []
    count = max(len(denials), result.get("permission_denials_count") or 0)
    # Unlisted denials can't be told apart, so they count as required.
    fatal = count > len(denials) or any(map(is_required, denials))
    if count:
        kind = "" if fatal else "optional "
        print(f"{count} {kind}tool calls were denied during the review:")
        for denial in denials:
            print(f"  {describe(denial)}")
    if fatal:
        print(
            "Review: a review that was refused a tool it needs to read the PR or post its "
            "findings does not count, whatever its verdict. Re-run it; if the same calls are "
            "refused again, the workflow's allowed tools are missing them."
        )
        return 1
    output = read_json(args.output)
    verdict = output.get("verdict") if isinstance(output, dict) else None
    if verdict not in VERDICTS:
        print(
            f"Review: no verdict in {args.output}. A review counts only when it returns "
            f"{' or '.join(VERDICTS)}; re-run the review job."
        )
        return 1
    if verdict == "APPROVE":
        return 0
    print(str(output.get("summary") or "").strip())
    print("Review: changes were requested. Fix the hard findings in the review comment and push.")
    return 1
