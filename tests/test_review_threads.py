import io
import json
import subprocess

import pytest

from devtool.review_threads import QueryError, gh_graphql, main

FIX = (
    "  Resolve each hard finding and each person's thread before the PR merges. A judgement "
    "finding from the reviewer needs a reply saying why it was taken or left, and can stay open. "
    "Resolving a thread starts no run, so re-run this job after resolving one.\n"
)


def thread(path, line, author, body, resolved=False, outdated=False, url="https://x/1", replies=()):
    first = {"author": {"login": author}, "body": body, "url": url}
    others = [{"author": {"login": who}, "body": text, "url": url} for who, text in replies]
    return {
        "isResolved": resolved,
        "isOutdated": outdated,
        "path": path,
        "line": line,
        "comments": {"nodes": [first, *others]},
    }


def pages(*pages):
    """A fake GraphQL client serving `pages` of threads in order, recording each call."""
    calls = []

    def query(text, variables):
        calls.append(variables)
        index = len(calls) - 1
        more = index + 1 < len(pages)
        return {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": more, "endCursor": f"c{index + 1}"},
                        "nodes": pages[index],
                    }
                }
            }
        }

    query.calls = calls
    return query


def run(query):
    return main(["--repo", "o/r", "--pr", "12"], stdin=io.StringIO(), query=query)


def test_a_review_with_an_unresolved_thread_fails_listing_each_one_and_the_fix(capsys):
    query = pages(
        [
            thread("a.swift", 4, "claude", "[Standards] hard: C3\nmore", url="https://x/4"),
            thread("b.swift", 9, "onato", "Done?", resolved=True),
            thread("c.swift", 2, "onato", "Why not `async let`?", url="https://x/2"),
        ]
    )

    code = run(query)

    assert code == 1
    assert capsys.readouterr().out == (
        "Review threads: 2 blocking on PR #12.\n"
        "  a.swift:4  claude: [Standards] hard: C3  https://x/4\n"
        "  c.swift:2  onato: Why not `async let`?  https://x/2\n" + FIX
    )
    assert query.calls == [{"owner": "o", "name": "r", "number": 12, "after": None}]


def test_a_reviewer_judgement_thread_blocks_until_someone_replies_with_why(capsys):
    judgement = "[Spec] judgement: maybe guard the branch"
    query = pages([thread("w.yml", 12, "claude", judgement, url="https://x/12")])

    code = run(query)

    assert code == 1
    assert capsys.readouterr().out == (
        "Review threads: 1 blocking on PR #12.\n"
        "  w.yml:12  claude: [Spec] judgement: maybe guard the branch  https://x/12\n" + FIX
    )


def test_an_answered_reviewer_judgement_thread_is_left_open_without_blocking(capsys):
    judgement = "[Standards] judgement — a smell"
    answered = thread(
        "w.yml", 3, "claude", judgement, url="https://x/3", replies=[("onato", "Leaving it: …")]
    )

    code = run(pages([answered]))

    assert code == 0
    assert capsys.readouterr().out == (
        "Review threads: none blocking on PR #12. Judgement findings answered and left open:\n"
        "  w.yml:3  claude: [Standards] judgement — a smell  https://x/3\n"
    )


def test_a_reply_by_the_reviewer_itself_does_not_answer_a_judgement(capsys):
    judgement = "[Spec] judgement: maybe"
    query = pages([thread("w.yml", 5, "claude", judgement, replies=[("claude", "Still?")])])

    assert run(query) == 1


def test_a_persons_thread_labelled_like_a_judgement_blocks_until_resolved(capsys):
    labelled = "[Spec] judgement: please rename"
    query = pages([thread("w.yml", 5, "onato", labelled, replies=[("someone", "Leaving it")])])

    assert run(query) == 1


def test_resolved_threads_or_none_at_all_pass(capsys):
    for query in [pages([thread("a.swift", 1, "x", "y", resolved=True)]), pages([])]:
        code = run(query)

        assert code == 0
        assert capsys.readouterr().out == "Review threads: none unresolved on PR #12.\n"


def test_an_outdated_unresolved_thread_still_fails(capsys):
    long = "x" * 100
    query = pages([thread("a.swift", None, "claude", long, outdated=True, url="https://x/7")])

    code = run(query)

    assert code == 1
    assert capsys.readouterr().out == (
        "Review threads: 1 blocking on PR #12.\n"
        f"  a.swift (outdated)  claude: {'x' * 79}…  https://x/7\n" + FIX
    )


def test_threads_past_the_first_page_are_read(capsys):
    query = pages(
        [thread("a.swift", 1, "x", "y", resolved=True)],
        [thread("z.swift", 3, "x", "late", url="https://x/9")],
    )

    code = run(query)

    assert code == 1
    assert "  z.swift:3  x: late  https://x/9\n" in capsys.readouterr().out
    assert [call["after"] for call in query.calls] == [None, "c1"]


def test_threads_that_cannot_be_read_fail_naming_the_error_and_the_fix(capsys):
    def query(text, variables):
        raise QueryError("gh: Resource not accessible by integration")

    code = run(query)

    assert code == 1
    assert capsys.readouterr().out == (
        "Review threads: can't read PR #12's threads: gh: Resource not accessible by "
        "integration\n"
        "  The job needs `pull-requests: read`. Grant it in the calling workflow, then re-run "
        "this job.\n"
    )


def test_gh_graphql_sends_the_query_and_typed_variables_and_returns_the_data():
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"data": {"ok": 1}}))

    data = gh_graphql("query Q", {"owner": "o", "number": 12, "after": "c1"}, run=fake_run)

    assert data == {"ok": 1}
    assert calls == [
        (
            [
                "gh",
                "api",
                "graphql",
                "-f",
                "query=query Q",
                "-f",
                "owner=o",
                "-F",
                "number=12",
                "-f",
                "after=c1",
            ],
            {"capture_output": True, "text": True},
        )
    ]


def test_gh_graphql_leaves_out_a_variable_that_is_none():
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"data": {}}))

    gh_graphql("query Q", {"after": None}, run=fake_run)

    assert calls == [["gh", "api", "graphql", "-f", "query=query Q"]]


def test_gh_graphql_raises_with_what_gh_said_when_it_fails():
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="gh: Not Found (HTTP 404)\n")

    with pytest.raises(QueryError) as error:
        gh_graphql("query Q", {}, run=fake_run)

    assert str(error.value) == "gh: Not Found (HTTP 404)"


def test_a_thread_whose_author_was_deleted_is_listed_as_ghost(capsys):
    gone = thread("a.swift", 1, "x", "old", url="https://x/3")
    gone["comments"]["nodes"][0]["author"] = None

    run(pages([gone]))

    assert "  a.swift:1  ghost: old  https://x/3\n" in capsys.readouterr().out
