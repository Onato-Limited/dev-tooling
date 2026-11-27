import pytest

from devtool.ralph_config import Config, ConfigError, load

REQUIRED = """\
# The GitHub App the loop works as; its key is in the login keychain.
app_id=123
installation_id=456
app_slug=some-app
board_owner=some-org
board_number=1
merge_mode=label
"""


def write(tmp_path, text):
    path = tmp_path / ".ralph" / "config"
    path.parent.mkdir()
    path.write_text(text)
    return path


def test_a_full_config_gives_every_value(tmp_path):
    path = write(
        tmp_path,
        REQUIRED
        + "review_check=claude / review\n"
        + "audit_check=audit / main\n"
        + "notify=scripts/notify.sh\n"
        + "cleanup_list=scripts/devices list\n"
        + "cleanup_prune=scripts/devices prune\n"
        + "update_hook=scripts/after-merge.sh\n"
        + "session_path=tools/bin\n",
    )

    assert load(path) == Config(
        app_id="123",
        installation_id="456",
        app_slug="some-app",
        board_owner="some-org",
        board_number=1,
        merge_mode="label",
        review_check="claude / review",
        audit_check="audit / main",
        notify="scripts/notify.sh",
        cleanup_list="scripts/devices list",
        cleanup_prune="scripts/devices prune",
        update_hook="scripts/after-merge.sh",
        session_path="tools/bin",
    )


def test_the_optional_values_take_their_defaults_when_absent(tmp_path):
    config = load(write(tmp_path, REQUIRED))

    assert (config.review_check, config.audit_check) == ("claude / review", "main-audit / audit")
    assert (config.notify, config.cleanup_list, config.cleanup_prune) == (None, None, None)
    assert (config.update_hook, config.session_path) == (None, None)


def test_values_are_trimmed_and_a_value_may_hold_an_equals_sign(tmp_path):
    config = load(write(tmp_path, REQUIRED + "notify = scripts/send.sh --to=owner \n"))

    assert config.notify == "scripts/send.sh --to=owner"


def test_a_commented_out_setting_is_ignored(tmp_path):
    config = load(write(tmp_path, "# notify=scripts/notify.sh\n#merge_mode=auto\n" + REQUIRED))

    assert (config.notify, config.merge_mode) == (None, "label")


def test_blank_lines_and_indented_comments_between_settings_are_ignored(tmp_path):
    config = load(
        write(tmp_path, REQUIRED + "\n   \n  # off for now\n  notify=scripts/notify.sh\n")
    )

    assert config.notify == "scripts/notify.sh"


@pytest.mark.parametrize("line", ["notify scripts/notify.sh", "=scripts/notify.sh"])
def test_a_line_that_is_not_name_equals_value_fails_naming_the_line(tmp_path, line):
    path = write(tmp_path, REQUIRED + line + "\n")

    with pytest.raises(ConfigError) as error:
        load(path)

    assert str(error.value) == (
        f"Ralph loop can't start: line 8 of {path} is {line!r}. Write each "
        "setting as name=value, or start the line with # to make it a comment."
    )


def test_a_missing_file_fails_naming_the_file_and_every_required_key(tmp_path):
    with pytest.raises(ConfigError) as error:
        load(tmp_path / ".ralph" / "config")

    assert str(error.value) == (
        f"Ralph loop can't start: {tmp_path}/.ralph/config is missing. Add it with these keys, "
        "one name=value per line: app_id, installation_id, app_slug, board_owner, "
        "board_number, merge_mode."
    )


def test_missing_required_keys_fail_naming_each_one_and_the_file(tmp_path):
    path = write(tmp_path, "app_id=123\napp_slug=\nmerge_mode=auto\n")

    with pytest.raises(ConfigError) as error:
        load(path)

    assert str(error.value) == (
        f"Ralph loop can't start: {path} is missing installation_id, app_slug, board_owner, "
        "board_number. Add each as a name=value line."
    )


def test_an_unknown_key_fails_naming_it_and_the_keys_there_are(tmp_path):
    path = write(tmp_path, REQUIRED + "notfy=scripts/notify.sh\n")

    with pytest.raises(ConfigError) as error:
        load(path)

    assert str(error.value) == (
        f"Ralph loop can't start: {path} has unknown keys: notfy. The keys are app_id, "
        "installation_id, app_slug, board_owner, board_number, merge_mode, review_check, "
        "audit_check, notify, cleanup_list, cleanup_prune, update_hook, session_path. Rename each "
        "to one of these, or remove it."
    )


@pytest.mark.parametrize("mode", ["auto", "label"])
def test_each_merge_mode_loads(tmp_path, mode):
    config = load(write(tmp_path, REQUIRED.replace("merge_mode=label", f"merge_mode={mode}")))

    assert config.merge_mode == mode


def test_a_merge_mode_other_than_auto_or_label_fails_naming_the_two(tmp_path):
    path = write(tmp_path, REQUIRED.replace("merge_mode=label", "merge_mode=squash"))

    with pytest.raises(ConfigError) as error:
        load(path)

    assert str(error.value) == (
        f"Ralph loop can't start: merge_mode in {path} is 'squash'. Set it to auto (enable "
        "auto-merge on each PR) or label (add merge-when-green to each PR)."
    )


@pytest.mark.parametrize("value", ["one", "²"])
def test_a_board_number_that_is_not_a_number_fails_naming_the_key(tmp_path, value):
    path = write(tmp_path, REQUIRED.replace("board_number=1", f"board_number={value}"))

    with pytest.raises(ConfigError) as error:
        load(path)

    assert str(error.value) == (
        f"Ralph loop can't start: board_number in {path} is {value!r}. Set it to the project's "
        "number, from its URL (.../projects/<number>)."
    )
