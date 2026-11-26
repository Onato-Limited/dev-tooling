import argparse
from importlib.metadata import version


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="devtool")
    parser.add_argument("--version", action="version", version=f"devtool {version('devtool')}")
    parser.add_subparsers(dest="command", metavar="command", required=True)
    parser.parse_args(argv)
