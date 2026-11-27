import io
import json

from devtool.secrets_report import main


def report(tmp_path, *findings):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(list(findings)))
    return ["--report", str(path)]


def test_an_empty_report_passes(tmp_path, capsys):
    code = main(report(tmp_path), stdin=io.StringIO())

    assert code == 0
    assert capsys.readouterr().out == "No secrets found in this PR's commits.\n"


# Fake values that look like secrets: `gitleaks:allow` keeps this repo's own scan from failing
# on them.
SECRET = "x8Fq2LmP9vR3tY7wK1zB5nC4hJ6dS0aE"  # gitleaks:allow


def finding(file, line, rule, commit):
    return {
        "RuleID": rule,
        "StartLine": line,
        "Match": f'api_key = "{SECRET}"',
        "Secret": SECRET,
        "File": file,
        "Commit": commit,
        "Fingerprint": f"{commit}:{file}:{rule}:{line}",
    }


def test_findings_fail_listing_each_one_then_the_rule_and_the_fix_without_the_secret(
    tmp_path, capsys
):
    argv = report(
        tmp_path,
        finding("src/config.py", 2, "generic-api-key", "a1b2c3d4e5f6a7b8"),  # gitleaks:allow
        finding("deploy/env", 9, "aws-access-token", "0f9e8d7c6b5a4f3e"),  # gitleaks:allow
    )

    code = main(argv, stdin=io.StringIO())

    assert code == 1
    out = capsys.readouterr().out
    assert out == (
        "Secrets: gitleaks found 2 possible secrets in this PR's commits.\n"
        "  src/config.py:2  generic-api-key  (commit a1b2c3d)\n"
        "  deploy/env:9  aws-access-token  (commit 0f9e8d7)\n"
        "  No secret may be committed. Remove it and rotate it: it is in this branch's pushed "
        "history, so deleting the line does not un-leak it. If a finding is a false positive, "
        "add its fingerprint to .gitleaksignore:\n"
        "    a1b2c3d4e5f6a7b8:src/config.py:generic-api-key:2\n"
        "    0f9e8d7c6b5a4f3e:deploy/env:aws-access-token:9\n"
    )
    assert SECRET not in out


def test_a_single_finding_is_counted_in_the_singular(tmp_path, capsys):
    argv = report(tmp_path, finding("a.py", 1, "generic-api-key", "a1b2c3d4"))  # gitleaks:allow

    main(argv, stdin=io.StringIO())

    first = capsys.readouterr().out.splitlines()[0]
    assert first == "Secrets: gitleaks found 1 possible secret in this PR's commits."
