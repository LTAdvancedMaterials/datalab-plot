# Local check loop for datalab-plot. GitHub Actions runs the same targets on
# push and PR (.github/workflows/ci.yml); run `make check` before committing so
# you find it first. Dev tools come from the `dev` dependency group: `uv sync`.
.PHONY: check lint fmt fmt-check types test cov drift

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

# --- Sync with the upstream internal codebase ---------------------------
# A few modules here are backports from a private internal application. See
# SYNC.md for what that means for anyone editing them.
#
# Where that checkout lives, which paths to compare, and the commit we last
# synced from are local configuration rather than public facts, so they live in
# sync.local.mk, which is untracked. SYNC.md gives the shape.
-include sync.local.mk

# List upstream commits touching the backported modules since the last sync.
drift:
	@test -n "$(UPSTREAM_PATH)" || { \
	  echo "UPSTREAM_PATH is unset. Create sync.local.mk (see SYNC.md), or"; \
	  echo "pass it inline: make drift UPSTREAM_PATH=/path/to/checkout"; \
	  exit 1; }
	@test -d "$(UPSTREAM_PATH)/.git" || { \
	  echo "$(UPSTREAM_PATH) is not a git checkout."; exit 1; }
	@test -n "$(UPSTREAM_SYNCED)" || { \
	  echo "UPSTREAM_SYNCED is unset. Set it in sync.local.mk."; exit 1; }
	@test -n "$(UPSTREAM_PATHS)" || { \
	  echo "UPSTREAM_PATHS is unset. Set it in sync.local.mk."; exit 1; }
	@git -C "$(UPSTREAM_PATH)" cat-file -e $(UPSTREAM_SYNCED) 2>/dev/null || { \
	  echo "Commit $(UPSTREAM_SYNCED) not found in $(UPSTREAM_PATH). Fetch, or fix UPSTREAM_SYNCED."; \
	  exit 1; }
	@echo "Upstream commits touching the backported modules since $(UPSTREAM_SYNCED):"
	@git -C "$(UPSTREAM_PATH)" log $(UPSTREAM_SYNCED)..HEAD --oneline -- $(UPSTREAM_PATHS) \
	  | sed 's/^/  /' \
	  || true
	@git -C "$(UPSTREAM_PATH)" log $(UPSTREAM_SYNCED)..HEAD --oneline -- $(UPSTREAM_PATHS) \
	  | grep -q . \
	  || echo "  (none - in sync)"
