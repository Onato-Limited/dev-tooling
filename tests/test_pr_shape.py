import io

import pytest

from devtool.pr_shape import main

GOOD_BODY = "Closes #12\n\n## What\nThings.\n"


def shape(tmp_path, title, body=GOOD_BODY, diff="", extra=()):
    body_file = tmp_path / "body.md"
    body_file.write_text(body)
    argv = ["--title", title, "--body-file", str(body_file), *extra]
    return main(argv, stdin=io.StringIO(diff))


def test_a_title_that_is_not_a_conventional_commit_fails_with_the_rule_and_the_fix(
    tmp_path, capsys
):
    code = shape(tmp_path, "Add the pr-shape check")

    assert code == 1
    assert capsys.readouterr().out == (
        "Title: `Add the pr-shape check` isn't a conventional commit.\n"
        "  Start it with a type and a colon, like `feat: add pr-shape` or `fix(cli): …` (types: "
        "feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert), or with "
        "`Structural: ` for a change that only moves or renames code.\n"
    )


@pytest.mark.parametrize(
    "title", ["feat: add pr-shape", "fix(cli)!: drop --old", "Structural: move gates"]
)
def test_conventional_and_structural_titles_with_a_closing_body_pass(tmp_path, capsys, title):
    code = shape(tmp_path, title)

    assert code == 0
    assert capsys.readouterr().out == "PR shape OK.\n"


@pytest.mark.parametrize("title", ["feature: x", "Feat: x", "feat:x", "fix(): x", "Structural:"])
def test_titles_that_only_look_conventional_fail(tmp_path, title):
    assert shape(tmp_path, title) == 1


def test_a_body_that_closes_no_issue_fails_and_every_broken_rule_is_reported(tmp_path, capsys):
    code = shape(tmp_path, "Add it", body="Part of #11\n")

    assert code == 1
    out = capsys.readouterr().out
    assert out.startswith("Title: `Add it` isn't a conventional commit.\n")
    assert out.endswith(
        "Body: doesn't close an issue.\n"
        "  Add a line like `Closes #12` (or Fixes/Resolves, or `Closes owner/repo#12`), so the "
        "issue closes when the PR merges.\n"
    )


@pytest.mark.parametrize(
    "body", ["Fixes #3", "resolved some-org/tools#4", "Closes: #5", "See below.\n\ncloses #6\n"]
)
def test_any_github_closing_keyword_counts(tmp_path, body):
    assert shape(tmp_path, "feat: x", body=body) == 0


@pytest.mark.parametrize("body", ["Part of #11", "Closes 12", "Closes #", "Discloses #4"])
def test_text_that_only_mentions_an_issue_does_not_count(tmp_path, body):
    assert shape(tmp_path, "feat: x", body=body) == 1


DIFF = """\
diff --git a/src/queue.py b/src/queue.py
--- a/src/queue.py
+++ b/src/queue.py
@@ -10,0 +11,2 @@ class Queue:
+        self.lock = threading.Lock()
+        self.size = 0
@@ -40 +42 @@ def drain(self):
-        old = Lock()
+        pass
diff --git a/docs/notes.md b/docs/notes.md
--- a/docs/notes.md
+++ b/docs/notes.md
@@ -1 +1 @@
-old
+A Lock in the docs.
"""
CONCURRENCY = ("--concurrency-pattern", r"\bLock\b", "--concurrency-paths", "src/")


def test_added_lines_matching_the_concurrency_pattern_need_a_concurrency_notes_section(
    tmp_path, capsys
):
    code = shape(tmp_path, "feat: x", diff=DIFF, extra=CONCURRENCY)

    assert code == 1
    assert capsys.readouterr().out == (
        "Concurrency: added lines match the concurrency pattern, and the body has no "
        "`## Concurrency notes` section.\n"
        "  src/queue.py:11: self.lock = threading.Lock()\n"
        "  Add a `## Concurrency notes` section saying what runs concurrently and why it is "
        "safe.\n"
    )


def test_a_concurrency_notes_section_satisfies_the_rule(tmp_path):
    body = GOOD_BODY + "\n## Concurrency notes\nOne writer; the lock guards size.\n"

    assert shape(tmp_path, "feat: x", body=body, diff=DIFF, extra=CONCURRENCY) == 0


def test_removed_lines_and_unmatched_added_lines_need_no_notes(tmp_path):
    extra = ("--concurrency-pattern", r"\bLock\b|\basync\b", "--concurrency-paths", "lib/")

    assert shape(tmp_path, "feat: x", diff=DIFF, extra=extra) == 0


def test_without_concurrency_paths_the_pattern_applies_to_every_file(tmp_path, capsys):
    code = shape(tmp_path, "feat: x", diff=DIFF, extra=CONCURRENCY[:2])

    assert code == 1
    assert "  docs/notes.md:1: A Lock in the docs.\n" in capsys.readouterr().out
