#!/usr/bin/env bash
# Conductor run script for datalab-plot — launches the Dash GUI.
#
# Bound to the Run button. Uses CONDUCTOR_PORT so each workspace gets its own
# port and several can run side by side (runScriptMode: concurrent). The CLI
# also probes upward for a free port, so a collision is never fatal.
set -euo pipefail

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

PORT="${CONDUCTOR_PORT:-8050}"

echo "==> datalab-plot GUI on http://localhost:${PORT}"
# --no-browser: Conductor manages the tab; don't pop a system browser.
exec uv run datalab-plot gui --no-browser --port "$PORT"
