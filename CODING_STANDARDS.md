# Coding standards

- **Tests first.** Each behaviour starts as a failing test, seen failing for the expected reason.
  Tests check behaviour through a command's or function's public interface, with expected values
  from an independent source (a literal, the spec, a file), never computed the way the code does.
- **Coverage.** 100% of lines and branches, enforced by `make coverage`. Untested code is deleted,
  not excluded.
- **No network in tests.** `tests/conftest.py` refuses `gh` and outbound sockets. GitHub is reached
  through a client passed in, which tests replace with a fake.
- **Failure messages name the rule and the fix.** A check that fails says which rule was broken,
  what it found (files, counts, names) and what to do about it, so nobody has to read the source to
  get a PR green.
- **No caller specifics.** Anything one project needs comes in as a workflow input or a file in the
  caller's repo.
- **Lint clean.** `make lint` passes with no warnings.
