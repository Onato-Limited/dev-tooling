# dev-tooling

Shared CI policy checks, PR review and Ralph loop tooling for Onato Limited projects.

## Use

```sh
uvx --from git+https://github.com/Onato-Limited/dev-tooling@v1 devtool --version
```

Pin `@v1`: it moves to each `v1.x.y` release and never to a breaking one.

## Develop

```sh
make bootstrap
make test lint coverage
```

See `CLAUDE.md` for the workflow and `CODING_STANDARDS.md` for the rules.
