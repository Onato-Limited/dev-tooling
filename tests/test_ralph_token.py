import base64
import io
import json
import os
import subprocess
import urllib.error
from datetime import datetime, timedelta

import pytest

from devtool.ralph_token import (
    TokenError,
    TokenFile,
    Tokens,
    github_post,
    keychain_key,
    openssl_signer,
)


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class FakeGitHubApps:
    """Hands out installation tokens in turn and records the JWT each request was made with."""

    def __init__(self, *tokens):
        self.tokens = list(tokens)
        self.requests = []

    def __call__(self, path, bearer):
        self.requests.append((path, bearer))
        value, expires_at = self.tokens.pop(0)
        return {"token": value, "expires_at": expires_at}


def tokens(github, now=1_795_000_000.0):
    return Tokens(
        "5872314", "184603282", sign=lambda data: b"signed:" + data, post=github, clock=lambda: now
    )


def test_an_app_jwt_signed_with_the_key_is_exchanged_for_an_installation_token():
    github = FakeGitHubApps(("ghs_first", "2026-11-23T12:00:00Z"))

    token = tokens(github).current()

    assert token == "ghs_first"
    path, jwt = github.requests[0]
    assert path == "/app/installations/184603282/access_tokens"
    header, payload, signature = jwt.split(".")
    assert json.loads(unb64(header)) == {"alg": "RS256", "typ": "JWT"}
    assert json.loads(unb64(payload)) == {
        "iat": 1_794_999_940,
        "exp": 1_795_000_540,
        "iss": "5872314",
    }
    assert unb64(signature) == f"signed:{header}.{payload}".encode()


def test_each_session_gets_a_newly_minted_token_even_while_the_last_is_valid():
    github = FakeGitHubApps(
        ("ghs_first", "2026-11-23T12:00:00Z"), ("ghs_second", "2026-11-23T12:20:00Z")
    )
    source = tokens(github, now=1_795_435_200.0 - 2400)

    first = source.session_env({})["GH_TOKEN"]
    second = source.session_env({})["GH_TOKEN"]

    assert (first, second) == ("ghs_first", "ghs_second")


def test_the_session_gets_the_token_as_gh_token_and_it_never_appears_when_printed():
    source = tokens(FakeGitHubApps(("ghs_secret", "2026-11-23T12:00:00Z")))

    env = source.session_env({"PATH": "/usr/bin", "GH_TOKEN": "owner-token"})

    assert env == {"PATH": "/usr/bin", "GH_TOKEN": "ghs_secret"}
    assert "ghs_secret" not in repr(source)


def completed(returncode=0, stdout=""):
    return subprocess.CompletedProcess([], returncode, stdout, "")


def test_the_keychain_key_is_decoded_from_the_hex_security_returns():
    key = "first line of the key\nsecond line\n"
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return completed(stdout=key.encode().hex() + "\n")

    assert keychain_key("ralph-bot", run) == key
    assert calls == [
        ["security", "find-generic-password", "-s", "ralph-bot", "-a", "private-key", "-w"]
    ]


def test_a_key_missing_from_the_keychain_stops_the_loop_naming_the_app_and_the_fix():
    with pytest.raises(TokenError) as error:
        keychain_key("some-app", lambda args, **kwargs: completed(returncode=44))

    assert str(error.value) == (
        "Ralph loop can't start: no private key for the some-app GitHub App in the login "
        "keychain (service some-app, account private-key). Add it with: security "
        'add-generic-password -s some-app -a private-key -w "$(cat <key>.pem)"'
    )


def test_openssl_signs_with_the_key_passed_through_a_pipe_never_a_file():
    seen = {}

    def run(args, **kwargs):
        (fd,) = kwargs["pass_fds"]
        seen.update(args=args, fd=fd, key=os.read(fd, 100), input=kwargs["input"])
        return completed(stdout=b"signature")

    signature = openssl_signer("KEY\n", run)(b"header.payload")

    assert signature == b"signature"
    assert seen["args"] == ["openssl", "dgst", "-sha256", "-sign", f"/dev/fd/{seen['fd']}"]
    assert (seen["key"], seen["input"]) == (b"KEY\n", b"header.payload")


def test_the_token_request_posts_to_the_github_api_with_the_jwt_as_bearer():
    sent = []

    def urlopen(request):
        sent.append(request)
        return io.BytesIO(b'{"token": "ghs_x", "expires_at": "2026-11-23T12:00:00Z"}')

    reply = github_post(urlopen)("/app/installations/1/access_tokens", "jwt.value.sig")

    assert reply == {"token": "ghs_x", "expires_at": "2026-11-23T12:00:00Z"}
    (request,) = sent
    assert (request.method, request.full_url) == (
        "POST",
        "https://api.github.com/app/installations/1/access_tokens",
    )
    assert request.get_header("Authorization") == "Bearer jwt.value.sig"


def test_a_token_request_github_refuses_names_the_request_the_status_and_what_to_check():
    def urlopen(request):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    with pytest.raises(TokenError) as error:
        github_post(urlopen)("/app/installations/1/access_tokens", "jwt")

    assert str(error.value) == (
        "Ralph loop can't get a GitHub token: POST /app/installations/1/access_tokens: "
        "HTTP 404 Not Found. Check app_id and installation_id in .ralph/config, and that the "
        "keychain key is that app's."
    )


def test_a_token_request_that_never_reached_github_says_so_without_the_config_hint():
    def urlopen(request):
        raise urllib.error.URLError("nodename nor servname provided")

    with pytest.raises(TokenError) as error:
        github_post(urlopen)("/app/installations/1/access_tokens", "jwt")

    assert str(error.value) == (
        "Ralph loop can't get a GitHub token: POST /app/installations/1/access_tokens didn't "
        "reach GitHub: <urlopen error nodename nor servname provided>. Check the network "
        "connection, then start the loop again."
    )


def test_a_key_openssl_rejects_stops_the_loop_with_what_openssl_said():
    def run(args, **kwargs):
        raise subprocess.CalledProcessError(1, args, b"", b"Could not read private key\n")

    with pytest.raises(TokenError) as error:
        openssl_signer("not a key", run)(b"header.payload")

    assert str(error.value) == (
        "Ralph loop can't sign the app's token request: openssl: Could not read private key. "
        "The keychain key must be the app's private key, in PEM form."
    )


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 27, 12, 0)

    def __call__(self):
        return self.now


def test_the_token_file_holds_a_fresh_token_only_the_owner_can_read(tmp_path):
    minted = iter(["ghs_first"])

    token = TokenFile(tmp_path / "token", lambda: next(minted), Clock())

    assert token.path.read_text() == "ghs_first"
    assert token.path.stat().st_mode & 0o777 == 0o600


def test_the_token_file_gets_a_new_token_once_45_minutes_pass_and_not_before(tmp_path):
    minted = iter(["ghs_first", "ghs_second"])
    clock = Clock()
    token = TokenFile(tmp_path / "token", lambda: next(minted), clock)

    clock.now += timedelta(minutes=44, seconds=59)
    token.refresh()
    assert token.path.read_text() == "ghs_first"

    clock.now += timedelta(seconds=1)
    token.refresh()
    assert token.path.read_text() == "ghs_second"
    assert not (tmp_path / "token.new").exists()
