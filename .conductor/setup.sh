#!/usr/bin/env bash
# Conductor setup script for datalab-plot.
#
# Runs once when a new workspace is created, from the workspace directory in a
# non-interactive shell. Goal: a fresh workspace that's immediately runnable —
# dependencies installed and credentials in place.
set -euo pipefail

echo "==> datalab-plot workspace setup"

# --- PATH ------------------------------------------------------------------
# Non-interactive shells don't source your full profile, so tools installed to
# ~/.local/bin or Homebrew may be missing. Add the usual suspects up front.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: 'uv' not found on PATH. Install it: https://docs.astral.sh/uv/" >&2
  exit 1
fi
echo "    uv: $(command -v uv) ($(uv --version))"

# --- Credentials -----------------------------------------------------------
# .worktreeinclude should already have copied .env from the repo root. This is
# a belt-and-suspenders fallback: pull it from the root if it's missing, and
# fall back to .env.example so the GUI at least boots (Connect modal) when no
# credentials exist yet.
if [ ! -f .env ]; then
  if [ -n "${CONDUCTOR_ROOT_PATH:-}" ] && [ -f "$CONDUCTOR_ROOT_PATH/.env" ]; then
    cp "$CONDUCTOR_ROOT_PATH/.env" .env
    echo "    .env: copied from repo root"
  elif [ -f .env.example ]; then
    cp .env.example .env
    echo "    .env: seeded from .env.example — edit DATALAB_API_KEY before connecting"
  else
    echo "    .env: none found — set DATALAB_URL / DATALAB_API_KEY before connecting" >&2
  fi
else
  echo "    .env: present"
fi

# --- Dependencies ----------------------------------------------------------
# All extras = runtime + gui + picker + dev tooling, into this workspace's own
# .venv (it can't be shared across worktrees — absolute paths).
echo "    installing dependencies (uv sync --all-extras)..."
uv sync --all-extras

echo "==> setup complete. Click Run to launch the GUI."
