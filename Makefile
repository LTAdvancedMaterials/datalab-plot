# Local check loop for datalab-plot. No CI service — run `make check` before
# committing. Dev tools come from the `dev` dependency group: `uv sync`.
.PHONY: check lint fmt fmt-check types test cov

# The one command to run before committing: lint + types + tests.
check: lint types test

lint:
	uv run ruff check src tests

fmt:
	uv run ruff format src tests

fmt-check:
	uv run ruff format --check src tests

types:
	uv run mypy

test:
	uv run pytest

cov:
	uv run pytest --cov=datalab_plot --cov-report=term-missing
