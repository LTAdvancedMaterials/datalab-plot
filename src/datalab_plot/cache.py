"""Cache directory resolution."""
from __future__ import annotations

import os
from pathlib import Path

import platformdirs


def cache_dir() -> Path:
    """Return the directory used to cache downloaded item files.

    Resolution order:
      1. ``$DATALAB_PLOT_CACHE`` if set.
      2. ``./cache/datalab_plot/`` if ``./cache`` already exists in CWD
         (so in-repo runs reuse the existing folder).
      3. The platform-appropriate user cache dir.
    """
    env = os.environ.get("DATALAB_PLOT_CACHE")
    if env:
        path = Path(env).expanduser()
    elif (Path.cwd() / "cache").is_dir():
        path = Path.cwd() / "cache" / "datalab_plot"
    else:
        path = Path(platformdirs.user_cache_dir("datalab-plot"))
    path.mkdir(parents=True, exist_ok=True)
    return path
