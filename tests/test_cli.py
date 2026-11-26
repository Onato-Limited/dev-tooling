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
    assert "choose from" in err
