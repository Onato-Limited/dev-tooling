import io

from devtool.protected_paths import main


def test_a_change_under_a_protected_path_fails_naming_the_files_and_the_fix(capsys):
    changed = io.StringIO("legacy/old.py\nsrc/new.py\nlegacy/nested/util.py\n")

    code = main(["--patterns", "# frozen code\nlegacy/\n"], stdin=changed)

    assert code == 1
    assert capsys.readouterr().out == (
        "Changed under protected paths:\n"
        "  legacy/old.py\n"
        "  legacy/nested/util.py\n"
        "Files matching the protected-paths input may not change in a PR. Revert the changes to "
        "these files. If one must change, remove its pattern from protected-paths in a PR of its "
        "own first.\n"
    )


def test_with_no_protected_paths_the_check_passes(capsys):
    code = main([], stdin=io.StringIO("legacy/old.py\n"))

    assert code == 0
    assert capsys.readouterr().out == "No protected paths changed.\n"


def test_changes_only_outside_the_protected_paths_pass(capsys):
    code = main(["--patterns", "legacy/"], stdin=io.StringIO("src/legacy.py\ndocs/a.md\n"))

    assert code == 0
    assert capsys.readouterr().out == "No protected paths changed.\n"
