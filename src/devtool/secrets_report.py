"""Explain a gitleaks JSON report: each finding, then the rule and the fix."""

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

FIX = (
    "  No secret may be committed. Remove it and rotate it: it is in this branch's pushed "
    "history, so deleting the line does not un-leak it. If a finding is a false positive, add "
    "its fingerprint to .gitleaksignore:"
)


def main(argv: list[str], stdin: TextIO = sys.stdin) -> int:
    parser = argparse.ArgumentParser(prog="devtool secrets-report", description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="gitleaks' JSON report")
    args = parser.parse_args(argv)

    findings = json.loads(args.report.read_text())
    if not findings:
        print("No secrets found in this PR's commits.")
        return 0
    # Match and Secret hold the secret itself, so they are never printed.
    count = f"{len(findings)} possible secret{'' if len(findings) == 1 else 's'}"
    print(f"Secrets: gitleaks found {count} in this PR's commits.")
    for found in findings:
        where = f"{found['File']}:{found['StartLine']}"
        print(f"  {where}  {found['RuleID']}  (commit {found['Commit'][:7]})")
    print(FIX)
    for found in findings:
        print(f"    {found['Fingerprint']}")
    return 1
