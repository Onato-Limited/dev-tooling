import io
import json

from devtool.review_comment import main


def write_output(tmp_path, output):
    path = tmp_path / "output.json"
    path.write_text(json.dumps(output))
    return path


def write_run(tmp_path, *messages):
    path = tmp_path / "execution.json"
    path.write_text(json.dumps([*messages, {"type": "result"}]))
    return path


INIT = {"type": "system", "subtype": "init", "model": "claude-fable-5-2"}


def run(output, execution, effort="high"):
    argv = ["--output", str(output), "--execution", str(execution), "--effort", effort]
    return main(argv, stdin=io.StringIO())


def test_the_comment_gets_a_footer_naming_the_model_that_ran_and_the_effort(tmp_path, capsys):
    output = write_output(tmp_path, {"verdict": "APPROVE", "comment": "## Standards\nNo findings"})
    execution = write_run(tmp_path, INIT)

    code = run(output, execution, effort="xhigh")

    assert code == 0
    assert capsys.readouterr().out == (
        "## Standards\nNo findings\n\n---\nReviewed by `claude-fable-5-2` at effort `xhigh`.\n"
    )


def test_the_model_comes_from_the_init_message_not_other_system_messages(tmp_path, capsys):
    output = write_output(tmp_path, {"comment": "## Spec"})
    execution = write_run(
        tmp_path,
        {"type": "system", "subtype": "hook_response", "model": "not-this-one"},
        {"type": "assistant", "model": "nor-this-one"},
        INIT,
    )

    run(output, execution)

    assert capsys.readouterr().out.endswith("Reviewed by `claude-fable-5-2` at effort `high`.\n")


def test_an_unknown_model_is_said_to_be_unknown(tmp_path, capsys):
    output = write_output(tmp_path, {"comment": "## Spec"})
    garbled = tmp_path / "garbled.json"
    garbled.write_text("not json")
    for execution in [
        write_run(tmp_path),
        write_run(tmp_path, {"type": "system", "subtype": "init"}),
        garbled,
        tmp_path / "absent.json",
    ]:
        code = run(output, execution)

        assert code == 0, execution
        assert capsys.readouterr().out.endswith(
            "Reviewed by an unknown model (the run has no init message naming one) "
            "at effort `high`.\n"
        ), execution


def test_a_missing_or_empty_comment_fails_naming_the_rule_and_the_fix(tmp_path, capsys):
    execution = write_run(tmp_path, INIT)
    garbled = tmp_path / "garbled.json"
    garbled.write_text("not json")
    for output in [
        write_output(tmp_path, {"verdict": "APPROVE"}),
        write_output(tmp_path, {"comment": "  \n"}),
        write_output(tmp_path, ["## Spec"]),
        garbled,
        tmp_path / "absent.json",
    ]:
        code = run(output, execution)

        assert code == 1, output
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == (
            f"Review: no comment to post in {output}. The review's structured output needs a "
            "non-empty `comment`; re-run the review job.\n"
        ), output
