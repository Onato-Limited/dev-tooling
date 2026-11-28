import argparse
import sys
from importlib.metadata import version

from devtool import (
    codeowners,
    main_audit,
    merge_when_green,
    pr_shape,
    pr_size,
    protected_paths,
    ralph_command,
    required_checks,
    review_comment,
    review_prompt,
    review_threads,
    review_verdict,
    secrets_report,
)

COMMANDS = {
    "pr-size": pr_size,
    "pr-shape": pr_shape,
    "protected-paths": protected_paths,
    "codeowners": codeowners,
    "required-checks": required_checks,
    "secrets-report": secrets_report,
    "review-verdict": review_verdict,
    "review-comment": review_comment,
    "review-prompt": review_prompt,
    "review-threads": review_threads,
    "merge-when-green": merge_when_green,
    "main-audit": main_audit,
    "ralph": ralph_command,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="devtool")
    parser.add_argument("--version", action="version", version=f"devtool {version('devtool')}")
    commands = parser.add_subparsers(dest="command", metavar="command", required=True)
    for name, module in COMMANDS.items():
        # Each command parses its own options, so its --help is its own. argparse.REMAINDER
        # can't be used for that: it drops arguments that start with "-".
        commands.add_parser(name, add_help=False, help=module.__doc__)
    args, rest = parser.parse_known_args(argv)
    # sys.stdin is read here, not as a default argument, so a replaced stdin is the one used.
    return COMMANDS[args.command].main(rest, stdin=sys.stdin)
