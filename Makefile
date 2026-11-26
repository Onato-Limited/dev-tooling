.PHONY: help bootstrap test lint coverage

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

bootstrap: ## Install the package and dev tools
	uv sync

test: ## All tests
	uv run pytest -q

lint: ## ruff lint and format check; warnings are errors
	uv run ruff check .
	uv run ruff format --check .

coverage: ## Tests with the 100% branch coverage gate
	uv run pytest -q --cov --cov-report=term-missing --cov-report=xml
