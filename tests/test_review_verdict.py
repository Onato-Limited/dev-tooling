import io
import json

from devtool.review_verdict import main


def write_run(tmp_path, result):
    execution = tmp_path / "execution.json"
    execution.write_text(json.dumps([{"type": "system"}, {"type": "result", **result}]))
    return execution


def write_output(tmp_path, verdict, summary="Fine."):
    output = tmp_path / "output.json"
    output.write_text(json.dumps({"verdict": verdict, "summary": summary, "comment": "## Spec"}))
    return output


def run(output, execution):
    return main(["--output", str(output), "--execution", str(execution)], stdin=io.StringIO())


def test_approve_exits_zero(tmp_path):
    output = write_output(tmp_path, "APPROVE", "Looks good.")
    execution = write_run(tmp_path, {"permission_denials": []})

    assert run(output, execution) == 0


def test_request_changes_fails_printing_the_summary_and_the_fix(tmp_path, capsys):
    output = write_output(tmp_path, "REQUEST_CHANGES", "Standards: test asserts on a boolean.")
    execution = write_run(tmp_path, {"permission_denials": []})

    code = run(output, execution)

    assert code == 1
    assert capsys.readouterr().out == (
        "Standards: test asserts on a boolean.\n"
        "Review: changes were requested. Fix the hard findings in the review comment and push.\n"
    )


def test_a_missing_output_file_fails_closed(tmp_path, capsys):
    execution = write_run(tmp_path, {"permission_denials": []})
    absent = tmp_path / "absent.json"

    code = run(absent, execution)

    assert code == 1
    assert capsys.readouterr().out == (
        f"Review: no verdict in {absent}. A review counts only when it returns APPROVE or "
        "REQUEST_CHANGES; re-run the review job.\n"
    )


def test_an_empty_garbled_or_unknown_verdict_fails_closed(tmp_path, capsys):
    output = tmp_path / "output.json"
    execution = write_run(tmp_path, {"permission_denials": []})
    for text in [
        "",
        "not json",
        "[]",
        json.dumps({"summary": "no verdict key"}),
        json.dumps({"verdict": "APPROVED"}),
        json.dumps({"verdict": "LGTM, APPROVE"}),
    ]:
        output.write_text(text)

        code = run(output, execution)

        assert code == 1, text
        assert f"Review: no verdict in {output}." in capsys.readouterr().out, text


def test_an_approval_after_a_required_tool_was_denied_fails_and_names_each_call(tmp_path, capsys):
    output = write_output(tmp_path, "APPROVE", "Read the full diff.")
    execution = write_run(
        tmp_path,
        {
            "permission_denials": [
                {"tool_name": "Bash", "tool_input": {"command": "gh pr diff 26 | head -50"}},
                {"tool_name": "Write", "tool_input": {"file_path": "/tmp/body.md"}},
            ]
        },
    )

    code = run(output, execution)

    assert code == 1
    assert capsys.readouterr().out == (
        "2 tool calls were denied during the review:\n"
        "  Bash: gh pr diff 26 | head -50\n"
        "  Write: /tmp/body.md\n"
        "Review: a review that was refused a tool it needs to read the PR or post its findings "
        "does not count, whatever its verdict. Re-run it; if the same calls are refused again, "
        "the workflow's allowed tools are missing them.\n"
    )


def test_each_gather_and_report_tool_is_required(tmp_path):
    output = write_output(tmp_path, "APPROVE")
    for denial in [
        {"tool_name": "Bash", "tool_input": {"command": "gh pr view 26 --json title,body,files"}},
        {"tool_name": "Bash", "tool_input": {"command": "cat CLAUDE.md; gh pr diff 26"}},
        {"tool_name": "Bash", "tool_input": {"command": "gh issue view 6"}},
        {
            "tool_name": "mcp__github_inline_comment__create_inline_comment",
            "tool_input": {"path": "Makefile", "body": "[Spec] hard"},
        },
    ]:
        execution = write_run(tmp_path, {"permission_denials": [denial]})

        assert run(output, execution) == 1, denial


def test_denied_optional_commands_are_listed_but_do_not_fail_the_review(tmp_path, capsys):
    output = write_output(tmp_path, "APPROVE")
    execution = write_run(
        tmp_path,
        {
            "permission_denials": [
                {"tool_name": "Bash", "tool_input": {"command": "git diff origin/main --stat"}},
                {"tool_name": "Bash", "tool_input": {"command": "gh api repos/o/r/branches/main"}},
                {"tool_name": "Edit", "tool_input": {"old_string": "a"}},
            ]
        },
    )

    code = run(output, execution)

    assert code == 0
    assert capsys.readouterr().out == (
        "3 optional tool calls were denied during the review:\n"
        "  Bash: git diff origin/main --stat\n"
        "  Bash: gh api repos/o/r/branches/main\n"
        '  Edit: {"old_string": "a"}\n'
    )


def test_a_denial_count_without_the_list_still_fails(tmp_path, capsys):
    output = write_output(tmp_path, "APPROVE")
    execution = write_run(tmp_path, {"permission_denials_count": 2})

    code = run(output, execution)

    assert code == 1
    assert capsys.readouterr().out.startswith("2 tool calls were denied during the review:\n")


def test_a_missing_or_unreadable_execution_file_fails_closed(tmp_path, capsys):
    output = write_output(tmp_path, "APPROVE")
    garbled = tmp_path / "garbled.json"
    garbled.write_text("not json")
    no_result = tmp_path / "no_result.json"
    no_result.write_text(json.dumps([{"type": "system"}]))
    for execution in [tmp_path / "absent.json", garbled, no_result]:
        code = run(output, execution)

        assert code == 1, execution
        assert capsys.readouterr().out == (
            f"Review: no run result in {execution}. A review counts only once its run "
            "finishes; re-run the review job, and check its Review step's log if it fails again.\n"
        )


def test_a_run_that_ended_in_an_error_fails_printing_the_error(tmp_path, capsys):
    output = write_output(tmp_path, "APPROVE")
    execution = write_run(
        tmp_path,
        {"is_error": True, "result": "API Error: 404 model: claude-x not found", "num_turns": 1},
    )

    code = run(output, execution)

    assert code == 1
    assert capsys.readouterr().out == (
        "Review: the Claude run ended in an error: API Error: 404 model: claude-x not found\n"
        "  Fix the cause it names (the model, effort or CLAUDE_CODE_OAUTH_TOKEN the workflow was "
        "given), then re-run the review job.\n"
    )


def test_an_error_run_without_error_text_still_fails_and_says_so(tmp_path, capsys):
    output = write_output(tmp_path, "APPROVE")
    execution = write_run(tmp_path, {"is_error": True})

    code = run(output, execution)

    assert code == 1
    assert capsys.readouterr().out.startswith(
        "Review: the Claude run ended in an error: (the run gave no error text)\n"
    )
