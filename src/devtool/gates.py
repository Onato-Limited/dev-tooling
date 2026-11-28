from fnmatch import fnmatchcase


def matches_any(path: str, patterns: list[str]) -> bool:
    """Whether the path matches, by gitignore rules: the last pattern that matches decides, and a
    `!` pattern excludes."""
    matched = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        if _matches(path, pattern[1:] if negated else pattern):
            matched = not negated
    return matched


def _matches(path: str, pattern: str) -> bool:
    # gitignore rules: a slash at the start or in the middle anchors the pattern at the root;
    # a trailing slash matches directories only. fnmatch's `*` crosses `/`, which gives `**`.
    directory_only = pattern.endswith("/")
    body = pattern.strip("/")
    if pattern.startswith("/") or "/" in body:
        return fnmatchcase(path, body + "/*" if directory_only else body)
    parts = path.split("/")
    candidates = parts[:-1] if directory_only else parts
    return any(fnmatchcase(part, body) for part in candidates)


def approved_by(events: list[dict], label: str, approver: str | None) -> bool:
    """Whether the latest change to the label was the approver adding it."""
    changes = [
        event
        for event in events
        if event["event"] in ("labeled", "unlabeled") and event["label"]["name"] == label
    ]
    if not changes:
        return False
    latest = changes[-1]
    return latest["event"] == "labeled" and latest["actor"]["login"] == approver


# A COMMENTED review leaves the reviewer's earlier decision standing; PENDING isn't submitted.
DECIDING = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")


def approved_by_review(reviews: list[dict], approver: str | None) -> bool:
    """Whether the approver's latest deciding review on the PR is an approval."""
    decisions = [
        r["state"] for r in reviews if r["user"]["login"] == approver and r["state"] in DECIDING
    ]
    return bool(decisions) and decisions[-1] == "APPROVED"


def load_patterns(text: str) -> list[str]:
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]
