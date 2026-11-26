import io
import tomllib
from pathlib import Path

import pytest

from devtool.cli import main

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def test_version_flag_prints_the_package_version(capsys):
    declared = tomllib.loads(PYPROJECT.read_text())["project"]["version"]

    with pytest.raises(SystemExit) as exit_:
        main(["--version"])

    assert exit_.value.code == 0
    assert capsys.readouterr().out == f"devtool {declared}\n"


def test_an_unknown_command_exits_2_and_lists_the_commands(capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["no-such-command"])

    assert exit_.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice: 'no-such-command'" in err
    assert "choose from pr-size" in err


def test_pr_size_runs_on_the_numstat_from_stdin_and_returns_its_exit_code(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("11\t0\tsrc/a.kt\n"))

    code = main(["pr-size", "--limit", "10", "--exclude-file", "/nonexistent"])

    assert code == 1
    assert capsys.readouterr().out.startswith("11 changed lines, limit 10\n")
